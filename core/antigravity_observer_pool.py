"""Antigravity CLI interactive sessions.

This pool starts the real ``agy`` CLI in tmux with Gemini OAuth/MCP config and
a transparent observer proxy for ``daily-cloudcode-pa.googleapis.com``. The
same tmux/proxy foundation is used by both the diagnostics observer action and
the ``antigravity-interactive`` LLM provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING
import hashlib
import json
import logging
import os
import shlex
import socket
import subprocess
import threading
import time
import uuid

import core.paths as _paths
from core.cc_interactive_certs import ca_private_key_is_host_only, generate_leaf
from core.docker_utils import docker_cmd, get_server_id, to_host_path, translate_path

if TYPE_CHECKING:
    from core.llm_client import LLMClient


logger = logging.getLogger(__name__)

ANTIGRAVITY_BACKEND_HOST = "daily-cloudcode-pa.googleapis.com"


@dataclass
class AntigravityObserverSession:
    key: tuple[str, str, str, str]
    name: str
    workdir: str
    container_workdir: str
    log_path: str
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    initial_context_loaded: bool = False
    last_error: str = ""
    emitted_tool_use_ids: set = field(default_factory=set)
    emitted_tool_result_ids: set = field(default_factory=set)
    manual_ingest_enabled: bool = False
    manual_ingest_suspended: bool = False
    manual_ingest_offset: int = 0
    manual_ingest_stop: threading.Event = field(default_factory=threading.Event)
    manual_ingest_thread: Optional[threading.Thread] = None
    manual_ingest_seen_requests: set = field(default_factory=set)
    injected_prompt_hashes: dict = field(default_factory=dict)

    @property
    def agent_name(self) -> str:
        return self.key[2]

    @property
    def service_id(self) -> str:
        return self.key[3]


class AntigravityObserverPool:
    """Persistent observer containers keyed by user/conversation/agent/service."""

    _instance: Optional["AntigravityObserverPool"] = None
    _instance_lock = threading.Lock()
    _TMUX_TARGET = "pawflow-agy:0.0"
    _LITERAL_CHUNK_BYTES = 512
    _LITERAL_CHUNK_DELAY_SECONDS = 0.2
    _NO_DONE_IDLE_DRAIN_SECONDS = 8.0

    @classmethod
    def instance(cls) -> "AntigravityObserverPool":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[tuple[str, str, str, str], AntigravityObserverSession] = {}

    @staticmethod
    def _safe(value: str) -> str:
        return (value or "").replace(":", "_").replace("/", "_").replace("\\", "_")

    @staticmethod
    def _base_dir() -> Path:
        return _paths.RUNTIME_DIR / "sessions" / "antigravity-observer"

    @classmethod
    def _workdir(cls, user_id: str, conversation_id: str, agent_name: str) -> str:
        if not user_id:
            raise ValueError("user_id is required for Antigravity observer")
        if not conversation_id:
            raise ValueError("conversation_id is required for Antigravity observer")
        if not agent_name:
            raise ValueError("agent_name is required for Antigravity observer")
        path = cls._base_dir() / cls._safe(user_id) / cls._safe(conversation_id) / agent_name
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    @staticmethod
    def _physical_container_workdir(user_id: str, conversation_id: str, agent_name: str) -> str:
        return "/cc_sessions/{}/{}/{}".format(
            AntigravityObserverPool._safe(user_id),
            AntigravityObserverPool._safe(conversation_id),
            agent_name,
        )

    @staticmethod
    def _container_workdir(user_id: str, conversation_id: str, agent_name: str) -> str:
        return "/cc_sessions/{}/{}".format(
            AntigravityObserverPool._safe(conversation_id),
            agent_name,
        )

    def start(self, *, user_id: str, conversation_id: str, agent_name: str,
              service_id: str = "", model: str = "") -> AntigravityObserverSession:
        key = (user_id, conversation_id, agent_name, service_id or "")
        stale = None
        with self._lock:
            existing = self._sessions.get(key)
            if existing and self._is_usable(existing):
                existing.last_used = time.time()
                self._ensure_manual_ingest(existing)
                return existing
            if existing:
                self._sessions.pop(key, None)
                stale = existing
        if stale:
            logger.info("Restarting stale Antigravity observer session %s", stale.name)
            self.kill(stale)
        state = self._start_new(user_id, conversation_id, agent_name, service_id or "", model or "")
        self._ensure_manual_ingest(state)
        with self._lock:
            self._sessions[key] = state
        return state

    def ensure_started(self, client, model: str, user_id: str,
                       conversation_id: str, agent_name: str) -> AntigravityObserverSession:
        service_id = getattr(client, "_agent_service", "") or ""
        key = (user_id, conversation_id, agent_name, service_id)
        stale = None
        with self._lock:
            existing = self._sessions.get(key)
            if existing and self._is_usable(existing):
                existing.last_used = time.time()
                return existing
            if existing:
                self._sessions.pop(key, None)
                stale = existing
        if stale:
            logger.info("Restarting stale Antigravity interactive session %s", stale.name)
            self.kill(stale)
        state = self._start_new(
            user_id, conversation_id, agent_name, service_id, model or "",
            client=client)
        with self._lock:
            self._sessions[key] = state
        return state

    def find_session(self, user_id: str, conversation_id: str, agent_name: str,
                     service_id: str = "") -> Optional[AntigravityObserverSession]:
        key = (user_id, conversation_id, agent_name, service_id or "")
        with self._lock:
            state = self._sessions.get(key)
            if state and self._is_alive(state.name):
                state.last_used = time.time()
                return state
            if state:
                self._sessions.pop(key, None)
        return None

    def list_sessions(self, user_id: str, conversation_id: str, service_id: str = "") -> list[dict]:
        now = time.time()
        out = []
        with self._lock:
            for key, state in list(self._sessions.items()):
                uid, conv, agent, svc = key
                if uid != user_id or conv != conversation_id:
                    continue
                if service_id and svc != service_id:
                    continue
                alive = self._is_alive(state.name)
                if not alive:
                    self._sessions.pop(key, None)
                    continue
                out.append({
                    "user_id": uid,
                    "conv_id": conv,
                    "agent_name": agent,
                    "service_id": svc,
                    "container_name": state.name,
                    "log_path": state.log_path,
                    "idle_seconds": max(0.0, now - state.last_used),
                    "lived_seconds": max(0.0, now - state.created_at),
                    "provider": "antigravity-observer",
                })
        return out

    def touch(self, state: AntigravityObserverSession) -> None:
        state.last_used = time.time()

    def suspend_manual_ingest(self, state: AntigravityObserverSession) -> None:
        state.manual_ingest_suspended = True

    def resume_manual_ingest(self, state: AntigravityObserverSession) -> None:
        state.manual_ingest_suspended = False

    def _ensure_manual_ingest(self, state: AntigravityObserverSession) -> None:
        state.manual_ingest_enabled = True
        thread = state.manual_ingest_thread
        if thread and thread.is_alive():
            return
        if state.manual_ingest_stop.is_set():
            state.manual_ingest_stop = threading.Event()
        try:
            state.manual_ingest_offset = os.path.getsize(state.log_path)
        except OSError:
            state.manual_ingest_offset = 0
        thread = threading.Thread(
            target=self._manual_ingest_loop,
            args=(state,),
            daemon=True,
            name=f"ag-manual-ingest-{state.key[1][:8]}-{state.agent_name[:16]}",
        )
        state.manual_ingest_thread = thread
        thread.start()

    def _manual_ingest_loop(self, state: AntigravityObserverSession) -> None:
        try:
            self._run_manual_ingest_loop(state)
        except Exception:
            logger.exception(
                "Antigravity manual ingest watcher crashed for %s/%s",
                state.key[1][:8], state.agent_name)

    def _run_manual_ingest_loop(self, state: AntigravityObserverSession) -> None:
        from core.llm_providers.antigravity_interactive import (
            _NO_DONE_IDLE_DRAIN_SECONDS,
            _AntigravityLogTail,
            _POST_DONE_IDLE_DRAIN_SECONDS,
        )

        tail = _AntigravityLogTail(state.log_path, state.manual_ingest_offset)
        turn = self._new_manual_turn()
        while not state.manual_ingest_stop.is_set():
            event = tail.wait_event(timeout=0.25)
            state.manual_ingest_offset = tail.offset
            if not event:
                if turn["done"] and time.time() - turn["done_at"] >= _POST_DONE_IDLE_DRAIN_SECONDS:
                    self._flush_manual_turn(state, turn)
                    turn = self._new_manual_turn()
                elif self._manual_turn_idle_expired(turn, _NO_DONE_IDLE_DRAIN_SECONDS):
                    self._flush_manual_turn(state, turn)
                    turn = self._new_manual_turn()
                continue
            self.touch(state)
            if state.manual_ingest_suspended:
                turn = self._new_manual_turn()
                continue
            etype = event.get("type", "")
            if etype == "ag_user_prompt":
                if turn.get("prompt_seen") and not turn.get("done"):
                    continue
                request_id = str(event.get("request_id") or "")
                if request_id and request_id in state.manual_ingest_seen_requests:
                    continue
                if request_id:
                    state.manual_ingest_seen_requests.add(request_id)
                prompt_text = str(event.get("text") or "")
                if (self._consume_injected_prompt(state, prompt_text)
                        or self._is_provider_context_prompt(prompt_text)):
                    turn = self._new_manual_turn()
                    continue
                if turn.get("text_parts") or turn.get("thinking_parts") or turn.get("tool_calls"):
                    self._flush_manual_turn(state, turn)
                    turn = self._new_manual_turn()
                self._persist_manual_user_prompt(state, event)
                turn["prompt_seen"] = True
                continue
            if etype not in {"ag_text_delta", "ag_model_delta"}:
                continue
            self._accumulate_manual_event(state, turn, event)
            if turn["done"] and time.time() - turn["done_at"] >= _POST_DONE_IDLE_DRAIN_SECONDS:
                self._flush_manual_turn(state, turn)
                turn = self._new_manual_turn()

    @staticmethod
    def _new_manual_turn() -> dict:
        return {
            "text_parts": [],
            "thinking_parts": [],
            "tool_calls": [],
            "tool_by_id": {},
            "usage": {},
            "done": False,
            "done_at": 0.0,
            "last_event_at": 0.0,
            "awaiting_tool_followup": False,
            "prompt_seen": False,
        }

    @staticmethod
    def _manual_turn_idle_expired(turn: dict, idle_seconds: float) -> bool:
        last_event_at = float(turn.get("last_event_at") or 0.0)
        if not last_event_at or turn.get("done"):
            return False
        if not (turn.get("text_parts") or turn.get("thinking_parts") or turn.get("tool_calls")):
            return False
        return time.time() - last_event_at >= max(0.0, float(idle_seconds or 0.0))

    def _accumulate_manual_event(self, state: AntigravityObserverSession,
                                 turn: dict, event: dict) -> None:
        turn["last_event_at"] = time.time()
        if event.get("usage") and isinstance(event.get("usage"), dict):
            turn["usage"].update(event["usage"])
        tool_calls = event.get("tool_calls") or []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            tc_id = str(tc.get("id") or tc.get("tool_call_id") or f"ag_{uuid.uuid4().hex[:12]}")
            if tc_id in state.emitted_tool_use_ids:
                continue
            state.emitted_tool_use_ids.add(tc_id)
            args = tc.get("arguments") or tc.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            entry = {"id": tc_id, "name": str(tc.get("name") or tc.get("tool") or ""), "arguments": args}
            turn["tool_by_id"][tc_id] = entry
            turn["tool_calls"].append(dict(entry))
            turn["awaiting_tool_followup"] = True
        for tr in event.get("tool_results") or []:
            if not isinstance(tr, dict):
                continue
            raw_tc_id = str(tr.get("tool_use_id") or tr.get("tool_call_id") or tr.get("id") or "")
            name = str(tr.get("name") or tr.get("tool") or "")
            tc_id = raw_tc_id
            if not tc_id or tc_id not in turn["tool_by_id"]:
                matches = [
                    tc for tc in turn["tool_calls"]
                    if not tc.get("result") and (not name or tc.get("name") == name)
                ]
                if len(matches) == 1:
                    tc_id = str(matches[0].get("id") or "")
            if not tc_id:
                continue
            dedupe_id = raw_tc_id if raw_tc_id and raw_tc_id in turn["tool_by_id"] else tc_id
            if dedupe_id in state.emitted_tool_result_ids:
                continue
            state.emitted_tool_result_ids.add(dedupe_id)
            state.emitted_tool_result_ids.add(tc_id)
            result = tr.get("content") or tr.get("result") or tr.get("response") or "(no output)"
            for tc in turn["tool_calls"]:
                if tc.get("id") == tc_id:
                    tc["result"] = result
                    break
        thinking = event.get("thinking", "") or "".join(event.get("thinking_texts") or [])
        if thinking:
            turn["thinking_parts"].append(thinking)
        text = event.get("text", "") or "".join(event.get("texts") or [])
        if text:
            turn["text_parts"].append(text)
            if not tool_calls and turn.get("awaiting_tool_followup"):
                turn["awaiting_tool_followup"] = False
        if event.get("done") or (event.get("finish_reason") and not turn.get("awaiting_tool_followup")):
            turn["done"] = True
            turn["done_at"] = time.time()

    def _manual_agent_source(self, state: AntigravityObserverSession, usage: dict | None = None) -> dict:
        usage = usage or {}
        source = {
            "type": "agent",
            "name": state.agent_name,
            "llm_service": state.service_id,
            "provider": "antigravity-interactive",
            "model": "",
            "containerized": True,
            "observer_manual": True,
        }
        if usage.get("input_tokens") or usage.get("output_tokens"):
            source["tokens_in"] = int(usage.get("input_tokens", 0) or 0)
            source["tokens_out"] = int(usage.get("output_tokens", 0) or 0)
        return source

    def _persist_manual_user_prompt(self, state: AntigravityObserverSession, event: dict) -> None:
        text = str(event.get("text") or "").strip()
        if not text:
            return
        if self._is_provider_context_prompt(text):
            logger.info(
                "[antigravity-observer] ignored provider context prompt in manual ingest container=%s",
                state.name)
            return
        from core.llm_client import LLMMessage
        from core.conversation_writer import ConversationWriter

        cid = state.key[1]
        user_id = state.key[0]
        source = {
            "type": "user",
            "name": user_id,
            "target_agent": state.agent_name,
            "channel": "antigravity-observer",
        }
        msg = LLMMessage(role="user", content=text, source=source, conversation_id=cid)
        store_msg = {
            "role": "user",
            "content": text,
            "source": source,
            "msg_id": msg.msg_id,
            "ts": msg.timestamp,
            "seq": msg.seq or None,
        }
        ConversationWriter.for_conversation(cid).enqueue_message(
            store_msg,
            agent_name=state.agent_name,
            user_id=user_id,
            sse_events=[{"type": "new_message", "data": {
                "role": "user",
                "content": text,
                "msg_id": msg.msg_id,
                "ts": msg.timestamp,
                "source": source,
            }}],
        )

    def _flush_manual_turn(self, state: AntigravityObserverSession, turn: dict) -> None:
        text = "".join(turn.get("text_parts") or []).strip()
        thinking = "".join(turn.get("thinking_parts") or [])
        tool_calls = turn.get("tool_calls") or []
        if not text and not thinking and not tool_calls:
            return
        from core.llm_client import LLMMessage, LLMToolCall, unwrap_mcp_tool
        from core.conversation_writer import ConversationWriter
        from tasks.ai.agent_core import AgentCoreMixin

        cid = state.key[1]
        user_id = state.key[0]
        writer = ConversationWriter.for_conversation(cid)
        source = self._manual_agent_source(state, turn.get("usage") or {})
        if text or (thinking and not tool_calls):
            msg = LLMMessage(
                role="assistant",
                content=text,
                thinking=thinking if not tool_calls else "",
                source=source,
                conversation_id=cid,
            )
            sse = []
            if msg.thinking:
                sse.append({"type": "thinking_content", "data": {
                    "text": msg.thinking,
                    "msg_id": msg.msg_id,
                    "ts": msg.timestamp,
                    "agent_name": state.agent_name,
                    "source": source,
                }})
            if text:
                sse.append({"type": "new_message", "data": {
                    "role": "assistant",
                    "content": text,
                    "msg_id": msg.msg_id,
                    "ts": msg.timestamp,
                    "source": source,
                }})
            store_msg = {
                "role": "assistant",
                "content": msg.content,
                "source": source,
                "msg_id": msg.msg_id,
                "ts": msg.timestamp,
                "seq": msg.seq or None,
            }
            if msg.thinking:
                store_msg["thinking"] = msg.thinking
            writer.enqueue_message(store_msg, agent_name=state.agent_name, user_id=user_id, sse_events=sse or None)
        if tool_calls:
            tc_objects = []
            for tc in tool_calls:
                name, args = unwrap_mcp_tool(tc.get("name", ""), tc.get("arguments", {}) or {})
                tc_objects.append(LLMToolCall(id=tc.get("id", ""), name=name, arguments=args if isinstance(args, dict) else {}))
            if tc_objects:
                tc_msg = LLMMessage(
                    role="assistant",
                    content="",
                    tool_calls=tc_objects,
                    thinking=thinking,
                    source=source,
                    conversation_id=cid,
                )
                tc_sse = []
                if thinking:
                    tc_sse.append({"type": "thinking_content", "data": {
                        "text": thinking,
                        "msg_id": tc_msg.msg_id,
                        "ts": tc_msg.timestamp,
                        "agent_name": state.agent_name,
                        "source": source,
                    }})
                for tc_obj in tc_objects:
                    tc_sse.append({"type": "tool_call", "data": {
                        "tool": tc_obj.name,
                        "arguments": tc_obj.arguments,
                        "tc_id": tc_obj.id,
                        "agent_name": state.agent_name,
                        "llm_service": state.service_id,
                        "msg_id": tc_msg.msg_id,
                        "ts": tc_msg.timestamp,
                        "source": source,
                    }})
                writer.enqueue_message({
                    "role": "assistant",
                    "content": "",
                    "source": source,
                    "msg_id": tc_msg.msg_id,
                    "ts": tc_msg.timestamp,
                    "seq": tc_msg.seq or None,
                    "thinking": thinking or "",
                    "tool_calls": [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in tc_objects],
                }, agent_name=state.agent_name, user_id=user_id, sse_events=tc_sse)
                for idx, tc_obj in enumerate(tc_objects):
                    raw = tool_calls[idx] if idx < len(tool_calls) else {}
                    has_result = any(raw.get(k) for k in ("result", "content", "response"))
                    if not has_result:
                        continue
                    result = raw.get("result") or raw.get("content") or raw.get("response")
                    tr_msg = LLMMessage(
                        role="tool",
                        content=AgentCoreMixin._wrap_tool_output(tc_obj.name, result),
                        tool_call_id=tc_obj.id,
                        conversation_id=cid,
                    )
                    tr_msg._tool_name = tc_obj.name
                    writer.enqueue_message({
                        "role": "tool",
                        "content": tr_msg.content,
                        "msg_id": tr_msg.msg_id,
                        "tool_call_id": tc_obj.id,
                        "ts": tr_msg.timestamp,
                        "seq": tr_msg.seq or None,
                    }, agent_name=state.agent_name, user_id=user_id, sse_events=[{"type": "tool_result", "data": {
                        "tool": tc_obj.name,
                        "result": str(result)[:2000],
                        "tc_id": tc_obj.id,
                        "msg_id": tr_msg.msg_id,
                        "ts": tr_msg.timestamp,
                        "agent_name": state.agent_name,
                        "llm_service": state.service_id,
                    }}])

    def send_text(self, state: AntigravityObserverSession, text: str) -> bool:
        state.last_error = ""
        if not self._is_alive(state.name):
            state.last_error = f"Container {state.name} is not running"
            return False
        self._remember_injected_prompt(state, text)
        logger.info(
            "[antigravity-interactive] tmux submit start container=%s bytes=%d",
            state.name, len((text or "").encode("utf-8")))
        if not self._send_multiline_text(state, text):
            return False
        # Antigravity renders tmux-injected text with a short delay. Submit only
        # after a bounded drain window so Enter does not race ahead of input.
        time.sleep(min(1.5, max(0.15, len(text or "") / 50000.0)))
        ok = self.send_keys(state, ["Enter"])
        if ok:
            logger.info(
                "[antigravity-interactive] tmux submit sent container=%s bytes=%d",
                state.name, len((text or "").encode("utf-8")))
        return ok

    def send_interrupt(self, state: AntigravityObserverSession, text: str) -> bool:
        state.last_error = ""
        if not self._is_alive(state.name):
            state.last_error = f"Container {state.name} is not running"
            return False
        self._remember_injected_prompt(state, text)
        return (self._send_multiline_text(state, text)
                and self.send_keys(state, ["Escape"])
                and self.send_keys(state, ["Enter"]))

    @staticmethod
    def _prompt_hash(text: str) -> str:
        return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()

    def _remember_injected_prompt(self, state: AntigravityObserverSession, text: str) -> None:
        digest = self._prompt_hash(text)
        if digest:
            state.injected_prompt_hashes[digest] = time.time()

    def _consume_injected_prompt(self, state: AntigravityObserverSession, text: str) -> bool:
        now = time.time()
        state.injected_prompt_hashes = {
            digest: ts for digest, ts in state.injected_prompt_hashes.items()
            if now - float(ts or 0) < 300.0
        }
        digest = self._prompt_hash(text)
        if digest and digest in state.injected_prompt_hashes:
            state.injected_prompt_hashes.pop(digest, None)
            logger.info(
                "[antigravity-interactive] ignored PawFlow-injected prompt in manual ingest container=%s",
                state.name)
            return True
        return False

    @staticmethod
    def _is_provider_context_prompt(text: str) -> bool:
        text = (text or "").strip()
        if not text:
            return False
        markers = (
            "<identity>\nYou are Antigravity",
            "You are Antigravity, a powerful agentic AI coding assistant",
            "PawFlow cold-session bootstrap.",
            ".pawflow_ag/initial_context.md",
            "Use your local filesystem/file-read capability",
            "Latest turn to answer now:",
            "<web_application_development>",
            "<communication_style>",
        )
        return any(marker in text for marker in markers)

    def _send_multiline_text(self, state: AntigravityObserverSession, text: str) -> bool:
        """Type text into agy, using Shift+Enter for embedded newlines."""
        payload = text or ""
        if not payload:
            return True
        lines = payload.split("\n")
        for idx, line in enumerate(lines):
            if line and not self._send_literal_text(state, line):
                return False
            if idx < len(lines) - 1:
                if not self.send_keys(state, ["S-Enter"]):
                    return False
                time.sleep(self._LITERAL_CHUNK_DELAY_SECONDS)
        return True

    def _send_literal_text(self, state: AntigravityObserverSession, text: str) -> bool:
        payload = text or ""
        if not payload:
            return True
        chunk = []
        size = 0
        for ch in payload:
            encoded_len = len(ch.encode("utf-8"))
            if chunk and size + encoded_len > self._LITERAL_CHUNK_BYTES:
                if not self._send_literal_chunk(state, "".join(chunk)):
                    return False
                time.sleep(self._LITERAL_CHUNK_DELAY_SECONDS)
                chunk = []
                size = 0
            chunk.append(ch)
            size += encoded_len
        if chunk and not self._send_literal_chunk(state, "".join(chunk)):
            return False
        return True

    def _send_literal_chunk(self, state: AntigravityObserverSession, chunk: str) -> bool:
        # Do not pass prompt text as a command-line argument: on Windows/WSL
        # relay paths it can be re-wrapped by a shell, and markup like
        # </message> is then parsed as redirection. Buffer stdin preserves the
        # text literally.
        return self._load_buffer(state, chunk) and self._paste_buffer(state)

    def force_stop(self, state: AntigravityObserverSession) -> bool:
        return self.send_keys(state, ["Escape", "Escape"])

    def send_keys(self, state: AntigravityObserverSession, keys: list[str]) -> bool:
        state.last_error = ""
        if not self._is_alive(state.name):
            state.last_error = f"Container {state.name} is not running"
            return False
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", "1000:1000", state.name,
                            "tmux", "send-keys", "-t", self._TMUX_TARGET, *keys],
            capture_output=True, timeout=10)
        if r.returncode != 0:
            state.last_error = self._command_error("tmux send-keys", r)
            return False
        return True

    def _load_buffer(self, state: AntigravityObserverSession, text: str) -> bool:
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "-i", "--user", "1000:1000", state.name,
                            "tmux", "load-buffer", "-"],
            input=(text or "").encode("utf-8"), capture_output=True, timeout=15)
        if r.returncode != 0:
            state.last_error = self._command_error("tmux load-buffer", r)
            return False
        return True

    def _paste_buffer(self, state: AntigravityObserverSession) -> bool:
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", "1000:1000", state.name,
                            "tmux", "paste-buffer", "-t", self._TMUX_TARGET],
            capture_output=True, timeout=10)
        if r.returncode != 0:
            state.last_error = self._command_error("tmux paste-buffer", r)
            return False
        return True

    @staticmethod
    def _command_error(label: str, result) -> str:
        stderr = getattr(result, "stderr", b"") or b""
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        detail = (stderr or stdout or "").strip()
        if detail:
            return f"{label} failed: {detail[:500]}"
        return f"{label} failed with exit code {getattr(result, 'returncode', '?')}"

    def _start_new(self, user_id: str, conversation_id: str, agent_name: str,
                   service_id: str, model: str, client=None) -> AntigravityObserverSession:
        workdir = self._workdir(user_id, conversation_id, agent_name)
        if client is None:
            from core.llm_client import LLMClient
            setup_client = LLMClient(provider="gemini", config={"provider": "gemini"})
        else:
            setup_client = client
        original_agent_service = getattr(setup_client, "_agent_service", "") or ""
        setup_client._agent_service = service_id or original_agent_service
        setup_client._user_id = user_id
        setup_client._agent_name = agent_name
        try:
            setup_client._gemini_setup_credentials(workdir)
            self._write_antigravity_config(setup_client, workdir, user_id, conversation_id, agent_name, model)
        finally:
            if client is not None:
                setup_client._agent_service = original_agent_service

        cert_dir = Path(workdir) / ".pawflow_ag" / "certs"
        certs = generate_leaf(cert_dir, common_name=ANTIGRAVITY_BACKEND_HOST)
        log_dir = Path(workdir) / ".pawflow_ag" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_id = uuid.uuid4().hex[:12]
        log_path = str(log_dir / f"observer-{log_id}.jsonl")
        stderr_path = str(log_dir / f"proxy-{log_id}.stderr.log")

        name = self._spawn_container(user_id=user_id, conversation_id=conversation_id, agent_name=agent_name)
        physical_workdir = self._physical_container_workdir(user_id, conversation_id, agent_name)
        container_workdir = self._container_workdir(user_id, conversation_id, agent_name)
        try:
            self._install_ca(name, physical_workdir)
            self._start_proxy(name=name, container_workdir=physical_workdir,
                              log_path=log_path, stderr_path=stderr_path, certs=certs)
            self._start_agy_tmux(name=name, container_workdir=physical_workdir)
        except Exception:
            subprocess.run(docker_cmd() + ["rm", "-f", name], capture_output=True, timeout=15)  # nosec B603
            raise

        return AntigravityObserverSession(
            key=(user_id, conversation_id, agent_name, service_id),
            name=name,
            workdir=workdir,
            container_workdir=container_workdir,
            log_path=log_path,
        )

    def _write_antigravity_config(self, client: "LLMClient", workdir: str, user_id: str,
                                  conversation_id: str, agent_name: str, model: str) -> None:
        mcp_servers, _internal_token = client._gemini_acp_mcp_servers(user_id, conversation_id, agent_name)
        client._gemini_acp_write_settings(
            workdir, model=model or "", effort="", thinking_budget=0,
            temperature=0.7, max_tokens=0, mcp_servers=mcp_servers,
            mcp_cwd=self._container_workdir(user_id, conversation_id, agent_name),
        )
        gemini_home = Path(workdir) / ".gemini"
        config_dir = gemini_home / "config"
        projects_dir = config_dir / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        mcp_config = client._gemini_acp_settings_mcp_servers(
            mcp_servers, self._container_workdir(user_id, conversation_id, agent_name))
        (config_dir / "mcp_config.json").write_text(
            json.dumps(mcp_config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        (gemini_home / "mcp_config.json").write_text(
            json.dumps({"mcpServers": mcp_config}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        antigravity_dir = gemini_home / "antigravity"
        antigravity_dir.mkdir(parents=True, exist_ok=True)
        antigravity_mcp = self._antigravity_mcp_config(mcp_config)
        (antigravity_dir / "mcp_config.json").write_text(
            json.dumps(antigravity_mcp, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        antigravity_cli_dir = gemini_home / "antigravity-cli"
        antigravity_cli_dir.mkdir(parents=True, exist_ok=True)
        (antigravity_cli_dir / "mcp_config.json").write_text(
            json.dumps(antigravity_mcp, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        cli_settings_path = antigravity_cli_dir / "settings.json"
        cli_settings = self._read_json(cli_settings_path)
        trusted = cli_settings.get("trustedWorkspaces")
        if not isinstance(trusted, list):
            trusted = []
        container_workdir = self._container_workdir(user_id, conversation_id, agent_name)
        if container_workdir not in trusted:
            trusted.append(container_workdir)
        cli_permissions = cli_settings.get("permissions")
        if not isinstance(cli_permissions, dict):
            cli_permissions = {}
        cli_allow = cli_permissions.get("allow")
        if not isinstance(cli_allow, list):
            cli_allow = []
        for pattern in ("mcp(pawflow/*)", "mcp_pawflow_*", "mcp_*"):
            if pattern not in cli_allow:
                cli_allow.append(pattern)
        cli_permissions["allow"] = cli_allow
        cli_settings["enableTelemetry"] = False
        cli_settings["trustedWorkspaces"] = trusted
        cli_settings["permissions"] = cli_permissions
        cli_settings["mcpServers"] = mcp_config
        cli_settings["allowMCPServers"] = ["pawflow"]
        cli_settings["mcp"] = {"allowed": ["pawflow"]}
        cli_settings_path.write_text(
            json.dumps(cli_settings, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        settings_path = gemini_home / "settings.json"
        settings = self._read_json(settings_path)
        permissions = settings.get("permissions")
        if not isinstance(permissions, dict):
            permissions = {}
        allow = permissions.get("allow")
        if not isinstance(allow, list):
            allow = []
        if "mcp(pawflow/*)" not in allow:
            allow.append("mcp(pawflow/*)")
        for pattern in ("mcp_pawflow_*", "mcp_*"):
            if pattern not in allow:
                allow.append(pattern)
        permissions["allow"] = allow
        settings["permissions"] = permissions
        settings["mcpServers"] = mcp_config
        settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        project_id = str(uuid.uuid5(uuid.NAMESPACE_URL, self._container_workdir(user_id, conversation_id, agent_name)))
        agents_dir = Path(workdir) / ".agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        (agents_dir / "mcp_config.json").write_text(
            json.dumps({"mcpServers": mcp_config}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        project = {
            "id": project_id,
            "name": self._container_workdir(user_id, conversation_id, agent_name),
            "projectResources": {
                "resources": [{
                    "gitFolder": {
                        "folderUri": f"file://{self._container_workdir(user_id, conversation_id, agent_name)}",
                        "allowWrite": True,
                    }
                }]
            },
        }
        (projects_dir / f"{project_id}.json").write_text(
            json.dumps(project, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        keybindings = gemini_home / "antigravity-cli" / "keybindings.json"
        keybindings.parent.mkdir(parents=True, exist_ok=True)
        if not keybindings.exists():
            keybindings.write_text("{}\n", encoding="utf-8")
        self._write_workspace_rules(workdir)

    @staticmethod
    def _write_workspace_rules(workdir: str) -> None:
        rules_dir = Path(workdir) / ".agents" / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        (rules_dir / "pawflow-mcp.md").write_text(
            "# PawFlow MCP Tools\n\n"
            "Use the configured MCP server `pawflow` for filesystem, shell, search, edit, patch, browser, web, image, and desktop actions.\n"
            "Do not create custom WebSocket, HTTP, relay, or token-based clients to call PawFlow directly.\n"
            "If the MCP server or a required MCP tool is unavailable, report that MCP is unavailable instead of bypassing it.\n",
            encoding="utf-8",
        )

    @staticmethod
    def _antigravity_mcp_entry(entry: dict) -> dict:
        """Return the MCP server shape documented by Antigravity."""
        allowed = {
            "type", "command", "serverUrl", "args", "env", "cwd", "headers",
            "authProviderType", "oauth", "disabled", "disabledTools", "timeout", "trust",
        }
        return {k: v for k, v in (entry or {}).items() if k in allowed and v not in (None, "")}

    @classmethod
    def _antigravity_mcp_config(cls, mcp_config: dict) -> dict:
        """Return the Antigravity/Jetski MCP customization shape."""
        servers = []
        for name, entry in (mcp_config or {}).items():
            spec = cls._antigravity_mcp_entry(entry)
            spec["serverName"] = name
            spec.setdefault("disabled", False)
            servers.append(spec)
        return {"mcpServers": servers}

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return {}

    def _spawn_container(self, *, user_id: str, conversation_id: str, agent_name: str) -> str:
        self._base_dir().mkdir(parents=True, exist_ok=True)
        project_root = Path(__file__).resolve().parents[1]
        sessions_host = translate_path(to_host_path(str(self._base_dir().resolve())))
        mounts = ["-v", f"{sessions_host}:/cc_sessions"]
        files = [
            (project_root / "tools" / "mcp_bridge.py", "/opt/pawflow/mcp_bridge.py"),
            (project_root / "tools" / "ag_observer_proxy.py", "/opt/pawflow/ag_observer_proxy.py"),
            (project_root / "docker" / "pawflow_sdk" / "pawflow.py", "/opt/pawflow/pawflow.py"),
        ]
        for src, dst in files:
            if src.exists():
                mounts += ["-v", f"{translate_path(to_host_path(str(src)))}:{dst}:ro"]
        pkg_dir = project_root / "pawflow_relay"
        if pkg_dir.is_dir():
            mounts += ["-v", f"{translate_path(to_host_path(str(pkg_dir)))}:/opt/pawflow/pawflow_relay:ro"]
        if not ca_private_key_is_host_only([m.split(":", 1)[0] for m in mounts if isinstance(m, str)]):
            raise RuntimeError("Refusing to mount Antigravity observer CA private key")

        owner = get_server_id()
        name = f"pf-{owner[:12]}-agyobs-{uuid.uuid4().hex[:8]}"
        image = os.environ.get("PAWFLOW_ANTIGRAVITY_IMAGE", os.environ.get("PAWFLOW_GEMINI_IMAGE", "pawflow-claude-code:latest"))
        run_args = [
            "-d", "--rm", "--name", name,
            *mounts,
            "--add-host", f"{ANTIGRAVITY_BACKEND_HOST}:127.0.0.1",
            "--add-host", "host.docker.internal:host-gateway",
            "--cap-add", "SYS_ADMIN",
            "--shm-size", "512m",
            "--tmpfs", "/tmp:rw,nosuid,size=512m",  # nosec B108 - Docker tmpfs mount target inside ephemeral container.
            "--user", "root",
            "--entrypoint", "/usr/bin/sleep",
            image,
            "infinity",
        ]
        result = subprocess.run(docker_cmd() + ["run"] + run_args,  # nosec B603
                                capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to spawn Antigravity observer container: {result.stderr[:500]}")
        subprocess.run(docker_cmd() + ["exec", "--user", "root", name, "chronyd"],  # nosec B603
                       capture_output=True, timeout=5)
        return name

    def _install_ca(self, name: str, container_workdir: str) -> None:
        ca_path = f"{container_workdir}/.pawflow_ag/certs/pawflow-ca.crt"
        cmd = f"cp {shlex.quote(ca_path)} /usr/local/share/ca-certificates/pawflow-ag.crt && update-ca-certificates"
        r = subprocess.run(docker_cmd() + ["exec", "--user", "root", name, "bash", "-lc", cmd],  # nosec B603
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(f"Failed to install Antigravity observer CA: {r.stderr[:300]}")

    def _start_proxy(self, *, name: str, container_workdir: str, log_path: str,
                     stderr_path: str = "", certs=None) -> None:
        ips = self._resolve_upstream_ips()
        container_log = self._container_session_path(log_path)
        container_stderr = self._container_session_path(stderr_path or f"{log_path}.stderr.log")
        if stderr_path:
            stderr_file = Path(stderr_path)
            stderr_file.parent.mkdir(parents=True, exist_ok=True)
            stderr_file.write_text(
                f"starting Antigravity observer proxy in {name}; "
                f"log={container_log}; upstream={ANTIGRAVITY_BACKEND_HOST}\n",
                encoding="utf-8",
            )
        env = [
            "-e", f"PAWFLOW_AG_OBSERVER_LOG={container_log}",
            "-e", f"PAWFLOW_AG_UPSTREAM_IPS={','.join(ips)}",
            "-e", f"PAWFLOW_AG_LEAF_CERT={container_workdir}/.pawflow_ag/certs/{Path(certs.cert_path).name}",
            "-e", f"PAWFLOW_AG_LEAF_KEY={container_workdir}/.pawflow_ag/certs/{Path(certs.key_path).name}",
        ]
        for key in ("PAWFLOW_AG_OBSERVER_LOG_B64", "PAWFLOW_AG_OBSERVER_MAX_B64_BYTES"):
            value = os.environ.get(key)
            if value:
                env += ["-e", f"{key}={value}"]
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "-d", "--user", "root", *env, name,
                            "bash", "-lc",
                            f"exec python3 /opt/pawflow/ag_observer_proxy.py >> {shlex.quote(container_stderr)} 2>&1"],
            capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            raise RuntimeError(f"Failed to start Antigravity observer proxy: {r.stderr[:300]}")
        self._wait_for_proxy_start(log_path, stderr_path=stderr_path)

    def _container_session_path(self, path: str) -> str:
        rel = Path(path).resolve().relative_to(self._base_dir().resolve())
        return "/cc_sessions/" + rel.as_posix()

    def _start_agy_tmux(self, *, name: str, container_workdir: str) -> None:
        parts = container_workdir.lstrip("/").split("/")
        if len(parts) < 3 or parts[0] != "cc_sessions":
            raise ValueError(f"container_workdir must look like /cc_sessions/<user>/<conv>/<agent>; got {container_workdir!r}")
        user_slot = "/cc_sessions/" + parts[1]
        ns_workdir = "/" + "/".join(parts[:1] + parts[2:])
        agy_bin = os.environ.get("PAWFLOW_ANTIGRAVITY_BIN", "agy")
        quoted_cmd = " ".join(shlex.quote(a) for a in [agy_bin, "--dangerously-skip-permissions"])
        drop_privs = "setpriv --reuid=1000 --regid=1000 --clear-groups --"
        shell = (
            f"mount --bind {shlex.quote(user_slot)} /cc_sessions && "
            f"cd {shlex.quote(ns_workdir)} && ("
            f"{drop_privs} tmux kill-session -t pawflow-agy 2>/dev/null || true; "
            f"{drop_privs} tmux new-session -d -s pawflow-agy "
            f"'env HOME={shlex.quote(ns_workdir)} "
            f"GEMINI_CLI_HOME={shlex.quote(ns_workdir)} "
            f"CASCADE_ENABLE_MCP_TOOLS=true "
            f"USER=pawflow TERM=xterm-256color "
            f"{quoted_cmd}')"
        )
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", "root", name,
                            "setsid", "--wait", "unshare", "-m", "--",
                            "bash", "-lc", shell],
            capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            raise RuntimeError(f"Failed to start Antigravity tmux: {r.stderr[:500]}")
        probe = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", "1000:1000", name,
                            "tmux", "has-session", "-t", "pawflow-agy"],
            capture_output=True, text=True, timeout=10)
        if probe.returncode != 0:
            raise RuntimeError(
                "Antigravity tmux session exited during startup: "
                f"{(probe.stderr or probe.stdout or '').strip()[:500]}")
        self._prime_agy_mcp(name)

    def _prime_agy_mcp(self, name: str) -> None:
        if os.environ.get("PAWFLOW_AGY_SKIP_MCP_PRIME", "").lower() in {"1", "true", "yes"}:
            return
        prime = (
            "sleep 1; "
            "tmux set-buffer -t pawflow-agy -- /mcp && "
            "tmux paste-buffer -t pawflow-agy && "
            "tmux send-keys -t pawflow-agy Enter && "
            "sleep 1; "
            "tmux send-keys -t pawflow-agy Enter && "
            "sleep 0.2; "
            "tmux send-keys -t pawflow-agy Escape"
        )
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", "1000:1000", name, "bash", "-lc", prime],
            capture_output=True, text=True, timeout=8)
        if r.returncode != 0:
            logger.warning(
                "Antigravity MCP priming failed for %s: %s",
                name, (r.stderr or r.stdout or "").strip()[:500])

    @staticmethod
    def _resolve_upstream_ips() -> list[str]:
        infos = socket.getaddrinfo(ANTIGRAVITY_BACKEND_HOST, 443, type=socket.SOCK_STREAM)
        seen = []
        for info in infos:
            ip = info[4][0]
            if ip not in seen and ip != "127.0.0.1":
                seen.append(ip)
        return seen

    @staticmethod
    def _is_alive(name: str) -> bool:
        try:
            result = subprocess.run(  # nosec B603
                docker_cmd() + ["inspect", "-f", "{{.State.Running}}", name],
                capture_output=True, text=True, timeout=5)
            return result.stdout.strip() == "true"
        except Exception:
            return False

    def _is_usable(self, state: AntigravityObserverSession) -> bool:
        return self._is_alive(state.name) and self._proxy_log_ready(state.log_path)

    @staticmethod
    def _proxy_log_ready(log_path: str) -> bool:
        path = Path(log_path)
        if not path.is_file():
            return False
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (event.get("type") == "proxy_start"
                        and event.get("upstream_host") == ANTIGRAVITY_BACKEND_HOST):
                    return True
        except OSError:
            return False
        return False

    def _wait_for_proxy_start(self, log_path: str, timeout: float = 3.0,
                              stderr_path: str = "") -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._proxy_log_ready(log_path):
                return
            time.sleep(0.05)
        detail = ""
        if stderr_path:
            try:
                stderr = Path(stderr_path).read_text(encoding="utf-8", errors="replace").strip()
                if stderr:
                    detail = f": {stderr[-500:]}"
            except OSError:
                pass
        raise RuntimeError(f"Antigravity observer proxy did not write proxy_start{detail}")

    def kill(self, state: AntigravityObserverSession) -> None:
        state.manual_ingest_stop.set()
        subprocess.run(docker_cmd() + ["rm", "-f", state.name], capture_output=True, timeout=15)  # nosec B603
        with self._lock:
            self._sessions.pop(state.key, None)
