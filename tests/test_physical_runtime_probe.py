"""The real-runtime harness must fail closed when evidence is incomplete."""

import json
from types import SimpleNamespace

import pytest

from tests import physical_runtime_probe as probe


def test_probe_refuses_execution_without_disposable_opt_in(monkeypatch):
    monkeypatch.delenv("PAWFLOW_DISPOSABLE_ACCEPTANCE", raising=False)
    with pytest.raises(RuntimeError, match="explicitly disposable"):
        probe.run_acceptance()


def test_probe_exports_use_separate_staging_home_and_synthetic_credentials():
    first, second = probe.exports_for_round(1)
    assert first["root_mount"] != second["root_mount"]
    assert first["home_mount"] != second["home_mount"]
    assert first["mode"] == "rw" and second["mode"] == "ro"
    assert first["environment"]["PAWFLOW_PROBE_TOKEN"] != second["environment"]["PAWFLOW_PROBE_TOKEN"]
    assert probe.exports_for_round(2)[0]["home_mount"] == first["home_mount"]


def test_descendant_evidence_excludes_unrelated_processes():
    table = {10: {"parent": 1, "start": "a"},
             11: {"parent": 10, "start": "b"},
             12: {"parent": 11, "start": "c"},
             13: {"parent": 1, "start": "d"}}
    assert probe.descendants(10, table) == {10: "a", 11: "b", 12: "c"}


def test_cleanup_rejects_a_surviving_owned_process(monkeypatch):
    monkeypatch.setattr(probe, "process_table", lambda: {11: {"parent": 1, "start": "owned"}})
    times = iter([0, 6])
    monkeypatch.setattr(probe.time, "monotonic", lambda: next(times))
    with pytest.raises(RuntimeError, match="survived"):
        probe.wait_cleanup({11: "owned"})


def test_cleanup_does_not_confuse_a_reused_pid_with_an_owned_process(monkeypatch):
    monkeypatch.setattr(probe, "process_table", lambda: {11: {"parent": 1, "start": "new"}})
    probe.wait_cleanup({11: "owned"})


def test_missing_worker_report_fails_when_supervisor_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "source", lambda name: tmp_path / name)
    with pytest.raises(RuntimeError, match="before both"):
        probe.wait_reports(SimpleNamespace(poll=lambda: 1), 1)


def test_worker_reports_with_shared_namespace_do_not_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "source", lambda name: tmp_path / name)
    for name in ("alpha", "beta"):
        home = tmp_path / name / "home"
        home.mkdir(parents=True)
        (home / "probe-1.json").write_text(json.dumps({
            "namespaces": {"mnt": "shared"},
        }))
    with pytest.raises(RuntimeError, match="share namespace"):
        probe.wait_reports(SimpleNamespace(poll=lambda: None), 1)
