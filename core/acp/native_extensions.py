"""Native ACP questions and notifications, separate from tool authorization."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from acp import RequestError
from acp.schema import AllowedOutcome, RequestPermissionResponse, ToolCallProgress

from core.acp.client_adapter import cancelled_permission_response
from core.native_user_input import collect_native_questions


def interaction_permission(
    tool_call: Any,
    options: list[Any],
    ask: Callable[..., Any],
) -> RequestPermissionResponse | None:
    """Preserve opaque Antigravity interaction option ids, even in auto mode."""
    if not any(str(option.option_id).startswith("interaction_") for option in options):
        return None
    question_id = getattr(tool_call, "tool_call_id", None)
    if not isinstance(question_id, str) or not question_id:
        raise RequestError.invalid_params({"message": "interaction requires toolCallId"})
    title = str(getattr(tool_call, "title", "") or "Choose an answer")
    answers = ask(
        [
            {
                "id": question_id,
                "question": title,
                "options": [
                    {"value": option.option_id, "label": option.name} for option in options
                ],
            }
        ],
        question_id,
    )
    selected = answers.get(question_id) if answers else None
    if not isinstance(selected, str) or not any(option.option_id == selected for option in options):
        return cancelled_permission_response()
    return RequestPermissionResponse(
        outcome=AllowedOutcome(outcome="selected", option_id=selected),
    )


class NativeAcpExtensions:
    """Translate one process's extension requests into durable user interactions."""

    def __init__(
        self,
        *,
        provider: str,
        live: Any,
        user_id: str,
        conversation_id: str,
        agent_name: str,
    ) -> None:
        self.provider = provider
        self.live = live
        self.identity = {
            "provider": provider,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "agent_name": agent_name,
        }
        self.todos: dict[str, dict[str, Any]] = {}
        self.lock = threading.RLock()
        self.sequence = 0

    def ask(self, questions: list[dict[str, Any]], request_id: str) -> Any:
        if self.live.cancel_event.is_set():
            return None
        return collect_native_questions(
            questions,
            **self.identity,
            cancel_event=self.live.cancel_event,
            request_id=request_id,
        )

    def _params(self, method: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        method = method.removeprefix("_")
        if isinstance(params.get("params"), dict) and "method" in params:
            if str(params["method"]).removeprefix("_") != method:
                raise ValueError("mismatched native ACP method")
            params = params["params"]
        session_id = params.get("sessionId")
        if not self.live.session_id or (
            session_id is not None and session_id != self.live.session_id
        ):
            raise ValueError("stale native ACP session")
        if self.provider == "grok-build-acp" and session_id != self.live.session_id:
            raise ValueError("Grok extension requires sessionId")
        return method, params

    @staticmethod
    def _required_text(value: Any, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            method, params = self._params(method, params)
            request_id = self._required_text(params.get("toolCallId"), "toolCallId")
            if self.provider == "cursor-acp":
                if method == "cursor/ask_question":
                    return self._cursor_questions(params, request_id)
                if method == "cursor/create_plan":
                    return self._plan(params, request_id, cursor=True)
                if method == "cursor/update_todos":
                    self._todos(params)
                    return {"outcome": {"outcome": "accepted", "todos": list(self.todos.values())}}
            if self.provider == "grok-build-acp":
                if method == "x.ai/ask_user_question":
                    return self._grok_questions(params, request_id)
                if method == "x.ai/exit_plan_mode":
                    return self._plan(params, request_id, cursor=False)
        except (ValueError, TypeError, KeyError) as exc:
            raise RequestError.invalid_params({"message": str(exc)}) from exc
        raise RequestError.method_not_found(method)

    def _cursor_questions(self, params: dict[str, Any], request_id: str) -> dict[str, Any]:
        questions = [
            {
                "id": q["id"],
                "question": q["prompt"],
                "options": [{"value": o["id"], "label": o["label"]} for o in q["options"]],
                "multi_select": q.get("allowMultiple") is True,
            }
            for q in params["questions"]
        ]
        answers = self.ask(questions, request_id)
        if answers is None:
            return {"outcome": {"outcome": "cancelled"}}
        resolved = []
        for question in questions:
            value = answers.get(question["id"])
            selected = value if isinstance(value, list) else [value]
            allowed = {option["value"] for option in question["options"]}
            if not selected or any(item not in allowed for item in selected):
                raise ValueError("Cursor answer must select supplied option ids")
            if not question["multi_select"] and len(selected) != 1:
                raise ValueError("Cursor question permits one answer")
            resolved.append({"questionId": question["id"], "selectedOptionIds": selected})
        return {"outcome": {"outcome": "answered", "answers": resolved}}

    def _grok_questions(self, params: dict[str, Any], request_id: str) -> dict[str, Any]:
        questions = [
            {
                "id": q.get("id") if q.get("id") is not None else q["question"],
                "question": q["question"],
                "options": [{"value": o["label"], "label": o["label"]} for o in q["options"]],
                "multi_select": q.get("multiSelect") is True,
                "allow_other": True,
            }
            for q in params["questions"]
        ]
        if len({q["question"] for q in questions}) != len(questions):
            raise ValueError("Grok question text must be unique")
        answers = self.ask(questions, request_id)
        if answers is None:
            return {"outcome": "cancelled"}
        result: dict[str, Any] = {"outcome": "accepted", "answers": {}}
        annotations = {}
        for native, question in zip(params["questions"], questions):
            value = answers.get(question["id"])
            values = value if isinstance(value, list) else [value]
            if not values or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError("Grok answer must contain non-empty strings")
            options = {o["label"]: o for o in native["options"]}
            selected = [v for v in values if v in options]
            notes = [v for v in values if v not in options]
            result["answers"][question["question"]] = selected or ["Other"]
            annotation = {}
            if notes:
                annotation["notes"] = "\n".join(notes)
            if not question["multi_select"]:
                preview = next(
                    (options[v].get("preview") for v in selected if options[v].get("preview")), None
                )
                if preview:
                    annotation["preview"] = preview
            if annotation:
                annotations[question["question"]] = annotation
        if annotations:
            result["annotations"] = annotations
        return result

    def _plan(self, params: dict[str, Any], request_id: str, *, cursor: bool) -> dict[str, Any]:
        plan = params.get("plan" if cursor else "planContent")
        if cursor:
            plan = self._required_text(plan, "plan")
        elif not isinstance(plan, str) or not plan.strip():
            plan = "The agent exited plan mode without supplying plan text."
        options = (
            [
                {"value": "accepted", "label": "Accept plan"},
                {"value": "rejected", "label": "Reject plan"},
            ]
            if cursor
            else [
                {"value": "approved", "label": "Approve plan"},
                {"value": "abandoned", "label": "Abandon plan"},
                {"value": "request_changes", "label": "Request changes"},
            ]
        )
        questions = [{"id": "plan", "question": plan, "options": options}]
        answers = self.ask(questions, request_id)
        if answers is None:
            return {"outcome": {"outcome": "cancelled"}} if cursor else {"outcome": "abandoned"}
        outcome = answers.get("plan")
        if outcome not in {o["value"] for o in options}:
            raise ValueError("Invalid native plan decision")
        if cursor:
            return {"outcome": {"outcome": outcome}}
        result = {"outcome": outcome}
        if outcome == "request_changes":
            feedback = self.ask(
                [{"id": "feedback", "question": "What should change in this plan?"}],
                request_id + ":feedback",
            )
            if feedback is None:
                return {"outcome": "abandoned"}
            result["feedback"] = self._required_text(feedback.get("feedback"), "feedback")
        return result

    def notification(self, method: str, params: dict[str, Any]) -> None:
        try:
            method, params = self._params(method, params)
            if self.provider == "cursor-acp" and method == "cursor/update_todos":
                self._todos(params)
        except (ValueError, TypeError, KeyError) as exc:
            raise RequestError.invalid_params({"message": str(exc)}) from exc

    def _todos(self, params: dict[str, Any]) -> None:
        if self.live.cancel_event.is_set():
            return
        if not isinstance(params.get("merge"), bool) or not isinstance(params.get("todos"), list):
            raise TypeError("Cursor todos require todos and merge")
        incoming = {}
        for todo in params["todos"]:
            identifier = self._required_text(todo.get("id"), "todo id")
            incoming[identifier] = dict(todo)
        with self.lock:
            if not params["merge"]:
                self.todos.clear()
            self.todos.update(incoming)
            self.sequence += 1
            update = ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id=f'{params["toolCallId"]}:todos:{self.sequence}',
                title="Cursor todos",
                kind="think",
                status="completed",
                raw_output={"todos": list(self.todos.values()), "merge": params["merge"]},
            )
        process = self.live.process
        if process is not None:
            process.events.publish_update(process.generation, self.live.session_id, update)
