import pytest

from pawflow_installer.commands import doctor_command, server_install_command
from tests.universal_installer_fixtures import install_request


def test_unix_published_install_invokes_canonical_script_with_exact_flags():
    request = install_request()
    command = server_install_command(request, "linux", "/bundle/scripts")
    assert command.mutating is True
    assert command.argv == (
        "bash",
        "/bundle/scripts/install-pawflow.sh",
        "--port",
        "9443",
        "--home",
        "/srv/pawflow",
        "--pull-images",
        "--version",
        "1.0.0-beta.247",
    )


def test_unix_source_options_are_forwarded_without_shell_interpolation():
    request = install_request(source="source")
    request.install.native = True
    request.install.skip_apparmor = True
    request.install.keep_old_images = True
    command = server_install_command(request, "macos", "/bundle/scripts")
    assert "--from-source" in command.argv
    assert "--native" in command.argv
    assert "--skip-apparmor" in command.argv
    assert "--keep-old-images" in command.argv


def test_windows_rejects_source_options_instead_of_guessing_wsl():
    request = install_request(source="source")
    with pytest.raises(ValueError, match="WSL2"):
        server_install_command(request, "windows", r"C:\bundle\scripts")


def test_doctor_is_read_only_and_uses_source_flag():
    request = install_request(source="source")
    command = doctor_command(request, "linux", "/bundle/scripts")
    assert command.mutating is False
    assert command.argv[-1] == "--source"
