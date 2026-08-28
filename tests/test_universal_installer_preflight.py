
from pawflow_installer.preflight import client_preflight, target_preflight
from pawflow_installer.transports.base import CommandResult
from tests.universal_installer_fixtures import install_request


class FakeTransport:
    def __init__(self, *, docker=True, daemon=True):
        self.docker = docker
        self.daemon = daemon
        self.commands = []

    def platform(self):
        return "linux", "x86_64"

    def command_exists(self, command):
        if command == "docker":
            return self.docker
        return True

    def run(self, command, on_output=None):
        self.commands.append(command)
        ok = self.daemon if command.argv[:2] == ("docker", "info") else True
        return CommandResult(0 if ok else 1, "OK\n" if ok else "", "failed" if not ok else "")

    def upload(self, source, destination):
        raise AssertionError("preflight must not upload")

    def start(self, command, on_output=None):
        raise AssertionError("preflight must not start")

    def cancel(self):
        pass


def test_client_preflight_accepts_creatable_state_directory(tmp_path):
    request = install_request()
    report = client_preflight(request, tmp_path / "missing" / "operations")
    assert report.passed is True


def test_target_preflight_classifies_daemon_failure_without_mutation():
    request = install_request()
    transport = FakeTransport(daemon=False)
    report = target_preflight(request, transport)
    assert report.passed is False
    assert all(command.mutating is False for command in transport.commands)
    assert any(check.check_id == "docker_daemon" for check in report.checks)


def test_remote_preflight_defers_canonical_doctor_until_verified_payload():
    report = target_preflight(install_request(target="ssh"), FakeTransport())
    doctor = next(check for check in report.checks if check.check_id == "canonical_doctor")
    assert doctor.status == "warning"
    assert report.passed is True
