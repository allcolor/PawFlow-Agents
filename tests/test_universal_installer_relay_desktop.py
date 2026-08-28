from pathlib import Path

import pytest

from pawflow_installer.relay_desktop import (
    RelayDesktopArtifact,
    autostart_plan,
    broad_shared_paths,
    parse_verification,
    render_launch_agent,
    render_systemd_unit,
    select_artifact,
    workspace_add_commands,
    workspace_names,
)
from tests.universal_installer_fixtures import install_request


def test_artifact_selection_is_platform_and_architecture_specific():
    assets = [
        RelayDesktopArtifact(
            "PawFlow Relay Desktop-0.1.0-linux-x86_64.AppImage", "https://a"
        ),
        RelayDesktopArtifact(
            "PawFlow Relay Desktop Setup 0.1.0-win-x64.exe", "https://b"
        ),
    ]
    selected = select_artifact(assets, system="linux", machine="amd64")
    assert selected.url == "https://a"


def test_multiple_paths_become_distinct_explicit_workspace_profiles():
    request = install_request(relay=True)
    config = request.relay_desktop.model_copy(deep=True)
    config.paths.append("/srv/other")
    names = workspace_names(config)
    assert names == [("work", "/srv/work"), ("work-2", "/srv/other")]
    commands = workspace_add_commands(config)
    assert commands[0].argv[3] == "work"
    assert commands[1].argv[3] == "work-2"
    assert "--mode" in commands[0].argv
    assert "rw" in commands[0].argv
    assert "--no-remote-desktop" in commands[0].argv


def test_read_only_capability_maps_to_read_only_relay():
    config = install_request(relay=True).relay_desktop.model_copy(deep=True)
    config.capabilities = ["filesystem.read"]
    command = workspace_add_commands(config)[0]
    assert command.argv[command.argv.index("--mode") + 1] == "ro"
    assert "--no-exec" in command.argv


def test_verification_requires_server_connected_field():
    assert parse_verification('{"relay_id":"fs_a","connected":true}') == {
        "relay_id": "fs_a",
        "connected": True,
    }
    with pytest.raises(ValueError):
        parse_verification('{"relay_id":"fs_a"}')


def test_autostart_definitions_are_secret_free():
    systemd = render_systemd_unit("/usr/bin/pawflow-relay", "work")
    launchd = render_launch_agent("/usr/bin/pawflow-relay", "work")
    assert "pawflow-relay start work" in systemd
    assert b"pawflow-relay" in launchd
    assert b"token" not in launchd.lower()
    assert "token" not in systemd.lower()


def test_linux_autostart_plan_is_per_user_and_opt_in(tmp_path):
    definition = autostart_plan(
        "linux", "/usr/bin/pawflow-relay", "work", home=tmp_path)
    assert definition.path == (
        tmp_path / ".config" / "systemd" / "user" / "pawflow-relay-work.service")
    assert definition.commands[1].argv == (
        "systemctl", "--user", "enable", "--now", "pawflow-relay-work.service")


def test_home_share_is_reported_for_second_confirmation():
    config = install_request(relay=True).relay_desktop.model_copy(deep=True)
    config.paths = [str(Path.home())]
    assert broad_shared_paths(config) == [str(Path.home())]
