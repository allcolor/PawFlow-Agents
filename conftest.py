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

import platform
import sys


def _strip_pylib_on_non_linux() -> None:
    """Remove `.pylib` variants from sys.path outside Linux."""
    if platform.system() == "Linux":
        return
    sys.path[:] = [
        p for p in sys.path
        if not (p.endswith(".pylib") or p.endswith(".pylib\\")
                or p.endswith(".pylib/"))
    ]


_strip_pylib_on_non_linux()
