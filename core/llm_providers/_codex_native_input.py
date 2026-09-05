"""Codex app-server native questions, with asynchronous reply correlation."""

from core.native_user_input import collect_native_questions, native_question_fields


def codex_questions(source, *, asynchronous=False):
    if not isinstance(source, list) or not source:
        raise ValueError("Codex requires a nonempty questions array")
    result = []
    for index, question in enumerate(source):
        if not isinstance(question, dict):
            raise ValueError("Codex question must be an object")
        options = question.get("options") or []
        if not isinstance(options, list):
            raise ValueError("Codex question options must be an array")
        if asynchronous:
            options = [{"label": label} for label in options]
        if any(not isinstance(o, dict) for o in options):
            raise ValueError("Codex question options must be objects")
        result.append({
            "id": str(index) if asynchronous else question.get("id"),
            "question": question.get("title" if asynchronous else "question"),
            "options": [{"value": o.get("label"), "label": o.get("label")} for o in options],
            "allow_other": asynchronous or question.get("isOther") is True,
            "multi_select": False,
        })
    native_question_fields(result)
    return result


def handle_server_request(client, proc, message):
    """Return immediately so the reader can receive serverRequest/resolved."""
    method = message.get("method")
    request_id = message["id"]
    params = message.get("params") or {}
    context = getattr(client, "_codex_native_context", None)
    manager = getattr(client, "_codex_native_inputs", None)
    if method not in {"item/tool/requestUserInput", "tool/requestUserInput"}:
        client._codex_app_send(proc, {"id": request_id, "error": {
            "code": -32601, "message": "Unsupported PawFlow client request"}})
        return
    if manager is None or context is None:
        client._codex_app_send(proc, {"id": request_id, "error": {
            "code": -32600, "message": "No active native input context"}})
        return

    def collect(cancel):
        try:
            if not isinstance(params, dict):
                raise ValueError("Codex request params must be an object")
            questions = codex_questions(params.get("questions"))
            answers = collect_native_questions(
                questions, provider="codex-app-server", **context,
                cancel_event=cancel, request_id=str(request_id))
            return {"id": request_id, "result": {"answers": {
                q["id"]: {"answers": (
                    [answers[q["id"]]] if answers is not None else [])}
                for q in questions}}}
        except (ValueError, TypeError, KeyError):
            return {"id": request_id, "error": {
                "code": -32602, "message": "Invalid native user input request"}}

    def reply(frame):
        if proc.poll() is None:
            client._codex_app_send(proc, frame)

    manager.submit(request_id, collect, reply)


def publish_async_questions(item, *, user_id, conversation_id, agent_name, thread_id, store=None):
    """Persist message-mode questions; the interaction store resumes the agent."""
    if not all((user_id, conversation_id, agent_name, thread_id, item.get("id"))):
        raise ValueError("Async Codex questions require full request identity")
    questions = codex_questions(item.get("questions"), asynchronous=True)
    fields, _ = native_question_fields(questions)
    if store is None:
        from core.confirmation_store import UserInteractionStore
        store = UserInteractionStore.instance()
    return store.create_interaction(
        conversation_id=conversation_id, user_id=user_id,
        requester_kind="provider", requester=agent_name,
        title="Codex: user input", message="Please answer the Codex questions.",
        kind="form", response_schema={"fields": fields},
        idempotency_key=f"codex-async:{agent_name}:{thread_id}:{item['id']}",
        continuation={"provider": "codex-app-server", "response_mode": "message",
                      "thread_id": thread_id, "item_id": item["id"]})
