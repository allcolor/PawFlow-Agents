"""Resumable universal installer state machine."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from pathlib import Path

from pawflow_installer.commands import CommandSpec, server_install_command
from pawflow_installer.events import InstallEvent, redact
from pawflow_installer.models import InstallRequest, utc_now
from pawflow_installer.preflight import client_preflight, target_preflight
from pawflow_installer.reachability import (
    probe_install_api,
    resolve_server_url,
    wizard_url,
)
from pawflow_installer.relay_desktop import (
    autostart_plan,
    broad_shared_paths,
    install_commands,
    parse_verification,
    relay_start_command,
    relay_verify_command,
    server_add_command,
    server_login_command,
    verify_artifact,
    workspace_add_commands,
    workspace_names,
    write_autostart,
)
from pawflow_installer.state import InstallerStateStore, OperationState, StepResult
from pawflow_installer.transports.base import InstallTransport
from pawflow_installer.transports.local import LocalTransport
from pawflow_installer.transports.ssh import SshTransport

PHASES = (
    "request_validated",
    "local_preflight",
    "target_discovery",
    "target_preflight",
    "reachability_plan",
    "server_payload_ready",
    "server_installing",
    "server_health",
    "wizard_ready",
    "relay_desktop_preflight",
    "relay_desktop_installing",
    "relay_desktop_pairing",
    "relay_desktop_configuring",
    "relay_desktop_starting",
    "relay_desktop_verifying",
    "completed",
)
MUTATING_PHASES = frozenset({
    "server_payload_ready",
    "server_installing",
    "relay_desktop_installing",
    "relay_desktop_pairing",
    "relay_desktop_configuring",
    "relay_desktop_starting",
})
_RELAY_PHASES = frozenset(phase for phase in PHASES if phase.startswith("relay_desktop_"))
_BOOTSTRAP_KEY = re.compile(
    r"(?i)(initial bootstrap private gateway key\s*:\s*)(\S+)"
)


class ConfirmationRequired(RuntimeError):
    pass


class InstallerEngine:
    def __init__(
        self,
        *,
        state_store: InstallerStateStore,
        scripts_root: Path,
        client_transport: InstallTransport | None = None,
        event_sink: Callable[[InstallEvent], None] | None = None,
        secret_sink: Callable[[str, str], None] | None = None,
        certificate_confirmation: Callable[[str], bool] | None = None,
        broad_path_confirmation: Callable[[list[str]], bool] | None = None,
    ):
        self.state_store = state_store
        self.scripts_root = Path(scripts_root)
        self.client_transport = client_transport or LocalTransport()
        self.event_sink = event_sink
        self.secret_sink = secret_sink
        self.certificate_confirmation = certificate_confirmation
        self.broad_path_confirmation = broad_path_confirmation
        self._cancelled = threading.Event()
        self._target_transport: InstallTransport | None = None
        self._target_os = ""
        self._server_url = ""
        self._bootstrap_key = ""

    @staticmethod
    def plan(request: InstallRequest) -> list[dict]:
        return [
            {
                "step_id": phase,
                "mutating": phase in MUTATING_PHASES,
                "skipped": phase in _RELAY_PHASES and not request.relay_desktop.install,
            }
            for phase in PHASES
        ]

    def cancel(self) -> None:
        self._cancelled.set()
        self.client_transport.cancel()
        if self._target_transport is not None:
            self._target_transport.cancel()

    def _event(
        self, state: OperationState, step_id: str, kind: str, message: str, **data
    ) -> None:
        if self.event_sink is not None:
            self.event_sink(InstallEvent(
                operation_id=state.operation_id,
                step_id=step_id,
                kind=kind,
                message=message,
                data=redact(data),
            ))

    def _transport(self, request: InstallRequest) -> InstallTransport:
        if self._target_transport is None:
            self._target_transport = (
                self.client_transport
                if request.target.kind == "local"
                else SshTransport(request.target)
            )
        return self._target_transport

    def _check_cancelled(self, state: OperationState) -> None:
        if self._cancelled.is_set() or state.cancelled:
            state.cancelled = True
            self.state_store.save(state)
            raise RuntimeError("installer operation was cancelled")

    def run(
        self,
        request: InstallRequest,
        *,
        confirmed: bool,
        operation_id: str | None = None,
    ) -> OperationState:
        state = (
            self.state_store.load(operation_id)
            if operation_id
            else self.state_store.create(request)
        )
        self.state_store.assert_matches(state, request)
        for phase in PHASES:
            self._check_cancelled(state)
            if phase in state.completed_steps:
                self._restore_runtime_evidence(state, phase)
                continue
            if phase in _RELAY_PHASES and not request.relay_desktop.install:
                self._complete(state, phase, {"skipped": True})
                continue
            if phase in MUTATING_PHASES and not confirmed:
                raise ConfirmationRequired(
                    f"explicit confirmation is required before phase {phase}"
                )
            self._execute_phase(state, request, phase)
        return state

    def _execute_phase(
        self, state: OperationState, request: InstallRequest, phase: str
    ) -> None:
        previous = state.step_results.get(phase)
        if previous and previous.status == "failed" and phase == "server_installing":
            recovered = self._probe_server(request)
            if recovered.get("ready"):
                self._complete(state, phase, {"recovered_from_health": True})
                return
        state.phase = phase
        state.step_results[phase] = StepResult(
            status="running",
            started_at=utc_now(),
            finished_at=None,
            evidence={},
            error=None,
        )
        self.state_store.save(state)
        self._event(state, phase, "step_started", f"Starting {phase}")
        try:
            evidence = self._phase(state, request, phase)
        except Exception as exc:
            state.step_results[phase] = StepResult(
                status="failed",
                started_at=state.step_results[phase].started_at,
                finished_at=utc_now(),
                evidence={},
                error=str(redact(str(exc))),
            )
            self.state_store.save(state)
            self._event(state, phase, "step_failed", str(exc))
            raise
        self._complete(state, phase, evidence)

    def _complete(self, state: OperationState, phase: str, evidence: dict) -> None:
        previous = state.step_results.get(phase)
        state.step_results[phase] = StepResult(
            status="completed",
            started_at=previous.started_at if previous else utc_now(),
            finished_at=utc_now(),
            evidence=redact(evidence),
            error=None,
        )
        if phase not in state.completed_steps:
            state.completed_steps.append(phase)
        state.phase = phase
        self.state_store.save(state)
        self._event(state, phase, "step_completed", f"Completed {phase}", **evidence)

    def _restore_runtime_evidence(self, state: OperationState, phase: str) -> None:
        evidence = state.step_results[phase].evidence
        if phase == "target_discovery":
            self._target_os = str(evidence.get("system") or "")
        elif phase == "reachability_plan":
            self._server_url = str(evidence.get("server_url") or "")

    def _phase(
        self, state: OperationState, request: InstallRequest, phase: str
    ) -> dict:
        transport = self._transport(request)
        if phase == "request_validated":
            return {"request_digest": request.digest()}
        if phase == "local_preflight":
            report = client_preflight(request, self.state_store.root)
            if not report.passed:
                raise RuntimeError("client preflight failed")
            return report.as_evidence()
        if phase == "target_discovery":
            self._target_os, architecture = transport.platform()
            return {"system": self._target_os, "architecture": architecture}
        if phase == "target_preflight":
            report = target_preflight(
                request,
                transport,
                str(self.scripts_root) if request.target.kind == "local" else None,
            )
            if not report.passed:
                raise RuntimeError("target preflight failed")
            return report.as_evidence()
        if phase == "reachability_plan":
            self._server_url = resolve_server_url(request)
            return {
                "server_url": self._server_url,
                "wizard_url": wizard_url(self._server_url),
                "mode": request.reachability.mode,
            }
        if phase == "server_payload_ready":
            if request.target.kind == "local":
                required = [
                    self.scripts_root / "install-pawflow.sh",
                    self.scripts_root / "install-pawflow.ps1",
                ]
                missing = [str(path) for path in required if not path.is_file()]
                if missing:
                    raise FileNotFoundError(", ".join(missing))
                return {"scripts_root": str(self.scripts_root)}
            remote_root = f".pawflow-installer/{state.operation_id}"
            prepared = transport.run(
                CommandSpec(("mkdir", "-p", remote_root), mutating=True))
            if not prepared.ok:
                raise RuntimeError(
                    prepared.stderr.strip() or "could not prepare remote installer payload")
            for name in (
                "install-pawflow.sh",
                "install-pawflow.ps1",
                "doctor-pawflow.sh",
                "doctor-pawflow.ps1",
            ):
                transport.upload(self.scripts_root / name, f"{remote_root}/{name}")
            return {"scripts_root": remote_root}
        if phase == "server_installing":
            root = str(self.scripts_root)
            payload = state.step_results.get("server_payload_ready")
            if payload:
                root = str(payload.evidence.get("scripts_root") or root)
            command = server_install_command(request, self._target_os, root)
            result = transport.run(command, self._output_handler(state, phase))
            if not result.ok:
                raise RuntimeError(result.stderr.strip() or "PawFlow installer failed")
            return {"returncode": result.returncode}
        if phase == "server_health":
            return self._probe_server(request)
        if phase == "wizard_ready":
            return {
                "wizard_url": wizard_url(self._server_url),
                "install_complete": self._probe_server(request).get(
                    "install_complete", False
                ),
            }
        if phase == "relay_desktop_preflight":
            if (
                not self.client_transport.command_exists("pawflow-relay")
                and not request.relay_desktop.artifact_path
            ):
                raise RuntimeError(
                    "Relay Desktop is not installed and no verified artifact path was provided"
                )
            return {"installed": self.client_transport.command_exists("pawflow-relay")}
        if phase == "relay_desktop_installing":
            if self.client_transport.command_exists("pawflow-relay"):
                return {"already_installed": True}
            artifact = Path(str(request.relay_desktop.artifact_path))
            if not artifact.is_file():
                raise FileNotFoundError(str(artifact))
            verify_artifact(artifact, str(request.relay_desktop.artifact_sha256))
            system, _architecture = self.client_transport.platform()
            install_root = Path.home() / ".local" / "bin"
            for command in install_commands(artifact, system, install_root):
                result = self.client_transport.run(command, self._output_handler(state, phase))
                if not result.ok:
                    raise RuntimeError(result.stderr.strip() or "Relay Desktop install failed")
            return {"artifact": artifact.name}
        if phase == "relay_desktop_pairing":
            for command in (
                server_add_command(request.relay_desktop),
                server_login_command(request.relay_desktop),
            ):
                result = self.client_transport.run(command, self._output_handler(state, phase))
                if not result.ok:
                    raise RuntimeError(result.stderr.strip() or "Relay Desktop pairing failed")
            return {"server_name": request.relay_desktop.server_name}
        if phase == "relay_desktop_configuring":
            broad = broad_shared_paths(request.relay_desktop)
            if broad and (
                self.broad_path_confirmation is None
                or not self.broad_path_confirmation(broad)
            ):
                raise ConfirmationRequired(
                    "broad Relay Desktop paths require a second explicit confirmation")
            names = []
            for command in workspace_add_commands(request.relay_desktop):
                result = self.client_transport.run(command, self._output_handler(state, phase))
                if not result.ok:
                    raise RuntimeError(
                        result.stderr.strip() or "Relay Desktop configuration failed"
                    )
                names.append(command.argv[3])
            autostart = []
            if request.relay_desktop.autostart:
                system, _architecture = self.client_transport.platform()
                for name in names:
                    definition = autostart_plan(system, "pawflow-relay", name)
                    write_autostart(definition)
                    for command in definition.commands:
                        result = self.client_transport.run(
                            command, self._output_handler(state, phase))
                        if not result.ok:
                            raise RuntimeError(
                                result.stderr.strip() or "Relay autostart setup failed")
                    autostart.append(str(definition.path or "windows-task"))
            return {"workspaces": names, "autostart": autostart}
        if phase == "relay_desktop_starting":
            processes = {}
            for name, _path in workspace_names(request.relay_desktop):
                processes[name] = self.client_transport.start(
                    relay_start_command(name), self._output_handler(state, phase)
                )
            return {"processes": processes}
        if phase == "relay_desktop_verifying":
            verified = []
            for name, _path in workspace_names(request.relay_desktop):
                result = self.client_transport.run(relay_verify_command(name))
                if not result.ok:
                    raise RuntimeError(result.stderr.strip() or "Relay verification failed")
                evidence = parse_verification(result.stdout)
                if not evidence["connected"]:
                    raise RuntimeError(f"Relay {evidence['relay_id']} is not connected")
                verified.append(evidence)
            return {"relays": verified}
        if phase == "completed":
            return {"wizard_url": wizard_url(self._server_url)}
        raise ValueError(f"unknown installer phase: {phase}")

    def _output_handler(
        self, state: OperationState, phase: str
    ) -> Callable[[str, str], None]:
        def handle(channel: str, line: str) -> None:
            match = _BOOTSTRAP_KEY.search(line)
            if match:
                if self.secret_sink is not None:
                    self.secret_sink("Initial bootstrap Private Gateway key", match.group(2))
                self._bootstrap_key = match.group(2)
                line = _BOOTSTRAP_KEY.sub(r"\1[REDACTED]", line)
            self._event(state, phase, "command_output", line.rstrip(), channel=channel)
        return handle

    def _probe_server(self, request: InstallRequest) -> dict:
        if not self._server_url:
            self._server_url = resolve_server_url(request)
        accepted = request.reachability.certificate_sha256
        status = probe_install_api(
            self._server_url,
            accepted_certificate_sha256=accepted,
            confirm_certificate=self.certificate_confirmation,
            gateway_key=self._bootstrap_key or None,
        )
        ready = status.status in {200, 401, 403}
        if not ready:
            raise RuntimeError(f"PawFlow bootstrap endpoint returned HTTP {status.status}")
        return {
            "ready": True,
            "status": status.status,
            "install_complete": bool(status.payload.get("install_complete")),
            "authenticated": status.status == 200,
            "certificate_sha256": status.certificate_sha256,
        }
