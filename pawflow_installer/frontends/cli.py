"""Terminal frontend for the PawFlow universal installer."""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from pydantic import ValidationError

from pawflow_installer.engine import ConfirmationRequired, InstallerEngine
from pawflow_installer.events import InstallEvent, redact
from pawflow_installer.models import InstallRequest
from pawflow_installer.reachability import wizard_url
from pawflow_installer.relay_desktop import broad_shared_paths
from pawflow_installer.state import InstallerStateStore


def _add_request_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--request", help="Read the complete InstallRequest JSON from this file")
    parser.add_argument("--target", choices=["local", "ssh"])
    parser.add_argument("--host")
    parser.add_argument("--ssh-port", type=int)
    parser.add_argument("--user")
    parser.add_argument("--identity-file")
    parser.add_argument("--host-key-policy", choices=["strict", "accept-new"])
    parser.add_argument("--pawflow-home")
    parser.add_argument("--port", type=int)
    parser.add_argument("--version")
    parser.add_argument("--source", dest="install_source", choices=["published", "source"])
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--keep-old-images", action="store_true")
    parser.add_argument("--skip-apparmor", action="store_true")
    parser.add_argument(
        "--reachability",
        choices=["local", "tailscale", "existing_https", "public_manual"],
    )
    parser.add_argument("--reachability-host")
    parser.add_argument("--certificate-sha256")
    parser.add_argument("--relay-desktop", action="store_true")
    parser.add_argument("--relay-server-url")
    parser.add_argument("--relay-server-name")
    parser.add_argument("--relay-workspace-name")
    parser.add_argument("--share", action="append", default=[])
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--relay-autostart", action="store_true")
    parser.add_argument("--relay-artifact")
    parser.add_argument("--relay-artifact-sha256")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawflow-install",
        description="Install PawFlow locally or over SSH with one resumable engine.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        command = sub.add_parser(name)
        _add_request_arguments(command)
        command.add_argument("--json", action="store_true")
        if name == "run":
            command.add_argument(
                "--yes", action="store_true", help="Confirm all planned mutations"
            )
            command.add_argument("--no-open", action="store_true")

    resume = sub.add_parser("resume")
    resume.add_argument("operation_id")
    resume.add_argument("--yes", action="store_true")
    resume.add_argument("--json", action="store_true")
    resume.add_argument("--no-open", action="store_true")

    status = sub.add_parser("status")
    status.add_argument("operation_id", nargs="?")
    status.add_argument("--json", action="store_true")

    cancel = sub.add_parser("cancel")
    cancel.add_argument("operation_id")

    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("operation_id")
    cleanup.add_argument("--yes", action="store_true")

    sub.add_parser("gui")
    return parser


def _require_args(args: argparse.Namespace, names: list[str]) -> None:
    missing = [name.replace("_", "-") for name in names if getattr(args, name) is None]
    if missing:
        raise ValueError(f"missing required options: {', '.join('--' + name for name in missing)}")


def request_from_args(args: argparse.Namespace) -> InstallRequest:
    if args.request:
        with open(args.request, encoding="utf-8") as handle:
            return InstallRequest.model_validate(json.load(handle))
    _require_args(args, ["target", "pawflow_home", "port", "install_source", "reachability"])
    target = {"kind": args.target}
    if args.target == "ssh":
        _require_args(args, ["host", "ssh_port", "user", "host_key_policy"])
        target.update({
            "host": args.host,
            "port": args.ssh_port,
            "user": args.user,
            "identity_file": args.identity_file,
            "host_key_policy": args.host_key_policy,
        })
    relay = {
        "install": bool(args.relay_desktop),
        "server_url": args.relay_server_url if args.relay_desktop else None,
        "server_name": args.relay_server_name if args.relay_desktop else None,
        "workspace_name": args.relay_workspace_name if args.relay_desktop else None,
        "capabilities": list(args.capability) if args.relay_desktop else [],
        "paths": list(args.share) if args.relay_desktop else [],
        "autostart": bool(args.relay_autostart) if args.relay_desktop else False,
        "artifact_path": args.relay_artifact if args.relay_desktop else None,
        "artifact_sha256": (
            args.relay_artifact_sha256 if args.relay_desktop else None
        ),
    }
    return InstallRequest.model_validate({
        "version": 1,
        "target": target,
        "install": {
            "pawflow_home": args.pawflow_home,
            "port": args.port,
            "version": args.version,
            "source": args.install_source,
            "native": bool(args.native),
            "keep_old_images": bool(args.keep_old_images),
            "skip_apparmor": bool(args.skip_apparmor),
        },
        "reachability": {
            "mode": args.reachability,
            "hostname": args.reachability_host,
            "certificate_sha256": args.certificate_sha256,
        },
        "relay_desktop": relay,
    })


class TerminalOutput:
    def __init__(self, json_mode: bool):
        self.json_mode = json_mode

    def event(self, event: InstallEvent) -> None:
        data = event.as_dict()
        if self.json_mode:
            print(json.dumps(data, sort_keys=True), flush=True)
        else:
            print(
                f"[{data['created_at']}] {data['step_id']}: "
                f"{data['kind']} — {data['message']}",
                flush=True,
            )

    def secret(self, label: str, value: str) -> None:
        stream = sys.stderr
        print(f"{label}: {value}", file=stream, flush=True)

    def confirm_certificate(self, fingerprint: str) -> bool:
        print(
            "The PawFlow server presented an untrusted HTTPS certificate.",
            file=sys.stderr,
        )
        print(f"SHA-256: {fingerprint}", file=sys.stderr)
        answer = input("Trust this exact certificate for this operation? [y/N] ")
        return answer.strip().lower() in {"y", "yes", "o", "oui"}

    def confirm_broad_paths(self, paths: list[str]) -> bool:
        print(
            "Relay Desktop would expose broad filesystem paths:",
            file=sys.stderr,
        )
        for path in paths:
            print(f"  - {path}", file=sys.stderr)
        answer = input("Confirm these exact broad shares? [y/N] ")
        return answer.strip().lower() in {"y", "yes", "o", "oui"}


def _confirmed(args: argparse.Namespace) -> bool:
    if getattr(args, "yes", False):
        return True
    answer = input("Execute the mutating steps in this plan? [y/N] ")
    return answer.strip().lower() in {"y", "yes", "o", "oui"}


def _engine(store: InstallerStateStore, output: TerminalOutput) -> InstallerEngine:
    return InstallerEngine(
        state_store=store,
        scripts_root=Path(__file__).resolve().parents[2] / "scripts",
        event_sink=output.event,
        secret_sink=output.secret,
        certificate_confirmation=output.confirm_certificate,
        broad_path_confirmation=output.confirm_broad_paths,
    )


def _print(value: object, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(redact(value), indent=2, sort_keys=True))
    else:
        print(json.dumps(redact(value), indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = InstallerStateStore()
    if args.command == "gui":
        from pawflow_installer.frontends.gui import main as gui_main
        gui_main()
        return 0
    try:
        if args.command == "plan":
            request = request_from_args(args)
            result = {
                "request": request.semantic_payload(),
                "request_digest": request.digest(),
                "steps": InstallerEngine.plan(request),
                "broad_shared_paths": broad_shared_paths(request.relay_desktop),
            }
            _print(result, args.json)
            return 0
        if args.command == "run":
            request = request_from_args(args)
            output = TerminalOutput(args.json)
            state = _engine(store, output).run(request, confirmed=_confirmed(args))
            if not args.no_open:
                webbrowser.open(wizard_url(
                    state.step_results["reachability_plan"].evidence["server_url"]
                ))
            _print({"operation_id": state.operation_id, "phase": state.phase}, args.json)
            return 0
        if args.command == "resume":
            state = store.load(args.operation_id)
            request = InstallRequest.model_validate(state.request)
            output = TerminalOutput(args.json)
            state = _engine(store, output).run(
                request,
                confirmed=_confirmed(args),
                operation_id=args.operation_id,
            )
            if not args.no_open:
                webbrowser.open(wizard_url(
                    state.step_results["reachability_plan"].evidence["server_url"]
                ))
            _print({"operation_id": state.operation_id, "phase": state.phase}, args.json)
            return 0
        if args.command == "status":
            states = [store.load(args.operation_id)] if args.operation_id else store.list()
            _print([state.model_dump(mode="json") for state in states], args.json)
            return 0
        if args.command == "cancel":
            state = store.mark_cancelled(args.operation_id)
            _print({"operation_id": state.operation_id, "cancelled": True}, False)
            return 0
        if args.command == "cleanup":
            if not args.yes:
                answer = input(
                    "Remove only this installer's operation state and logs? [y/N] "
                )
                if answer.strip().lower() not in {"y", "yes", "o", "oui"}:
                    return 1
            store.cleanup(args.operation_id)
            _print({"operation_id": args.operation_id, "cleaned": True}, False)
            return 0
    except (OSError, RuntimeError, ValueError, ValidationError, ConfirmationRequired) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 1
