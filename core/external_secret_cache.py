"""Bounded in-memory cache for materialized external secrets."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass

from core.secret_provider import ProviderValue


@dataclass
class _CacheEntry:
    value: ProviderValue
    expires_at: float


class ExternalSecretCache:
    """TTL/LRU cache with single-flight fetches and no persistent plaintext."""

    _instance: ExternalSecretCache | None = None
    _instance_lock = threading.Lock()

    def __init__(self, max_entries: int = 512):
        self.max_entries = max(1, int(max_entries))
        self._entries: OrderedDict[Hashable, _CacheEntry] = OrderedDict()
        self._inflight: dict[Hashable, threading.Event] = {}
        self._generation = 0
        self._lock = threading.RLock()

    @classmethod
    def instance(cls) -> ExternalSecretCache:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._generation += 1

    def invalidate(self, key: Hashable) -> None:
        with self._lock:
            if self._entries.pop(key, None) is not None:
                self._generation += 1

    def freshness_token(self) -> int:
        """Change whenever cached plaintext may need downstream refresh."""
        now = time.monotonic()
        with self._lock:
            expired = [
                key for key, entry in self._entries.items()
                if entry.expires_at <= now
            ]
            if expired:
                for key in expired:
                    self._entries.pop(key, None)
                self._generation += 1
            return self._generation

    def get_or_fetch(self, key: Hashable, ttl_seconds: float,
                     fetch: Callable[[], ProviderValue]) -> ProviderValue:
        """Return a cached value or fetch it once across concurrent callers."""

        ttl = max(0.0, float(ttl_seconds))
        while True:
            now = time.monotonic()
            with self._lock:
                entry = self._entries.get(key)
                if entry is not None and entry.expires_at > now:
                    self._entries.move_to_end(key)
                    return entry.value
                if entry is not None:
                    self._entries.pop(key, None)
                event = self._inflight.get(key)
                if event is None:
                    event = threading.Event()
                    self._inflight[key] = event
                    leader = True
                else:
                    leader = False
            if leader:
                break
            event.wait()

        try:
            value = fetch()
            if not isinstance(value, ProviderValue):
                raise TypeError("secret provider fetch must return ProviderValue")
            effective_ttl = ttl if value.ttl_seconds is None else max(
                0.0, float(value.ttl_seconds))
            with self._lock:
                # Downstream resolved-value caches include this token. A
                # zero-TTL fetch must advance it too, otherwise they could
                # retain that plaintext indefinitely.
                self._generation += 1
                if effective_ttl > 0:
                    self._entries[key] = _CacheEntry(
                        value=value, expires_at=time.monotonic() + effective_ttl)
                    self._entries.move_to_end(key)
                    while len(self._entries) > self.max_entries:
                        self._entries.popitem(last=False)
            return value
        finally:
            with self._lock:
                done = self._inflight.pop(key, None)
                if done is not None:
                    done.set()
