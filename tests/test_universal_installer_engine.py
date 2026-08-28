import json

from pawflow_installer.engine import PHASES, InstallerEngine
from pawflow_installer.reachability import InstallApiStatus
from pawflow_installer.state import InstallerStateStore
from pawflow_installer.transports.base import CommandResult
from tests.universal_installer_fixtures import install_request


class FakeTransport:
    def __init__(self):
        self.commands = []
        self.uploads = []
        self.started = []
        self.cancelled = False

    def platform(self):
        return "linux", "x86_64"

    def command_exists(self, command):
        return True

    def run(self, command, on_output=None):
        self.commands.append(command)
        if on_output and "install-pawflow.sh" in " ".join(command.argv):
            on_output("stdout", "Initial bootstrap Private Gateway key: topsecret\n")
        if command.argv[:3] == ("pawflow-relay", "--json", "verify"):
            return CommandResult(0, '{"relay_id":"fs_client_work","connected":true}', "")
        return CommandResult(0, "OK\n", "")

    def upload(self, source, destination):
        self.uploads.append((source, destination))

    def start(self, command, on_output=None):
        self.started.append(command)
        return 4000 + len(self.started)

    def cancel(self):
        self.cancelled = True


def _scripts(root):
    root.mkdir()
    for name in (
        "install-pawflow.sh",
        "install-pawflow.ps1",
        "doctor-pawflow.sh",
        "doctor-pawflow.ps1",
    ):
        (root / name).write_text("# test\n", encoding="utf-8")


def _probe(*args, **kwargs):
    return InstallApiStatus(
        url="https://localhost:9443/install/api",
        status=200,
        payload={"install_complete": False},
        certificate_sha256="a" * 64,
    )


def test_engine_completes_all_non_relay_phases_and_redacts_bootstrap_key(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("pawflow_installer.engine.probe_install_api", _probe)
    scripts = tmp_path / "scripts"
    _scripts(scripts)
    transport = FakeTransport()
    events = []
    secrets = []
    engine = InstallerEngine(
        state_store=InstallerStateStore(tmp_path / "state"),
        scripts_root=scripts,
        client_transport=transport,
        event_sink=lambda event: events.append(event.as_dict()),
        secret_sink=lambda label, value: secrets.append((label, value)),
    )
    state = engine.run(install_request(), confirmed=True)

    assert state.phase == "completed"
    assert state.completed_steps == list(PHASES)
    assert secrets == [("Initial bootstrap Private Gateway key", "topsecret")]
    assert "topsecret" not in json.dumps(events)
    assert all(
        state.step_results[phase].evidence.get("skipped")
        for phase in PHASES if phase.startswith("relay_desktop_")
    )


def test_engine_resumes_without_repeating_completed_mutations(monkeypatch, tmp_path):
    monkeypatch.setattr("pawflow_installer.engine.probe_install_api", _probe)
    scripts = tmp_path / "scripts"
    _scripts(scripts)
    transport = FakeTransport()
    store = InstallerStateStore(tmp_path / "state")
    request = install_request()
    engine = InstallerEngine(
        state_store=store,
        scripts_root=scripts,
        client_transport=transport,
    )
    first = engine.run(request, confirmed=True)
    count = len(transport.commands)
    second = engine.run(
        request, confirmed=True, operation_id=first.operation_id
    )
    assert second.phase == "completed"
    assert len(transport.commands) == count


def test_relay_completion_requires_server_observed_connectivity(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("pawflow_installer.engine.probe_install_api", _probe)
    monkeypatch.setattr(
        "pawflow_installer.preflight._relay_keychain_available", lambda: True
    )
    scripts = tmp_path / "scripts"
    _scripts(scripts)
    transport = FakeTransport()
    state = InstallerEngine(
        state_store=InstallerStateStore(tmp_path / "state"),
        scripts_root=scripts,
        client_transport=transport,
    ).run(install_request(relay=True), confirmed=True)
    evidence = state.step_results["relay_desktop_verifying"].evidence
    assert evidence["relays"][0]["connected"] is True
    assert transport.started[0].argv[:2] == ("pawflow-relay", "start")


def test_broad_relay_share_requires_a_distinct_confirmation(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("pawflow_installer.engine.probe_install_api", _probe)
    monkeypatch.setattr(
        "pawflow_installer.preflight._relay_keychain_available", lambda: True
    )
    scripts = tmp_path / "scripts"
    _scripts(scripts)
    request = install_request(relay=True)
    request.relay_desktop.paths = ["/"]
    confirmed_paths = []
    state = InstallerEngine(
        state_store=InstallerStateStore(tmp_path / "state"),
        scripts_root=scripts,
        client_transport=FakeTransport(),
        broad_path_confirmation=lambda paths: confirmed_paths.extend(paths) or True,
    ).run(request, confirmed=True)
    assert state.phase == "completed"
    assert confirmed_paths == ["/"]
