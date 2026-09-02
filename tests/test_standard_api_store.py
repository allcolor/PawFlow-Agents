"""Session ledger, CAS, replay, cleanup, and reconstruction contracts."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.a2a_store import A2AStore
from core._a2a_standard_api import (
    ApiRunQuotaExceeded,
    ApiSessionQuotaExceeded,
)
from core.standard_api_canonical import compute_hash_chain, eligible_prefixes
from core.standard_api_runtime import (
    finalize_api_run_with_checkpoint,
    resolve_api_turn,
)
from core.standard_api_types import (
    NormalizedApiTurn,
    NormalizedVisibleItem,
    StandardApiNamespace,
)


_CONFIG = {
    "standard_api_enabled": True,
    "api_model_id": "pawflow-agent",
    "api_permission_mode": "read_only",
    "api_session_ttl_seconds": 3600,
    "api_max_sessions_per_key": 20,
    "api_max_concurrent_runs_per_key": 4,
    "strict_fields": False,
    "api_request_overrides_json": {},
    "api_input_modalities_json": ["text"],
    "api_chat_completions_enabled": True,
    "api_responses_enabled": False,
    "api_anthropic_messages_enabled": False,
    "api_disconnect_policy": "cancel",
}


@pytest.fixture()
def configured(tmp_path, monkeypatch):
    from core import standard_api_config

    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY, "chat_completions", True)
    store = A2AStore(tmp_path / "a2a.sqlite3")
    publication = store.configure_publication(
        "alice",
        "conv-1",
        "Agent",
        standard_api_config=_CONFIG,
    )
    raw, key = store.create_key(publication["publication_id"], "client")
    namespace = StandardApiNamespace(
        publication_id=publication["publication_id"],
        api_generation=publication["api_generation"],
        key_id=key["key_id"],
        dialect="chat_completions",
        api_model_id=publication["api_model_id"],
    )
    return {
        "store": store,
        "publication": publication,
        "key": key,
        "raw": raw,
        "namespace": namespace,
    }


@pytest.fixture()
def responses_configured(tmp_path, monkeypatch):
    from core import standard_api_config

    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY, "responses", True)
    config = dict(_CONFIG)
    config["api_chat_completions_enabled"] = False
    config["api_responses_enabled"] = True
    store = A2AStore(tmp_path / "a2a-responses.sqlite3")
    publication = store.configure_publication(
        "alice",
        "conv-1",
        "Agent",
        standard_api_config=config,
    )
    raw, key = store.create_key(publication["publication_id"], "responses")
    namespace = StandardApiNamespace(
        publication_id=publication["publication_id"],
        api_generation=publication["api_generation"],
        key_id=key["key_id"],
        dialect="responses",
        api_model_id=publication["api_model_id"],
    )
    return {
        "store": store,
        "publication": publication,
        "key": key,
        "raw": raw,
        "namespace": namespace,
    }


def _item(kind, **data):
    return NormalizedVisibleItem(kind=kind, data=data)


def _finalize_head(store, namespace, session, run_id, lease_id, items):
    hashes = compute_hash_chain(namespace, items, b"secret")
    prefixes = eligible_prefixes(items, hashes)
    return store.finalize_api_run(
        run_id,
        lease_id,
        visible_head_hash=hashes[-1],
        item_count=len(items),
        prefixes=prefixes,
    ), hashes


def test_response_record_is_finalized_retrieved_isolated_and_tombstoned(
        responses_configured):
    store = responses_configured["store"]
    namespace = responses_configured["namespace"]
    session = store.create_api_session(
        namespace, "conv-1::a2a::api_response", now=100)
    admission = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-response",
        request_id="req-response",
        body_fingerprint="body-response",
        now=100,
    )
    items = (
        _item("user_message", content="hello"),
        _item("response_output", output=[{
            "type": "message",
            "id": "msg_response",
            "status": "completed",
            "role": "assistant",
            "content": [{
                "type": "output_text",
                "text": "hi",
                "annotations": [],
            }],
        }]),
    )
    hashes = compute_hash_chain(namespace, items, b"secret")
    envelope = {
        "id": "resp_response",
        "object": "response",
        "created_at": 100,
        "status": "completed",
        "model": namespace.api_model_id,
        "previous_response_id": None,
        "output": items[-1].data["output"],
    }

    store.finalize_api_run(
        "run-response",
        admission["lease_id"],
        visible_head_hash=hashes[-1],
        item_count=len(items),
        prefixes=eligible_prefixes(items, hashes),
        response_id=envelope["id"],
        response_record={
            "previous_response_id": "",
            "visible_items": items,
            "output": envelope["output"],
            "envelope": envelope,
        },
        now=101,
    )

    retrieved = store.get_api_response(
        namespace, envelope["id"], now=102)
    assert retrieved["session_id"] == session["session_id"]
    assert retrieved["visible_items"] == items
    assert retrieved["output"] == envelope["output"]
    assert retrieved["envelope"] == envelope

    other_key = store.create_key(
        responses_configured["publication"]["publication_id"], "other")[1]
    other_namespace = StandardApiNamespace(
        publication_id=namespace.publication_id,
        api_generation=namespace.api_generation,
        key_id=other_key["key_id"],
        dialect=namespace.dialect,
        api_model_id=namespace.api_model_id,
    )
    assert store.get_api_response(
        other_namespace, envelope["id"], now=102) is None

    deleted = store.delete_api_response(
        namespace, envelope["id"], now=103)
    assert deleted["response_id"] == envelope["id"]
    assert deleted["deleted_at"] == 103
    assert store.get_api_response(
        namespace, envelope["id"], now=104) is None
    assert store.delete_api_response(
        namespace, envelope["id"], now=104) is None


def test_response_store_false_is_not_retrievable_or_usable_as_parent(
        responses_configured):
    store = responses_configured["store"]
    namespace = responses_configured["namespace"]
    session = store.create_api_session(
        namespace, "conv-1::a2a::api_ephemeral", now=100)
    admission = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-ephemeral",
        request_id="req-ephemeral",
        body_fingerprint="body-ephemeral",
        now=100,
    )

    store.finalize_api_run(
        "run-ephemeral",
        admission["lease_id"],
        visible_head_hash="head-ephemeral",
        item_count=1,
        prefixes=[{
            "prefix_hash": "head-ephemeral",
            "item_count": 1,
            "boundary_kind": "response_output",
        }],
        response_id="resp_ephemeral",
        now=101,
    )

    assert store.get_api_run("run-ephemeral")["response_id"] == "resp_ephemeral"
    assert store.get_api_response(
        namespace, "resp_ephemeral", now=102) is None


def test_stored_response_expires_with_its_retention_window(
        responses_configured):
    store = responses_configured["store"]
    namespace = responses_configured["namespace"]
    session = store.create_api_session(
        namespace, "conv-1::a2a::api_expiring", now=100)
    admission = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-expiring-response",
        request_id="req-expiring-response",
        body_fingerprint="body-expiring-response",
        now=100,
    )
    item = _item("response_output", output=[])
    hashes = compute_hash_chain(namespace, (item,), b"secret")
    store.finalize_api_run(
        "run-expiring-response",
        admission["lease_id"],
        visible_head_hash=hashes[-1],
        item_count=1,
        prefixes=eligible_prefixes((item,), hashes),
        response_id="resp_expiring",
        response_record={
            "previous_response_id": "",
            "visible_items": (item,),
            "output": [],
            "envelope": {"id": "resp_expiring", "object": "response"},
        },
        now=101,
    )

    assert store.get_api_response(
        namespace, "resp_expiring", now=3700) is not None
    assert store.get_api_response(
        namespace, "resp_expiring", now=3702) is None


def test_response_record_rolls_back_when_finalization_fails(
        responses_configured):
    store = responses_configured["store"]
    namespace = responses_configured["namespace"]
    session = store.create_api_session(
        namespace, "conv-1::a2a::api_response_rollback", now=100)
    admission = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-response-rollback",
        request_id="req-response-rollback",
        body_fingerprint="body-response-rollback",
        now=100,
    )
    item = _item("response_output", output=[])
    hashes = compute_hash_chain(namespace, (item,), b"secret")
    with store._connect() as connection:
        connection.executescript(
            """
            CREATE TRIGGER fail_response_finalization
            BEFORE UPDATE OF status ON api_export_runs
            WHEN NEW.status = 'completed'
            BEGIN
                SELECT RAISE(ABORT, 'forced response finalization failure');
            END;
            """
        )

    with pytest.raises(
            sqlite3.IntegrityError,
            match="forced response finalization failure"):
        store.finalize_api_run(
            "run-response-rollback",
            admission["lease_id"],
            visible_head_hash=hashes[-1],
            item_count=1,
            prefixes=eligible_prefixes((item,), hashes),
            response_id="resp_rollback",
            response_record={
                "previous_response_id": "",
                "visible_items": (item,),
                "output": [],
                "envelope": {"id": "resp_rollback", "object": "response"},
            },
            now=101,
        )

    assert store.get_api_response(
        namespace, "resp_rollback", now=102) is None
    assert store.get_api_run("run-response-rollback")["status"] == "running"
    current = store.get_api_session(session["session_id"])
    assert current["state"] == "running"
    assert current["visible_head_hash"] == ""
    assert current["item_count"] == 0


def test_prefix_lookup_is_unique_cross_namespace_isolated_and_ambiguous(
        configured):
    store = configured["store"]
    namespace = configured["namespace"]
    items = (
        _item("user_message", content="hello"),
        _item("assistant_message", content="hi"),
    )

    first = store.create_api_session(
        namespace, "conv-1::a2a::api_first", now=100)
    admission = store.acquire_api_session(
        first["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-first",
        request_id="req-first",
        body_fingerprint="body-first",
        now=100,
    )
    _result, hashes = _finalize_head(
        store, namespace, first, "run-first", admission["lease_id"], items)

    found = store.lookup_api_prefix(
        namespace,
        [{"prefix_hash": hashes[-1], "item_count": 2}],
        now=101,
    )
    assert found["status"] == "unique"
    assert found["session"]["session_id"] == first["session_id"]

    other_key = store.create_key(
        configured["publication"]["publication_id"], "other")[1]
    other_namespace = StandardApiNamespace(
        publication_id=namespace.publication_id,
        api_generation=namespace.api_generation,
        key_id=other_key["key_id"],
        dialect=namespace.dialect,
        api_model_id=namespace.api_model_id,
    )
    assert store.lookup_api_prefix(
        other_namespace,
        [{"prefix_hash": hashes[-1], "item_count": 2}],
        now=101,
    )["status"] == "miss"

    second = store.create_api_session(
        namespace, "conv-1::a2a::api_second", now=102)
    second_admission = store.acquire_api_session(
        second["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-second",
        request_id="req-second",
        body_fingerprint="body-second",
        now=102,
    )
    _finalize_head(
        store,
        namespace,
        second,
        "run-second",
        second_admission["lease_id"],
        items,
    )
    ambiguous = store.lookup_api_prefix(
        namespace,
        [{"prefix_hash": hashes[-1], "item_count": 2}],
        now=103,
    )
    assert ambiguous["status"] == "ambiguous"
    assert ambiguous["candidate_count"] == 2


def test_head_cas_active_retry_attach_and_idempotent_finalization(configured):
    store = configured["store"]
    session = store.create_api_session(
        configured["namespace"], "conv-1::a2a::api_cas", now=100)

    acquired = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-one",
        request_id="req-one",
        body_fingerprint="body-one",
        lease_seconds=30,
        replay_window_seconds=10,
        now=100,
    )
    assert acquired["status"] == "acquired"

    attached = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-retry",
        request_id="req-retry",
        body_fingerprint="body-one",
        lease_seconds=30,
        replay_window_seconds=10,
        now=105,
    )
    assert attached["status"] == "attached"
    assert attached["run"]["run_id"] == "run-one"

    busy = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-other",
        request_id="req-other",
        body_fingerprint="different",
        now=105,
    )
    assert busy["status"] == "busy"

    finalized = store.finalize_api_run(
        "run-one",
        acquired["lease_id"],
        visible_head_hash="head-one",
        item_count=2,
        prefixes=[{
            "prefix_hash": "head-one",
            "item_count": 2,
            "boundary_kind": "assistant_message",
        }],
        now=110,
    )
    assert finalized["idempotent"] is False

    again = store.finalize_api_run(
        "run-one",
        acquired["lease_id"],
        visible_head_hash="head-one",
        item_count=2,
        prefixes=[],
        now=111,
    )
    assert again["idempotent"] is True

    stale = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-stale",
        request_id="req-stale",
        body_fingerprint="body-stale",
        now=112,
    )
    assert stale["status"] == "stale"

    current = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="head-one",
        expected_item_count=2,
        run_id="run-current",
        request_id="req-current",
        body_fingerprint="body-current",
        now=112,
    )
    assert current["status"] == "acquired"


def test_pending_client_tool_batch_settles_once_and_order_independently(
        configured):
    store = configured["store"]
    session = store.create_api_session(
        configured["namespace"], "conv-1::a2a::api_tools", now=100)
    admission = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-tools",
        request_id="req-tools",
        body_fingerprint="body-tools",
        now=100,
    )
    definitions = (
        {"name": "lookup", "description": "Lookup", "parameters": {
            "type": "object", "properties": {"q": {"type": "string"}}}},
        {"name": "weather", "description": "Weather", "parameters": {
            "type": "object", "properties": {"city": {"type": "string"}}}},
    )
    finalized = store.finalize_api_run(
        "run-tools",
        admission["lease_id"],
        visible_head_hash="head-tools",
        item_count=1,
        prefixes=[{
            "prefix_hash": "head-tools",
            "item_count": 1,
            "boundary_kind": "client_tool_call_batch",
        }],
        pending_client_tool_calls=(
            {"id": "call-lookup", "name": "lookup",
             "arguments": {"q": "PawFlow"}},
            {"id": "call-weather", "name": "weather",
             "arguments": {"city": "Brussels"}},
        ),
        client_tool_definitions=definitions,
        now=101,
    )

    assert finalized["session"]["state"] == "waiting_tool"
    batch = store.get_pending_api_tool_batch(session["session_id"])
    assert batch["state"] == "pending"
    assert [(call["call_id"], call["tool_name"]) for call in batch["calls"]] == [
        ("call-lookup", "lookup"), ("call-weather", "weather")]

    resumed = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="head-tools",
        expected_item_count=1,
        run_id="run-results",
        request_id="req-results",
        body_fingerprint="body-results",
        now=102,
    )
    assert resumed["status"] == "acquired"
    results = (
        {"id": "call-weather", "content": "sunny"},
        {"id": "call-lookup", "content": {"value": 42}},
    )
    settled = store.settle_api_tool_batch(
        "run-results", resumed["lease_id"],
        results=results,
        client_tool_definitions=definitions,
        now=103,
    )
    assert settled["idempotent"] is False
    assert settled["batch"]["state"] == "settled"
    assert all(call["state"] == "settled"
               for call in settled["batch"]["calls"])

    replay = store.settle_api_tool_batch(
        "run-results", resumed["lease_id"],
        results=results,
        client_tool_definitions=definitions,
        now=104,
    )
    assert replay["idempotent"] is True
    with pytest.raises(ValueError, match="different results"):
        store.settle_api_tool_batch(
            "run-results", resumed["lease_id"],
            results=(
                {"id": "call-weather", "content": "rain"},
                {"id": "call-lookup", "content": {"value": 42}},
            ),
            client_tool_definitions=definitions,
            now=105,
        )


def test_pending_client_tool_settlement_rejects_partial_forged_and_changed_schema(
        configured):
    store = configured["store"]
    session = store.create_api_session(
        configured["namespace"], "conv-1::a2a::api_tool_validation", now=100)
    admission = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-tool-validation",
        request_id="req-tool-validation",
        body_fingerprint="body-tool-validation",
        now=100,
    )
    definitions = ({"name": "lookup", "description": "Lookup", "parameters": {
        "type": "object", "properties": {"q": {"type": "string"}}}},)
    store.finalize_api_run(
        "run-tool-validation",
        admission["lease_id"],
        visible_head_hash="head-tool-validation",
        item_count=1,
        prefixes=[{
            "prefix_hash": "head-tool-validation",
            "item_count": 1,
            "boundary_kind": "client_tool_call_batch",
        }],
        pending_client_tool_calls=(
            {"id": "call-lookup", "name": "lookup",
             "arguments": {"q": "PawFlow"}},),
        client_tool_definitions=definitions,
        now=101,
    )
    resumed = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="head-tool-validation",
        expected_item_count=1,
        run_id="run-tool-results",
        request_id="req-tool-results",
        body_fingerprint="body-tool-results",
        now=102,
    )

    with pytest.raises(ValueError, match="every pending"):
        store.settle_api_tool_batch(
            "run-tool-results", resumed["lease_id"],
            results=(), client_tool_definitions=definitions, now=103)
    with pytest.raises(ValueError, match="unknown"):
        store.settle_api_tool_batch(
            "run-tool-results", resumed["lease_id"],
            results=({"id": "call-forged", "content": "x"},),
            client_tool_definitions=definitions, now=103)
    with pytest.raises(ValueError, match="schema changed"):
        store.settle_api_tool_batch(
            "run-tool-results", resumed["lease_id"],
            results=({"id": "call-lookup", "content": "x"},),
            client_tool_definitions=({"name": "lookup", "description": "Lookup",
                                      "parameters": {"type": "object"}},),
            now=103)
    assert store.get_pending_api_tool_batch(
        session["session_id"])["state"] == "pending"


def test_generation_reset_cancels_pending_client_tools(configured):
    store = configured["store"]
    session = store.create_api_session(
        configured["namespace"], "conv-1::a2a::api_tool_reset", now=100)
    admission = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-tool-reset",
        request_id="req-tool-reset",
        body_fingerprint="body-tool-reset",
        now=100,
    )
    definitions = ({"name": "lookup", "description": "Lookup",
                    "parameters": {"type": "object"}},)
    store.finalize_api_run(
        "run-tool-reset",
        admission["lease_id"],
        visible_head_hash="head-tool-reset",
        item_count=1,
        prefixes=[{
            "prefix_hash": "head-tool-reset",
            "item_count": 1,
            "boundary_kind": "client_tool_call_batch",
        }],
        pending_client_tool_calls=(
            {"id": "call-reset", "name": "lookup", "arguments": {}},),
        client_tool_definitions=definitions,
        now=101,
    )

    store.reset_api_sessions(
        configured["publication"]["publication_id"], now=102)
    batch = store.get_api_tool_batch_for_run("run-tool-reset")
    assert batch["state"] == "canceled"
    assert batch["calls"][0]["state"] == "canceled"
    expired = store.get_api_session(session["session_id"])
    assert expired["state"] == "quarantined"
    assert expired["expires_at"] == 102


def test_pending_tool_finalization_rolls_back_as_one_transaction(configured):
    store = configured["store"]
    session = store.create_api_session(
        configured["namespace"], "conv-1::a2a::api_tool_rollback", now=100)
    admission = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-tool-rollback",
        request_id="req-tool-rollback",
        body_fingerprint="body-tool-rollback",
        now=100,
    )
    with store._connect() as connection:
        connection.executescript(
            """
            CREATE TRIGGER fail_api_tool_call_insert
            BEFORE INSERT ON api_export_tool_calls
            BEGIN
                SELECT RAISE(ABORT, 'forced tool call failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced tool call failure"):
        store.finalize_api_run(
            "run-tool-rollback",
            admission["lease_id"],
            visible_head_hash="head-tool-rollback",
            item_count=1,
            prefixes=[{
                "prefix_hash": "head-tool-rollback",
                "item_count": 1,
                "boundary_kind": "client_tool_call_batch",
            }],
            pending_client_tool_calls=(
                {"id": "call-rollback", "name": "lookup", "arguments": {}},),
            client_tool_definitions=(
                {"name": "lookup", "description": "Lookup",
                 "parameters": {"type": "object"}},),
            now=101,
        )

    assert store.get_api_session(session["session_id"])["state"] == "running"
    assert store.get_api_run("run-tool-rollback")["status"] == "running"
    assert store.get_api_tool_batch_for_run("run-tool-rollback") is None
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM api_export_prefixes").fetchone()[0] == 0


def test_expired_successor_lease_restores_settled_tool_batch(configured):
    store = configured["store"]
    session = store.create_api_session(
        configured["namespace"], "conv-1::a2a::api_tool_expire", now=100)
    first = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-tool-call",
        request_id="req-tool-call",
        body_fingerprint="body-tool-call",
        now=100,
    )
    definitions = ({"name": "lookup", "description": "Lookup",
                    "parameters": {"type": "object"}},)
    store.finalize_api_run(
        "run-tool-call",
        first["lease_id"],
        visible_head_hash="head-tool-call",
        item_count=1,
        prefixes=[{
            "prefix_hash": "head-tool-call",
            "item_count": 1,
            "boundary_kind": "client_tool_call_batch",
        }],
        pending_client_tool_calls=(
            {"id": "call-expire", "name": "lookup", "arguments": {}},),
        client_tool_definitions=definitions,
        now=101,
    )
    successor = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="head-tool-call",
        expected_item_count=1,
        run_id="run-tool-result-expire",
        request_id="req-tool-result-expire",
        body_fingerprint="body-tool-result-expire",
        lease_seconds=5,
        now=102,
    )
    store.settle_api_tool_batch(
        "run-tool-result-expire",
        successor["lease_id"],
        results=({"id": "call-expire", "content": "ok"},),
        client_tool_definitions=definitions,
        now=103,
    )

    assert store.abandon_expired_api_runs(now=108) == 1
    restored = store.get_api_tool_batch_for_run("run-tool-call")
    assert restored["state"] == "pending"
    assert restored["settled_by_run_id"] == ""
    assert restored["calls"][0]["state"] == "pending"
    assert restored["calls"][0]["result_fingerprint"] == ""


def test_failed_successor_run_restores_tool_batch_and_quarantines_session(
        configured):
    store = configured["store"]
    session = store.create_api_session(
        configured["namespace"], "conv-1::a2a::api_tool_fail", now=100)
    first = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-tool-call-fail",
        request_id="req-tool-call-fail",
        body_fingerprint="body-tool-call-fail",
        now=100,
    )
    definitions = ({"name": "lookup", "description": "Lookup",
                    "parameters": {"type": "object"}},)
    store.finalize_api_run(
        "run-tool-call-fail",
        first["lease_id"],
        visible_head_hash="head-tool-call-fail",
        item_count=1,
        prefixes=[{
            "prefix_hash": "head-tool-call-fail",
            "item_count": 1,
            "boundary_kind": "client_tool_call_batch",
        }],
        pending_client_tool_calls=(
            {"id": "call-fail", "name": "lookup", "arguments": {}},),
        client_tool_definitions=definitions,
        now=101,
    )
    successor = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="head-tool-call-fail",
        expected_item_count=1,
        run_id="run-tool-result-fail",
        request_id="req-tool-result-fail",
        body_fingerprint="body-tool-result-fail",
        now=102,
    )
    store.settle_api_tool_batch(
        "run-tool-result-fail",
        successor["lease_id"],
        results=({"id": "call-fail", "content": "ok"},),
        client_tool_definitions=definitions,
        now=103,
    )

    assert store.fail_api_run(
        "run-tool-result-fail",
        successor["lease_id"],
        error_code="agent_error",
        now=104,
    ) is True
    assert store.fail_api_run(
        "run-tool-result-fail",
        successor["lease_id"],
        error_code="agent_error",
        now=105,
    ) is False
    assert store.get_api_run("run-tool-result-fail")["status"] == "failed"
    assert store.get_api_session(session["session_id"])["state"] == "quarantined"
    restored = store.get_api_tool_batch_for_run("run-tool-call-fail")
    assert restored["state"] == "pending"
    assert restored["calls"][0]["state"] == "pending"


def test_session_and_run_quotas_are_transactional(tmp_path, monkeypatch):
    from core import standard_api_config

    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY, "chat_completions", True)
    store = A2AStore(tmp_path / "a2a.sqlite3")
    config = dict(
        _CONFIG,
        api_max_sessions_per_key=2,
        api_max_concurrent_runs_per_key=1,
    )
    publication = store.configure_publication(
        "alice", "conv-1", "Agent", standard_api_config=config)
    _raw, key = store.create_key(publication["publication_id"], "client")
    namespace = StandardApiNamespace(
        publication_id=publication["publication_id"],
        api_generation=1,
        key_id=key["key_id"],
        dialect="chat_completions",
        api_model_id="pawflow-agent",
    )
    first = store.create_api_session(
        namespace, "conv-1::a2a::api_one", now=100)
    second = store.create_api_session(
        namespace, "conv-1::a2a::api_two", now=100)
    with pytest.raises(ApiSessionQuotaExceeded):
        store.create_api_session(
            namespace, "conv-1::a2a::api_three", now=100)

    store.acquire_api_session(
        first["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-one",
        request_id="req-one",
        body_fingerprint="body-one",
        now=100,
    )
    with pytest.raises(ApiRunQuotaExceeded):
        store.acquire_api_session(
            second["session_id"],
            expected_head_hash="",
            expected_item_count=0,
            run_id="run-two",
            request_id="req-two",
            body_fingerprint="body-two",
            now=100,
        )


def test_session_quota_serializes_across_store_instances(tmp_path, monkeypatch):
    from core import standard_api_config

    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY, "chat_completions", True)
    database = tmp_path / "a2a.sqlite3"
    primary = A2AStore(database)
    config = dict(_CONFIG, api_max_sessions_per_key=1)
    publication = primary.configure_publication(
        "alice", "conv-1", "Agent", standard_api_config=config)
    _raw, key = primary.create_key(publication["publication_id"], "client")
    namespace = StandardApiNamespace(
        publication_id=publication["publication_id"],
        api_generation=publication["api_generation"],
        key_id=key["key_id"],
        dialect="chat_completions",
        api_model_id=publication["api_model_id"],
    )
    stores = (A2AStore(database), A2AStore(database))
    barrier = threading.Barrier(2)

    def create(index):
        barrier.wait()
        try:
            stores[index].create_api_session(
                namespace, f"conv-1::a2a::api_quota_{index}", now=100)
        except ApiSessionQuotaExceeded:
            return "quota"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create, range(2)))

    assert sorted(outcomes) == ["created", "quota"]


def test_exact_retry_attaches_before_run_quota_across_store_instances(
        tmp_path, monkeypatch):
    from core import standard_api_config

    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY, "chat_completions", True)
    database = tmp_path / "a2a.sqlite3"
    primary = A2AStore(database)
    config = dict(
        _CONFIG,
        api_max_sessions_per_key=2,
        api_max_concurrent_runs_per_key=1,
    )
    publication = primary.configure_publication(
        "alice", "conv-1", "Agent", standard_api_config=config)
    _raw, key = primary.create_key(publication["publication_id"], "client")
    namespace = StandardApiNamespace(
        publication_id=publication["publication_id"],
        api_generation=publication["api_generation"],
        key_id=key["key_id"],
        dialect="chat_completions",
        api_model_id=publication["api_model_id"],
    )
    sessions = (
        primary.create_api_session(
            namespace, "conv-1::a2a::api_retry_zero", now=100),
        primary.create_api_session(
            namespace, "conv-1::a2a::api_retry_one", now=100),
    )
    stores = (A2AStore(database), A2AStore(database))
    barrier = threading.Barrier(2)

    def acquire(index):
        barrier.wait()
        return stores[index].acquire_api_session(
            sessions[index]["session_id"],
            expected_head_hash="",
            expected_item_count=0,
            run_id=f"run-{index}",
            request_id=f"request-{index}",
            body_fingerprint="same-body",
            now=100,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        admissions = list(executor.map(acquire, range(2)))

    assert sorted(item["status"] for item in admissions) == [
        "acquired", "attached"]
    assert len({item["run"]["run_id"] for item in admissions}) == 1


def test_failed_run_insert_rolls_back_lease_and_active_admission(configured):
    store = configured["store"]
    namespace = configured["namespace"]
    session = store.create_api_session(
        namespace, "conv-1::a2a::api_rollback", now=100)
    with store._connect() as connection:
        connection.executescript(
            """
            CREATE TRIGGER fail_api_run_insert
            BEFORE INSERT ON api_export_runs
            BEGIN
                SELECT RAISE(ABORT, 'forced run insert failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced run insert failure"):
        store.acquire_api_session(
            session["session_id"],
            expected_head_hash="",
            expected_item_count=0,
            run_id="run-rollback",
            request_id="req-rollback",
            body_fingerprint="body-rollback",
            now=100,
        )

    rolled_back = store.get_api_session(session["session_id"])
    assert rolled_back["state"] == "idle"
    assert rolled_back["lease_id"] == ""
    assert store.get_api_run("run-rollback") is None
    assert store.find_active_api_run(
        namespace,
        parent_head_hash="",
        parent_item_count=0,
        body_fingerprint="body-rollback",
        now=100,
    ) is None

    with store._connect() as connection:
        connection.execute("DROP TRIGGER fail_api_run_insert")
    assert store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-rollback",
        request_id="req-rollback",
        body_fingerprint="body-rollback",
        now=100,
    )["status"] == "acquired"


def test_expired_lease_is_abandoned_and_cleanup_deletes_conversation(configured):
    store = configured["store"]
    session = store.create_api_session(
        configured["namespace"], "conv-1::a2a::api_expire", now=100)
    store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-expire",
        request_id="req-expire",
        body_fingerprint="body-expire",
        lease_seconds=10,
        now=100,
    )

    assert store.abandon_expired_api_runs(now=111) == 1
    assert store.get_api_run("run-expire")["status"] == "abandoned"
    assert store.get_api_session(session["session_id"])["state"] == "quarantined"

    deleted = []
    assert store.sweep_expired_api_sessions(
        lambda conversation_id: deleted.append(conversation_id) or True,
        now=4000,
    ) == 1
    assert deleted == ["conv-1::a2a::api_expire"]
    assert store.get_api_session(session["session_id"]) is None


class _ConversationStore:
    def __init__(self):
        self.saved = []
        self.deleted = []
        self.extras = {}

    def save(self, cid, messages, ttl=0, user_id="", status=""):
        self.saved.append({
            "cid": cid,
            "messages": messages,
            "ttl": ttl,
            "user_id": user_id,
            "status": status,
        })

    def set_extra(self, cid, key, value):
        self.extras[(cid, key)] = value

    def delete(self, cid, user_id=""):
        self.deleted.append({"cid": cid, "user_id": user_id})
        return True


def test_finalizer_creates_checkpoint_before_sqlite_and_discards_on_failure():
    calls = []

    class _Ledger:
        def finalize_api_run(self, *args, **kwargs):
            calls.append(("finalize", kwargs["checkpoint_id"]))
            raise sqlite3.IntegrityError("forced finalization failure")

    class _Checkpoints:
        def create_api_checkpoint(self, cid, message):
            calls.append(("create", cid, message))
            return "a" * 40

        def discard_api_checkpoint(self, cid, checkpoint_id):
            calls.append(("discard", cid, checkpoint_id))
            return True

    with pytest.raises(sqlite3.IntegrityError, match="forced finalization"):
        finalize_api_run_with_checkpoint(
            _Ledger(),
            _Checkpoints(),
            "conv-api",
            "run-api",
            "lease-api",
            visible_head_hash="head-api",
            item_count=1,
            prefixes=[{
                "prefix_hash": "head-api",
                "item_count": 1,
                "boundary_kind": "assistant_message",
            }],
        )

    assert calls == [
        ("create", "conv-api", "standard API run run-api"),
        ("finalize", "a" * 40),
        ("discard", "conv-api", "a" * 40),
    ]


def test_resolver_forks_verified_checkpoint_when_matching_session_is_busy(
        configured):
    store = configured["store"]
    namespace = configured["namespace"]
    publication = configured["publication"]
    key = configured["key"]
    conversations = _ConversationStore()
    completed = (
        _item("user_message", content="hello"),
        _item("assistant_message", content="hi"),
    )
    hashes = compute_hash_chain(namespace, completed, b"secret")
    source = store.create_api_session(
        namespace, "conv-1::a2a::api_source", now=100)
    first = store.acquire_api_session(
        source["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-source",
        request_id="req-source",
        body_fingerprint="body-source",
        now=100,
    )
    store.finalize_api_run(
        "run-source",
        first["lease_id"],
        visible_head_hash=hashes[-1],
        item_count=len(completed),
        prefixes=eligible_prefixes(completed, hashes)[-1:],
        checkpoint_id="b" * 40,
        now=101,
    )
    blocker = store.acquire_api_session(
        source["session_id"],
        expected_head_hash=hashes[-1],
        expected_item_count=len(completed),
        run_id="run-blocker",
        request_id="req-blocker",
        body_fingerprint="different-body",
        now=102,
    )
    assert blocker["status"] == "acquired"

    fork_calls = []
    conversations.verify_api_checkpoint = (
        lambda cid, checkpoint_id: (
            cid == source["internal_conversation_id"]
            and checkpoint_id == "b" * 40))

    def _fork(cid, checkpoint_id, *, user_id):
        fork_calls.append((cid, checkpoint_id, user_id))
        return "conv-1::a2a::api_forked"

    conversations.fork_at_checkpoint = _fork
    turn = NormalizedApiTurn(
        namespace=namespace,
        visible_items=completed + (_item("user_message", content="again"),),
        actionable_suffix_start=len(completed),
        request_id="req-forked",
        body_fingerprint="body-forked",
    )

    resolution = resolve_api_turn(
        store,
        publication,
        key,
        turn,
        hash_secret=b"secret",
        conversation_store=conversations,
        now=103,
    )

    assert resolution.outcome == "forked"
    assert resolution.matched_item_count == len(completed)
    assert resolution.ingress_items == turn.visible_items[len(completed):]
    assert resolution.checkpoint_unavailable is False
    assert fork_calls == [(
        source["internal_conversation_id"], "b" * 40, "alice")]

    conversations.verify_api_checkpoint = lambda *_args: False
    lost_turn = NormalizedApiTurn(
        namespace=namespace,
        visible_items=completed + (_item("user_message", content="lost"),),
        actionable_suffix_start=len(completed),
        request_id="req-checkpoint-lost",
        body_fingerprint="body-checkpoint-lost",
    )
    reconstructed = resolve_api_turn(
        store,
        publication,
        key,
        lost_turn,
        hash_secret=b"secret",
        conversation_store=conversations,
        now=104,
    )

    assert reconstructed.outcome == "reconstructed"
    assert reconstructed.checkpoint_unavailable is True


def test_resolver_reuses_exact_head_and_reconstructs_ambiguous_history(
        configured):
    store = configured["store"]
    namespace = configured["namespace"]
    publication = configured["publication"]
    key = configured["key"]
    conversations = _ConversationStore()
    initial_items = (
        _item("user_message", content="hello"),
    )
    first_turn = NormalizedApiTurn(
        namespace=namespace,
        visible_items=initial_items,
        actionable_suffix_start=0,
        request_id="req-first",
        body_fingerprint="body-first",
    )

    first = resolve_api_turn(
        store,
        publication,
        key,
        first_turn,
        hash_secret=b"secret",
        conversation_store=conversations,
        now=100,
    )
    assert first.outcome == "reconstructed"
    assert first.matched_item_count == 0
    assert first.ingress_items == initial_items
    assert conversations.saved[0]["ttl"] == 0
    assert "::a2a::" in conversations.saved[0]["cid"]

    retry_turn = NormalizedApiTurn(
        namespace=namespace,
        visible_items=initial_items,
        actionable_suffix_start=0,
        request_id="req-retry",
        body_fingerprint="body-first",
    )
    attached = resolve_api_turn(
        store,
        publication,
        key,
        retry_turn,
        hash_secret=b"secret",
        conversation_store=conversations,
        now=100.5,
    )
    assert attached.outcome == "attached"
    assert attached.run["run_id"] == first.run["run_id"]
    assert attached.ingress_items == ()
    assert len(conversations.saved) == 1

    completed = (
        _item("user_message", content="hello"),
        _item("assistant_message", content="hi"),
    )
    hashes = compute_hash_chain(namespace, completed, b"secret")
    store.finalize_api_run(
        first.run["run_id"],
        first.lease_id,
        visible_head_hash=hashes[-1],
        item_count=2,
        prefixes=eligible_prefixes(completed, hashes),
        now=101,
    )

    followup_items = completed + (_item("user_message", content="again"),)
    followup = NormalizedApiTurn(
        namespace=namespace,
        visible_items=followup_items,
        actionable_suffix_start=2,
        request_id="req-follow",
        body_fingerprint="body-follow",
    )
    matched = resolve_api_turn(
        store,
        publication,
        key,
        followup,
        hash_secret=b"secret",
        conversation_store=conversations,
        now=102,
    )
    assert matched.outcome == "matched"
    assert matched.session["session_id"] == first.session["session_id"]
    assert matched.matched_item_count == 2
    assert matched.ingress_items == followup_items[2:]
    assert len(conversations.saved) == 1


def test_resolver_discards_a_reconstruction_that_loses_the_admission_race(
        configured, monkeypatch):
    store = configured["store"]
    namespace = configured["namespace"]
    publication = configured["publication"]
    key = configured["key"]
    conversations = _ConversationStore()
    turn = NormalizedApiTurn(
        namespace=namespace,
        visible_items=(_item("user_message", content="hello"),),
        actionable_suffix_start=0,
        request_id="req-winner",
        body_fingerprint="same-body",
    )
    winner = resolve_api_turn(
        store,
        publication,
        key,
        turn,
        hash_secret=b"secret",
        conversation_store=conversations,
        now=100,
    )
    monkeypatch.setattr(
        store, "find_active_api_run", lambda *_args, **_kwargs: None)
    retry = resolve_api_turn(
        store,
        publication,
        key,
        NormalizedApiTurn(
            namespace=namespace,
            visible_items=turn.visible_items,
            actionable_suffix_start=0,
            request_id="req-loser",
            body_fingerprint="same-body",
        ),
        hash_secret=b"secret",
        conversation_store=conversations,
        now=100.5,
    )

    assert retry.outcome == "attached"
    assert retry.run["run_id"] == winner.run["run_id"]
    assert len(conversations.saved) == 2
    assert conversations.deleted == [{
        "cid": conversations.saved[1]["cid"],
        "user_id": publication["owner_user_id"],
    }]
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM api_export_sessions").fetchone()[0] == 1


def test_running_old_generation_drains_then_expires_on_finalization(configured):
    store = configured["store"]
    session = store.create_api_session(
        configured["namespace"], "conv-1::a2a::api_draining", now=100)
    admission = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-draining",
        request_id="req-draining",
        body_fingerprint="body-draining",
        now=100,
    )

    reset = store.reset_api_sessions(
        configured["publication"]["publication_id"], now=101)
    assert reset["api_generation"] == 2
    assert store.get_api_session(session["session_id"])["state"] == "running"

    finalized = store.finalize_api_run(
        "run-draining",
        admission["lease_id"],
        visible_head_hash="head-draining",
        item_count=1,
        prefixes=[{
            "prefix_hash": "head-draining",
            "item_count": 1,
            "boundary_kind": "assistant_message",
        }],
        now=102,
    )
    assert finalized["session"]["state"] == "idle"
    assert finalized["session"]["expires_at"] == 102


def test_replay_journal_is_bounded_and_generation_reset_expires_idle_sessions(
        configured):
    store = configured["store"]
    session = store.create_api_session(
        configured["namespace"], "conv-1::a2a::api_replay", now=100)
    admission = store.acquire_api_session(
        session["session_id"],
        expected_head_hash="",
        expected_item_count=0,
        run_id="run-replay",
        request_id="req-replay",
        body_fingerprint="body-replay",
        now=100,
    )
    for value in range(3):
        store.append_api_run_event(
            "run-replay", {"delta": value}, max_events=2, now=101 + value)
    assert [entry["event"] for entry in store.read_api_run_events(
        "run-replay")] == [{"delta": 1}, {"delta": 2}]

    store.finalize_api_run(
        "run-replay",
        admission["lease_id"],
        visible_head_hash="head-replay",
        item_count=1,
        prefixes=[{
            "prefix_hash": "head-replay",
            "item_count": 1,
            "boundary_kind": "assistant_message",
        }],
        now=105,
    )
    reset = store.reset_api_sessions(
        configured["publication"]["publication_id"], now=106)
    assert reset["api_generation"] == 2
    expired = store.get_api_session(session["session_id"])
    assert expired["expires_at"] == 106
