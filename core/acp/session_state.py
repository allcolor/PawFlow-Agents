"""Generation-fenced events shared by ACP loop and provider threads."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal

AcpEventKind = Literal[
    "update",
    "response",
    "error",
    "cancelled",
    "process_exit",
]


@dataclass(frozen=True)
class AcpSessionEvent:
    """One ordered update or terminal signal from an ACP connection."""

    sequence: int
    generation: int
    session_id: str
    kind: AcpEventKind
    payload: Any = None
    error: BaseException | None = None

    @property
    def terminal(self) -> bool:
        return self.kind != "update"


class AcpEventChannel:
    """Nonblocking bounded updates with a reserved terminal delivery path."""

    def __init__(self, max_updates: int = 256) -> None:
        if max_updates <= 0:
            raise ValueError("max_updates must be positive")
        self._max_updates = max_updates
        self._updates: deque[AcpSessionEvent] = deque()
        self._terminals: deque[AcpSessionEvent] = deque()
        self._condition = threading.Condition()
        self._sequence = 0
        self._dropped_updates = 0

    @property
    def dropped_updates(self) -> int:
        with self._condition:
            return self._dropped_updates

    def publish_update(
        self,
        generation: int,
        session_id: str,
        update: Any,
    ) -> AcpSessionEvent:
        with self._condition:
            event = self._new_event(
                generation=generation,
                session_id=session_id,
                kind="update",
                payload=update,
            )
            if len(self._updates) == self._max_updates:
                self._updates.popleft()
                self._dropped_updates += 1
            self._updates.append(event)
            self._condition.notify()
            return event

    def publish_terminal(
        self,
        generation: int,
        session_id: str,
        kind: AcpEventKind,
        *,
        payload: Any = None,
        error: BaseException | None = None,
    ) -> AcpSessionEvent:
        if kind == "update":
            raise ValueError("terminal event kind cannot be update")
        with self._condition:
            event = self._new_event(
                generation=generation,
                session_id=session_id,
                kind=kind,
                payload=payload,
                error=error,
            )
            self._terminals.append(event)
            self._condition.notify()
            return event

    def get(self, timeout: float | None = None) -> AcpSessionEvent:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._updates and not self._terminals:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("timed out waiting for ACP event")
                self._condition.wait(remaining)

            if not self._updates:
                return self._terminals.popleft()
            if not self._terminals:
                return self._updates.popleft()
            if self._updates[0].sequence < self._terminals[0].sequence:
                return self._updates.popleft()
            return self._terminals.popleft()

    def drain(self) -> list[AcpSessionEvent]:
        events: list[AcpSessionEvent] = []
        with self._condition:
            while self._updates or self._terminals:
                if not self._updates:
                    events.append(self._terminals.popleft())
                elif (
                    not self._terminals
                    or self._updates[0].sequence < self._terminals[0].sequence
                ):
                    events.append(self._updates.popleft())
                else:
                    events.append(self._terminals.popleft())
        return events

    def _new_event(
        self,
        *,
        generation: int,
        session_id: str,
        kind: AcpEventKind,
        payload: Any = None,
        error: BaseException | None = None,
    ) -> AcpSessionEvent:
        self._sequence += 1
        return AcpSessionEvent(
            sequence=self._sequence,
            generation=generation,
            session_id=session_id,
            kind=kind,
            payload=payload,
            error=error,
        )
