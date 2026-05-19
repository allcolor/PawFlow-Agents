"""Phase 9 — startup security report.

Gathers a snapshot of the security-relevant configuration at boot and
logs it. In production mode (PAWFLOW_ENV=production or
PAWFLOW_PUBLIC_MODE=true) we additionally REJECT the boot when a
critical setting is unsafe — weak default credentials, fail-open
approval, missing master secret key in env, etc.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger("pawflow.security")


def is_production() -> bool:
    """Return True when this boot should apply production-grade checks.
    Reads the env vars exactly once per call."""
    if (os.environ.get("PAWFLOW_ENV", "").strip().lower()
            == "production"):
        return True
    if (os.environ.get("PAWFLOW_PUBLIC_MODE", "").strip().lower()
            in ("1", "true", "yes")):
        return True
    return False


@dataclass
class SecurityReport:
    production: bool = False
    secret_key_source: str = ""
    approval_fail_open: bool = False
    capability_store_present: bool = False
    private_gateway_enabled: bool = False
    auth_enabled: bool = False
    listener_bind: str = ""
    fatal_errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def render(self) -> List[str]:
        """Return the report as a list of human-readable log lines
        suitable for `logger.info` line-by-line."""
        lines = [
            "==== PawFlow security report ====",
            f"  production mode  : {'YES' if self.production else 'no'}",
            f"  secret key       : {self.secret_key_source}",
            f"  approval policy  : "
            f"{'FAIL-OPEN (dev)' if self.approval_fail_open else 'fail-closed'}",
            f"  capability store : "
            f"{'present' if self.capability_store_present else 'missing'}",
            f"  private gateway  : "
            f"{'enabled' if self.private_gateway_enabled else 'disabled'}",
            f"  auth enabled     : "
            f"{'yes' if self.auth_enabled else 'no'}",
            f"  listener bind    : {self.listener_bind or '(unknown)'}",
        ]
        for w in self.warnings:
            lines.append(f"  warning          : {w}")
        for e in self.fatal_errors:
            lines.append(f"  FATAL            : {e}")
        lines.append("=================================")
        return lines


def _resolve_secret_key_source() -> str:
    if os.environ.get("PAWFLOW_SECRET_KEY_B64"):
        return "PAWFLOW_SECRET_KEY_B64 (env, raw 32 bytes)"
    if os.environ.get("PAWFLOW_SECRET_KEY"):
        return "PAWFLOW_SECRET_KEY (env, scrypt-derived)"
    return "data/config/secret.key (on-disk fallback)"


def _approval_fail_open() -> bool:
    return os.environ.get("PAWFLOW_APPROVAL_FAIL_OPEN", "").lower() in (
        "1", "true", "yes")


def _capability_store_present() -> bool:
    try:
        from core.paths import CAPABILITIES_FILE
        return CAPABILITIES_FILE.exists()
    except Exception:
        return False


def _private_gateway_enabled() -> bool:
    """Best-effort: report whether any global privateGateway service is enabled."""
    try:
        from services.private_gateway import PrivateGateway
        return bool(PrivateGateway.is_enabled_static())
    except Exception:
        return False


def _auth_enabled() -> bool:
    try:
        from core.security import SecurityManager
        sm = SecurityManager.get_instance()
        return getattr(sm, "_initialized", True) is not False
    except Exception:
        return False


def _listener_bind() -> str:
    """Best-effort: read the http listener config from the global
    service registry. Returns 'host:port' if available."""
    try:
        from core.service_registry import ServiceRegistry
        sr = ServiceRegistry.get_instance()
        for sid, sdef in sr.get_all("global", "").items():
            if getattr(sdef, "service_type", "") == "httpListener":
                inst = sr.get_live_instance("global", "", sid)
                host = getattr(inst, "_host", "") if inst else ""
                port = getattr(inst, "_port", "") if inst else ""
                if host or port:
                    return f"{host or '0.0.0.0'}:{port}"  # nosec B104 - reporting display fallback, not a bind.
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    return ""


def build_report() -> SecurityReport:
    rep = SecurityReport(
        production=is_production(),
        secret_key_source=_resolve_secret_key_source(),
        approval_fail_open=_approval_fail_open(),
        capability_store_present=_capability_store_present(),
        private_gateway_enabled=_private_gateway_enabled(),
        auth_enabled=_auth_enabled(),
        listener_bind=_listener_bind(),
    )
    if rep.production:
        # Production must not run with the on-disk fallback key.
        if rep.secret_key_source.startswith("data/config/secret.key"):
            rep.fatal_errors.append(
                "production mode requires PAWFLOW_SECRET_KEY_B64 "
                "(or PAWFLOW_SECRET_KEY) to be set — the on-disk "
                "fallback key is dev-only.")
        # Production must not run with fail-open approval.
        if rep.approval_fail_open:
            rep.fatal_errors.append(
                "PAWFLOW_APPROVAL_FAIL_OPEN must NOT be set in "
                "production — unset it or use the dev environment.")
        # Production should bind to a real interface, not 0.0.0.0
        # without an explicit warning. Public deployments behind a
        # reverse proxy / private gateway need to be aware.
        if rep.listener_bind and rep.listener_bind.startswith("0.0.0.0:"):
            rep.warnings.append(
                "HTTP listener bound to 0.0.0.0 — ensure the host "
                "firewall / private gateway restrict who can reach it.")
    else:
        if not rep.capability_store_present:
            rep.warnings.append(
                "capability store file missing — cli.py should call "
                "capability_auth.init_db() at boot before this report.")
    return rep


def enforce(rep: SecurityReport) -> None:
    """Log the report and abort the boot if any fatal error was
    flagged in production mode. Tests can call build_report() directly
    and inspect the result without going through enforce().
    """
    for line in rep.render():
        logger.info(line)
    if rep.fatal_errors:
        msg = (
            "PawFlow boot blocked by security report ("
            f"{len(rep.fatal_errors)} fatal error(s)). See the lines "
            "above marked FATAL.")
        logger.error(msg)
        raise SystemExit(msg)
