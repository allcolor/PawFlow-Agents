"""Translate documented Claude hooks into PawFlow user interactions."""

from __future__ import annotations

import json
import threading
import uuid

from core.native_user_input import collect_native_questions
from core.tool_approval import ToolApprovalGate


def _read_only_denies(conversation_id: str, tool: str, inputs: dict) -> bool:
    return (ToolApprovalGate.get_mode(conversation_id) == "read_only"
            and not ToolApprovalGate.is_read_only_allowed(tool.lower(), inputs))


def answer_claude_hook(raw: dict, *, provider: str, user_id: str,
                       conversation_id: str, agent_name: str,
                       cancel_event: threading.Event) -> dict:
    """Preserve native question text and input when returning hook decisions."""
    if not isinstance(raw, dict):
        raise ValueError("Claude hook input must be an object")
    event = raw.get("hook_event_name")
    tool = raw.get("tool_name")
    if not isinstance(tool, str) or not tool.strip():
        raise ValueError("Claude hook tool_name must be a non-empty string")
    inputs = raw.get("tool_input")
    if not isinstance(inputs, dict):
        raise ValueError("Claude hook tool_input must be an object")
    request_id = str(raw.get("tool_use_id") or uuid.uuid4())
    if event == "PreToolUse" and tool == "AskUserQuestion":
        source = inputs.get("questions")
        if not isinstance(source, list) or not source:
            raise ValueError("AskUserQuestion requires questions")
        questions = []
        texts = set()
        for index, question in enumerate(source):
            if not isinstance(question, dict):
                raise ValueError("AskUserQuestion entries must be objects")
            title = question.get("question")
            if not isinstance(title, str) or title in texts:
                raise ValueError("AskUserQuestion text must be unique")
            texts.add(title)
            options = question.get("options", [])
            if not isinstance(options, list) or any(not isinstance(o, dict) for o in options):
                raise ValueError("AskUserQuestion options must be objects")
            if not isinstance(question.get("multiSelect", False), bool):
                raise ValueError("AskUserQuestion multiSelect must be a boolean")
            questions.append({
                "id": str(index), "question": title,
                "multi_select": question.get("multiSelect") is True,
                "allow_other": True,
                "options": [{"value": o.get("label"), "label": o.get("label")}
                            for o in options],
            })
        answers = collect_native_questions(
            questions, provider=provider, user_id=user_id,
            conversation_id=conversation_id, agent_name=agent_name,
            cancel_event=cancel_event, request_id=request_id)
        output = {"hookEventName": event, "permissionDecision": "deny",
                  "permissionDecisionReason": "User input was cancelled."}
        if answers is not None:
            mapped = {}
            for index, question in enumerate(source):
                value = answers[str(index)]
                mapped[question["question"]] = ", ".join(value) if isinstance(value, list) else value
            output.update(permissionDecision="allow", updatedInput={**inputs, "answers": mapped})
            output.pop("permissionDecisionReason")
        return {"hookSpecificOutput": output}
    if event == "PermissionRequest":
        blocked = {"hookSpecificOutput": {"hookEventName": event, "decision": {
            "behavior": "deny", "message": "Tool is blocked by PawFlow read_only mode."}}}
        if _read_only_denies(conversation_id, tool, inputs):
            return blocked
        answers = collect_native_questions(
            [{"id": "permission", "question": f"Allow {tool}?\n{json.dumps(inputs, ensure_ascii=False)[:12000]}",
              "options": [{"value": "allow", "label": "Allow once"},
                          {"value": "deny", "label": "Deny"}]}],
            provider=provider, user_id=user_id, conversation_id=conversation_id,
            agent_name=agent_name, cancel_event=cancel_event, request_id=request_id)
        # The conversation policy can change while a native prompt is pending.
        if _read_only_denies(conversation_id, tool, inputs):
            return blocked
        decision = {"behavior": "allow" if answers and answers["permission"] == "allow" else "deny"}
        if decision["behavior"] == "deny":
            decision["message"] = "Permission was denied or cancelled in PawFlow."
        return {"hookSpecificOutput": {"hookEventName": event, "decision": decision}}
    raise ValueError("Unsupported native Claude interaction hook")
