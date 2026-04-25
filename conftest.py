"""Root conftest.

Sole responsibility: strip `.pylib` from sys.path on non-Linux hosts.

The repo ships a vendored `.pylib/` dir with pinned deps (pyarrow,
fastavro, tiktoken, ...) pre-built for Linux x86_64 — used by the
Docker relay container which mounts it as part of PYTHONPATH at
runtime (see docker/relay-dev/Dockerfile). pyproject.toml's pytest
`pythonpath = [".", ".pylib"]` prepends it during test collection so
the same paths resolve inside the container.

On Windows (dev host), those Linux `.so` files shadow the host's
site-packages copies and pytest crashes on the first import of any
affected package. This root conftest runs BEFORE any collection and
removes the offending entry when the current interpreter isn't Linux.
"""

from __future__ import annotations

import sys


def _strip_pylib_on_non_linux() -> None:
    """Remove `.pylib` variants from sys.path outside Linux.

    Uses `sys.platform` (a constant string baked at interpreter
    startup) rather than `platform.system()`. On Python 3.14 Windows,
    `platform.system()` triggers `_win32_ver()` → `_wmi_query()`,
    which hangs indefinitely when WMI misbehaves (observed during
    pytest collection: every test invocation stalled the import of
    this conftest forever). `sys.platform` has no syscall — instant.
    """
    if sys.platform.startswith("linux"):
        return
    sys.path[:] = [
        p for p in sys.path
        if not (p.endswith(".pylib") or p.endswith(".pylib\\")
                or p.endswith(".pylib/"))
    ]


_strip_pylib_on_non_linux()


# Keep the LiveSessionRegistry singleton clean between tests: any test
# that hits _stream_claude_code with a keep-alive-eligible mock proc
# would otherwise register a fake session that a LATER test for the
# same (user, conv, agent) would adopt on reuse, producing spurious
# "Turn1 not found" failures. The registry doesn't own any resources
# worth preserving across pytest tests.
import pytest


@pytest.fixture(autouse=True)
def _clear_cc_live_registry():
    yield
    try:
        from core.cc_live_registry import LiveSessionRegistry
        reg = LiveSessionRegistry.instance()
        # No-op killer: mock procs don't need real teardown, we just
        # want the dict cleared.
        reg.shutdown_all(killer=lambda _p: None)
        reg._sweeper_stop.clear()
        reg._sweeper_started = False
    except Exception:
        pass
