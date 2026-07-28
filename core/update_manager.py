"""Update manager — version inventory and agent-CLI image rebuild.

Two levels, deliberately separated:

* **Inventory (read-only).** :func:`check_updates` reports, per component, the
  version that is installed versus the version that is published. It starts no
  container that outlives the call, writes nothing, and is safe to run from the
  admin gear menu at any time.
* **CLI tools image rebuild.** :func:`rebuild_cli_image` rebuilds the image that
  hosts the agent CLIs (Claude Code, Codex, Gemini, Antigravity). It touches
  neither the server process nor any relay nor any user data — only the *next*
  CLI pool spawn sees the new image.
* **Relay images.** :func:`rebuild_relay_image` rebuilds the workspace and
  minimal relay images from the sources shipped in the server image, and
  :func:`restart_server_relays` moves the running relay containers onto the
  result. The restart replaces containers only: workspaces, volumes and relay
  identities are preserved by :meth:`ServerRelayManager.recreate`.

Version signals per component:

* server        — ``core.__version__`` versus the latest published GitHub release.
* relay images  — ``relay_image_version`` from the image catalog versus the tags
  actually present in the local Docker daemon.
* CLI tools     — the versions recorded inside the image at build time (see
  ``CLI_VERSIONS_PATH``) versus the npm registry's ``latest``.

Antigravity is installed from an unversioned install script, so it has no
published version to compare against: it is reported as unpinned, and the only
way to refresh it is a forced rebuild (``force=True`` → ``docker build
--no-cache``), which is exactly why forcing exists.

All Docker invocations go through :func:`core.docker_utils.docker_cmd` so the
Windows ``wsl docker`` path keeps working.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess  # nosec B404
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.docker_utils import docker_cmd

logger = logging.getLogger(__name__)

APP_ROOT = Path(__file__).resolve().parents[1]

GITHUB_RELEASE_URL = "https://api.github.com/repos/allcolor/PawFlow-Agents/releases/latest"
NPM_REGISTRY_URL = "https://registry.npmjs.org/{package}/latest"
HTTP_TIMEOUT = 15

DEFAULT_CLI_IMAGE = "pawflow-claude-code:latest"
CLI_BUILD_CONTEXT = APP_ROOT / "docker" / "claude-code"

#: Versions of the installed CLIs, written into the image at build time by the
#: Dockerfile. Read back with a throwaway ``docker run --rm``.
CLI_VERSIONS_PATH = "/opt/pawflow/cli_versions.json"

#: Agent CLIs pinned by npm version. ``build_arg`` must match the ``ARG`` names
#: in ``docker/claude-code/Dockerfile`` and the specs in
#: ``docker/claude-code/build.sh`` — ``tests/test_update_manager.py`` pins that.
CLI_PACKAGES = (
    {"key": "claude", "label": "Claude Code", "package": "@anthropic-ai/claude-code",
     "build_arg": "CLAUDE_CODE_VERSION"},
    {"key": "codex", "label": "Codex", "package": "@openai/codex",
     "build_arg": "CODEX_VERSION"},
    {"key": "gemini", "label": "Gemini CLI", "package": "@google/gemini-cli",
     "build_arg": "GEMINI_VERSION"},
)

#: Installed from ``antigravity.google/cli/install.sh``, which publishes no
#: version endpoint. Recorded in the image but never comparable.
UNPINNED_CLI_KEY = "antigravity"

RELAY_IMAGES = (
    {"key": "relay-dev", "repository": "ghcr.io/allcolor/pawflow-relay-dev"},
    {"key": "relay-minimal", "repository": "ghcr.io/allcolor/pawflow-relay-minimal"},
)

#: How each relay image is built from the sources shipped in the server image.
#: The build contexts mirror ``.github/workflows/docker-publish.yml`` — a local
#: rebuild must produce the same image the published one would be. ``param`` is
#: the ``global_parameters.json`` key that names the tag the relay manager
#: actually runs, so a rebuild always lands on the tag that will be spawned.
RELAY_BUILD_TARGETS = (
    {"key": "relay-dev", "label": "Workspace relay image",
     "param": "server_relay_image",
     "dockerfile": "docker/relay-dev/Dockerfile", "context": "."},
    {"key": "relay-minimal", "label": "Minimal relay image",
     "param": "server_relay_minimal_image",
     "profile": "server-minimal",
     "context": "docker/relay-generated/server-minimal"},
)

RELAY_IMAGE_GENERATOR = "scripts/generate-relay-image.py"


# ── helpers ──────────────────────────────────────────────────────────


def cli_image_name() -> str:
    """Name of the agent-CLI tools image, matching ``build.sh``."""
    return os.environ.get("PAWFLOW_CLI_LLM_IMAGE", "").strip() or DEFAULT_CLI_IMAGE


def _run(args: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603
        docker_cmd() + args, capture_output=True, text=True, timeout=timeout)


def _fetch_json(url: str) -> Optional[Dict[str, Any]]:
    try:
        import requests
        resp = requests.get(
            url, timeout=HTTP_TIMEOUT,
            headers={"User-Agent": "PawFlow-UpdateManager/1.0",
                     "Accept": "application/json"})
        if resp.status_code != 200:
            logger.debug("Update check %s returned HTTP %s", url, resp.status_code)
            return None
        return resp.json()
    except Exception:
        logger.debug("Update check failed for %s", url, exc_info=True)
        return None


def _is_newer(available: str, current: str) -> bool:
    """True when ``available`` is strictly newer than ``current``.

    Uses PEP 440 ordering when available so the release tag ``1.0.0-beta.35``
    and the packaged version ``1.0.0b35`` compare equal instead of looking like
    an update. Falls back to plain inequality when the versions are not
    parseable (npm pre-release tags, ``unknown`` markers).
    """
    if not available or not current:
        return False
    try:
        from packaging.version import InvalidVersion, parse
        try:
            return parse(available) > parse(current)
        except InvalidVersion:
            pass
    except Exception:
        logger.debug("packaging unavailable, comparing versions as strings", exc_info=True)
    return available != current


def _component(key: str, label: str, current: str, available: str,
               **extra: Any) -> Dict[str, Any]:
    entry = {
        "key": key,
        "label": label,
        "current": current,
        "available": available,
        "update_available": _is_newer(available, current),
    }
    entry.update(extra)
    return entry


# ── current (installed) versions ─────────────────────────────────────


def local_image_tags(repository: str) -> List[str]:
    """Tags of ``repository`` present in the local Docker daemon."""
    try:
        result = _run(["images", "--format", "{{.Tag}}", repository], timeout=15)
    except Exception:
        logger.debug("docker images failed for %s", repository, exc_info=True)
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines()
            if line.strip() and line.strip() != "<none>"]


def installed_cli_versions() -> Dict[str, str]:
    """CLI versions recorded inside the tools image at build time.

    Empty dict when the image is missing or predates the version stamp — the
    caller reports those components as not installed rather than guessing.
    """
    image = cli_image_name()
    try:
        # Check presence first: `docker run` on a missing local image would try
        # to pull it, and this image is never published to a registry.
        present = _run(["image", "inspect", image], timeout=15)
        if present.returncode != 0:
            return {}
        result = _run(["run", "--rm", "--entrypoint", "cat", image, CLI_VERSIONS_PATH],
                      timeout=60)
    except Exception:
        logger.debug("Could not read %s from %s", CLI_VERSIONS_PATH, image, exc_info=True)
        return {}
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def catalog_relay_version() -> str:
    """``relay_image_version`` from the shipped relay image catalog."""
    catalog = APP_ROOT / "config" / "relay_image_catalog.json"
    try:
        return str(json.loads(catalog.read_text(encoding="utf-8")).get("relay_image_version", ""))
    except Exception:
        logger.debug("Could not read relay image catalog", exc_info=True)
        return ""


def server_version() -> str:
    from core import __version__
    return str(__version__)


# ── published (available) versions ───────────────────────────────────


def latest_server_release() -> str:
    """Tag of the latest published GitHub release, without the ``v`` prefix."""
    data = _fetch_json(GITHUB_RELEASE_URL) or {}
    tag = str(data.get("tag_name", "") or "")
    return tag[1:] if tag.startswith("v") else tag


def latest_npm_version(package: str) -> str:
    """``latest`` version published on the npm registry, or empty on failure."""
    data = _fetch_json(NPM_REGISTRY_URL.format(package=package)) or {}
    return str(data.get("version", "") or "")


# ── inventory ────────────────────────────────────────────────────────


def check_updates() -> Dict[str, Any]:
    """Structured current-versus-available report for every component.

    Never raises: a component whose version cannot be resolved reports an empty
    string and ``update_available`` false, so the dialog degrades to "unknown"
    instead of failing whole.
    """
    components: List[Dict[str, Any]] = []

    components.append(_component(
        "server", "PawFlow server", server_version(), latest_server_release(),
        group="server"))

    catalog_version = catalog_relay_version()
    for image in RELAY_IMAGES:
        tags = local_image_tags(image["repository"])
        installed = catalog_version if catalog_version in tags else (tags[0] if tags else "")
        components.append(_component(
            image["key"], image["repository"].rsplit("/", 1)[-1],
            installed, catalog_version,
            group="relay", repository=image["repository"], local_tags=tags))

    installed_clis = installed_cli_versions()
    for cli in CLI_PACKAGES:
        components.append(_component(
            cli["key"], cli["label"],
            installed_clis.get(cli["key"], ""), latest_npm_version(cli["package"]),
            group="cli", package=cli["package"]))
    components.append(_component(
        UNPINNED_CLI_KEY, "Antigravity",
        installed_clis.get(UNPINNED_CLI_KEY, ""), "",
        group="cli", unpinned=True))

    return {
        "components": components,
        "cli_image": cli_image_name(),
        "update_count": sum(1 for c in components if c["update_available"]),
    }


# ── CLI tools image rebuild ──────────────────────────────────────────


def _resolved_build_args() -> List[str]:
    """``--build-arg`` pairs pinning each npm CLI to its latest version.

    The pinned version is part of the npm-install layer's cache key: unchanged
    version reuses the cache, new release invalidates it. A package whose
    version cannot be resolved falls back to ``latest`` — same behaviour as
    ``build.sh``, and the reason ``force`` exists.
    """
    args: List[str] = []
    for cli in CLI_PACKAGES:
        version = latest_npm_version(cli["package"]) or "latest"
        args.extend(["--build-arg", f"{cli['build_arg']}={version}"])
    return args


_build_lock = threading.Lock()


def cli_build_running() -> bool:
    """True while a CLI image rebuild is in progress."""
    return _build_lock.locked()


def rebuild_cli_image(force: bool = False,
                      on_output: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Rebuild the agent-CLI tools image. Blocking — call from a worker thread.

    ``force=True`` adds ``--no-cache``, which is the only way to pick up a new
    Antigravity build (its install script carries no version) or to recover from
    a poisoned layer cache. It costs a full rebuild — several minutes.

    Only one rebuild runs at a time: a concurrent call returns immediately with
    ``busy`` set rather than starting a second ``docker build`` on the same tag.

    ``on_output`` receives build output line by line for progress reporting.
    Returns ``{"ok": bool, "image": str, "forced": bool, "exit_code": int,
    "output": str}``.
    """
    if not _build_lock.acquire(blocking=False):
        return {"ok": False, "busy": True, "image": cli_image_name(),
                "forced": bool(force), "exit_code": -1,
                "output": "A rebuild of this image is already running"}
    try:
        return _rebuild_cli_image(force, on_output)
    finally:
        _build_lock.release()


def _rebuild_cli_image(force: bool,
                       on_output: Optional[Callable[[str], None]]) -> Dict[str, Any]:
    image = cli_image_name()
    if not CLI_BUILD_CONTEXT.is_dir():
        return {"ok": False, "image": image, "forced": bool(force), "exit_code": -1,
                "output": f"Build context not found: {CLI_BUILD_CONTEXT}"}

    args = ["build"]
    if force:
        args.append("--no-cache")
    platform = os.environ.get("PAWFLOW_DOCKER_PLATFORM", "").strip()
    if platform:
        args.extend(["--platform", platform])
    args.extend(_resolved_build_args())
    args.extend(["-t", image, str(CLI_BUILD_CONTEXT)])

    exit_code, lines = _stream_command(docker_cmd() + args, on_output)
    if exit_code is None:
        return {"ok": False, "image": image, "forced": bool(force), "exit_code": -1,
                "output": "\n".join(lines)}

    return {
        "ok": exit_code == 0,
        "image": image,
        "forced": bool(force),
        "exit_code": exit_code,
        # Tail only: a full build log is megabytes and the UI shows progress live.
        "output": "\n".join(lines[-40:]),
    }


def _stream_command(argv: List[str],
                    on_output: Optional[Callable[[str], None]]) -> tuple:
    """Run ``argv``, forwarding merged stdout/stderr line by line.

    Returns ``(exit_code, lines)``; ``exit_code`` is None when the process could
    not be started, with the reason as the single line.
    """
    lines: List[str] = []
    try:
        proc = subprocess.Popen(  # nosec B603
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
    except Exception as exc:
        return None, [f"Could not start {argv[0]}: {exc}"]

    for raw in proc.stdout or []:
        line = raw.rstrip("\n")
        lines.append(line)
        if on_output:
            try:
                on_output(line)
            except Exception:
                logger.debug("Build progress callback failed", exc_info=True)
    return proc.wait(), lines


# ── relay images ─────────────────────────────────────────────────────


_relay_build_lock = threading.Lock()
_relay_restart_lock = threading.Lock()


def relay_build_target(key: str) -> Dict[str, Any]:
    """Build recipe for a relay image key. Raises on an unknown key."""
    for target in RELAY_BUILD_TARGETS:
        if target["key"] == key:
            return target
    raise ValueError(f"Unknown relay image: {key}")


def relay_image_name(key: str) -> str:
    """Tag the relay manager would spawn for this relay image key.

    Read live from the same settings source the manager uses, so a rebuild can
    never land on a tag nothing runs.
    """
    from core._relay_naming import _cfg
    return _cfg(relay_build_target(key)["param"])


def relay_build_running() -> bool:
    """True while a relay image rebuild is in progress."""
    return _relay_build_lock.locked()


def relay_restart_running() -> bool:
    """True while server relays are being recreated."""
    return _relay_restart_lock.locked()


def rebuild_relay_image(key: str, force: bool = False,
                        on_output: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Rebuild a relay image. Blocking — call from a worker thread.

    Building the image does not affect any running relay: containers keep the
    image they were started from until :func:`restart_server_relays` recreates
    them.

    One rebuild at a time across both relay images — they are multi-gigabyte
    builds competing for the same disk, and a second concurrent build is far
    more likely to fill the host than to finish sooner.
    """
    target = relay_build_target(key)
    if not _relay_build_lock.acquire(blocking=False):
        return {"ok": False, "busy": True, "key": key, "image": relay_image_name(key),
                "forced": bool(force), "exit_code": -1,
                "output": "A relay image rebuild is already running"}
    try:
        return _rebuild_relay_image(target, force, on_output)
    finally:
        _relay_build_lock.release()


def _rebuild_relay_image(target: Dict[str, Any], force: bool,
                         on_output: Optional[Callable[[str], None]]) -> Dict[str, Any]:
    key = target["key"]
    image = relay_image_name(key)

    def _failed(message: str) -> Dict[str, Any]:
        return {"ok": False, "key": key, "image": image, "forced": bool(force),
                "exit_code": -1, "output": message}

    lines: List[str] = []
    if target.get("profile"):
        # Generated context: the Dockerfile does not exist until the catalog is
        # rendered, and the catalog may have changed since the last build.
        generated, gen_lines = _generate_relay_context(target, image, on_output)
        lines.extend(gen_lines)
        if not generated:
            return _failed("\n".join(lines[-40:]))

    context = APP_ROOT / target["context"]
    if not context.is_dir():
        return _failed(f"Build context not found: {context}")
    args = ["build"]
    if force:
        args.append("--no-cache")
    platform = os.environ.get("PAWFLOW_DOCKER_PLATFORM", "").strip()
    if platform:
        args.extend(["--platform", platform])
    dockerfile = target.get("dockerfile")
    if dockerfile:
        path = APP_ROOT / dockerfile
        if not path.is_file():
            return _failed(f"Dockerfile not found: {path}")
        args.extend(["-f", str(path)])
    args.extend(["-t", image, str(context)])

    exit_code, build_lines = _stream_command(docker_cmd() + args, on_output)
    lines.extend(build_lines)
    if exit_code is None:
        return _failed("\n".join(lines[-40:]))
    return {
        "ok": exit_code == 0,
        "key": key,
        "image": image,
        "forced": bool(force),
        "exit_code": exit_code,
        "output": "\n".join(lines[-40:]),
    }


def _generate_relay_context(target: Dict[str, Any], image: str,
                            on_output: Optional[Callable[[str], None]]) -> tuple:
    """Render the generated build context for a profile-based relay image."""
    import sys

    generator = APP_ROOT / RELAY_IMAGE_GENERATOR
    if not generator.is_file():
        return False, [f"Relay image generator not found: {generator}"]
    argv = [sys.executable, str(generator),
            "--profile", target["profile"],
            "--out", str(APP_ROOT / target["context"]),
            "--image", image]
    exit_code, lines = _stream_command(argv, on_output)
    if exit_code is None:
        return False, lines
    if exit_code != 0:
        return False, lines + [f"Relay image generation failed (exit {exit_code})"]
    return True, lines


def restart_server_relays(
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """Recreate every server relay container on the current relay images.

    Sequential on purpose: each relay can carry gigabytes of workspace, and
    recreating several at once multiplies the disk and memory pressure while
    every affected conversation is already without a relay.

    One failure does not stop the sweep — the remaining relays are still worth
    moving, and the failures are reported per relay.
    """
    from core.server_relay_manager import ServerRelayManager

    if not _relay_restart_lock.acquire(blocking=False):
        return {"ok": False, "busy": True, "total": 0, "restarted": 0, "failed": []}
    try:
        manager = ServerRelayManager.get_instance()
        entries = manager.list_all()
        total = len(entries)
        restarted = 0
        failed: List[Dict[str, Any]] = []
        for index, entry in enumerate(entries, start=1):
            conv_id = entry.get("conv_id", "")
            kind = entry.get("kind") or "workspace"
            try:
                manager.recreate(conv_id, kind=kind)
                restarted += 1
                error = ""
            except Exception as exc:
                error = str(exc)
                failed.append({"conv_id": conv_id, "kind": kind, "error": error})
                logger.error("[update] Could not recreate %s relay for conv %s: %s",
                             kind, conv_id, exc, exc_info=True)
            if on_progress:
                try:
                    on_progress({"index": index, "total": total, "conv_id": conv_id,
                                 "kind": kind, "ok": not error, "error": error})
                except Exception:
                    logger.debug("Restart progress callback failed", exc_info=True)
        return {"ok": not failed, "total": total, "restarted": restarted, "failed": failed}
    finally:
        _relay_restart_lock.release()
