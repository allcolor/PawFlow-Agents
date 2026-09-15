"""Static physical relay plans preserve the existing workspace identities."""

import json
from dataclasses import FrozenInstanceError

import pytest

from pawflow_relay import manager
from pawflow_relay.physical_plan import plan_physical_relay


def share(name="a", **changes):
    record = {
        "name": name, "server": "prod", "path": f"/projects/{name}",
        "relay_id": f"fs_owner_{name}", "docker_image": "relay:verified",
        "mode": "rw", "allow_exec": True, "allow_remote_desktop": True,
        "allow_local": False, "allow_service_tunnels": False,
    }
    record.update(changes)
    return record


def test_group_has_private_workspace_and_original_home_volumes():
    plan = plan_physical_relay("work", [share(), share("b", mode="ro")])
    assert plan.physical_id == "work"
    assert plan.server == "prod"
    assert plan.docker_image == "relay:verified"
    a, b = plan.exports
    assert (a.relay_id, b.relay_id) == ("fs_owner_a", "fs_owner_b")
    assert a.workspace_target == b.workspace_target == "/workspace"
    assert a.home_target == b.home_target == "/home/pawflow"
    assert a.home_volume == "pawflow_home_fs_owner_a"
    assert b.home_volume == "pawflow_home_fs_owner_b"
    assert a.root_source == "/projects/a"
    assert b.mode == "ro"
    assert a.root_mount != b.root_mount
    assert a.home_mount != b.home_mount
    assert a.root_mount.startswith("/run/pawflow-physical/")
    assert a.home_mount.startswith("/run/pawflow-physical/")


def test_single_share_uses_existing_identity_and_default_image():
    plan = plan_physical_relay("work", [share(docker_image="")])
    assert plan.docker_image == "pawflow-relay-dev:latest"
    assert len(plan.exports) == 1
    assert plan.exports[0].home_volume == "pawflow_home_fs_owner_a"


def test_reordered_shares_do_not_require_a_restart():
    a, b = share(), share("b")
    first = plan_physical_relay("work", [a, b])
    second = plan_physical_relay("work", [b, a])
    assert first == second
    assert first.revision == second.revision


@pytest.mark.parametrize("change", [
    {"path": "/projects/replacement"},
    {"mode": "ro"},
    {"allow_exec": False},
    {"allow_remote_desktop": False},
    {"allow_local": True},
    {"allow_service_tunnels": True},
])
def test_effective_workspace_changes_require_new_physical_revision(change):
    first = plan_physical_relay("work", [share()])
    changed = plan_physical_relay("work", [share(**change)])
    assert first.revision != changed.revision


def test_adding_or_removing_a_directory_changes_revision_without_changing_home():
    first = plan_physical_relay("work", [share()])
    second = plan_physical_relay("work", [share(), share("b")])
    assert first.revision != second.revision
    assert first.exports[0] == second.exports[0]
    assert plan_physical_relay("work", [share()]).revision == first.revision


def test_plan_is_snapshot_and_does_not_retain_credentials():
    record = share(session_token="do-not-copy", gateway_key="private-key")
    plan = plan_physical_relay("work", [record])
    revision = plan.revision
    record.update(path="/changed", mode="ro")
    assert plan.exports[0].root_source == "/projects/a"
    assert plan.exports[0].mode == "rw"
    assert plan.revision == revision
    assert "do-not-copy" not in repr(plan)
    assert "private-key" not in repr(plan)
    with pytest.raises(FrozenInstanceError):
        plan.exports[0].root_source = "/changed"


def test_bookkeeping_fields_do_not_restart_group():
    first = plan_physical_relay("work", [share()])
    second = plan_physical_relay("work", [share(created_at="later", updated_at="later")])
    assert first.revision == second.revision


@pytest.mark.parametrize("records", [
    [],
    [share(), share()],
    [share(), share("b", relay_id="fs_owner_a")],
    [share(), share("b", server="other")],
    [share(), share("b", docker_image="other:image")],
    [share(relay_id="")],
    [share(relay_id="../other")],
    [share(mode="readmaybe")],
    [share(path="")],
    [share(server="")],
    [share(allow_exec="false")],
    [share(docker_image=0)],
])
def test_invalid_or_incompatible_group_fails_before_launch(records):
    with pytest.raises(ValueError):
        plan_physical_relay("work", records)


@pytest.mark.parametrize("physical_id", ["", "../work", "a/b", "a,b"])
def test_physical_identity_is_required_and_path_safe(physical_id):
    with pytest.raises(ValueError):
        plan_physical_relay(physical_id, [share()])


def test_opaque_staging_names_do_not_collide_for_long_similar_relay_ids():
    a = "fs_owner_" + "x" * 70 + "a"
    b = "fs_owner_" + "x" * 70 + "b"
    plan = plan_physical_relay("work", [share(relay_id=a), share("b", relay_id=b)])
    assert plan.exports[0].root_mount != plan.exports[1].root_mount


def test_manager_plans_named_workspaces_without_mutating_config(monkeypatch, tmp_path):
    home = tmp_path / "relay-home"
    home.mkdir()
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    records = {
        "a": share(path=str(first)),
        "b": share("b", path=str(second), mode="ro"),
    }
    config = home / "workspaces.json"
    config.write_text(json.dumps(records), encoding="utf-8")
    before = config.read_bytes()
    monkeypatch.setenv("PAWFLOW_RELAY_HOME", str(home))
    plan = manager.plan_workspaces("work", ["a", "b"])
    assert len(plan.exports) == 2
    assert plan.exports[1].mode == "ro"
    assert config.read_bytes() == before
    assert sorted(path.name for path in home.iterdir()) == ["workspaces.json"]


def test_manager_rejects_missing_directory_before_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(manager, "_load_json", lambda filename: {
        "a": share(path=str(tmp_path / "missing"))})
    with pytest.raises(ValueError, match="directory"):
        manager.plan_workspaces("work", ["a"])


@pytest.mark.parametrize("names", [[], "a", ["a", "a"]])
def test_manager_requires_explicit_nonempty_distinct_workspace_names(names):
    with pytest.raises(ValueError):
        manager.plan_workspaces("work", names)
