"""Present native provider questions through the existing interaction store."""

from __future__ import annotations

import threading
from typing import Any


class NativeInputRequests:
    """Bound pending native questions and fence replies after cancellation."""

    def __init__(self):
        self._lock = threading.RLock()
        self._reply_lock = threading.Lock()
        self._pending = {}
        self._active = set()
        self._closed = False

    @property
    def pending(self):
        with self._lock:
            return bool(self._pending)

    def submit(self, request_id, collect, reply):
        with self._lock:
            if self._closed:
                return
            if request_id in self._pending:
                raise ValueError("Duplicate native request id")
            if len(self._active) >= 16:
                raise ValueError("Too many pending native questions")
            cancel = threading.Event()
            self._pending[request_id] = cancel
            self._active.add(cancel)

        def run():
            try:
                result = collect(cancel)
                # Serialize writes without holding the state lock: a full stdin
                # pipe must not block cancellation or process shutdown.
                with self._reply_lock:
                    with self._lock:
                        send = self._pending.get(request_id) is cancel and not cancel.is_set()
                    if send:
                        reply(result)
            finally:
                with self._lock:
                    self._active.discard(cancel)
                    if self._pending.get(request_id) is cancel:
                        self._pending.pop(request_id)
        threading.Thread(target=run, daemon=True, name="native-user-input").start()

    def cancel(self, request_id):
        with self._lock:
            event = self._pending.pop(request_id, None)
            if event is not None:
                event.set()

    def close(self):
        with self._lock:
            self._closed = True
            for event in self._pending.values():
                event.set()
            self._pending.clear()


def native_question_fields(questions):
    """Validate native questions and return form fields plus opaque id bindings."""
    if not isinstance(questions, list) or not 1 <= len(questions) <= 16:
        raise ValueError("native questions must contain between 1 and 16 questions")
    fields = []
    bindings = []
    ids = set()
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            raise ValueError("native question must be an object")
        identifier = question.get("id")
        title = question.get("question")
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise ValueError("native question ids must be non-empty and unique")
        if not isinstance(title, str) or not title.strip() or len(title) > 16000:
            raise ValueError("native question text must contain 1 to 16000 characters")
        ids.add(identifier)
        options = question.get("options", [])
        if not isinstance(options, list) or len(options) > 64:
            raise ValueError("native question options must be an array of at most 64 entries")
        values = set()
        normalized = []
        for option in options:
            if not isinstance(option, dict):
                raise ValueError("native question option must be an object")
            value = option.get("value")
            label = option.get("label")
            if not isinstance(value, str) or not value or value in values:
                raise ValueError("native option values must be non-empty and unique")
            if not isinstance(label, str) or not label.strip() or len(label) > 2000:
                raise ValueError("native option labels must contain 1 to 2000 characters")
            values.add(value)
            normalized.append({"value": value, "label": label})
        name = f"question_{index}"
        for flag in ("multi_select", "allow_other"):
            if not isinstance(question.get(flag, False), bool):
                raise ValueError(f"native question {flag} must be a boolean")
        multiple = question.get("multi_select") is True
        field = {"name": name, "label": title, "required": True,
                 "type": ("multi" if multiple else "choice") if options else "multiline",
                 "options": normalized, "min_length": 1, "max_length": 16000,
                 "allow_other": question.get("allow_other") is True,
                 "no_default": True}
        fields.append(field)
        bindings.append((identifier, name))
    return fields, bindings


def collect_native_questions(
    questions: list[dict[str, Any]], *, provider: str, user_id: str,
    conversation_id: str, agent_name: str, cancel_event: threading.Event,
    request_id: str, store: Any = None,
) -> dict[str, Any] | None:
    """Wait on a provider worker; user questions never use tool auto-approval."""
    if not all((provider, user_id, conversation_id, agent_name, request_id)):
        raise ValueError("native questions require provider and complete request identity")
    fields, bindings = native_question_fields(questions)
    if cancel_event.is_set():
        return None
    if store is None:
        from core.confirmation_store import UserInteractionStore
        store = UserInteractionStore.instance()
    record = store.create_interaction(
        conversation_id=conversation_id, user_id=user_id,
        requester_kind="provider", requester=agent_name,
        title=f"{provider}: user input", message="Please answer the provider questions.",
        kind="form", response_schema={"fields": fields},
        continuation={"provider": provider, "native_request_id": request_id},
    )
    interaction_id = record["request_id"]
    try:
        while not cancel_event.is_set():
            current = store.get_confirmation(interaction_id)
            if current is None or current["status"] in {"cancelled", "expired"}:
                return None
            if current["status"] == "answered":
                answer = current["answer"]
                return {identifier: answer[name] for identifier, name in bindings}
            cancel_event.wait(0.2)
        return None
    finally:
        # Cancellation and disconnects cannot leave a stale question in the UI.
        store.cancel(interaction_id, cancelled_by=agent_name)
