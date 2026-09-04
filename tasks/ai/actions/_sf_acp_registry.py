"""service_flow branch: import agents from the public ACP registry.

WP-R2 of ``docs/ACP_REGISTRY_ANTIGRAVITY_PLAN.md``. The Resources > Services
form gets an **Import from ACP registry** action for provider ``acp``: the
user picks a registry entry and a distribution, the server resolves it into
``acp`` form values (``core/acp/registry.py``), the form is filled, and saving
the service is the normal install/edit submit. Nothing is created behind the
user's back and nothing is auto-upgraded.

Actions:

- ``acp_registry_catalogue``: catalogue rows (no download URLs), the platform
  of this server, and which package runners (``npx``/``uvx``) exist here.
- ``acp_registry_prepare``: one entry + distribution -> form values. Package
  distributions answer at once; a ``binary`` distribution is downloaded and
  verified in a background job.
- ``acp_registry_prepare_status``: poll a binary job.
- ``acp_registry_check_update``: compare a service's pinned import with the
  registry and report; never changes the service.

Imported agents launch where the ``acp`` provider launches them (the PawFlow
server process), so runner availability is checked on this host. Any user who
can create an ``acp`` service can already type an arbitrary ``acp_command``;
these actions add no capability beyond that.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import uuid
from typing import Any

from core.acp import registry as acp_registry
from tasks.ai.actions._sf_base import (
    _UNHANDLED,
    _resolve_service_definition_for_action,
)

logger = logging.getLogger(__name__)

ACTIONS = frozenset({
    "acp_registry_catalogue",
    "acp_registry_prepare",
    "acp_registry_prepare_status",
    "acp_registry_check_update",
})
PACKAGE_KINDS = ("npx", "uvx")

#: Binary download jobs, newest last. Bounded: a job is a few hundred bytes
#: and only exists so the UI can poll a download that may take minutes.
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.RLock()
_MAX_JOBS = 32


def _reply(flowfile, payload: dict) -> list:
    flowfile.set_content(json.dumps(payload).encode())
    return [flowfile]


def _runners() -> dict[str, bool]:
    return {kind: bool(shutil.which(kind)) for kind in PACKAGE_KINDS}


def _form_values(config: dict[str, Any]) -> dict[str, Any]:
    """Service config -> values for the schema form.

    The ``acp_args`` / ``acp_env`` / ``acp_registry`` fields are string fields
    holding JSON (``services/llm_connection.py`` schema), so the form receives
    JSON text, not objects.
    """
    values = dict(config)
    values["acp_args"] = json.dumps(list(config.get("acp_args") or []))
    values["acp_env"] = json.dumps(dict(config.get("acp_env") or {}), sort_keys=True)
    values["acp_registry"] = json.dumps(config.get("acp_registry") or {}, sort_keys=True)
    return values


def _default_cwd(agent_id: str) -> str:
    path = acp_registry.agents_dir() / agent_id / "workspace"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _catalogue(body: dict) -> dict:
    catalogue = acp_registry.load_catalogue(refresh=bool(body.get("refresh")))
    try:
        platform = acp_registry.host_platform()
    except acp_registry.RegistryError as exc:
        logger.warning("[acp-registry] no binary platform for this host: %s", exc)
        platform = ""
    return {
        "ok": True,
        "entries": [entry.summary() for entry in catalogue.entries],
        "registry_version": catalogue.registry_version,
        "fetched_at": catalogue.fetched_at,
        "stale": catalogue.stale,
        "platform": platform,
        "runners": _runners(),
    }


def _remember_job(job_id: str, job: dict) -> None:
    with _JOBS_LOCK:
        while len(_JOBS) >= _MAX_JOBS:
            _JOBS.pop(next(iter(_JOBS)))
        _JOBS[job_id] = job


def _prepare(body: dict) -> dict:
    agent_id = str(body.get("agent_id") or "").strip()
    distribution = str(body.get("distribution") or "").strip()
    if not agent_id or not distribution:
        raise ValueError("agent_id and distribution are required")
    catalogue = acp_registry.load_catalogue()
    entry = catalogue.get(agent_id)
    cwd = str(body.get("cwd") or "").strip() or _default_cwd(agent_id)

    if distribution in PACKAGE_KINDS:
        # Resolve the runner on this host at call time (monkeypatch-friendly,
        # and PATH may change between server restarts).
        config = acp_registry.service_config_for_package(
            entry, distribution, cwd=cwd, which=shutil.which)
        return {"ok": True, "status": "ready", "config": _form_values(config),
                "stale": catalogue.stale}
    if distribution != "binary":
        raise ValueError(f"unknown distribution: {distribution!r}")
    if entry.quarantined:
        raise acp_registry.RegistryError(
            f"{entry.id} is quarantined by the registry: {entry.quarantine_reason}")
    platform = acp_registry.host_platform()
    if entry.binary_for(platform) is None:
        raise acp_registry.RegistryError(
            f"{entry.id} {entry.version} has no binary for {platform}")

    job_id = uuid.uuid4().hex[:12]
    job: dict[str, Any] = {"status": "pending", "agent_id": agent_id,
                           "version": entry.version, "platform": platform}
    _remember_job(job_id, job)

    def _run() -> None:
        try:
            done = acp_registry.materialise_binary(entry, platform)
            config = acp_registry.service_config_for_binary(entry, done, cwd=cwd)
            job.update(status="ready", config=_form_values(config),
                       verified=done.verified)
        except Exception as exc:  # reported to the poller, never raised in a thread
            logger.warning("[acp-registry] %s binary import failed: %s", agent_id, exc)
            job.update(status="error", error=str(exc))

    threading.Thread(target=_run, daemon=True, name=f"acp-registry-{job_id}").start()
    return {"ok": True, "status": "pending", "job_id": job_id}


def _prepare_status(body: dict) -> dict:
    job_id = str(body.get("job_id") or "").strip()
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise ValueError(f"unknown ACP registry job: {job_id!r}")
    return {"ok": True, "job_id": job_id, **job}


def _check_update(body: dict, user_id: str) -> dict:
    service_id = str(body.get("service_id") or "").strip()
    if not service_id:
        raise ValueError("Missing service_id")
    sdef = _resolve_service_definition_for_action(
        service_id, user_id, body.get("conversation_id", ""), body.get("scope", ""))
    if not sdef:
        raise ValueError(f"Service '{service_id}' not found")
    config = getattr(sdef, "config", {}) or {}
    report = acp_registry.check_update(config, acp_registry.load_catalogue())
    if report["quarantined"]:
        message = (f"{report['id']} {report['pinned']} is quarantined by the registry: "
                   f"{report['quarantine_reason']}")
    elif report["update_available"]:
        message = (f"{report['id']}: registry lists {report['latest']}, this service pins "
                  f"{report['pinned']}. Re-import to upgrade.")
    else:
        message = f"{report['id']} {report['pinned']} is the latest registry version."
    if report["stale_catalogue"]:
        message += " (catalogue served from cache)"
    return {"ok": True, "message": message, **report}


def _handle_sf_acp_registry(self, action, body, store, user_id, flowfile, _helpers):
    """service_flow cluster for the ACP registry. Returns result or _UNHANDLED."""
    del self, store, _helpers
    if action not in ACTIONS:
        return _UNHANDLED
    body = body if isinstance(body, dict) else {}
    try:
        if action == "acp_registry_catalogue":
            payload = _catalogue(body)
        elif action == "acp_registry_prepare":
            payload = _prepare(body)
        elif action == "acp_registry_prepare_status":
            payload = _prepare_status(body)
        else:
            payload = _check_update(body, user_id)
    except ValueError as exc:  # RegistryError and RegistryUnavailable are ValueErrors
        payload = {"error": str(exc)}
    except Exception as exc:
        logger.exception("[acp-registry] %s failed", action)
        payload = {"error": f"ACP registry action failed: {exc}"}
    return _reply(flowfile, payload)


__all__ = ["ACTIONS", "_handle_sf_acp_registry"]
