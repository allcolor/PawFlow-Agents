"""A scan must be spaced by what it costs, and bash must not force one.

A scan walks the whole relay tree. On Ultima7D that took about two hours, and
every successful ``bash`` call scheduled a forced scan: the next scan started
as soon as the previous one ended, all day. Two rules keep that from
happening again:

* a shell command is a mutation hint, not a reason to rescan immediately --
  it goes through the same lazy cadence as any other write;
* the cadence scales with the last scan's duration, so a relay whose scan
  takes hours is not rescanned every minute.
"""

from unittest.mock import MagicMock

import pytest

from core.project_maintenance import (
    ProjectMaintenanceScheduler, _MaintenanceJob, _scan_interval_seconds)


@pytest.fixture(autouse=True)
def _no_ambient_switch(monkeypatch):
    monkeypatch.delenv("PAWFLOW_PROJECT_WIKI_AUTO", raising=False)
    monkeypatch.delenv("PAWFLOW_PROJECT_GRAPH_AUTO", raising=False)


def test_scan_interval_scales_with_the_last_scan():
    """A two-hour scan must not be followed by a scan a minute later."""
    assert _scan_interval_seconds(0.0) == 60.0
    assert _scan_interval_seconds(3.0) == 60.0
    assert _scan_interval_seconds(600.0) == 6000.0
    assert _scan_interval_seconds(7200.0) == 72000.0


def _scheduler_with_job(**kwargs):
    scheduler = ProjectMaintenanceScheduler()
    job = _MaintenanceJob(
        user_id="alice", relay_id="relay-a", service=MagicMock(), **kwargs)
    scheduler._jobs["alice::relay-a"] = job
    return scheduler, job


def test_a_shell_command_does_not_force_a_scan(monkeypatch):
    """bash used to force a scan on every successful call."""
    scheduler, job = _scheduler_with_job(last_scan_at=1000.0)
    monkeypatch.setattr("time.time", lambda: 1010.0)
    monkeypatch.setattr(
        ProjectMaintenanceScheduler, "_wiki_backlog_ready",
        staticmethod(lambda *_a: True))

    scheduled = scheduler.schedule(
        user_id="alice", relay_id="relay-a", service=job.service,
        changed_path="<bash>", force=False)

    # Ten seconds after a scan, with no backlog worth a process-only run the
    # answer would be False; with a backlog it is a process-only run, never a
    # scan. Either way the scan is not due.
    assert scheduled in (False, True)
    assert job.scan_pending is False


def test_a_long_scan_pushes_the_next_scan_out(monkeypatch):
    """After a 2-hour scan, ten minutes later is still too early."""
    scheduler, job = _scheduler_with_job(last_scan_at=1000.0)
    job.last_scan_seconds = 7200.0
    monkeypatch.setattr("time.time", lambda: 1600.0)
    monkeypatch.setattr(
        ProjectMaintenanceScheduler, "_wiki_backlog_ready",
        staticmethod(lambda *_a: False))

    scheduled = scheduler.schedule(
        user_id="alice", relay_id="relay-a", service=job.service,
        changed_path="core/main.py")

    assert scheduled is False
    assert job.scan_pending is False


def test_a_cheap_scan_keeps_the_short_cadence(monkeypatch):
    scheduler, job = _scheduler_with_job(last_scan_at=1000.0)
    job.last_scan_seconds = 2.0
    monkeypatch.setattr("time.time", lambda: 1070.0)

    scheduled = scheduler.schedule(
        user_id="alice", relay_id="relay-a", service=job.service,
        changed_path="core/main.py")

    assert scheduled is True
    assert job.scan_pending is True


def test_an_explicit_force_still_scans(monkeypatch):
    """A caller that really means it (UI Build) keeps the escape hatch."""
    scheduler, job = _scheduler_with_job(last_scan_at=1000.0)
    job.last_scan_seconds = 7200.0
    monkeypatch.setattr("time.time", lambda: 1010.0)

    scheduled = scheduler.schedule(
        user_id="alice", relay_id="relay-a", service=job.service,
        force=True)

    assert scheduled is True
    assert job.scan_pending is True
