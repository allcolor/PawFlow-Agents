"""Resolve AppArmor profiles for PawFlow-spawned containers.

Two profiles ship in ``docker/apparmor/``:

* ``pawflow-mount`` — provider-pool containers. Confines them to the single
  mount they need: the per-user session-slot bind
  ``/cc_sessions_host/** -> /cc_sessions/``.
* ``pawflow-relay`` — relay containers. Allows only the FUSE mounts the
  relay legitimately creates (combined server-fs at ``/tmp/pf_combined_fs``,
  rclone remotes under ``/remote``) and keeps every other mount denied.

Hosts that have not loaded a profile keep the previous behaviour
(``apparmor:unconfined``), so existing installs are unaffected until the
operator runs ``apparmor_parser -r -W docker/apparmor/<profile>``.

Each choice is made once per process: a throwaway probe container is
started under the profile (the server may itself run in a container where
the host's securityfs is not visible, so asking Docker is the only
reliable check). ``PAWFLOW_APPARMOR_PROFILE`` (pools) and
``PAWFLOW_RELAY_APPARMOR_PROFILE`` (relays) override the detection with a
verbatim profile name (e.g. ``unconfined`` or a custom profile).
"""

import logging
import os
import subprocess  # nosec B404
import threading
from typing import Dict, List

logger = logging.getLogger(__name__)

POOL_PROFILE = "pawflow-mount"
RELAY_PROFILE = "pawflow-relay"

_lock = threading.Lock()
_resolved: Dict[str, str] = {}


def apparmor_security_opts(probe_image: str) -> List[str]:
    """Return ``["--security-opt", "apparmor=<profile>"]`` for pool containers."""
    profile = _resolve(POOL_PROFILE, "PAWFLOW_APPARMOR_PROFILE",
                       probe_image, "Pool")
    return ["--security-opt", f"apparmor={profile}"]


def relay_apparmor_security_opts(probe_image: str) -> List[str]:
    """Return ``["--security-opt", "apparmor=<profile>"]`` for relay containers."""
    profile = _resolve(RELAY_PROFILE, "PAWFLOW_RELAY_APPARMOR_PROFILE",
                       probe_image, "Relay")
    return ["--security-opt", f"apparmor={profile}"]


def _resolve(profile: str, env_key: str, probe_image: str, what: str) -> str:
    forced = os.environ.get(env_key, "").strip()
    if forced:
        return forced
    with _lock:
        if profile not in _resolved:
            if _profile_usable(probe_image, profile):
                _resolved[profile] = profile
                logger.info(
                    "%s containers confined with AppArmor profile '%s'",
                    what, profile)
            else:
                _resolved[profile] = "unconfined"
                logger.warning(
                    "AppArmor profile '%s' is not usable on the Docker host; "
                    "%s containers fall back to apparmor:unconfined. Load "
                    "docker/apparmor/%s with apparmor_parser to confine "
                    "them.", profile, what.lower(), profile)
        return _resolved[profile]


def _profile_usable(probe_image: str, profile: str = POOL_PROFILE) -> bool:
    """True when Docker can start a container under the given profile."""
    from core.docker_utils import docker_cmd
    try:
        result = subprocess.run(  # nosec B603
            docker_cmd() + [
                "run", "--rm",
                "--security-opt", f"apparmor={profile}",
                "--entrypoint", "/bin/true", probe_image,
            ],
            capture_output=True, text=True, timeout=60)
    except Exception as exc:
        logger.warning("AppArmor probe could not run: %s", exc)
        return False
    if result.returncode != 0:
        logger.info("AppArmor probe rejected profile '%s': %s",
                    profile, result.stderr.strip()[:300])
    return result.returncode == 0


def _reset_for_tests() -> None:
    with _lock:
        _resolved.clear()
