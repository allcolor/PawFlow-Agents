"""Pure policy-gating logic: decisions, merging, redaction, classification.

Implements docs/POLICY_GATING_SERVICE_PLAN.md sections 8.6-8.7, 11.3-11.4,
12.3 and 13. No I/O lives here so every rule is unit-testable; the service
(``services/gating_service.py``) and the engine call into this module.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

DECISIONS = ("allow", "deny", "ask", "abstain")
FINAL_DECISIONS = ("allow", "deny", "ask")
FAILURE_DECISIONS = ("ask", "deny")
LLM_SCOPES = ("mutating", "all", "none")
#: Restrictiveness order: a more restrictive decision always wins a merge.
_SEVERITY = {"deny": 3, "ask": 2, "allow": 1, "abstain": 0}

#: Calls the authorization mechanism itself needs (plan section 11.4). Kept
#: deliberately small; user-visible reads are NOT internal.
INTERNAL_UNGATED_TOOLS = frozenset({
    "get_tool_schema", "ask_user", "request_confirmation", "compact_result",
})
#: Calls that widen what an agent can do and therefore always keep a human
#: confirmation floor even when a gate says allow (plan section 11.3 / A8).
HARD_CONFIRM_TOOLS = frozenset({
    "create_tool", "delete_tool", "manage_resource", "manage_package",
    "store_secret",
})
HARD_CONFIRM_ACTION_PREFIXES = ("a2a_publication_", "mcp_publish", "agui_")

_SECRET_KEY_RE = re.compile(
    r"(authorization|passw|secret|token|api[-_]?key|cookie|credential|private[-_]?key)",
    re.IGNORECASE)


def normalize_decision(value: Any) -> str:
    """Return one of DECISIONS or "" when the value is not a valid decision."""
    text = str(value or "").strip().lower()
    return text if text in DECISIONS else ""


def evaluator_result(decision: str, reason: str = "", *, source: str,
                     source_id: str = "", rule_id: str = "",
                     matched_directive_ids: Optional[Iterable[str]] = None,
                     metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build one evaluator result record (plan section 8.7)."""
    normalized = normalize_decision(decision)
    if not normalized:
        raise ValueError(f"invalid gating decision: {decision!r}")
    return {
        "decision_id": uuid.uuid4().hex,
        "created_at": time.time(),
        "decision": normalized,
        "reason": sanitize_reason(reason),
        "rule_id": str(rule_id or "")[:120],
        "matched_directive_ids": [str(x) for x in (matched_directive_ids or []) if x][:32],
        "source": str(source or ""),
        "source_id": str(source_id or ""),
        "metadata": dict(metadata or {}),
    }


def sanitize_reason(reason: Any, limit: int = 500) -> str:
    """Reasons are displayed and logged: strip markup and bound the length."""
    text = re.sub(r"<[^>]*>", "", str(reason or ""))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return " ".join(text.split())[:limit]


def merge_decisions(results: Iterable[Dict[str, Any]],
                    failure_decision: str = "ask") -> str:
    """``deny > ask > allow``; nothing decided maps to ``failure_decision``."""
    best = "abstain"
    for result in results:
        decision = normalize_decision((result or {}).get("decision"))
        if decision and _SEVERITY[decision] > _SEVERITY[best]:
            best = decision
    if best == "abstain":
        return failure_decision if failure_decision in FAILURE_DECISIONS else "ask"
    return best


def compose_final(conversation: Optional[str], agent: Optional[str]) -> str:
    """Conversation gate + agent gate (plan section 9.3): agent can tighten only."""
    candidates = [d for d in (conversation, agent) if d in FINAL_DECISIONS]
    if not candidates:
        return "ask"
    return max(candidates, key=lambda d: _SEVERITY[d])


# ── redaction ────────────────────────────────────────────────────────

def redact_arguments(arguments: Any, secret_values: Iterable[str] = (),
                     *, max_string: int = 2000, max_items: int = 64,
                     max_depth: int = 8) -> Any:
    """Copy of the arguments safe for scripts, LLMs, SSE and audit storage."""
    secrets = sorted({str(s) for s in secret_values if s and len(str(s)) >= 4},
                     key=len, reverse=True)

    def _scrub_text(text: str) -> str:
        for secret in secrets:
            if secret in text:
                text = text.replace(secret, "<secret>")
        if len(text) > max_string:
            text = text[:max_string] + f"...<{len(text) - max_string} more chars>"
        return text

    def _walk(value: Any, depth: int) -> Any:
        if depth > max_depth:
            return "<depth limit>"
        if isinstance(value, dict):
            out: Dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= max_items:
                    out["<truncated>"] = f"{len(value) - max_items} more keys"
                    break
                key_text = str(key)
                if _SECRET_KEY_RE.search(key_text):
                    out[key_text] = "<redacted>"
                else:
                    out[key_text] = _walk(item, depth + 1)
            return out
        if isinstance(value, (list, tuple)):
            items = [_walk(item, depth + 1) for item in list(value)[:max_items]]
            if len(value) > max_items:
                items.append(f"<{len(value) - max_items} more items>")
            return items
        if isinstance(value, str):
            return _scrub_text(value)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return _scrub_text(str(value))

    return _walk(arguments, 0)


def canonical_hash(tool_name: str, redacted_arguments: Any) -> str:
    """Audit correlation hash of the canonical *redacted* call."""
    payload = json.dumps({"tool": str(tool_name), "arguments": redacted_arguments},
                         sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── classification ───────────────────────────────────────────────────

def is_mutating_call(tool_name: str, arguments: Dict[str, Any]) -> bool:
    """True unless PawFlow's read-only policy would let the call through.

    ``ToolApprovalGate.is_read_only_allowed`` is the single source of truth
    for "this is a read"; everything else is treated as mutating, so an
    unknown tool is never silently classified as harmless.
    """
    from core.tool_approval import ToolApprovalGate
    try:
        return not ToolApprovalGate.is_read_only_allowed(tool_name, arguments)
    except Exception:
        return True


def classify_call(tool_name: str, arguments: Dict[str, Any], *,
                  permission_mode: str = "default", tool_permission: str = "",
                  read_only_override: bool = False,
                  capability_effects: Optional[Iterable[Any]] = None) -> Tuple[str, str]:
    """Return ``(class, reason)`` with class in
    ``internal_ungated | hard_deny | hard_confirm | ordinary``.

    Structural guards are PawFlow's own rules; a gate can never weaken them.
    """
    from core.tool_approval import ToolApprovalGate
    name = ToolApprovalGate.normalize_tool_name(tool_name)
    args = arguments if isinstance(arguments, dict) else {}
    if name in INTERNAL_UNGATED_TOOLS:
        return "internal_ungated", "authorization plumbing"
    if tool_permission == "deny":
        return "hard_deny", "tool is denied by permission settings"
    if permission_mode in ("read_only", "advisor_read_only"):
        if capability_effects is not None:
            from core.agent_contracts import READ_ONLY_EFFECTS, CapabilityEffect
            try:
                effects = {
                    value if isinstance(value, CapabilityEffect)
                    else CapabilityEffect(value) for value in capability_effects
                }
            except ValueError:
                return "hard_deny", "unknown capability effect"
            if not effects or not effects <= READ_ONLY_EFFECTS:
                return "hard_deny", "read-only mode"
        elif not ToolApprovalGate.is_read_only_allowed(name, args):
            return "hard_deny", "read-only mode"
    if read_only_override and not ToolApprovalGate.is_read_only_allowed(name, args):
        return "hard_deny", "read-only mode"
    if tool_permission == "confirm":
        return "hard_confirm", "tool requires confirmation by permission settings"
    if name in HARD_CONFIRM_TOOLS:
        return "hard_confirm", "widens agent capabilities"
    action = str(args.get("action") or "")
    if action.startswith(HARD_CONFIRM_ACTION_PREFIXES):
        return "hard_confirm", "changes a published surface"
    if ToolApprovalGate.is_command_bearing_tool(name):
        command = str(args.get("command") or args.get("cmd") or "")
        if command and ToolApprovalGate._is_catastrophic_command(command):
            return "hard_confirm", "catastrophic command"
    try:
        paths = ToolApprovalGate._write_paths(name, args)
    except Exception:
        paths = []
    if any(ToolApprovalGate._is_protected_path(p) for p in paths):
        return "hard_confirm", "writes a protected path"
    return "ordinary", ""


# ── envelope ─────────────────────────────────────────────────────────

def build_envelope(*, user_id: str, conversation_id: str, agent_name: str,
                   turn_id: str, tool_name: str, arguments: Dict[str, Any],
                   call_id: str = "", authorization: Optional[Dict[str, Any]] = None,
                   secret_values: Iterable[str] = (), classification: str = "ordinary",
                   max_authority_chars: Optional[int] = None,
                   capability_effects: Optional[Iterable[Any]] = None) -> Dict[str, Any]:
    """Immutable evaluator input (plan section 8.6). Arguments are redacted."""
    from core.authorization_context import DEFAULT_ENVELOPE_CHARS, authority_envelope
    redacted = redact_arguments(arguments, secret_values)
    authority = None
    if authorization:
        authority = authority_envelope(
            authorization, max_authority_chars or DEFAULT_ENVELOPE_CHARS)
    return {
        "schema_version": 1,
        "decision_id": uuid.uuid4().hex,
        "created_at": time.time(),
        "identity": {
            "user_id": str(user_id or ""),
            "conversation_id": str(conversation_id or ""),
            "agent_name": str(agent_name or ""),
            "turn_id": str(turn_id or ""),
            "authorization_context_id": (authority or {}).get("context_id", ""),
            "authorization_revision": (authority or {}).get("revision", 0),
        },
        "authority": authority,
        "authority_missing": authority is None,
        "tool_call": {
            "call_id": str(call_id or ""),
            "canonical_name": str(tool_name or ""),
            "arguments": redacted,
            "arguments_sha256": canonical_hash(tool_name, redacted),
            "policy_classification": classification,
            "mutating": is_mutating_call(tool_name, arguments),
            "capability_effects": [
                str(getattr(value, "value", value))
                for value in (capability_effects or ())
            ],
        },
    }


# ── LLM output parsing (provider-agnostic, plan A2) ───────────────────

_ALLOWED_LLM_KEYS = {"decision", "reason", "matched_directive_ids", "rule_id"}


def _first_json_object(text: str) -> Optional[Dict[str, Any]]:
    depth = 0
    start = -1
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    value = json.loads(text[start:index + 1])
                except ValueError:
                    start = -1
                    continue
                return value if isinstance(value, dict) else None
    return None


def parse_llm_decision(text: Any) -> Optional[Dict[str, Any]]:
    """Strictly parse the gate LLM answer; None means "malformed".

    Works on any provider output (fenced, prefixed with prose, JSON mode or
    not). Unknown keys are rejected so a model cannot smuggle control fields.
    """
    raw = _first_json_object(str(text or ""))
    if raw is None:
        return None
    if set(raw) - _ALLOWED_LLM_KEYS:
        return None
    decision = normalize_decision(raw.get("decision"))
    if not decision:
        return None
    matched = raw.get("matched_directive_ids") or []
    if not isinstance(matched, list):
        return None
    return {
        "decision": decision,
        "reason": sanitize_reason(raw.get("reason")),
        "matched_directive_ids": [str(x) for x in matched][:32],
        "rule_id": str(raw.get("rule_id") or "")[:120],
    }


def evaluator_summary(results: List[Dict[str, Any]]) -> str:
    """One-line, user-facing explanation assembled from evaluator reasons."""
    parts = []
    for result in results:
        decision = result.get("decision", "")
        reason = result.get("reason") or ""
        label = result.get("source_id") or result.get("source") or "gate"
        parts.append(f"{label}: {decision}" + (f" ({reason})" if reason else ""))
    return "; ".join(parts)[:900]


__all__ = [
    "DECISIONS", "FAILURE_DECISIONS", "FINAL_DECISIONS", "HARD_CONFIRM_TOOLS",
    "INTERNAL_UNGATED_TOOLS", "LLM_SCOPES", "build_envelope", "canonical_hash",
    "classify_call", "compose_final", "evaluator_result", "evaluator_summary",
    "is_mutating_call", "merge_decisions", "normalize_decision",
    "parse_llm_decision", "redact_arguments", "sanitize_reason",
]
