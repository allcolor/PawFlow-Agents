"""Central tool authorization engine (policy gating plan, section 11).

V0 scope: the engine sits on top of PawFlow's legacy permission checks in the
primary AgentLoop path. When no gate is bound it returns ``legacy`` and the
caller behaves exactly as before. When a gate is bound it:

1. classifies the call (``internal_ungated`` → legacy, ``hard_deny`` → deny);
2. loads the authority of the running work lineage by explicit reference
   (contextvar, then the per-agent active record written at user ingress);
3. builds a redacted envelope and evaluates the conversation gate, then the
   agent gate, composing ``deny > ask > allow``;
4. keeps the human floor for ``hard_confirm`` calls (an ``allow`` becomes
   ``ask``);
5. appends one redacted audit record (UUID + timestamp) per decision.

Broken bindings, missing evaluators and exceptions never turn into execution.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.authorization_context import (
    AuthorizationContextStore, AuthorizationRef, active_authority_ref,
    get_current_ref)
from core.gating_policy import build_envelope, classify_call, compose_final

logger = logging.getLogger(__name__)

_AUDIT_LOCK = threading.Lock()
_SAFE = re.compile(r"[^A-Za-z0-9_.:@-]+")


@dataclass
class ToolAuthorizationResult:
    """``decision`` is ``legacy`` (no gate bound; caller keeps its own rules),
    ``execute``, ``ask`` or ``deny``."""

    decision: str
    reason: str = ""
    classification: str = "ordinary"
    gates: List[Dict[str, Any]] = field(default_factory=list)
    envelope: Optional[Dict[str, Any]] = None
    decision_id: str = ""
    authority_ref: Optional[Dict[str, Any]] = None


def _audit_dir() -> Path:
    from core.paths import RUNTIME_DIR
    return Path(RUNTIME_DIR) / "gating-decisions"


def _audit(conversation_id: str, record: Dict[str, Any]) -> None:
    """Append one redacted decision record; failure never blocks the caller
    but is logged (WP8 replaces this JSONL with the SQLite store)."""
    try:
        path = _audit_dir() / (_SAFE.sub("_", conversation_id or "unknown")[:128] + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _AUDIT_LOCK, open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        logger.warning("gating audit append failed for %s", conversation_id[:8], exc_info=True)


def list_decisions(conversation_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Most recent decision records (newest last), for the decision viewer."""
    path = _audit_dir() / (_SAFE.sub("_", conversation_id or "unknown")[:128] + ".jsonl")
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return []
    for line in lines[-max(1, int(limit)):]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def load_authority(user_id: str, conversation_id: str, agent_name: str):
    """Return ``(doc, ref)`` for the running lineage; ``(None, None)`` when no
    explicit reference exists. Always the newest revision (plan 14.2)."""
    ref = get_current_ref() or active_authority_ref(conversation_id, agent_name)
    if ref is None:
        return None, None
    doc = AuthorizationContextStore.instance().snapshot(user_id, conversation_id, ref.context_id)
    if doc is None:
        return None, ref
    return doc, AuthorizationContextStore.ref(doc)


def authorize_tool_call(*, tool_name: str, arguments: Dict[str, Any], user_id: str,
                        conversation_id: str, agent_name: str, turn_id: str = "",
                        call_id: str = "", permission_mode: str = "default",
                        tool_permission: str = "", read_only_override: bool = False,
                        secret_values: Iterable[str] = (),
                        resolved_gates: Optional[Dict[str, Any]] = None) -> ToolAuthorizationResult:
    """Evaluate the bound policy gates for one prepared tool call."""
    try:
        if resolved_gates is None:
            from core.gating_bindings import resolve_gates
            resolved_gates = resolve_gates(user_id, conversation_id, agent_name)
    except Exception:
        logger.warning("gate resolution failed for %s/%s; legacy permissions apply",
                       conversation_id[:8], agent_name, exc_info=True)
        return ToolAuthorizationResult("legacy", "gate resolution failed")
    if not resolved_gates.get("bound"):
        return ToolAuthorizationResult("legacy")

    classification, why = classify_call(
        tool_name, arguments, permission_mode=permission_mode,
        tool_permission=tool_permission, read_only_override=read_only_override)
    if classification == "internal_ungated":
        return ToolAuthorizationResult("legacy", why, classification)
    if classification == "hard_deny":
        result = ToolAuthorizationResult("deny", f"structural guard: {why}", classification,
                                         decision_id=uuid.uuid4().hex)
        _audit(conversation_id, _record(result, tool_name, user_id, agent_name, turn_id))
        return result

    doc, ref = None, None
    try:
        doc, ref = load_authority(user_id, conversation_id, agent_name)
    except Exception:
        logger.debug("authority load failed", exc_info=True)
    envelope = build_envelope(
        user_id=user_id, conversation_id=conversation_id, agent_name=agent_name,
        turn_id=turn_id, tool_name=tool_name, arguments=arguments, call_id=call_id,
        authorization=doc, secret_values=secret_values, classification=classification)
    ref_dict = ref.to_dict() if isinstance(ref, AuthorizationRef) else None

    if resolved_gates.get("broken"):
        errors = "; ".join(filter(None, (
            (resolved_gates.get("conversation") or {}).get("error", ""),
            (resolved_gates.get("agent") or {}).get("error", ""))))
        result = ToolAuthorizationResult(
            "ask", f"policy gate unavailable ({errors}); confirmation required",
            classification, envelope=envelope, decision_id=envelope["decision_id"],
            authority_ref=ref_dict)
        _audit(conversation_id, _record(result, tool_name, user_id, agent_name, turn_id))
        return result

    per_origin: Dict[str, str] = {}
    gate_results: List[Dict[str, Any]] = []
    for gate in resolved_gates.get("gates") or []:
        service = gate.get("service")
        origin = gate.get("origin", "conversation")
        try:
            outcome = service.evaluate(envelope, user_id=user_id, conversation_id=conversation_id)
        except Exception as exc:  # noqa: BLE001 - a gate failure must settle as ask
            logger.warning("gate %s raised: %s", gate.get("ref"), exc, exc_info=True)
            outcome = {"decision": "ask", "reason": f"gate failure: {exc}", "evaluators": []}
        outcome = dict(outcome, origin=origin, ref=gate.get("ref"))
        gate_results.append(outcome)
        per_origin[origin] = outcome.get("decision", "ask")
        if per_origin[origin] == "deny":
            break
    final = compose_final(per_origin.get("conversation"), per_origin.get("agent"))
    reason = " | ".join(
        f"{r['origin']} gate {r.get('decision')}: {r.get('reason', '')}".strip()
        for r in gate_results)
    if classification == "hard_confirm" and final == "allow":
        final = "ask"
        reason = f"{why} requires confirmation; {reason}"
    mapped = {"allow": "execute", "deny": "deny", "ask": "ask"}[final]
    result = ToolAuthorizationResult(mapped, reason[:900], classification, gate_results,
                                     envelope=envelope, decision_id=envelope["decision_id"],
                                     authority_ref=ref_dict)
    _audit(conversation_id, _record(result, tool_name, user_id, agent_name, turn_id))
    return result


def _record(result: ToolAuthorizationResult, tool_name: str, user_id: str,
            agent_name: str, turn_id: str) -> Dict[str, Any]:
    env = result.envelope or {}
    call = env.get("tool_call") or {}
    return {
        "decision_id": result.decision_id or uuid.uuid4().hex,
        "created_at": time.time(),
        "user_id": user_id,
        "agent_name": agent_name,
        "turn_id": turn_id,
        "tool": tool_name,
        "arguments": call.get("arguments"),
        "arguments_sha256": call.get("arguments_sha256", ""),
        "classification": result.classification,
        "authority": result.authority_ref,
        "decision": result.decision,
        "reason": result.reason,
        "gates": [{"origin": g.get("origin"), "ref": g.get("ref"),
                   "decision": g.get("decision"), "reason": g.get("reason"),
                   "evaluators": [{k: e.get(k) for k in (
                       "source", "source_id", "decision", "reason", "rule_id")}
                       for e in (g.get("evaluators") or [])]}
                  for g in result.gates],
    }


def gate_for_runtime(*, tool_name: str, arguments: Any, user_id: str, conversation_id: str,
                     agent_name: str, runtime: str, permission_mode: str = "default",
                     tool_permission: str = "", allow_prompt: bool = True,
                     approval_cid: str = "", secret_values: Iterable[str] = ()) -> Optional[str]:
    """Shared adapter for the secondary runtimes (WP6).

    Returns ``None`` when no gate is bound (the runtime keeps its legacy rules),
    ``""`` when the gate allows (execute; the generic prompt is replaced), or an
    error string the runtime must return instead of executing. ``ask`` opens the
    normal approval dialog, or — for UX-less callers (``allow_prompt=False``) —
    becomes a needs-confirmation error.
    """
    args = arguments if isinstance(arguments, dict) else {}
    result = authorize_tool_call(
        tool_name=tool_name, arguments=args, user_id=user_id, conversation_id=conversation_id,
        agent_name=agent_name, permission_mode=permission_mode, tool_permission=tool_permission,
        secret_values=secret_values)
    if result.decision == "legacy":
        return None
    if result.decision == "deny":
        return f"Error: Tool '{tool_name}' was denied by the policy gate: {result.reason}"
    if result.decision == "execute":
        return ""
    if not allow_prompt:
        return (f"Error: Tool '{tool_name}' requires interactive confirmation "
                f"(policy gate, {runtime} runtime): {result.reason[:160]}")
    from core.tool_approval import ToolApprovalGate
    approval = ToolApprovalGate.check(
        tool_name, f"[policy gate] {result.reason[:160]} — {tool_name}",
        approval_cid or conversation_id, user_id, arguments=args, agent_name=agent_name)
    if approval != "approved":
        return (f"Error: Tool '{tool_name}' was {approval} by the user "
                "(policy gate asked for confirmation).")
    return ""


def interim_guard(user_id: str, conversation_id: str, agent_name: str, tool_name: str,
                  arguments: Any, *, runtime: str) -> str:
    """Fail closed on runtimes not yet wired to the engine (plan decision 19).

    Returns an error string when a gate is bound for this conversation/agent
    and the call is not authorization plumbing; "" otherwise. The unmigrated
    runtime must return that error instead of executing the call ungated.
    """
    if not conversation_id:
        return ""
    try:
        from core.gating_bindings import resolve_gates
        gates = resolve_gates(user_id, conversation_id, agent_name)
    except Exception:
        logger.debug("interim gate resolution failed", exc_info=True)
        return ""
    if not gates.get("bound"):
        return ""
    classification, _why = classify_call(
        tool_name, arguments if isinstance(arguments, dict) else {})
    if classification == "internal_ungated":
        return ""
    _audit(conversation_id, {
        "decision_id": uuid.uuid4().hex, "created_at": time.time(), "user_id": user_id,
        "agent_name": agent_name, "tool": tool_name, "decision": "deny",
        "classification": classification, "runtime": runtime,
        "reason": "runtime not yet gated; policy gate bound"})
    return (f"Error: Tool '{tool_name}' was refused: a policy gate is bound to this "
            f"conversation or agent and the {runtime} runtime is not gated yet. Run "
            "the call from the main agent or unbind the gate.")


def record_execution_outcome(conversation_id: str, decision_id: str, outcome: str) -> None:
    """Append the execution outcome (started/succeeded/failed/denied) for a decision."""
    if not decision_id:
        return
    _audit(conversation_id, {"decision_id": decision_id, "created_at": time.time(),
                             "outcome": str(outcome)})


__all__ = ["ToolAuthorizationResult", "authorize_tool_call", "gate_for_runtime",
           "interim_guard", "list_decisions", "load_authority", "record_execution_outcome"]

# os is used by callers patching the audit dir in tests; keep the import explicit.
_ = os
