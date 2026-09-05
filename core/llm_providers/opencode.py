"""Native OpenCode SDK v2 provider (managed isolated Docker runtimes only).

The server protocol follows OpenCode >=1.14.19 and t3code's pinned adapter
dd7bc147f799f290eb58578a7f81643ecf9ad52e. No JavaScript SDK is imported by
PawFlow: tools/opencode_bridge.py carries HTTP/SSE over private Docker stdio.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from core._llm_types import ColdStartRequired, LLMClientError, LLMResponse
from core.opencode_pool import (
    OpenCodeCancelled, OpenCodeHTTPError, OpenCodePool, OpenCodeRuntime,
    scope_digest,
)

PROVIDER = "opencode"
_STATE_LOCK = threading.Lock()
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_ENV = {"HOME", "USER", "LOGNAME", "PATH", "SHELL", "BASH_ENV", "ENV"}
_RESERVED_PREFIXES = ("PAWFLOW_", "OPENCODE_", "XDG_", "LD_", "DYLD_", "PYTHON", "NODE", "BUN_")


def _boolean(value, default):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if str(value).lower() in {"true", "1", "yes", "on"}:
        return True
    if str(value).lower() in {"false", "0", "no", "off"}:
        return False
    raise ValueError("OpenCode boolean option is invalid")


def validate_opencode_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate service fields without starting Docker or requiring credentials."""
    mode = str(config.get("opencode_mode") or "managed").strip()
    if mode != "managed" or config.get("opencode_base_url"):
        raise ValueError("OpenCode supports managed runtimes only; shared HTTP endpoints are unsafe for session MCP")
    if str(config.get("auth_mode") or "none") != "none":
        raise ValueError("OpenCode uses native CLI authentication; auth_mode must be none")
    if config.get("credential_service_id") or config.get("api_key"):
        raise ValueError("OpenCode uses native CLI auth or provider keys in opencode_env")
    raw = config.get("opencode_env") or {}
    try:
        env = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError as exc:
        raise ValueError("opencode_env must be a JSON object of strings") from exc
    if not isinstance(env, dict):
        raise ValueError("opencode_env must be a JSON object of strings")
    for name, value in env.items():
        if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
            raise ValueError("opencode_env contains an invalid variable name")
        if name in _RESERVED_ENV or name.startswith(_RESERVED_PREFIXES):
            raise ValueError(f"opencode_env cannot override runtime variable {name}")
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError("opencode_env values must be strings without NUL")
    mcp_mode = str(config.get("opencode_mcp_mode") or "pawflow").strip()
    if mcp_mode not in {"pawflow", "none"}:
        raise ValueError("opencode_mcp_mode must be pawflow or none")
    return {
        "mode": mode, "env": dict(env), "mcp_mode": mcp_mode,
        "agent": str(config.get("opencode_agent") or "").strip(),
        "variant": str(config.get("opencode_variant") or "").strip(),
        "load_session": _boolean(config.get("opencode_load_session"), True),
        "reuse_process": _boolean(config.get("opencode_reuse_process"), True),
        "configured_model": str(config.get("model") or ""),
    }


def _model(slug):
    provider, sep, model = str(slug).strip().partition("/")
    if not sep or not provider or not model:
        raise ValueError("OpenCode model must be providerID/modelID")
    return {"providerID": provider, "modelID": model}


@dataclass
class _OpenCodeLiveSession:
    revision: str
    model: str
    runtime: Any = None
    session_id: str = ""
    internal_token: str = ""
    active: bool = False
    active_owner: Any = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    turn_lock: Any = field(default_factory=threading.Lock)


class _OpenCodeTurn:
    """Deduplicate snapshots/deltas and account only for this prompt's messages."""

    def __init__(self, session_id, prompt_id, callback=None, thinking_callback=None, block_callback=None):
        self.session_id = session_id
        self.prompt_id = prompt_id
        self.callback = callback
        self.thinking_callback = thinking_callback
        self.block_callback = block_callback
        self.messages = {}
        self.parts = {}
        self.emitted = {}
        self.started_tools = set()
        self.completed_tools = set()
        self.text = []
        self.thinking = []
        self.steps = {}
        self.error = None
        self.finish = ""
        self.completed = False

    def owns(self, info):
        return (info.get("sessionID") == self.session_id
                and info.get("role") == "assistant"
                and info.get("parentID") == self.prompt_id)

    def message(self, info):
        self.messages[info["id"]] = info
        if self.owns(info):
            if info.get("error"):
                self.error = info["error"]
            if info.get("time", {}).get("completed") and info.get("finish") not in {None, "tool-calls", "unknown"}:
                self.completed = True
                self.finish = info["finish"]

    def _text(self, part, text):
        previous = self.emitted.get(part["id"], "")
        if previous.startswith(text):
            return
        if not text.startswith(previous):
            raise LLMClientError("OpenCode changed an already streamed text prefix")
        delta = text[len(previous):]
        self.emitted[part["id"]] = text
        if part["type"] == "reasoning":
            self.thinking.append(delta)
            if self.thinking_callback:
                self.thinking_callback(delta)
        else:
            self.text.append(delta)
            if self.callback:
                self.callback(delta)

    def part(self, part):
        if part.get("sessionID") != self.session_id:
            return
        info = self.messages.get(part.get("messageID"), {})
        if not self.owns(info):
            return
        part = dict(part)
        if part.get("type") in {"text", "reasoning"}:
            previous = self.emitted.get(part["id"], "")
            if previous.startswith(str(part.get("text") or "")):
                part["text"] = previous
        self.parts[part["id"]] = part
        kind = part.get("type")
        if kind in {"text", "reasoning"}:
            self._text(part, str(part.get("text") or ""))
        elif kind == "step-finish":
            self.steps[part["id"]] = part.get("tokens") or {}
        elif kind == "tool":
            call_id = part.get("callID") or part["id"]
            ident = f"opencode:{self.prompt_id}:{call_id}"
            tool = str(part.get("tool") or "opencode_tool")
            if tool.startswith("pawflow_"):
                tool = "mcp__pawflow__" + tool[len("pawflow_"):]
            state = part.get("state") or {}
            if ident not in self.started_tools and state.get("status") != "pending":
                self.started_tools.add(ident)
                if self.block_callback:
                    self.block_callback("tool_use", {
                        "id": ident, "name": tool,
                        "arguments": state.get("input") or {},
                        "thinking": "", "tool_origin": PROVIDER,
                    })
            if state.get("status") in {"completed", "error"} and ident not in self.completed_tools:
                self.completed_tools.add(ident)
                if self.block_callback:
                    result = state.get("output") if state.get("status") == "completed" else state.get("error")
                    self.block_callback("tool_result", {
                        "tc_id": ident, "tool": tool, "result": result or "",
                        "is_error": state.get("status") == "error",
                    })

    def delta(self, props):
        if props.get("sessionID") != self.session_id or props.get("field") != "text":
            return
        part = self.parts.get(props.get("partID"))
        if part is None or part.get("type") not in {"text", "reasoning"}:
            return
        if props.get("messageID") != part.get("messageID"):
            return
        part["text"] = str(part.get("text") or "") + str(props.get("delta") or "")
        self._text(part, part["text"])

    def response(self, model, *, cancelled=False):
        # OpenCode's input counter excludes cache; PawFlow exposes caches
        # separately, matching its Anthropic accounting contract.
        usage = list(self.steps.values())
        if not usage:
            usage = [info["tokens"] for info in self.messages.values()
                     if self.owns(info) and "tokens" in info]
        tokens_in = sum(int(x.get("input") or 0) for x in usage)
        tokens_out = sum(int(x.get("output") or 0) + int(x.get("reasoning") or 0) for x in usage)
        cache_read = sum(int((x.get("cache") or {}).get("read") or 0) for x in usage)
        cache_write = sum(int((x.get("cache") or {}).get("write") or 0) for x in usage)
        return LLMResponse(
            content="".join(self.text), thinking="".join(self.thinking),
            model=model, tokens_in=tokens_in, tokens_out=tokens_out,
            cache_read_tokens=cache_read, cache_creation_tokens=cache_write,
            input_usage_native=bool(usage),
            finish_reason="cancelled" if cancelled else (self.finish or "end_turn"),
            raw={"session_id": self.session_id, "tool_results": len(self.completed_tools)})


class LLMOpenCodeMixin:
    """Native OpenCode session lifecycle exposed through the LLM client."""

    def _opencode_shared_state(self):
        with _STATE_LOCK:
            if getattr(self, "_opencode_live_sessions", None) is None:
                self._opencode_live_sessions = {}
                self._opencode_live_lock = threading.RLock()
        return self._opencode_live_sessions, self._opencode_live_lock

    @staticmethod
    def _opencode_revision(config, model):
        return scope_digest((config, model, OpenCodePool.image(), OpenCodePool.binary()))

    def _opencode_has_live_session(self, service_id, user_id, conversation_id, agent_name):
        sessions, lock = self._opencode_shared_state()
        with lock:
            live = sessions.get((user_id, conversation_id, agent_name, service_id))
            if not live:
                return False
            config = validate_opencode_config(self._config_ref)
            return bool(live.runtime and live.runtime.is_running and live.session_id
                        and live.revision == self._opencode_revision(config, live.model))

    @staticmethod
    def _opencode_store_key(scope, revision):
        return "opencode_session:v1:" + scope_digest((*scope, revision))

    def _opencode_get_stored_session(self, scope, revision):
        from core.conversation_store import ConversationStore
        return str(ConversationStore.instance().get_extra(
            scope[1], self._opencode_store_key(scope, revision)) or "")

    def _opencode_set_stored_session(self, scope, revision, session_id):
        from core.conversation_store import ConversationStore
        ConversationStore.instance().set_extra(
            scope[1], self._opencode_store_key(scope, revision), session_id)

    def _opencode_environment(self, live, scope, config):
        if live.internal_token:
            from core.internal_auth import revoke_token
            revoke_token(live.internal_token)
            live.internal_token = ""  # nosec B105 - revoke and clear runtime token.
        env = dict(config["env"])
        runtime_config = {
            "autoupdate": False, "share": "disabled",
            "compaction": {"auto": False, "prune": False},
            "permission": {"*": "ask", "question": "allow", "task": "deny"},
            "mcp": {},
        }
        if config["mcp_mode"] == "pawflow":
            from core.docker_utils import get_host_ip
            from core.internal_auth import mint_token
            from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin
            relay_url, relay_token = ClaudeCodeSessionMixin._get_tool_relay_info()
            if not relay_url:
                raise LLMClientError("OpenCode PawFlow MCP requires a connected toolRelay service")
            host_ip = get_host_ip()
            relay_url = str(relay_url).replace("localhost", host_ip).replace("127.0.0.1", host_ip)
            live.internal_token = mint_token()
            runtime_config["mcp"]["pawflow"] = {
                "type": "local", "enabled": True,
                "command": ["/usr/bin/python3", "/opt/pawflow/mcp_bridge.py"],
                "environment": {
                    "PAWFLOW_TOOL_RELAY_URL": relay_url,
                    "PAWFLOW_TOOL_RELAY_TOKEN": str(relay_token or ""),
                    "PAWFLOW_INTERNAL_TOKEN": live.internal_token,
                    "PAWFLOW_USER_ID": scope[0],
                    "PAWFLOW_CONVERSATION_ID": scope[1],
                    "PAWFLOW_AGENT_NAME": scope[2],
                },
            }
            # Authorization for PawFlow MCP calls remains inside its tool
            # registry. OpenCode must not add a second, unrelated permission.
            runtime_config["permission"]["pawflow_*"] = "allow"
        env.update({
            "OPENCODE_CONFIG_CONTENT": json.dumps(runtime_config),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
            "OPENCODE_DISABLE_AUTOUPDATE": "true",
            "OPENCODE_DISABLE_AUTOCOMPACT": "true",
            "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true",
            "OPENCODE_ENABLE_QUESTION_TOOL": "true",
        })
        return env

    def _opencode_new_runtime(self, scope, revision, env, ephemeral):
        return OpenCodeRuntime(scope, revision, env, ephemeral=ephemeral)

    def _opencode_close_entry(self, key, live):
        sessions, lock = self._opencode_shared_state()
        with lock:
            if sessions.get(key) is live:
                sessions.pop(key)
        live.cancel_event.set()
        token, live.internal_token = live.internal_token, ""
        if token:
            from core.internal_auth import revoke_token
            revoke_token(token)
        if live.runtime is not None:
            live.runtime.close()

    def _opencode_abort_active(self, *, force: bool):
        sessions, lock = self._opencode_shared_state()
        with lock:
            entries = [(key, live) for key, live in sessions.items()
                       if live.active and live.active_owner is self]
        for key, live in entries:
            live.cancel_event.set()
            if force:
                self._opencode_close_entry(key, live)
            elif live.runtime is not None and live.session_id:
                try:
                    live.runtime.request("POST", f"/session/{quote(live.session_id, safe='')}/abort", timeout=3)
                except Exception:
                    self._opencode_close_entry(key, live)

    def _opencode_close_all(self):
        sessions, lock = self._opencode_shared_state()
        with lock:
            entries = list(sessions.items())
        for key, live in entries:
            self._opencode_close_entry(key, live)

    def _opencode_permission(self, live, scope, props):
        from core.tool_approval import ToolApprovalGate
        from core.tool_authorization import gate_for_runtime
        permission = str(props.get("permission") or "")
        # Child tasks cannot inherit a new session without a PawFlow identity.
        if permission == "task":
            return "reject"
        tool = {"webfetch": "fetch", "websearch": "web_search",
                "codesearch": "search", "external_directory": "read"}.get(permission, permission)
        arguments = dict(props.get("metadata") or {})
        patterns = props.get("patterns") or []
        arguments["patterns"] = patterns
        if permission == "bash" and len(patterns) == 1:
            arguments.setdefault("command", patterns[0])
        if permission in {"read", "edit"} and len(patterns) == 1:
            arguments.setdefault("path", patterns[0])
        mode = ToolApprovalGate.get_mode(scope[1])
        if mode == "read_only" and not ToolApprovalGate.is_read_only_allowed(tool, arguments):
            return "reject"
        decision = gate_for_runtime(
            tool_name=tool, arguments=arguments, user_id=scope[0],
            conversation_id=scope[1], agent_name=scope[2], runtime=PROVIDER,
            permission_mode=mode, cancel_event=live.cancel_event)
        if live.cancel_event.is_set():
            raise OpenCodeCancelled()
        if decision is not None:
            return "once" if decision == "" else "reject"
        approval = ToolApprovalGate.check(
            tool, f"OpenCode: {permission}", scope[1], scope[0],
            arguments=arguments, agent_name=scope[2],
            cancel_event=live.cancel_event)
        return "once" if approval == "approved" else "reject"

    def _opencode_question(self, live, scope, props):
        from core.confirmation_store import UserInteractionStore
        store = UserInteractionStore.instance()
        fields = []
        questions = props.get("questions") or []
        if not questions:
            raise LLMClientError("OpenCode question contains no questions")
        for index, question in enumerate(questions):
            options = question.get("options") or []
            custom = question.get("custom", True)
            multiple = bool(question.get("multiple"))
            kind = "multi" if multiple and not custom else "choice"
            if custom or len(options) < 2:
                kind = "multiline" if multiple else "text"
            labels = [str(opt["label"]) for opt in options]
            label = str(question.get("question") or question.get("header") or "")
            if custom and options:
                label += "\n" + "\n".join(
                    f"{opt['label']}: {opt.get('description', '')}" for opt in options)
            if custom and multiple:
                label += "\nEnter one answer per line."
            fields.append({
                "name": f"q{index}", "type": kind, "label": label,
                "required": True, "options": labels, "no_default": True,
            })
        record = store.create_interaction(
            conversation_id=scope[1], user_id=scope[0],
            requester_kind="provider", requester=scope[2],
            message="Please answer OpenCode's questions.", title="OpenCode",
            kind="form", response_schema={"fields": fields},
            idempotency_key=f"opencode:{scope_digest(scope)}:{live.session_id}:{props['id']}")
        request_id = record["request_id"]
        try:
            while not live.cancel_event.is_set():
                record = store.get_interaction(request_id)
                if record is None or record["status"] in {"cancelled", "expired"}:
                    return None
                if record["status"] == "answered":
                    answer = record["answer"]
                    replies = []
                    for index, field_info in enumerate(fields):
                        value = answer[f"q{index}"]
                        if isinstance(value, list):
                            replies.append(value)
                        elif questions[index].get("multiple"):
                            replies.append([line for line in value.splitlines() if line])
                        else:
                            replies.append([value])
                    return replies
                live.cancel_event.wait(0.2)
            raise OpenCodeCancelled()
        finally:
            store.cancel_interaction(request_id, cancelled_by="opencode")

    def _opencode_prompt(self, messages, cold):
        current = next((m for m in reversed(messages) if getattr(m, "role", "") == "user"), None)
        if current is None:
            raise ValueError("OpenCode requires a user message")
        if cold:
            system, text = self._serialize_messages_for_cli(list(messages), None)
        else:
            system = ""
            content = getattr(current, "content", "")
            text = content if isinstance(content, str) else "\n".join(
                str(p.get("text") or "") for p in content if p.get("type") == "text")
        parts = [{"type": "text", "text": text}]
        content = getattr(current, "content", "")
        if isinstance(content, list):
            for part in content:
                kind = part.get("type")
                if kind == "text":
                    continue
                if kind == "document" and part.get("text"):
                    parts.append({"type": "text", "text": str(part["text"])})
                    continue
                source = part.get("source") or {}
                if source.get("type") == "base64" and source.get("data") and source.get("media_type"):
                    parts.append({"type": "file", "mime": source["media_type"],
                                  "url": f"data:{source['media_type']};base64,{source['data']}"})
                    continue
                raise ValueError("OpenCode attachments require inline base64 source or document text")
        payload = {"parts": parts}
        if system:
            payload["system"] = system
        return payload

    def _opencode_reconcile(self, runtime, session_path, turn):
        # A matching completed assistant message is required before accepting
        # idle. Snapshots also recover out-of-order/missed part metadata.
        messages = runtime.request("GET", session_path + "/message")
        if not isinstance(messages, list):
            raise LLMClientError("OpenCode returned invalid message history")
        for item in messages:
            info = item.get("info") or {}
            if not info.get("id"):
                continue
            turn.message(info)
            if turn.owns(info):
                for part in item.get("parts") or []:
                    turn.part(part)

    def _stream_opencode(
        self, messages: Sequence[Any], model: str, temperature: float,
        max_tokens: int, tools: Sequence[Any] | None, callback: Any = None,
        thinking_budget: int = 0, thinking_callback: Any = None,
        turn_callback: Any = None, block_callback: Any = None, *,
        call_user_id: str | None = None, call_conversation_id: str | None = None,
        call_agent_name: str | None = None, call_event_cid: str | None = None,
        call_ephemeral_stream: bool | None = None,
    ) -> Any:
        del temperature, max_tokens, tools, thinking_budget, turn_callback
        config = validate_opencode_config(self._config_ref)
        selected_model = _model(model)
        service_id = str(getattr(self, "_agent_service", "") or self._config_ref.get("_service_id") or "")
        scope = (str(call_user_id or ""), str(call_conversation_id or ""),
                 str(call_agent_name or ""), service_id)
        if any(not value for value in scope):
            raise ValueError("OpenCode requires user, conversation, agent and service identity")
        ephemeral = bool(call_ephemeral_stream)
        revision = self._opencode_revision(config, model)
        key = scope if not ephemeral else (*scope[:2], scope[2] + ":ephemeral:" + uuid.uuid4().hex, scope[3])
        sessions, lock = self._opencode_shared_state()
        with lock:
            live = sessions.get(key)
            replaced = live if live and live.revision != revision else None
            if live is None or replaced is not None:
                live = _OpenCodeLiveSession(revision=revision, model=model)
                sessions[key] = live
        if replaced is not None:
            self._opencode_close_entry(key, replaced)
        turn = _OpenCodeTurn("", "msg_" + uuid.uuid4().hex, callback, thinking_callback, block_callback)
        clean = False
        submitted = False
        with live.turn_lock:
            # An entry removed by force stop is never resurrected by a waiter.
            if live.cancel_event.is_set():
                return turn.response(model, cancelled=True)
            live.active = True
            live.active_owner = self
            try:
                runtime = live.runtime
                created = False
                if runtime is None or not runtime.is_running:
                    if runtime is not None:
                        runtime.close()
                    env = self._opencode_environment(live, scope, config)
                    runtime = self._opencode_new_runtime(key, revision, env, ephemeral)
                    live.runtime = runtime
                    runtime.start()
                    stored = self._opencode_get_stored_session(scope, revision) if config["load_session"] and not ephemeral else ""
                    live.session_id = ""
                    if stored:
                        try:
                            response = runtime.request("GET", "/session/" + quote(stored, safe=""))
                            if response.get("id") != stored:
                                raise LLMClientError("OpenCode returned a different session")
                            live.session_id = stored
                        except OpenCodeHTTPError as exc:
                            if exc.status != 404:
                                raise
                            self._opencode_set_stored_session(scope, revision, "")
                    if not live.session_id:
                        if not ephemeral and getattr(self, "_pawflow_context_is_delta", False):
                            raise ColdStartRequired("OpenCode session is unavailable; rebuild full PawFlow context")
                        result = runtime.request("POST", "/session", {
                            "title": f"PawFlow {scope[2]}",
                            "permission": [
                                {"permission": "*", "pattern": "*", "action": "ask"},
                                {"permission": "question", "pattern": "*", "action": "allow"},
                                {"permission": "task", "pattern": "*", "action": "deny"},
                                *([{"permission": "pawflow_*", "pattern": "*", "action": "allow"}]
                                  if config["mcp_mode"] == "pawflow" else []),
                            ],
                        })
                        live.session_id = str(result.get("id") or "")
                        if not live.session_id:
                            raise LLMClientError("OpenCode session.create returned no session id")
                        created = True
                session_path = "/session/" + quote(live.session_id, safe="")
                turn.session_id = live.session_id
                payload = self._opencode_prompt(messages, cold=created)
                payload.update(messageID=turn.prompt_id, model=selected_model)
                if config["agent"]:
                    payload["agent"] = config["agent"]
                if config["variant"]:
                    payload["variant"] = config["variant"]
                runtime.drain_events()
                if live.cancel_event.is_set():
                    raise OpenCodeCancelled()
                submitted = True
                runtime.request("POST", session_path + "/prompt_async", payload)
                seen_requests = set()
                while True:
                    if live.cancel_event.is_set():
                        raise OpenCodeCancelled()
                    event = runtime.next_event()
                    if event is None:
                        continue
                    kind = event.get("type")
                    props = event.get("properties") or {}
                    if kind == "message.updated":
                        info = props.get("info") or {}
                        if info.get("sessionID") == live.session_id:
                            turn.message(info)
                    elif kind == "message.part.updated":
                        part = props.get("part") or {}
                        if part.get("sessionID") != live.session_id:
                            continue
                        if part.get("messageID") not in turn.messages:
                            item = runtime.request("GET", session_path + "/message/" + quote(str(part["messageID"]), safe=""))
                            turn.message(item["info"])
                        turn.part(part)
                    elif kind == "message.part.delta":
                        turn.delta(props)
                    elif props.get("sessionID") == live.session_id:
                        if kind in {"permission.asked", "question.asked"}:
                            request_key = (kind, props["id"])
                            if request_key in seen_requests:
                                continue
                            seen_requests.add(request_key)
                            request_path = "/" + kind.split(".")[0] + "/" + quote(props["id"], safe="")
                            if kind == "permission.asked":
                                reply = self._opencode_permission(live, scope, props)
                                runtime.request("POST", request_path + "/reply", {"reply": reply})
                            else:
                                answers = self._opencode_question(live, scope, props)
                                if answers is None:
                                    runtime.request("POST", request_path + "/reject")
                                else:
                                    runtime.request("POST", request_path + "/reply", {"answers": answers})
                        elif kind == "session.error":
                            turn.error = props.get("error") or {"name": "UnknownError"}
                        elif kind == "session.status" and props.get("status", {}).get("type") == "idle":
                            self._opencode_reconcile(runtime, session_path, turn)
                            if turn.completed or turn.error:
                                break
                    if turn.error:
                        break
                if turn.error:
                    if turn.error.get("name") == "MessageAbortedError":
                        raise OpenCodeCancelled()
                    raise LLMClientError("OpenCode session failed: " + str(turn.error.get("name") or "UnknownError"))
                if not ephemeral:
                    self._opencode_set_stored_session(scope, revision, live.session_id)
                result = turn.response(model)
                if result.input_usage_native and hasattr(self, "_record_observed_context"):
                    self._record_observed_context(
                        scope[1], scope[2], result.tokens_in + result.cache_read_tokens
                        + result.cache_creation_tokens, mode="session")
                    self.publish_observed_context_usage(
                        scope[1], scope[2], user_id=scope[0],
                        event_cid=call_event_cid or scope[1], source="opencode_usage")
                clean = True
                return result
            except OpenCodeCancelled:
                return turn.response(model, cancelled=True)
            except (ColdStartRequired, LLMClientError):
                if live.cancel_event.is_set():
                    return turn.response(model, cancelled=True)
                raise
            except Exception as exc:
                if live.cancel_event.is_set():
                    return turn.response(model, cancelled=True)
                raise LLMClientError("OpenCode provider failed: " + type(exc).__name__) from exc
            finally:
                live.active = False
                live.active_owner = None
                if not clean and submitted and not ephemeral:
                    self._opencode_set_stored_session(scope, revision, "")
                if not clean or ephemeral or not config["reuse_process"]:
                    self._opencode_close_entry(key, live)


__all__ = ["PROVIDER", "LLMOpenCodeMixin", "validate_opencode_config"]
