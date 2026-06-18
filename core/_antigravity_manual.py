"""Antigravity CLI interactive sessions.

This pool starts the real ``agy`` CLI in tmux with Gemini OAuth/MCP config and
a transparent observer proxy for ``daily-cloudcode-pa.googleapis.com``. The
same tmux/proxy foundation is used by both the diagnostics observer action and
the ``antigravity-interactive`` LLM provider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import logging
import os
import threading
import time
import uuid


if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)
# Split out of antigravity_observer_pool.py for the <=800-line rule; the
# mixin is composed back into AntigravityObserverPool (invariant 2: MRO/shared state).

from core._antigravity_base import AntigravityObserverSession, ANTIGRAVITY_BACKEND_HOST  # noqa: F401,E402


class _AntigravityManualIngestMixin:
    """Manual tmux-capture ingest: turn assembly, tool-call flush, prompt persistence."""

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
            if etype not in {"ag_text_delta", "ag_model_delta", "tool_use", "tool_result", "hook", "request_start"}:
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
            "pending_tool_results": [],
            "usage": {},
            "done": False,
            "done_at": 0.0,
            "last_event_at": 0.0,
            "awaiting_tool_followup": False,
            "prompt_seen": False,
            "live_text_msg_id": "",
            "live_text_ts": 0.0,
            "live_text_emitted": False,
            "flushed_tool_call_ids": set(),
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
        from core.llm_client import is_mcp_tool_call_name

        turn["last_event_at"] = time.time()
        etype = event.get("type", "")
        if etype == "request_start":
            return
        if etype == "hook":
            if event.get("hook_event_name") == "Stop":
                turn["done"] = True
                turn["done_at"] = time.time()
            return
        if etype == "tool_use":
            event = {
                "type": "ag_text_delta",
                "tool_calls": [{
                    "id": event.get("tool_use_id") or event.get("id") or "",
                    "name": event.get("name", ""),
                    "arguments": event.get("arguments") or {},
                    "tool_origin": event.get("tool_origin", ""),
                }],
            }
        elif etype == "tool_result":
            event = {
                "type": "ag_text_delta",
                "tool_results": [{
                    "tool_use_id": event.get("tool_use_id") or event.get("id") or "",
                    "name": event.get("name", ""),
                    "content": event.get("content", ""),
                    "tool_origin": event.get("tool_origin", ""),
                }],
            }
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
            name = str(tc.get("name") or tc.get("tool") or "")
            origin = str(tc.get("tool_origin") or "")
            if not origin and is_mcp_tool_call_name(name):
                origin = "mcp"
            if not origin and name:
                origin = "native"
            if any(existing.get("name") == name
                   and existing.get("arguments") == args
                   and (existing.get("tool_origin") or "") == origin
                   for existing in turn.get("tool_calls") or []):
                state.emitted_tool_use_ids.add(tc_id)
                continue
            entry = {"id": tc_id, "name": name, "arguments": args}
            if origin:
                entry["tool_origin"] = origin
            turn["tool_by_id"][tc_id] = entry
            turn["tool_calls"].append(dict(entry))
            turn["awaiting_tool_followup"] = True
            self._drain_manual_pending_tool_results(state, turn)
        if tool_calls:
            self._flush_manual_tool_calls_now(state, turn)
        for tr in event.get("tool_results") or []:
            if not isinstance(tr, dict):
                continue
            if not self._apply_manual_tool_result(state, turn, tr):
                turn.setdefault("pending_tool_results", []).append(dict(tr))
        thinking = event.get("thinking", "") or "".join(event.get("thinking_texts") or [])
        if thinking:
            turn["thinking_parts"].append(thinking)
        text = event.get("text", "") or "".join(event.get("texts") or [])
        if text:
            self._publish_manual_text_token(state, turn, text)
            turn["text_parts"].append(text)
            if not tool_calls and turn.get("awaiting_tool_followup"):
                turn["awaiting_tool_followup"] = False
        if event.get("done") or (event.get("finish_reason") and not turn.get("awaiting_tool_followup")):
            turn["done"] = True
            turn["done_at"] = time.time()

    def _flush_manual_tool_calls_now(self, state: AntigravityObserverSession,
                                     turn: dict) -> None:
        pending = [
            tc for tc in (turn.get("tool_calls") or [])
            if tc.get("id") not in (turn.get("flushed_tool_call_ids") or set())
        ]
        if not pending:
            return
        from core.llm_client import LLMMessage, LLMToolCall, is_mcp_tool_call_name, unwrap_mcp_tool
        from core.conversation_writer import ConversationWriter

        cid = state.key[1]
        user_id = state.key[0]
        source = self._manual_agent_source(state, turn.get("usage") or {})
        tc_objects = []
        for tc in pending:
            raw_name = tc.get("name", "")
            name, args = unwrap_mcp_tool(raw_name, tc.get("arguments", {}) or {})
            tool_origin = tc.get("tool_origin", "") or ""
            if not tool_origin and is_mcp_tool_call_name(raw_name):
                tool_origin = "mcp"
            tc_objects.append(LLMToolCall(
                id=tc.get("id", ""),
                name=name,
                arguments=args if isinstance(args, dict) else {},
                tool_origin=tool_origin,
            ))
        if not tc_objects:
            return
        tc_msg = LLMMessage(
            role="assistant",
            content="",
            tool_calls=tc_objects,
            thinking="".join(turn.get("thinking_parts") or []),
            source=source,
            conversation_id=cid,
        )
        tc_sse = []
        for tc_obj in tc_objects:
            state.manual_live_tool_calls[tc_obj.id] = {
                "name": tc_obj.name,
                "tool_origin": tc_obj.tool_origin,
            }
            tc_data = {
                "tool": tc_obj.name,
                "arguments": tc_obj.arguments,
                "tc_id": tc_obj.id,
                "agent_name": state.agent_name,
                "llm_service": state.service_id,
                "msg_id": tc_msg.msg_id,
                "ts": tc_msg.timestamp,
                "source": source,
            }
            if tc_obj.tool_origin:
                tc_data["tool_origin"] = tc_obj.tool_origin
            tc_sse.append({"type": "tool_call", "data": tc_data})
        ConversationWriter.for_conversation(cid).enqueue_message({
            "role": "assistant",
            "content": "",
            "source": source,
            "msg_id": tc_msg.msg_id,
            "ts": tc_msg.timestamp,
            "seq": tc_msg.seq or None,
            "thinking": tc_msg.thinking or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "name": tc.name,
                    "arguments": tc.arguments,
                    **({"tool_origin": tc.tool_origin} if tc.tool_origin else {}),
                }
                for tc in tc_objects
            ],
        }, agent_name=state.agent_name, user_id=user_id, sse_events=tc_sse)
        turn.setdefault("flushed_tool_call_ids", set()).update(tc.id for tc in tc_objects)

    def _publish_manual_text_token(self, state: AntigravityObserverSession,
                                   turn: dict, text: str) -> None:
        if not text:
            return
        if not turn.get("live_text_msg_id"):
            turn["live_text_msg_id"] = uuid.uuid4().hex[:12]
            turn["live_text_ts"] = time.time()
        source = self._manual_agent_source(state, turn.get("usage") or {})
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(state.key[1], "token", {
                "agent_name": state.agent_name,
                "text": text,
                "msg_id": turn["live_text_msg_id"],
                "ts": turn["live_text_ts"],
                "source": source,
            })
            turn["live_text_emitted"] = True
        except Exception:
            logger.debug(
                "[antigravity-observer] live token publish failed for %s/%s",
                state.key[1][:8], state.agent_name, exc_info=True)

    def _apply_manual_tool_result(self, state: AntigravityObserverSession,
                                  turn: dict, tr: dict) -> bool:
        from core.llm_client import unwrap_mcp_tool

        raw_tc_id = str(tr.get("tool_use_id") or tr.get("tool_call_id") or tr.get("id") or "")
        name = str(tr.get("name") or tr.get("tool") or "")
        match_name, _match_args = unwrap_mcp_tool(name, tr)
        tc_id = raw_tc_id
        if not tc_id or tc_id not in turn["tool_by_id"]:
            matches = [
                tc for tc in turn["tool_calls"]
                if not tc.get("result") and self._manual_tool_result_matches(tc, name, match_name, tr)
            ]
            if len(matches) == 1:
                tc_id = str(matches[0].get("id") or "")
        if not tc_id:
            return False
        dedupe_id = raw_tc_id if raw_tc_id and raw_tc_id in turn["tool_by_id"] else tc_id
        if dedupe_id in state.emitted_tool_result_ids:
            return True
        state.emitted_tool_result_ids.add(dedupe_id)
        state.emitted_tool_result_ids.add(tc_id)
        result = tr.get("content") or tr.get("result") or tr.get("response") or "(no output)"
        for tc in turn["tool_calls"]:
            if tc.get("id") == tc_id:
                tc["result"] = result
                if tr.get("tool_origin") and not tc.get("tool_origin"):
                    tc["tool_origin"] = tr.get("tool_origin")
                break
        turn["awaiting_tool_followup"] = any(
            not tc.get("result") for tc in turn.get("tool_calls") or [])
        return True

    def _drain_manual_pending_tool_results(self, state: AntigravityObserverSession,
                                           turn: dict) -> None:
        pending = turn.get("pending_tool_results") or []
        if not pending:
            return
        misses = []
        for event in pending:
            if not self._apply_manual_tool_result(state, turn, event):
                misses.append(event)
        turn["pending_tool_results"] = misses

    @staticmethod
    def _manual_tool_result_matches(tc: dict, name: str, match_name: str, tr: dict) -> bool:
        tc_origin = str(tc.get("tool_origin") or "")
        result_origin = str(tr.get("tool_origin") or "")
        if tc_origin == "mcp" and result_origin and result_origin != "mcp":
            return False
        if not name:
            return True
        if tc.get("name") == name:
            return True
        from core.llm_client import unwrap_mcp_tool

        return unwrap_mcp_tool(tc.get("name", ""), tc.get("arguments", {}) or {})[0] == match_name

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
        if self._consume_injected_prompt(state, text):
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
        from core.llm_client import LLMMessage, LLMToolCall, is_mcp_tool_call_name, unwrap_mcp_tool
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
                msg_id=turn.get("live_text_msg_id") or "",
                timestamp=float(turn.get("live_text_ts") or 0.0),
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
                if turn.get("live_text_emitted"):
                    usage = turn.get("usage") or {}
                    sse.append({"type": "turn_complete", "data": {
                        "agent_name": state.agent_name,
                        "msg_id": msg.msg_id,
                        "source": source,
                        "model": source.get("model", ""),
                        "provider": source.get("provider", ""),
                        "tokens_in": int(usage.get("input_tokens", 0) or 0),
                        "tokens_out": int(usage.get("output_tokens", 0) or 0),
                        "ts": msg.timestamp,
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
        flushed_tool_call_ids = turn.get("flushed_tool_call_ids") or set()
        unflushed_tool_calls = [
            tc for tc in tool_calls
            if tc.get("id") not in flushed_tool_call_ids
        ]
        if unflushed_tool_calls:
            tc_objects = []
            for tc in unflushed_tool_calls:
                raw_name = tc.get("name", "")
                name, args = unwrap_mcp_tool(raw_name, tc.get("arguments", {}) or {})
                tool_origin = tc.get("tool_origin", "") or ""
                if not tool_origin and is_mcp_tool_call_name(raw_name):
                    tool_origin = "mcp"
                tc_objects.append(LLMToolCall(
                    id=tc.get("id", ""),
                    name=name,
                    arguments=args if isinstance(args, dict) else {},
                    tool_origin=tool_origin,
                ))
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
                    tc_data = {
                        "tool": tc_obj.name,
                        "arguments": tc_obj.arguments,
                        "tc_id": tc_obj.id,
                        "agent_name": state.agent_name,
                        "llm_service": state.service_id,
                        "msg_id": tc_msg.msg_id,
                        "ts": tc_msg.timestamp,
                        "source": source,
                    }
                    if tc_obj.tool_origin:
                        tc_data["tool_origin"] = tc_obj.tool_origin
                    tc_sse.append({"type": "tool_call", "data": tc_data})
                writer.enqueue_message({
                    "role": "assistant",
                    "content": "",
                    "source": source,
                    "msg_id": tc_msg.msg_id,
                    "ts": tc_msg.timestamp,
                    "seq": tc_msg.seq or None,
                    "thinking": thinking or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "name": tc.name,
                            "arguments": tc.arguments,
                            **({"tool_origin": tc.tool_origin} if tc.tool_origin else {}),
                        }
                        for tc in tc_objects
                    ],
                }, agent_name=state.agent_name, user_id=user_id, sse_events=tc_sse)
                for idx, tc_obj in enumerate(tc_objects):
                    raw = unflushed_tool_calls[idx] if idx < len(unflushed_tool_calls) else {}
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
                    tool_origin = getattr(tc_obj, "tool_origin", "") or ""
                    tr_event = {
                        "tool": tc_obj.name,
                        "result": str(result)[:2000],
                        "tc_id": tc_obj.id,
                        "msg_id": tr_msg.msg_id,
                        "ts": tr_msg.timestamp,
                        "agent_name": state.agent_name,
                        "llm_service": state.service_id,
                    }
                    if tool_origin:
                        tr_msg._tool_origin = tool_origin
                        tr_event["tool_origin"] = tool_origin
                    writer.enqueue_message({
                        "role": "tool",
                        "content": tr_msg.content,
                        "msg_id": tr_msg.msg_id,
                        "tool_call_id": tc_obj.id,
                        **({"tool_origin": tool_origin} if tool_origin else {}),
                        "ts": tr_msg.timestamp,
                        "seq": tr_msg.seq or None,
                    }, agent_name=state.agent_name, user_id=user_id, sse_events=[{"type": "tool_result", "data": tr_event}])
        for raw in tool_calls:
            if raw.get("id") not in flushed_tool_call_ids:
                continue
            has_result = any(raw.get(k) for k in ("result", "content", "response"))
            if not has_result:
                continue
            raw_name = raw.get("name", "")
            name, args = unwrap_mcp_tool(raw_name, raw.get("arguments", {}) or {})
            tool_origin = raw.get("tool_origin", "") or ""
            if not tool_origin and is_mcp_tool_call_name(raw_name):
                tool_origin = "mcp"
            result = raw.get("result") or raw.get("content") or raw.get("response")
            tr_msg = LLMMessage(
                role="tool",
                content=AgentCoreMixin._wrap_tool_output(name, result),
                tool_call_id=raw.get("id", ""),
                conversation_id=cid,
            )
            tr_msg._tool_name = name
            tr_event = {
                "tool": name,
                "result": str(result)[:2000],
                "tc_id": raw.get("id", ""),
                "msg_id": tr_msg.msg_id,
                "ts": tr_msg.timestamp,
                "agent_name": state.agent_name,
                "llm_service": state.service_id,
            }
            if tool_origin:
                tr_msg._tool_origin = tool_origin
                tr_event["tool_origin"] = tool_origin
            writer.enqueue_message({
                "role": "tool",
                "content": tr_msg.content,
                "msg_id": tr_msg.msg_id,
                "tool_call_id": raw.get("id", ""),
                **({"tool_origin": tool_origin} if tool_origin else {}),
                "ts": tr_msg.timestamp,
                "seq": tr_msg.seq or None,
            }, agent_name=state.agent_name, user_id=user_id, sse_events=[{"type": "tool_result", "data": tr_event}])
