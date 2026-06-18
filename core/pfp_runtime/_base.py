"""Shared base types/constants for the package runtime (errors, format markers).

Split out of core/pfp_runtime.py for the <=800-line rule; re-exported from
core.pfp_runtime (invariant 1: import-path stability).
"""

from __future__ import annotations
import logging
import re

logger = logging.getLogger(__name__)


RUNTIME_INVOKE_FORMAT = "pawflow.package.runtime.invoke.v1"

RUNTIME_RESULT_FORMAT = "pawflow.package.runtime.result.v1"

HOST_CALL_FORMAT = "pawflow.package.runtime.host_call.v1"

def _safe_cache_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@+-]", "_", str(value or "")) or "package"

class PackageRuntimeError(RuntimeError):
    """Raised when a PFP runtime object cannot be safely prepared."""
