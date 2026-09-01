"""Managed frontend-execution action endpoints for the AG-UI server
(plan ``docs/WEBMCP_INTEGRATION_PLAN.md`` §B1-X / B6 — step P1-F).

After a managed run's ``RUN_FINISHED`` carries its ``batch_token``, the
widget drives the batch through these ``POST /agui/{id}?action=...``
endpoints, all backed by the single-commit A2AStore batch machine built
in P1-D:

- ``claim_batch`` — CAS ``frozen → reserved_pre_effect`` (idempotent per
  ``batchClaimId``); returns the ``owner_token`` and per-call receipts.
- ``renew`` — idempotent CAS on the pre-effect lease (owner token).
- ``begin`` — idempotent CAS to the effect boundary (receipt), gated by
  the per-call catalogue identity.
- ``deposit`` — deposit one call outcome (receipt) under the closed
  state matrix; duplicates replay, conflicts are ``409``.
- ``attach`` — resume one run's SSE by ``attach_token`` (B1-J): gapless
  journal replay from the caller's ``afterSeq`` watermark, then follow.
  Attach can NEVER admit and never starts a pilot.

``handle_cancel`` serves ``DELETE /agui/{id}`` + the
``X-PawFlow-Cancel-Token`` header (B1-J): idempotent, journaled
cancellation through ``A2AStore.cancel_agui_run``.

All credentials travel in the ``X-PawFlow-Exec-Token`` header (never a
query string); the store verifies them against the pinned row and every
failure maps to a stable HTTP status. The mode is publication-fixed —
an action can never select it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from services.mcp_server_endpoint import _header, _json_response

logger = logging.getLogger(__name__)

_EXEC_TOKEN_HEADER = "X-PawFlow-Exec-Token"  # nosec B105 - public header name
_CANCEL_TOKEN_HEADER = "X-PawFlow-Cancel-Token"  # nosec B105 - public header name


def _exec_token(req) -> str:
    return _header(req, _EXEC_TOKEN_HEADER).strip()


# Stable HTTP mapping (plan B1-X). Only the DECLARED business exceptions
# reach the client; anything else is a 500 with a generic message (never
# str(exc), which could leak internals). AguiTokenInvalid is uniform on
# purpose — it never distinguishes malformed / unknown / bad-MAC /
# wrong-scope, so a probe learns nothing about the batch.
_STATUS = {
    "AguiTokenInvalid": (401, "token_invalid", "invalid credential"),
    "AguiClaimExpired": (409, "claim_expired", "claim expired"),
    "AguiBatchClaimed": (409, "batch_already_claimed",
                         "batch already claimed"),
    "AguiBatchIncomplete": (409, "batch_incomplete",
                            "batch incomplete"),
    "AguiDepositRejected": (409, "deposit_rejected",
                            "deposit rejected"),
    "AguiReceiptConflict": (409, "receipt_conflict",
                            "receipt conflict"),
}


def _dispatch(req, fn) -> Optional[Dict[str, Any]]:
    """Run one store call, translating ONLY the declared batch errors
    into a stable JSON response. Returns the store result on success, or
    None when a response was already written."""
    from core._a2a_turn_batch import AguiCatalogueRejected
    try:
        return fn()
    except AguiCatalogueRejected as rejected:
        # Not an error: the call terminalized without executing.
        _json_response(req, 200, {"outcome": rejected.outcome,
                                  "terminal": True})
        return None
    except Exception as exc:
        name = type(exc).__name__
        mapped = _STATUS.get(name)
        if mapped is None:
            # An undeclared failure is a server fault, never a 4xx with
            # the raw message.
            logger.exception("AG-UI action failed unexpectedly")
            _json_response(req, 500, {"error": "internal_error"})
            return None
        status, code, message = mapped
        _json_response(req, status, {"error": code, "message": message})
        return None


def req_body(req) -> Dict[str, Any]:
    from services.a2a_server_endpoint import _request_json
    body = _request_json(req)
    return body if isinstance(body, dict) else {}


# ── actions ──────────────────────────────────────────────────────────
# A v8.2 token is self-addressing: the handle inside it looks up the
# batch/call row directly, so NO threadId/runId is needed (and `runId is
# never an addressing key`, plan B0). The action reads only its
# credential (header) and its own body fields.

def _scope(publication, key):
    """The authenticated (publication_id, key_id) every transition is
    bound to — a credential from another publication fails closed."""
    return (publication["publication_id"], key["key_id"])


def action_claim_batch(req, store, publication, key) -> None:
    body = req_body(req)
    batch_token = _exec_token(req)
    batch_claim_id = str(body.get("batchClaimId") or "").strip()
    if not batch_claim_id:
        _json_response(req, 400, {"error": "batchClaimId is required"})
        return
    lease_seconds = _positive_float(body.get("leaseSeconds"), 60.0)
    result = _dispatch(req, lambda: store.claim_agui_batch(
        batch_token, batch_claim_id, lease_seconds=lease_seconds,
        scope=_scope(publication, key)))
    if result is None:
        return
    _json_response(req, 200, {
        "state": result["state"],
        "claimGeneration": result["claim_generation"],
        "ownerToken": result["owner_token"],
        "receipts": [{"toolCallId": r["tool_call_id"],
                      "receipt": r["receipt"]} for r in result["receipts"]],
    })


def action_renew(req, store, publication, key) -> None:
    owner_token = _exec_token(req)
    body = req_body(req)
    lease_seconds = _positive_float(body.get("leaseSeconds"), 60.0)
    result = _dispatch(req, lambda: store.renew_agui_batch(
        owner_token, lease_seconds=lease_seconds,
        scope=_scope(publication, key)))
    if result is None:
        return
    _json_response(req, 200, {"renewed": bool(result)})


def action_begin(req, store, publication, key) -> None:
    receipt = _exec_token(req)
    body = req_body(req)
    catalogue_id = body.get("catalogueId")
    catalogue_version = body.get("catalogueVersion")
    result = _dispatch(req, lambda: store.begin_agui_call(
        receipt,
        catalogue_id=(str(catalogue_id)
                      if catalogue_id is not None else None),
        catalogue_version=(str(catalogue_version)
                           if catalogue_version is not None else None),
        scope=_scope(publication, key)))
    if result is None:
        return
    _json_response(req, 200, {"begun": bool(result)})


def action_deposit(req, store, publication, key) -> None:
    receipt = _exec_token(req)
    body = req_body(req)
    kind = str(body.get("kind") or "").strip()
    if not kind:
        _json_response(req, 400, {"error": "kind is required"})
        return
    payload = body.get("payload")
    if payload is None:
        payload_json = ""
    elif isinstance(payload, str):
        payload_json = payload
    else:
        import json
        payload_json = json.dumps(payload, ensure_ascii=False,
                                  separators=(",", ":"))
    result = _dispatch(req, lambda: store.deposit_agui_call(
        receipt, kind, payload_json, scope=_scope(publication, key)))
    if result is None:
        return
    _json_response(req, 200, {
        "kind": result["kind"],
        "batchState": result["batch_state"],
        "replay": bool(result.get("replay")),
    })


def action_attach(req, store, publication, key) -> None:
    """Tail one run's journal by ``attach_token`` — replay after the
    caller's watermark, then follow. Never admits, never starts a
    pilot (B1-J: admission requires a full body through the run POST)."""
    attach_token = _exec_token(req)
    body = req_body(req)
    raw_after = body.get("afterSeq", 0)
    try:
        after_seq = int(raw_after)
    except (TypeError, ValueError):
        after_seq = -1
    if after_seq < 0:
        _json_response(req, 400, {"error": "invalid_after_seq",
                                  "message": "afterSeq must be >= 0"})
        return
    resolved = _dispatch(req, lambda: store.resolve_agui_attach(
        attach_token, scope=_scope(publication, key)))
    if resolved is None:
        return
    context_id, run_id = resolved["context_id"], resolved["run_id"]
    try:
        subscriber = store.acquire_agui_subscriber(
            context_id, run_id, after_seq=after_seq)
    except ValueError:
        _json_response(req, 400, {
            "error": "invalid_after_seq",
            "message": "afterSeq is beyond the committed sequence"})
        return
    except Exception:
        logger.exception("AG-UI attach subscriber setup failed")
        _json_response(req, 500, {"error": "internal_error"})
        return
    from core._agui_managed_runtime import tail_agui_journal
    from services.agui_server_endpoint import (
        _SSE_HEADERS, _SUBSCRIBER_EPOCH_HEADER,
    )
    epoch = int(subscriber["subscriber_epoch"])
    headers = dict(_SSE_HEADERS)
    headers[_SUBSCRIBER_EPOCH_HEADER] = str(epoch)
    req.complete_stream(
        200, headers,
        tail_agui_journal(store, context_id, run_id,
                          after_seq=after_seq, subscriber_epoch=epoch))


def handle_cancel(req) -> None:
    """``DELETE /agui/{id}`` + ``X-PawFlow-Cancel-Token``: idempotent,
    journaled cancellation. Authentication first; the mode gate matches
    the other managed actions."""
    from services.a2a_server_endpoint import _publication
    publication, key = _publication(req)
    if not publication or not key:
        return
    if not publication.get("managed_mode"):
        _json_response(req, 409, {
            "error": "not_managed",
            "message": "This publication is not in managed mode"})
        return
    cancel_token = _header(req, _CANCEL_TOKEN_HEADER).strip()
    from core.a2a_store import A2AStore
    store = A2AStore.instance()
    result = _dispatch(req, lambda: store.cancel_agui_run(
        cancel_token, scope=_scope(publication, key)))
    if result is None:
        return
    if not result["already"]:
        # The store cut the lease and fence; also stop the internal
        # agent turn NOW (handle-targeted, best effort — the pilot's
        # own lease check is the guaranteed backstop).
        from core._agui_managed_runtime import force_stop_managed_run
        force_stop_managed_run(result["context_id"], result["run_id"])
    _json_response(req, 200, {"outcome": result["outcome"],
                              "already": bool(result["already"])})


def _positive_float(value, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


_MANAGED_ACTIONS = {
    "attach": action_attach,
    "claim_batch": action_claim_batch,
    "renew": action_renew,
    "begin": action_begin,
    "deposit": action_deposit,
}


def handle_managed_action(req, action: str) -> bool:
    """Dispatch a ``POST /agui/{id}?action=...`` managed-batch action.

    Returns True when this is an ``?action=`` request (a response is
    always written), False only when there is NO action at all (the
    caller falls through to the plain run/attach handling).

    Authentication comes FIRST: even an unknown action must pass the
    publication + key gate before it learns it is unknown — an
    unauthenticated caller never probes the action surface.
    """
    if not action:
        return False
    from services.a2a_server_endpoint import _publication
    publication, key = _publication(req)
    if not publication or not key:
        return True  # _publication already wrote the auth/404 failure
    handler = _MANAGED_ACTIONS.get(action)
    if handler is None:
        _json_response(req, 400, {"error": "unknown_action",
                                  "message": f"Unknown action '{action}'"})
        return True
    if not publication.get("managed_mode"):
        _json_response(req, 409, {
            "error": "not_managed",
            "message": "This publication is not in managed mode"})
        return True
    from core.a2a_store import A2AStore
    store = A2AStore.instance()
    handler(req, store, publication, key)
    return True


__all__ = ["handle_managed_action", "handle_cancel", "_MANAGED_ACTIONS"]
