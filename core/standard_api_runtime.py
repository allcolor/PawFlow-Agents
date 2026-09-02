"""Session resolution and visible-history reconstruction for standard APIs."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, Iterable, Mapping, Sequence

from core.standard_api_canonical import (
    compute_hash_chain,
    eligible_prefixes,
)
from core.standard_api_types import (
    ApiTurnResolution,
    NormalizedApiTurn,
    NormalizedVisibleItem,
)


_IMPORT_NAMESPACE = uuid.UUID("09c2c0d7-6dbe-41ba-969e-9505b78aed92")
logger = logging.getLogger(__name__)


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    chunks = []
    if isinstance(value, (list, tuple)):
        for part in value:
            if not isinstance(part, Mapping):
                continue
            if part.get("type") == "text":
                chunks.append(str(part.get("text") or ""))
            elif part.get("type") in {"image_url", "image_data"}:
                media_type = str(part.get("media_type") or "application/octet-stream")
                chunks.append(f"[Client-supplied {media_type} input]")
    return "\n".join(chunks)


def _message_id(conversation_id: str, index: int, suffix: str = "") -> str:
    return str(uuid.uuid5(
        _IMPORT_NAMESPACE,
        f"{conversation_id}:{index}:{suffix}",
    ))


def _import_messages(
        conversation_id: str,
        items: Sequence[NormalizedVisibleItem],
        target_agent: str,
        *,
        created_at: float,
) -> list[Dict[str, Any]]:
    """Translate completed visible history into inert, untrusted transcript rows."""

    messages: list[Dict[str, Any]] = []
    source_base = {
        "type": "standard_api_import",
        "name": "API client history",
        "target_agent": target_agent,
        "visibility": "target_only",
        "trusted": False,
    }
    for index, item in enumerate(items):
        data = item.data
        if item.kind == "client_instruction":
            role = str(data.get("role") or "system")
            content = _content_text(data.get("content"))
            messages.append({
                "role": "user",
                "content": (
                    f"[Authenticated API client {role} instruction; "
                    "lower priority than publisher policy]\n" + content),
                "source": dict(source_base, instruction_role=role),
                "msg_id": _message_id(conversation_id, index),
                "ts": created_at + (index * 0.000001),
            })
            continue
        if item.kind == "user_message":
            messages.append({
                "role": "user",
                "content": _content_text(data.get("content")),
                "source": dict(source_base),
                "msg_id": _message_id(conversation_id, index),
                "ts": created_at + (index * 0.000001),
            })
            continue
        if item.kind == "assistant_message":
            row: Dict[str, Any] = {
                "role": "assistant",
                "content": _content_text(data.get("content")),
                "source": dict(source_base, server_verified=False),
                "msg_id": _message_id(conversation_id, index),
                "ts": created_at + (index * 0.000001),
            }
            if data.get("tool_calls"):
                row["tool_calls"] = list(data["tool_calls"])
            messages.append(row)
            continue
        if item.kind == "client_tool_call_batch":
            calls = []
            for call in data.get("calls") or []:
                calls.append({
                    "id": str(call.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(call.get("name") or ""),
                        "arguments": call.get("arguments", {}),
                    },
                })
            messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": calls,
                "source": dict(source_base, server_verified=False),
                "msg_id": _message_id(conversation_id, index),
                "ts": created_at + (index * 0.000001),
            })
            continue
        if item.kind == "client_tool_result_batch":
            for result_index, result in enumerate(data.get("results") or []):
                messages.append({
                    "role": "tool",
                    "content": _content_text(result.get("content")),
                    "tool_call_id": str(result.get("id") or ""),
                    "source": dict(source_base, historical_result=True),
                    "msg_id": _message_id(
                        conversation_id, index, str(result_index)),
                    "ts": created_at + (
                        index * 0.000001) + (result_index * 0.000000001),
                })
            continue
        if item.kind == "response_output":
            text_chunks = []
            tool_calls = []
            for output in data.get("output") or []:
                if not isinstance(output, Mapping):
                    continue
                if output.get("type") == "message":
                    for part in output.get("content") or []:
                        if (isinstance(part, Mapping)
                                and part.get("type") == "output_text"):
                            text_chunks.append(str(part.get("text") or ""))
                elif output.get("type") == "function_call":
                    tool_calls.append({
                        "id": str(output.get("call_id") or ""),
                        "type": "function",
                        "function": {
                            "name": str(output.get("name") or ""),
                            "arguments": output.get("arguments", "{}"),
                        },
                    })
            row = {
                "role": "assistant",
                "content": "".join(text_chunks),
                "source": dict(source_base, server_verified=False),
                "msg_id": _message_id(conversation_id, index),
                "ts": created_at + (index * 0.000001),
            }
            if tool_calls:
                row["tool_calls"] = tool_calls
            messages.append(row)
            continue
        messages.append({
            "role": "assistant",
            "content": _content_text(data.get("output")),
            "source": dict(source_base, server_verified=False),
            "msg_id": _message_id(conversation_id, index),
            "ts": created_at + (index * 0.000001),
        })
    return messages


def _seed_reconstructed_conversation(
        conversation_store,
        publication: Mapping[str, Any],
        conversation_id: str,
        history: Sequence[NormalizedVisibleItem],
        *,
        created_at: float,
) -> None:
    messages = _import_messages(
        conversation_id,
        history,
        str(publication["agent_name"]),
        created_at=created_at,
    )
    conversation_store.save(
        conversation_id,
        messages,
        ttl=0,
        user_id=str(publication["owner_user_id"]),
        status="idle",
    )
    conversation_store.set_extra(
        conversation_id,
        "title",
        f"Standard API · {publication.get('label') or publication['agent_name']}",
    )
    conversation_store.set_extra(
        conversation_id,
        "api_export_parent_conversation_id",
        publication["conversation_id"],
    )
    conversation_store.set_extra(
        conversation_id,
        "api_export_publication_id",
        publication["publication_id"],
    )
    conversation_store.set_extra(
        conversation_id,
        "api_export_imported_history",
        bool(history),
    )


def _new_internal_conversation_id(publication: Mapping[str, Any]) -> str:
    suffix = "api_" + uuid.uuid4().hex
    return f"{publication['conversation_id']}::a2a::{suffix}"


def _cleanup_unstarted_reconstruction(
        store,
        conversation_store,
        publication: Mapping[str, Any],
        session_id: str,
        conversation_id: str,
        *,
        now: float,
) -> None:
    """Delete the child before its ledger row, or leave a cleanup tombstone."""

    try:
        conversation_store.delete(
            conversation_id,
            user_id=publication["owner_user_id"],
        )
    except Exception:
        logger.warning(
            "Standard API reconstruction child cleanup failed for session %s",
            session_id,
            exc_info=True,
        )
        try:
            store.quarantine_unstarted_api_session(session_id, now=now)
        except Exception:
            logger.error(
                "Standard API reconstruction quarantine failed for session %s",
                session_id,
                exc_info=True,
            )
        return
    try:
        store.discard_empty_api_session(session_id)
    except Exception:
        logger.error(
            "Standard API reconstruction ledger cleanup failed for session %s",
            session_id,
            exc_info=True,
        )


def finalize_api_run_with_checkpoint(
        store,
        conversation_store,
        conversation_id: str,
        run_id: str,
        lease_id: str,
        **finalization,
) -> Dict[str, Any]:
    """Create the verified filesystem checkpoint before SQLite finalization."""

    checkpoint_id = ""
    try:
        checkpoint_id = str(conversation_store.create_api_checkpoint(
            conversation_id,
            f"standard API run {run_id}",
        ) or "")
    except Exception:
        logger.warning(
            "Standard API checkpoint creation failed for run %s; "
            "continuing without exact-fork support",
            run_id,
            exc_info=True,
        )
    try:
        finalized = store.finalize_api_run(
            run_id,
            lease_id,
            checkpoint_id=checkpoint_id,
            **finalization,
        )
        committed_checkpoint = str(
            (finalized.get("session") or {}).get("head_checkpoint_id") or "")
        if checkpoint_id and committed_checkpoint != checkpoint_id:
            try:
                conversation_store.discard_api_checkpoint(
                    conversation_id, checkpoint_id)
            except Exception:
                logger.warning(
                    "Could not discard unused standard API checkpoint for run %s",
                    run_id,
                    exc_info=True,
                )
        return finalized
    except Exception:
        if checkpoint_id:
            try:
                conversation_store.discard_api_checkpoint(
                    conversation_id, checkpoint_id)
            except Exception:
                logger.warning(
                    "Could not discard orphan standard API checkpoint for run %s",
                    run_id,
                    exc_info=True,
                )
        raise


def _fork_verified_api_prefix(
        store,
        conversation_store,
        publication: Mapping[str, Any],
        turn: NormalizedApiTurn,
        lookup: Mapping[str, Any],
        *,
        now: float,
) -> ApiTurnResolution | None:
    prefix = lookup["prefix"]
    checkpoint_id = str(prefix.get("checkpoint_id") or "")
    if not checkpoint_id:
        return None
    source_conversation_id = str(
        lookup["session"]["internal_conversation_id"])
    try:
        if not conversation_store.verify_api_checkpoint(
                source_conversation_id, checkpoint_id):
            return None
        conversation_id = conversation_store.fork_at_checkpoint(
            source_conversation_id,
            checkpoint_id,
            user_id=str(publication["owner_user_id"]),
        )
    except Exception:
        logger.warning(
            "Standard API exact checkpoint fork failed for session %s",
            lookup["session"]["session_id"],
            exc_info=True,
        )
        return None

    matched_count = int(prefix["item_count"])
    try:
        session = store.create_api_session(
            turn.namespace,
            conversation_id,
            visible_head_hash=str(prefix["prefix_hash"]),
            item_count=matched_count,
            now=now,
        )
    except Exception:
        try:
            conversation_store.delete(
                conversation_id,
                user_id=publication["owner_user_id"],
            )
        except Exception:
            logger.warning(
                "Could not delete an unregistered standard API checkpoint fork",
                exc_info=True,
            )
        raise

    try:
        admission = store.acquire_api_session(
            session["session_id"],
            expected_head_hash=str(prefix["prefix_hash"]),
            expected_item_count=matched_count,
            run_id=turn.request_id,
            request_id=turn.request_id,
            body_fingerprint=turn.body_fingerprint,
            now=now,
        )
    except Exception:
        _cleanup_unstarted_reconstruction(
            store,
            conversation_store,
            publication,
            session["session_id"],
            conversation_id,
            now=now,
        )
        raise
    if admission["status"] == "acquired":
        return ApiTurnResolution(
            outcome="forked",
            session=admission["session"],
            run=admission["run"],
            lease_id=admission["lease_id"],
            matched_item_count=matched_count,
            ingress_items=tuple(turn.visible_items[matched_count:]),
            lookup_status="unique",
        )
    _cleanup_unstarted_reconstruction(
        store,
        conversation_store,
        publication,
        session["session_id"],
        conversation_id,
        now=now,
    )
    if admission["status"] == "attached":
        return ApiTurnResolution(
            outcome="attached",
            session=admission["session"],
            run=admission["run"],
            lease_id=admission["lease_id"],
            matched_item_count=matched_count,
            ingress_items=(),
            lookup_status="unique",
        )
    return None


def resolve_api_turn(
        store,
        publication: Mapping[str, Any],
        key: Mapping[str, Any],
        turn: NormalizedApiTurn,
        *,
        hash_secret: bytes,
        conversation_store=None,
        now: float | None = None,
) -> ApiTurnResolution:
    """Resolve and acquire a standard API turn without appending ingress yet."""

    if publication["publication_id"] != turn.namespace.publication_id:
        raise ValueError("Turn namespace does not match its publication")
    if key["key_id"] != turn.namespace.key_id:
        raise ValueError("Turn namespace does not match its authenticated key")
    timestamp = time.time() if now is None else float(now)
    if conversation_store is None:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    hashes = compute_hash_chain(
        turn.namespace, turn.visible_items, hash_secret)
    prefixes = eligible_prefixes(turn.visible_items, hashes)
    lookup = store.lookup_api_prefix(
        turn.namespace, prefixes, now=timestamp)

    if lookup["status"] == "unique":
        prefix = lookup["prefix"]
        admission = store.acquire_api_session(
            lookup["session"]["session_id"],
            expected_head_hash=prefix["prefix_hash"],
            expected_item_count=int(prefix["item_count"]),
            run_id=turn.request_id,
            request_id=turn.request_id,
            body_fingerprint=turn.body_fingerprint,
            now=timestamp,
        )
        if admission["status"] in {"acquired", "attached"}:
            matched_count = int(prefix["item_count"])
            return ApiTurnResolution(
                outcome=(
                    "matched" if admission["status"] == "acquired"
                    else "attached"),
                session=admission["session"],
                run=admission["run"],
                lease_id=admission["lease_id"],
                matched_item_count=matched_count,
                ingress_items=tuple(turn.visible_items[matched_count:]),
                lookup_status="unique",
            )
        forked = _fork_verified_api_prefix(
            store,
            conversation_store,
            publication,
            turn,
            lookup,
            now=timestamp,
        )
        if forked is not None:
            return forked

    history_count = turn.actionable_suffix_start
    history = tuple(turn.visible_items[:history_count])
    history_head = hashes[history_count - 1] if history_count else ""
    active = store.find_active_api_run(
        turn.namespace,
        parent_head_hash=history_head,
        parent_item_count=history_count,
        body_fingerprint=turn.body_fingerprint,
        now=timestamp,
    )
    if active is not None:
        return ApiTurnResolution(
            outcome="attached",
            session=active["session"],
            run=active["run"],
            lease_id=active["lease_id"],
            matched_item_count=history_count,
            ingress_items=(),
            lookup_status=str(lookup["status"]),
        )

    conversation_id = _new_internal_conversation_id(publication)
    session = store.create_api_session(
        turn.namespace,
        conversation_id,
        visible_head_hash=history_head,
        item_count=history_count,
        now=timestamp,
    )
    try:
        _seed_reconstructed_conversation(
            conversation_store,
            publication,
            conversation_id,
            history,
            created_at=timestamp,
        )
    except Exception:
        _cleanup_unstarted_reconstruction(
            store,
            conversation_store,
            publication,
            session["session_id"],
            conversation_id,
            now=timestamp,
        )
        raise

    admission = store.acquire_api_session(
        session["session_id"],
        expected_head_hash=history_head,
        expected_item_count=history_count,
        run_id=turn.request_id,
        request_id=turn.request_id,
        body_fingerprint=turn.body_fingerprint,
        now=timestamp,
    )
    if admission["status"] == "attached":
        _cleanup_unstarted_reconstruction(
            store,
            conversation_store,
            publication,
            session["session_id"],
            conversation_id,
            now=timestamp,
        )
        return ApiTurnResolution(
            outcome="attached",
            session=admission["session"],
            run=admission["run"],
            lease_id=admission["lease_id"],
            matched_item_count=history_count,
            ingress_items=(),
            lookup_status=str(lookup["status"]),
        )
    if admission["status"] != "acquired":
        raise RuntimeError(
            "Newly reconstructed API session could not be acquired")
    return ApiTurnResolution(
        outcome="reconstructed",
        session=admission["session"],
        run=admission["run"],
        lease_id=admission["lease_id"],
        matched_item_count=history_count,
        ingress_items=tuple(turn.visible_items[history_count:]),
        lookup_status=str(lookup["status"]),
        checkpoint_unavailable=lookup["status"] in {"unique", "ambiguous"},
    )


__all__ = ["finalize_api_run_with_checkpoint", "resolve_api_turn"]
