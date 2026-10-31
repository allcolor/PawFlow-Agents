"""Run a read_history transcript scan in a child interpreter.

A search without a current full-text index decodes the JSON rows of every
candidate segment. In the server process that work holds the GIL, and every
unrelated thread waits for it: relay sockets, the UI, the agent poller, the
tools of other agents. On the 2.3 M-row Ultima7 transcript searches held 57 to
78 % of the GIL samples and an unrelated ``edit`` took 21.8 s (2026-10-31).
A child process has its own GIL, so the server thread that serves the search
only waits on a pipe. The search itself is no faster; nothing else is slower.

Every search gets its own child, with no shared gate and no deadline: two
searches are independent and neither waits for the other.
"""

import contextlib
import json
import os
import subprocess  # nosec B404 -- fixed interpreter + module argv, no shell
import sys
from pathlib import Path
from typing import Any, Dict, List

from core._conversation_store_transcript import _CsTranscriptMixin
from core.segmented_jsonl import SegmentedJsonl

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = "core.handlers._history_search_process"
#: The child yields the CPU to the server when both want it.
_NICE = 10


def run_search_process(request: Dict[str, Any]) -> str:
    """Answer one search in a child process and return its rendered text.

    ``request`` is the store's ``history_search_snapshot`` plus
    ``conversation_id``, ``query``, ``role_filter``, ``agent_filter``,
    ``offset`` and ``limit``. Raises ``RuntimeError`` when the child fails.
    """
    result = subprocess.run(  # nosec B603 -- fixed interpreter + module argv
        [sys.executable, "-m", _MODULE],
        input=json.dumps(request), capture_output=True, text=True,
        cwd=str(_ROOT), check=False)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or not lines:
        detail = (result.stderr.strip().splitlines() or ["no output"])[-1]
        raise RuntimeError(
            f"history search process exited {result.returncode}: {detail}")
    return json.loads(lines[-1])["text"]


class _SnapshotLog(SegmentedJsonl):
    """The transcript as the server saw it when the search was asked.

    The server flushed its buffered appends and measured the segment display
    counts under the conversation lock; the child reuses both instead of
    touching the index the server owns.
    """

    def __init__(self, flat_path: str, paths: List[str],
                 role_rows: Dict[str, int]):
        super().__init__(Path(flat_path))
        self._paths = [Path(path) for path in paths]
        self._role_rows = {Path(path): int(rows)
                           for path, rows in role_rows.items()}

    def iter_paths(self) -> List[Path]:
        return list(self._paths)

    def role_rows_by_path(self) -> Dict[Path, int]:
        return dict(self._role_rows)


class _SnapshotStore(_CsTranscriptMixin):
    """The transcript readers of ConversationStore over one snapshot."""

    def __init__(self, log: _SnapshotLog):
        self._log = log

    def exists(self, cid: str) -> bool:
        return True

    def _transcript_log(self, cid: str) -> _SnapshotLog:
        return self._log

    def _get_conv_lock(self, cid: str):
        return contextlib.nullcontext()


def answer(request: Dict[str, Any]) -> str:
    """The child's work: the handler's own scan over the snapshot."""
    from core.handlers.history import ReadHistoryHandler

    store = _SnapshotStore(_SnapshotLog(
        request["transcript_path"], request["paths"], request["role_rows"]))
    handler = ReadHistoryHandler()
    handler.set_conversation_id(request["conversation_id"])
    return handler._scan_search(
        store, request["query"], request["role_filter"],
        request["agent_filter"], int(request["offset"]),
        int(request["limit"]))


def main() -> None:
    with contextlib.suppress(OSError):
        os.nice(_NICE)
    text = answer(json.loads(sys.stdin.read()))
    sys.stdout.write(json.dumps({"text": text}) + "\n")


if __name__ == "__main__":
    main()
