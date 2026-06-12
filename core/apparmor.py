"""Resolve the AppArmor profile for provider-pool containers.

The ``pawflow-mount`` profile (``docker/apparmor/pawflow-mount``) confines
pool containers to the single mount they need: the per-user session-slot
bind ``/cc_sessions_host/** -> /cc_sessions/``. Hosts that have not loaded
the profile keep the previous behaviour (``apparmor:unconfined``), so
existing installs are unaffected until the operator runs
``apparmor_parser -r -W docker/apparmor/pawflow-mount``.

The choice is made once per process: a throwaway probe container is
started under the profile (the server may itself run in a container where
the host's securityfs is not visible, so asking Docker is the only
reliable check). ``PAWFLOW_APPARMOR_PROFILE`` overrides the detection
with a verbatim profile name (e.g. ``unconfined`` or a custom profile).
"""

import logging
import os
import subprocess
import threading
from typing import List, Optional

logger = logging.getLogger(__name__)

POOL_PROFILE = "pawflow-mount"

_lock = threading.Lock()
_resolved: Optional[str] = None


def apparmor_security_opts(probe_image: str) -> List[str]:
    """Return ``["--security-opt", "apparmor=<profile>"]`` for pool containers."""
    return ["--security-opt", f"apparmor={_resolve(probe_image)}"]


def _resolve(probe_image: str) -> str:
    global _resolved
    forced = os.environ.get("PAWFLOW_APPARMOR_PROFILE", "").strip()
    if forced:
        return forced
    with _lock:
        if _resolved is None:
            if _profile_usable(probe_image):
                _resolved = POOL_PROFILE
                logger.info(
                    "Pool containers confined with AppArmor profile '%s'",
                    POOL_PROFILE)
            else:
                _resolved = "unconfined"
                logger.warning(
                    "AppArmor profile '%s' is not usable on the Docker host; "
                    "pool containers fall back to apparmor:unconfined. Load "
                    "docker/apparmor/pawflow-mount with apparmor_parser to "
                    "confine them.", POOL_PROFILE)
        return _resolved


def _profile_usable(probe_image: str) -> bool:
    """True when Docker can start a container under the pool profile."""
    from core.docker_utils import docker_cmd
    try:
        result = subprocess.run(  # nosec B603
            docker_cmd() + [
                "run", "--rm",
                "--security-opt", f"apparmor={POOL_PROFILE}",
                "--entrypoint", "/bin/true", probe_image,
            ],
            capture_output=True, text=True, timeout=60)
    except Exception as exc:
        logger.warning("AppArmor probe could not run: %s", exc)
        return False
    if result.returncode != 0:
        logger.info("AppArmor probe rejected profile '%s': %s",
                    POOL_PROFILE, result.stderr.strip()[:300])
    return result.returncode == 0


def _reset_for_tests() -> None:
    global _resolved
    with _lock:
        _resolved = None
