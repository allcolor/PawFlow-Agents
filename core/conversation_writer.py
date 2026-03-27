"""Conversation Writer — FIFO write queue for guaranteed message ordering.

All conversation message writes go through this queue. A single writer
thread per conversation dequeues and writes to ConversationStore in order.

Usage:
    writer = ConversationWriter.for_conversation(conversation_id)
    writer.enqueue(messages, user_id=..., context_agent=..., status=...)
"""

import logging
import queue
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_IDLE_TIMEOUT = 300  # 5 minutes idle → writer thread exits


class ConversationWriter:

    _instances: Dict[str, 'ConversationWriter'] = {}
    _global_lock = threading.Lock()

    @classmethod
    def for_conversation(cls, cid: str) -> 'ConversationWriter':
        with cls._global_lock:
            w = cls._instances.get(cid)
            if w and w._alive:
                return w
            w = cls(cid)
            cls._instances[cid] = w
            return w

    @classmethod
    def shutdown_all(cls):
        with cls._global_lock:
            for w in cls._instances.values():
                w._stop = True
            cls._instances.clear()

    def __init__(self, cid: str):
        self._cid = cid
        self._queue: queue.Queue = queue.Queue()
        self._stop = False
        self._alive = True
        self._context_op_active = False
        self._resume_event = threading.Event()
        self._thread = threading.Thread(
            target=self._writer_loop, daemon=True,
            name=f"conv-writer-{cid[:8]}")
        self._thread.start()

    def enqueue(self, messages: List[Dict], user_id: str = "",
                context_agent: str = "", status: str = "",
                wait: bool = False) -> Optional[threading.Event]:
        """Add messages to the write queue. Non-blocking unless wait=True."""
        evt = threading.Event() if wait else None
        self._queue.put({
            "op": "append",
            "messages": messages,
            "user_id": user_id,
            "context_agent": context_agent,
            "status": status,
            "_done_event": evt,
        })
        if wait and evt:
            evt.wait(timeout=30)
        return evt

    def enqueue_agent_flush(self, agent_name: str,
                            public_messages: List[Dict],
                            private_messages: List[Dict],
                            user_id: str = "", ttl: int = 0,
                            wait: bool = False) -> Optional[threading.Event]:
        """Enqueue an agent_flush operation (transcript + all contexts atomically)."""
        evt = threading.Event() if wait else None
        self._queue.put({
            "op": "agent_flush",
            "agent_name": agent_name,
            "public": public_messages,
            "private": private_messages,
            "user_id": user_id,
            "ttl": ttl,
            "_done_event": evt,
        })
        if wait and evt:
            evt.wait(timeout=30)
        return evt

    def pause_for_context_op(self):
        """Pause message writing — context operation in progress."""
        self._context_op_active = True

    def resume_after_context_op(self):
        """Resume message writing — context operation done."""
        self._context_op_active = False
        self._resume_event.set()

    def flush(self, timeout: float = 10.0):
        """Block until all queued messages are written."""
        evt = threading.Event()
        self._queue.put({"_flush": True, "_done_event": evt})
        evt.wait(timeout=timeout)

    def _writer_loop(self):
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        while not self._stop:
            # Wait for context op to finish
            while self._context_op_active and not self._stop:
                self._resume_event.wait(timeout=1)
                self._resume_event.clear()

            try:
                item = self._queue.get(timeout=_IDLE_TIMEOUT)
            except queue.Empty:
                # Idle timeout — exit thread
                with self._global_lock:
                    if self._queue.empty():
                        self._alive = False
                        ConversationWriter._instances.pop(self._cid, None)
                        return
                continue

            evt = item.get("_done_event")
            if item.get("_flush"):
                if evt:
                    evt.set()
                continue

            try:
                op = item.get("op", "append")
                if op == "agent_flush":
                    store.agent_flush(
                        self._cid, item["agent_name"],
                        public_messages=item.get("public", []),
                        private_messages=item.get("private", []),
                        user_id=item.get("user_id", ""),
                        ttl=item.get("ttl", 0))
                else:
                    msgs = item.get("messages", [])
                    if msgs:
                        store.append_messages(
                            self._cid, msgs,
                            user_id=item.get("user_id", ""),
                            status=item.get("status", ""))
                        ctx_agent = item.get("context_agent")
                        if ctx_agent:
                            store.append_to_agent_context(
                                self._cid, ctx_agent, msgs)
            except Exception as e:
                logger.error("[conv-writer:%s] write failed: %s",
                             self._cid[:8], e, exc_info=True)
            finally:
                if evt:
                    evt.set()

        self._alive = False
