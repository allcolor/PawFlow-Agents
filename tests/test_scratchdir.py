"""Unit tests for ScratchDir contracts and durable metadata."""

import json

import pytest

from core import paths
from core.handlers.scratchdir import ScratchDirHandler
from core.scratchdir_models import (
    MAX_TTL_HOURS,
    ScratchDirError,
    ScratchDirState,
    require_scope,
    validate_quotas,
    validate_ttl,
)
from core.scratchdir_store import ScratchDirStore
from core.tool_registry import create_default_registry


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SCRATCHDIRS_DIR", tmp_path / "scratchdirs")
    ScratchDirStore._instance = None
    value = ScratchDirStore.instance()
    yield value
    ScratchDirStore._instance = None


def test_scope_ttl_and_quota_validation():
    assert require_scope("u", "c", "a", "r") == ("u", "c", "a", "r")
    with pytest.raises(ScratchDirError) as exc:
        require_scope("u", "", "a", "r")
    assert exc.value.code == "scratchdir_context_missing"
    assert validate_ttl(None) == 168
    assert validate_ttl(MAX_TTL_HOURS) == MAX_TTL_HOURS
    with pytest.raises(ScratchDirError):
        validate_ttl(0)
    assert validate_quotas() == (1024 * 1024 * 1024, 10_000)
    with pytest.raises(ScratchDirError):
        validate_quotas(0, 1)


def test_activate_is_scoped_idempotent_and_public_shape_hides_locator(store):
    first = store.activate(
        "u", "c", "a", "r", locator="opaque-1", operation_id="op-1",
        now=100.0)
    same = store.activate(
        "u", "c", "a", "r", locator="opaque-other",
        operation_id="op-1", now=101.0)
    other_agent = store.activate(
        "u", "c", "b", "r", locator="opaque-2",
        operation_id="op-2", now=102.0)

    assert same == first
    assert other_agent.id != first.id
    assert first.state == ScratchDirState.ACTIVE.value
    assert first.epoch == first.revision == 1
    public = first.public_dict()
    assert public["format"] == "pawflow.scratchdir.v1"
    assert public["url"] == "fs://scratchdir/"
    assert public["mount_path"] == "/scratch"
    assert "locator" not in public
    assert "user_id" not in public
    assert "conversation_id" not in public
    assert "agent_name" not in public


def test_expiry_and_renewal_are_explicit(store):
    store.activate(
        "u", "c", "a", "r", locator="opaque", operation_id="op",
        ttl_hours=1, now=100.0)
    assert store.get("u", "c", "a", "r", now=3699.0).state == "active"
    assert store.get("u", "c", "a", "r", now=3700.0).state == "expired"
    with pytest.raises(ScratchDirError) as exc:
        store.renew("u", "c", "a", "r", now=3700.0)
    assert exc.value.code == "scratchdir_not_active"

    store.activate(
        "u", "c", "a", "r", locator="opaque-new",
        operation_id="op-new", ttl_hours=1, now=3800.0)
    renewed = store.renew(
        "u", "c", "a", "r", ttl_hours=2, now=3900.0)
    assert renewed.epoch == 2
    assert renewed.expires_at == 3900.0 + 7200
    assert renewed.revision == 4


def test_usage_clear_and_operation_fencing(store):
    store.activate(
        "u", "c", "a", "r", locator="opaque", operation_id="create",
        now=100.0)
    used = store.update_usage(
        "u", "c", "a", "r", observed_bytes=42, observed_files=3,
        reconciled_at=110.0)
    assert (used.observed_bytes, used.observed_files) == (42, 3)

    clearing = store.begin_clear(
        "u", "c", "a", "r", operation_id="clear-1", now=120.0)
    assert clearing.state == "clearing"
    assert clearing.epoch == 2
    same = store.begin_clear(
        "u", "c", "a", "r", operation_id="clear-1", now=121.0)
    assert same == clearing
    with pytest.raises(ScratchDirError) as exc:
        store.finish_clear(
            "u", "c", "a", "r", operation_id="wrong", now=122.0)
    assert exc.value.code == "scratchdir_state_conflict"
    cleared = store.finish_clear(
        "u", "c", "a", "r", operation_id="clear-1", now=123.0)
    assert cleared.state == "cleared"
    assert cleared.locator == ""
    assert cleared.observed_bytes == cleared.observed_files == 0
    assert store.finish_clear(
        "u", "c", "a", "r", operation_id="clear-1", now=124.0) == cleared


def test_context_hint_is_metadata_only(store):
    assert store.context_hint("u", "c", "a", "r") == ""
    store.activate(
        "u", "c", "a", "r", locator="secret-physical-path",
        operation_id="op")
    hint = store.context_hint("u", "c", "a", "r")
    assert "ScratchDir: active" in hint
    assert "fs://scratchdir/" in hint
    assert "secret-physical-path" not in hint


def test_handler_schema_and_fail_closed_before_lifecycle_wiring():
    handler = ScratchDirHandler()
    assert handler.name == "scratchdir"
    assert handler.parameters_schema["required"] == ["action"]
    assert set(handler.parameters_schema["properties"]["action"]["enum"]) == {
        "status", "ensure", "renew", "clear"}
    assert "scratchdir_context_missing" in handler.execute({"action": "status"})
    handler.set_user_id("u")
    handler.set_conversation_id("c")
    handler.set_agent_name("a")
    assert "scratchdir_unavailable" in handler.execute({"action": "ensure"})


def test_handler_delegates_authenticated_scope_to_manager():
    calls = []

    class Manager:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return {"format": "pawflow.scratchdir.v1", "status": "active"}

    handler = ScratchDirHandler()
    handler.set_user_id("u")
    handler.set_conversation_id("c")
    handler.set_agent_name("a")
    handler.set_scratchdir_manager(Manager())
    payload = json.loads(handler.execute({"action": "ensure", "ttl_hours": 12}))
    assert payload["status"] == "active"
    assert calls == [{
        "action": "ensure",
        "user_id": "u",
        "conversation_id": "c",
        "agent_name": "a",
        "ttl_hours": 12,
    }]


def test_default_registry_exposes_scratchdir():
    assert create_default_registry().get("scratchdir") is not None
