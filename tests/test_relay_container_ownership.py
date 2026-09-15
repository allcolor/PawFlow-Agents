"""Docker relay cleanup must never target a neighbouring workspace."""
from types import SimpleNamespace

import pytest

from pawflow_relay import _thread_base as base


@pytest.mark.parametrize("left,right", [
    ("fs_allcolor_d0bbacb3", "fs_allcolor_6c7334fb"),
    ("fs_client_12345678", "fs_client_12349999"),
    ("relay.a", "relay_a"),
    ("relay_a", "relay-a"),
])
def test_container_identity_uses_the_complete_unmodified_relay_id(left, right):
    assert base._relay_container_prefix(left) != base._relay_container_prefix(right)
    assert base._relay_container_prefix(left) == base._relay_container_prefix(left)


@pytest.mark.parametrize("relay_id", ["", None])
def test_container_identity_requires_a_relay_id(relay_id):
    with pytest.raises(ValueError):
        base._relay_container_prefix(relay_id)


def test_cleanup_removes_only_exact_owned_names_even_with_broad_docker_results(monkeypatch):
    relay_id = "fs_allcolor_d0bbacb3"
    monkeypatch.setattr(base.secrets, "token_hex", lambda size: "0123abcd")
    owned = base._make_relay_container_name(relay_id, "relay")
    neighbour = base._make_relay_container_name("fs_allcolor_6c7334fb", "relay")
    rows = [
        ("owned", owned),
        ("neighbour", neighbour),
        ("legacy", "pf-fs-allcolor--relay-0123abcd"),
        ("substring", "unrelated-" + owned),
        ("suffix", owned + "-unrelated"),
        ("malformed", base._relay_container_prefix(relay_id) + "-relay-bad"),
    ]
    removed = []

    def docker(args, **kwargs):
        if args[1:3] == ["ps", "-a"]:
            return SimpleNamespace(returncode=0, stdout="\n".join(
                identifier + "\t" + name for identifier, name in rows))
        assert args[1:3] == ["rm", "-f"]
        removed.append(args[3])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(base, "docker_cmd", lambda: ["docker"])
    monkeypatch.setattr(base.subprocess, "run", docker)
    assert base.cleanup_relay_containers(relay_id) == 1
    assert removed == ["owned"]


def test_cleanup_reports_failed_removal_when_container_is_still_present(monkeypatch):
    relay_id = "fs_allcolor_d0bbacb3"
    name = base._make_relay_container_name(relay_id, "relay")

    def docker(args, **kwargs):
        if args[1:3] == ["ps", "-a"]:
            return SimpleNamespace(returncode=0, stdout="owned\t" + name)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(base, "docker_cmd", lambda: ["docker"])
    monkeypatch.setattr(base.subprocess, "run", docker)
    with pytest.raises(base.RelayContainerCleanupError, match="remove.*container"):
        base.cleanup_relay_containers(relay_id)


@pytest.mark.parametrize("operation", ["ps", "rm"])
@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_cleanup_reports_docker_failures(monkeypatch, operation, failure):
    relay_id = "cleanup-fixture"
    name = base._make_relay_container_name(relay_id, "relay")

    def docker(args, **kwargs):
        if args[1] == operation:
            if failure == "timeout":
                raise base.subprocess.TimeoutExpired(args, 10)
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(returncode=0, stdout="owned\t" + name)

    monkeypatch.setattr(base, "docker_cmd", lambda: ["docker"])
    monkeypatch.setattr(base.subprocess, "run", docker)
    with pytest.raises(RuntimeError, match="container"):
        base.cleanup_relay_containers(relay_id)


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_cleanup_accepts_concurrent_removal_only_after_confirming_absence(monkeypatch, failure):
    relay_id = "cleanup-fixture"
    name = base._make_relay_container_name(relay_id, "relay")
    neighbour = base._make_relay_container_name("neighbour", "relay")
    calls = []

    def docker(args, **kwargs):
        calls.append(args[1])
        if args[1] == "ps":
            rows = "neighbour\t" + neighbour
            if calls.count("ps") == 1:
                rows += "\nowned\t" + name
            return SimpleNamespace(returncode=0, stdout=rows)
        assert args[-1] == "owned"
        if failure == "timeout":
            raise base.subprocess.TimeoutExpired(args, 10)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(base, "docker_cmd", lambda: ["docker"])
    monkeypatch.setattr(base.subprocess, "run", docker)
    assert base.cleanup_relay_containers(relay_id) == 1
    assert calls == ["ps", "rm", "ps"]


def test_cleanup_does_not_accept_failed_absence_verification(monkeypatch):
    relay_id = "cleanup-fixture"
    name = base._make_relay_container_name(relay_id, "relay")
    calls = []

    def docker(args, **kwargs):
        calls.append(args[1])
        if len(calls) == 1:
            return SimpleNamespace(returncode=0, stdout="owned\t" + name)
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(base, "docker_cmd", lambda: ["docker"])
    monkeypatch.setattr(base.subprocess, "run", docker)
    with pytest.raises(RuntimeError, match="list.*containers"):
        base.cleanup_relay_containers(relay_id)
    assert calls == ["ps", "rm", "ps"]
