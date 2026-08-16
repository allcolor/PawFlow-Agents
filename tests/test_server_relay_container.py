"""Atomic Docker-name ownership for managed server relay containers."""

import threading

import pytest

from core import _server_relay_container as relay_container


@pytest.fixture(autouse=True)
def _reset_container_start_state():
    with relay_container._LOCKS_GUARD:
        relay_container._LOCKS.clear()
        relay_container._GENERATIONS.clear()


def _owned(container_id="old", running=True):
    return {
        "id": container_id,
        "running": running,
        "labels": {
            relay_container.PAWFLOW_SPAWNED_LABEL: "1",
            relay_container.PAWFLOW_SERVER_LABEL: "server-1",
            relay_container._KIND_LABEL: relay_container._MANAGED_RELAY_KIND,
        },
    }


def _result(args, code=0, stdout="", stderr=""):
    return relay_container.subprocess.CompletedProcess(args, code, stdout, stderr)


def test_overlapping_starts_run_docker_once_and_reuse_the_winner(monkeypatch):
    state = {"container": None}
    first_run_started = threading.Event()
    release_first_run = threading.Event()
    run_calls = []
    monkeypatch.setattr(relay_container, "get_server_id", lambda: "server-1")
    monkeypatch.setattr(
        relay_container, "_inspect_container", lambda _name: state["container"])

    def fake_run(args, **_kwargs):
        run_calls.append(args)
        first_run_started.set()
        assert release_first_run.wait(2)
        state["container"] = _owned("new", running=True)
        return _result(args, stdout="new\n")

    monkeypatch.setattr(relay_container.subprocess, "run", fake_run)
    results = []
    threads = [threading.Thread(target=lambda: results.append(
        relay_container.start_managed_relay_container(
            "pawflow-relay-srv-test", ["docker", "run"]))) for _ in range(2)]
    threads[0].start()
    assert first_run_started.wait(2)
    threads[1].start()
    release_first_run.set()
    for thread in threads:
        thread.join(2)

    assert not any(thread.is_alive() for thread in threads)
    assert run_calls == [["docker", "run"]]
    assert sorted(results) == [("new", False), ("new", True)]


def test_explicit_replace_removes_owned_container_before_start(monkeypatch):
    monkeypatch.setattr(relay_container, "get_server_id", lambda: "server-1")
    monkeypatch.setattr(
        relay_container, "_inspect_container", lambda _name: _owned())
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        if "rm" in args:
            return _result(args, stdout="old\n")
        return _result(args, stdout="new\n")

    monkeypatch.setattr(relay_container.subprocess, "run", fake_run)

    assert relay_container.start_managed_relay_container(
        "pawflow-relay-srv-test", ["docker", "run"], replace=True,
    ) == ("new", False)
    assert calls == [["docker", "rm", "-f", "old"], ["docker", "run"]]


def test_preexisting_healthy_container_is_replaced_without_overlap(monkeypatch):
    monkeypatch.setattr(relay_container, "get_server_id", lambda: "server-1")
    monkeypatch.setattr(
        relay_container, "_inspect_container", lambda _name: _owned())
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        if "rm" in args:
            return _result(args, stdout="old\n")
        return _result(args, stdout="new\n")

    monkeypatch.setattr(relay_container.subprocess, "run", fake_run)

    assert relay_container.start_managed_relay_container(
        "pawflow-relay-srv-test", ["docker", "run"],
    ) == ("new", False)
    assert calls == [["docker", "rm", "-f", "old"], ["docker", "run"]]


def test_foreign_container_is_never_removed(monkeypatch):
    foreign = _owned()
    foreign["labels"][relay_container.PAWFLOW_SERVER_LABEL] = "other-server"
    monkeypatch.setattr(relay_container, "get_server_id", lambda: "server-1")
    monkeypatch.setattr(
        relay_container, "_inspect_container", lambda _name: foreign)
    monkeypatch.setattr(
        relay_container.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("Docker must not be called"),
    )

    with pytest.raises(RuntimeError, match="not a managed relay owned"):
        relay_container.start_managed_relay_container(
            "pawflow-relay-srv-test", ["docker", "run"], replace=True)


def test_failed_removal_prevents_a_conflicting_run(monkeypatch):
    monkeypatch.setattr(relay_container, "get_server_id", lambda: "server-1")
    monkeypatch.setattr(
        relay_container, "_inspect_container", lambda _name: _owned())
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        return _result(args, code=1, stderr="daemon refused")

    monkeypatch.setattr(relay_container.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="daemon refused"):
        relay_container.start_managed_relay_container(
            "pawflow-relay-srv-test", ["docker", "run"], replace=True)
    assert calls == [["docker", "rm", "-f", "old"]]
