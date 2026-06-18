"""Shared imports/consts/locks + small classes for the conversation_store split."""

import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional


logger = logging.getLogger(__name__)

_CTX_CACHE_MAX_MESSAGES = int(os.getenv("PAWFLOW_CTX_CACHE_MAX_MESSAGES", "500") or "500")
_CTX_CACHE_MAX_CHARS = int(os.getenv("PAWFLOW_CTX_CACHE_MAX_CHARS", "1000000") or "1000000")
_CTX_CACHE_MAX_CONVS = int(os.getenv("PAWFLOW_CTX_CACHE_MAX_CONVS", "20") or "20")
_CONV_LOCK_DIAG_MS = float(os.getenv("PAWFLOW_CONV_LOCK_DIAG_MS", "20") or "20")
_GIT_RETENTION_DAYS = int(os.getenv("PAWFLOW_CONV_GIT_RETENTION_DAYS", "7") or "7")
_GIT_RETENTION_COMMITS = int(os.getenv("PAWFLOW_CONV_GIT_RETENTION_COMMITS", "250") or "250")
_GIT_RETENTION_INTERVAL_SEC = int(os.getenv("PAWFLOW_CONV_GIT_RETENTION_INTERVAL_SEC", "86400") or "86400")
_HOT_METADATA_FLUSH_INTERVAL_SEC = float(os.getenv("PAWFLOW_HOT_METADATA_FLUSH_INTERVAL_SEC", "2.0") or "2.0")
_HOT_METADATA_FLUSH_MSG_DELTA = int(os.getenv("PAWFLOW_HOT_METADATA_FLUSH_MSG_DELTA", "20") or "20")
_HOT_METADATA_KEYS = (
    "_meta_msg_count", "_meta_preview", "_meta_updated_at", "_meta_max_seq")
_HOT_METADATA_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="conv-meta-flush")
_GIT_RETENTION_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="conv-git-retention")
_GIT_RETENTION_RUNNING: set[str] = set()
_GIT_RETENTION_RUNNING_LOCK = threading.Lock()


class ConversationLockedError(RuntimeError):
    """Raised when a write is attempted against an encrypted conversation whose
    DEK is not currently unlocked in the KeyVault. Refusing here is what keeps
    the hot append path from ever persisting plaintext into an encrypted
    conversation (the cache-cold / locked case)."""


class _ConversationTimedRLock:
    """RLock wrapper that logs slow holders with the acquiring call-site."""

    def __init__(self, cid: str):
        self._cid = cid
        self._lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._owner_ident: Optional[int] = None
        self._owner_label = ""
        self._owner_started = 0.0
        self._depth = 0

    @staticmethod
    def _caller_label() -> str:
        try:
            frame = sys._getframe(3)
        except ValueError:
            frame = sys._getframe(2)
        return f"{frame.f_code.co_name}:{frame.f_lineno}"

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        label = self._caller_label()
        started = time.monotonic()
        ident = threading.get_ident()
        with self._state_lock:
            blocked_by = self._owner_label
            reentrant = self._owner_ident == ident
        ok = self._lock.acquire(blocking, timeout)
        if not ok:
            return False
        waited_ms = (time.monotonic() - started) * 1000.0
        with self._state_lock:
            if reentrant and self._owner_ident == ident:
                self._depth += 1
            else:
                self._owner_ident = ident
                self._owner_label = label
                self._owner_started = time.monotonic()
                self._depth = 1
        if waited_ms >= _CONV_LOCK_DIAG_MS and not reentrant:
            logger.warning(
                "[conv-lock:%s] waited %.1fms at %s blocked_by=%s",
                self._cid[:8], waited_ms, label, blocked_by or "?")
        return True

    def release(self) -> None:
        ident = threading.get_ident()
        log_label = ""
        held_ms = 0.0
        with self._state_lock:
            if self._owner_ident == ident and self._depth > 0:
                self._depth -= 1
                if self._depth == 0:
                    log_label = self._owner_label
                    held_ms = (time.monotonic() - self._owner_started) * 1000.0
                    self._owner_ident = None
                    self._owner_label = ""
                    self._owner_started = 0.0
        try:
            self._lock.release()
        finally:
            if held_ms >= _CONV_LOCK_DIAG_MS:
                logger.warning(
                    "[conv-lock:%s] held %.1fms by %s",
                    self._cid[:8], held_ms, log_label or "?")

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
