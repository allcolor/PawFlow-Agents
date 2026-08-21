"""Deterministic policy scripts for the gating service (plan sections 8.2, 12.1).

A ``gating_script`` resource holds a Python ``evaluate(event)`` function that
returns ``{"decision": "allow|deny|ask|abstain", "reason": ..., "rule_id": ...}``.
Scripts run in the relay-backed sandbox used by agent hooks, never in the
server process, receive only the redacted envelope, cannot rewrite the call,
and default to ``abstain`` (the hook protocol's default ``allow`` is never
reused here). Any failure maps to the script's ``fail_decision``.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any, Dict, List, Optional, Tuple

from core.gating_policy import evaluator_result, normalize_decision

logger = logging.getLogger(__name__)

SCRIPT_RESOURCE_TYPE = "gating_script"
DEFAULT_SCRIPT_TIMEOUT_SECONDS = 10


def resolve_scripts(names: List[str], user_id: str,
                    conversation_id: str = "") -> List[Tuple[str, Optional[Dict[str, Any]]]]:
    """Resolve script resources conversation > user > global, in declared order."""
    from core.resource_store import ResourceStore
    store = ResourceStore.instance()
    resolved = []
    for raw in names or []:
        name = str(raw or "").strip()
        if not name:
            continue
        try:
            resource = store.get_any(SCRIPT_RESOURCE_TYPE, name, user_id, conversation_id)
        except Exception:
            logger.debug("gating script lookup failed: %s", name, exc_info=True)
            resource = None
        resolved.append((name, resource if isinstance(resource, dict) else None))
    return resolved


def script_applies(script: Dict[str, Any], tool_name: str) -> bool:
    tools = script.get("tools") or []
    if not tools:
        return True
    wanted = {str(t).strip().lower() for t in tools if str(t).strip()}
    return str(tool_name or "").lower() in wanted


def fail_decision_of(script: Dict[str, Any]) -> str:
    value = str((script or {}).get("fail_decision") or "ask").lower()
    return value if value in ("ask", "deny") else "ask"


def _source_wrapper(source: str, envelope: Dict[str, Any]) -> str:
    envelope_json = json.dumps(envelope, ensure_ascii=False)
    return (
        "import json\n"
        f"event = json.loads({envelope_json!r})\n"
        "result = None\n"
        + source
        + "\n"
        "if 'evaluate' in globals() and callable(evaluate):\n"
        "    result = evaluate(event)\n"
        "if not isinstance(result, dict):\n"
        "    result = {'decision': 'abstain'}\n"
        "print(json.dumps({'decision': result.get('decision', 'abstain'), "
        "'reason': result.get('reason', ''), 'rule_id': result.get('rule_id', ''), "
        "'metadata': result.get('metadata', {})}, ensure_ascii=False, default=str))\n"
    )


def _last_json_line(output: Any) -> Optional[Dict[str, Any]]:
    text = str(output or "").strip()
    for line in reversed([ln for ln in text.splitlines() if ln.strip()]):
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _execute_in_sandbox(code: str) -> str:
    from core.handlers.web_execute import ExecuteScriptHandler
    return str(ExecuteScriptHandler().execute({
        "code": code, "destination": "sandbox", "max_output": 4000}) or "")


def run_script(name: str, script: Optional[Dict[str, Any]], envelope: Dict[str, Any],
               *, timeout_seconds: float = DEFAULT_SCRIPT_TIMEOUT_SECONDS,
               executor=None) -> Dict[str, Any]:
    """Run one script and return an evaluator result; never raises."""
    if script is None:
        return evaluator_result("ask", f"policy script '{name}' is not available",
                                source="failure", source_id=name,
                                metadata={"error": "missing_script"})
    fail = fail_decision_of(script)
    if not script_applies(script, (envelope.get("tool_call") or {}).get("canonical_name", "")):
        return evaluator_result("abstain", "tool filter does not match",
                                source="script", source_id=name)
    source = str(script.get("source") or "")
    if not source.strip():
        return evaluator_result(fail, f"policy script '{name}' has no source",
                                source="failure", source_id=name)
    code = _source_wrapper(source, envelope)
    run = executor or _execute_in_sandbox
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(run, code)
        try:
            output = future.result(timeout=max(1.0, float(timeout_seconds)))
        except FutureTimeout:
            return evaluator_result(fail, f"policy script '{name}' timed out",
                                    source="failure", source_id=name,
                                    metadata={"error": "timeout"})
        except Exception as exc:
            return evaluator_result(fail, f"policy script '{name}' failed: {exc}",
                                    source="failure", source_id=name,
                                    metadata={"error": "exception"})
    finally:
        pool.shutdown(wait=False)
    parsed = _last_json_line(output)
    if parsed is None:
        return evaluator_result(fail, f"policy script '{name}' returned no JSON decision",
                                source="failure", source_id=name,
                                metadata={"error": "malformed"})
    decision = normalize_decision(parsed.get("decision"))
    if not decision:
        return evaluator_result(fail, f"policy script '{name}' returned an invalid decision",
                                source="failure", source_id=name,
                                metadata={"error": "invalid_decision"})
    metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
    return evaluator_result(decision, parsed.get("reason", ""), source="script",
                            source_id=name, rule_id=parsed.get("rule_id", ""),
                            metadata=metadata)


def run_scripts(scripts: List[Tuple[str, Optional[Dict[str, Any]]]],
                envelope: Dict[str, Any], *, timeout_seconds: float =
                DEFAULT_SCRIPT_TIMEOUT_SECONDS, executor=None) -> List[Dict[str, Any]]:
    """Run scripts in order; ``deny``/``ask`` short-circuit (nothing later can
    make the result less restrictive)."""
    results: List[Dict[str, Any]] = []
    for name, script in scripts:
        result = run_script(name, script, envelope, timeout_seconds=timeout_seconds,
                            executor=executor)
        results.append(result)
        if result["decision"] in ("deny", "ask"):
            break
    return results


__all__ = ["DEFAULT_SCRIPT_TIMEOUT_SECONDS", "SCRIPT_RESOURCE_TYPE", "fail_decision_of",
           "resolve_scripts", "run_script", "run_scripts", "script_applies"]
