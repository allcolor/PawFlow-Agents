"""
Signal Registry - intra-process synchronization for Wait/Notify.

Thread-safe singleton. Signals have a counter and an optional value.
"""

from __future__ import annotations
from typing import ClassVar, Optional, Dict, Any
import threading
from datetime import datetime


class SignalRegistry:
    """Signal registry for Wait/Notify synchronization."""

    _instance: ClassVar[Optional['SignalRegistry']] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self):
        self._signals: Dict[str, Dict[str, Any]] = {}
        self._signal_lock = threading.Lock()
        self._events: Dict[str, threading.Event] = {}

    @classmethod
    def get_instance(cls) -> 'SignalRegistry':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def notify(self, signal_id: str, value: str = "", delta: int = 1) -> Dict[str, Any]:
        """
        Increment a signal (called by NotifyTask).

        Args:
            signal_id: Signal identifier
            value: Optional value associated with the signal
            delta: Nombre a ajouter au compteur (default 1)

        Returns:
            Etat du signal apres notification
        """
        with self._signal_lock:
            if signal_id not in self._signals:
                self._signals[signal_id] = {
                    "count": 0,
                    "value": "",
                    "created": datetime.now().isoformat(),
                    "updated": datetime.now().isoformat(),
                }

            sig = self._signals[signal_id]
            sig["count"] += delta
            if value:
                sig["value"] = value
            sig["updated"] = datetime.now().isoformat()

            # Wake up any waiting threads
            if signal_id in self._events:
                self._events[signal_id].set()

            return dict(sig)

    def check(self, signal_id: str, target_count: int = 1) -> bool:
        """
        Check whether a signal has reached the threshold (called by WaitTask).

        Args:
            signal_id: Signal identifier
            target_count: Required notification countes

        Returns:
            True si le compteur >= target_count
        """
        with self._signal_lock:
            sig = self._signals.get(signal_id)
            if sig is None:
                return False
            return sig["count"] >= target_count

    def wait_for(self, signal_id: str, target_count: int = 1,
                 timeout: float = 30.0) -> bool:
        """
        Wait for a signal to reach the threshold (blocking).

        Args:
            signal_id: Signal identifier
            target_count: Required notification countes
            timeout: Timeout en secondes

        Returns:
            True si le signal a ete recu, False si timeout
        """
        # Check immediately
        if self.check(signal_id, target_count):
            return True

        # Create event for this signal
        with self._signal_lock:
            if signal_id not in self._events:
                self._events[signal_id] = threading.Event()
            event = self._events[signal_id]
            event.clear()

        # Wait with polling (to handle incremental notifications)
        elapsed = 0.0
        poll_interval = 0.5
        while elapsed < timeout:
            event.wait(timeout=poll_interval)
            if self.check(signal_id, target_count):
                return True
            elapsed += poll_interval

        return False

    def get_signal(self, signal_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a signal state."""
        with self._signal_lock:
            sig = self._signals.get(signal_id)
            return dict(sig) if sig else None

    def get_value(self, signal_id: str) -> Optional[str]:
        """Retrieve a signal value."""
        with self._signal_lock:
            sig = self._signals.get(signal_id)
            return sig["value"] if sig else None

    def clear(self, signal_id: str):
        """Effacer un signal."""
        with self._signal_lock:
            self._signals.pop(signal_id, None)
            self._events.pop(signal_id, None)

    def clear_all(self):
        """Clear all signals."""
        with self._signal_lock:
            self._signals.clear()
            self._events.clear()

    def list_signals(self) -> Dict[str, Dict[str, Any]]:
        """List all active signals."""
        with self._signal_lock:
            return {k: dict(v) for k, v in self._signals.items()}
