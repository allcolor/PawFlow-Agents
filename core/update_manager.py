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
import re
import shlex
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

#: Anonymous pull scope for a public GHCR repository, then its tag list. The
#: relay images are published there by ``.github/workflows/docker-publish.yml``,
#: so this is where "published" is actually true.
GHCR_TOKEN_URL = "https://ghcr.io/token?scope=repository:{repo}:pull&service=ghcr.io"  # nosec B105
GHCR_TAGS_URL = "https://ghcr.io/v2/{repo}/tags/list"

#: Pristine copy of the shipped config, baked into the server image. ``/app/config``
#: is a host bind mount seeded no-clobber by ``docker/server-entrypoint.sh``, so a
#: file that gained a key after the operator's first install keeps the old copy
#: there forever. Shipped, versioned data is read from the image instead.
DEFAULT_CONFIG_DIR = Path("/app/default-config")

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

#: Relay tags are dates assigned from ``relay_image_version``. Anything else in
#: the registry is a moving alias and names no version.
_RELAY_TAG_RE = re.compile(r"\d{4}\.\d{2}\.\d{2}")

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

#: Name of the throwaway container that restarts the stack. Kept (not --rm) so
#: `docker logs pawflow-updater` still explains a failed update afterwards.
UPDATER_CONTAINER = "pawflow-updater"

#: Image the updater runs in. The server image ships only the static docker
#: CLI — no compose plugin — so compose has to come from somewhere else.
DEFAULT_UPDATER_IMAGE = "docker:cli"


# ── helpers ──────────────────────────────────────────────────────────


def cli_image_name() -> str:
    """Name of the agent-CLI tools image, matching ``build.sh``."""
    return os.environ.get("PAWFLOW_CLI_LLM_IMAGE", "").strip() or DEFAULT_CLI_IMAGE


def _run(args: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603
        docker_cmd() + args, capture_output=True, text=True, timeout=timeout)


def _fetch_json(url: str, headers: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    try:
        import requests
        resp = requests.get(
            url, timeout=HTTP_TIMEOUT,
            headers={"User-Agent": "PawFlow-UpdateManager/1.0",
                     "Accept": "application/json", **(headers or {})})
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


def catalog_path() -> Path:
    """The relay image catalog to trust.

    Prefer the copy baked into the image over the one under ``/app/config``:
    the latter is a host bind mount, seeded with ``cp -a -n``, so an operator
    who installed before a key existed keeps a catalog without it for good.
    That is how ``relay_image_version`` -- added in `Decouple relay image
    release tags` -- can be missing on a perfectly current server.
    """
    shipped = DEFAULT_CONFIG_DIR / "relay_image_catalog.json"
    return shipped if shipped.is_file() else (
        APP_ROOT / "config" / "relay_image_catalog.json")


def catalog_relay_version() -> str:
    """``relay_image_version`` from the shipped relay image catalog."""
    try:
        return str(json.loads(catalog_path().read_text(encoding="utf-8")).get(
            "relay_image_version", ""))
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


def _ghcr_tags(repository: str) -> List[str]:
    """Every tag published for a public GHCR repository.

    The registry API needs a bearer token even for anonymous pulls of a public
    image, so this is two calls: one for the token, one for the list.
    """
    repo = repository.split("ghcr.io/", 1)[-1]
    token = (_fetch_json(GHCR_TOKEN_URL.format(repo=repo)) or {}).get("token")
    if not token:
        return []
    data = _fetch_json(GHCR_TAGS_URL.format(repo=repo),
                       headers={"Authorization": f"Bearer {token}"}) or {}
    tags = data.get("tags") or []
    return [str(t) for t in tags if str(t).strip()]


def latest_published_relay_tag(repository: str) -> str:
    """Newest relay tag actually published, or empty when the registry is mute.

    The relay tags are dates (``2026.07.16``), assigned from the catalog by the
    publish workflow, so newest is a plain sort of the date-shaped ones.
    ``latest`` and any other moving alias are ignored: they name no version.
    """
    dated = [t for t in _ghcr_tags(repository) if _RELAY_TAG_RE.fullmatch(t)]
    return max(dated) if dated else ""


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
        # Two repositories name the same thing: the published one and the tag
        # this server actually spawns (a local rebuild lands on the latter).
        # Reporting only the published one made a freshly rebuilt relay look
        # missing.
        try:
            configured = relay_image_name(image["key"])
        except ValueError:
            configured = ""
        configured_repo = configured.rsplit(":", 1)[0] if configured else ""
        tags = local_image_tags(image["repository"])
        local_tags = list(tags)
        if configured_repo and configured_repo != image["repository"]:
            local_tags += [f"{configured_repo}:{t}" for t in local_image_tags(configured_repo)]
        installed = catalog_version if catalog_version in tags else (tags[0] if tags else "")
        # "Published" means published: the registry is asked, and the catalog
        # is only the fallback for an offline or rate-limited server. Reporting
        # the catalog as published answered a different question -- what this
        # server expects -- and read as "unknown" whenever the catalog was
        # missing the key.
        published = latest_published_relay_tag(image["repository"]) or catalog_version
        components.append(_component(
            image["key"], image["repository"].rsplit("/", 1)[-1],
            installed, published,
            group="relay", repository=image["repository"],
            expected=catalog_version,
            configured_image=configured, local_tags=local_tags))

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


# ── server self-update ───────────────────────────────────────────────


def updater_image() -> str:
    """Image the updater container runs in.

    ``PAWFLOW_SERVER_UPDATE_IMAGE`` first, then ``server_update_image`` in
    ``global_parameters.json``, so an air-gapped host can point at a local
    mirror without a rebuild.
    """
    configured = os.environ.get("PAWFLOW_SERVER_UPDATE_IMAGE", "").strip()
    if not configured:
        try:
            from core.expression import _load_global_parameters
            configured = str(_load_global_parameters().get("server_update_image", "")).strip()
        except Exception:
            logger.debug("Could not read server_update_image", exc_info=True)
            configured = ""
    return configured or DEFAULT_UPDATER_IMAGE


def _updater_script(working_dir: str, pull_source: bool) -> str:
    """Shell run by the updater, in the project directory, on the host socket.

    Ordering is the safety property: everything that can fail without
    consequence happens *before* anything that stops the server.

    ``pull --ignore-buildable`` covers image-based deployments; a deployment
    that builds from source has nothing to pull and must not fail on that.
    ``up -d --build`` then covers both: it rebuilds what is buildable and
    recreates only the services whose image or config actually changed.
    """
    lines = [
        "set -eu",
        f"cd {shlex.quote(working_dir)}",
        "docker compose version",
    ]
    if pull_source:
        # --ff-only: a dirty or diverged tree stops the update instead of
        # being merged behind the operator's back.
        lines.append("git pull --ff-only")
    lines.extend([
        "docker compose pull --ignore-buildable || docker compose pull || true",
        "docker compose up -d --build",
    ])
    return "\n".join(lines)


def published_server_image(current_image: str) -> str:
    """The published image this server should move to, or empty.

    The publish workflow tags the server image with the release tag minus its
    ``v`` (``ghcr.io/allcolor/pawflow:1.0.0-beta.41``), so the repository of the
    running image plus the latest release is the whole answer. A digest-pinned
    or tagless image still resolves: only the repository part is kept.
    """
    tag = latest_server_release()
    repo = (current_image or "").split("@", 1)[0]
    if ":" in repo.rsplit("/", 1)[-1]:
        repo = repo.rsplit(":", 1)[0]
    return f"{repo}:{tag}" if repo and tag else ""


def _artifact_refresh_lines(image: str, target_dir: str,
                            env: Dict[str, str], force: bool) -> List[str]:
    """Shell that copies the new image's host artifacts into ``target_dir``.

    ``docker cp`` out of a throw-away container, exactly as
    ``install-pawflow.sh`` does: the image is the source of truth for what a
    version's host side looks like, and re-deriving it here would be a second
    one that drifts.

    Runs in a subshell with its own trap, so a copy that fails still removes
    the container it created. The updater runs as root, so the files land
    root-owned; they are chowned to the uid/gid the deployment itself runs as,
    or the operator's next command-line install could not overwrite them.

    Ordering is the safety property, as everywhere else on this path: this runs
    after the pull and before the server is touched, so a failure here still
    leaves a running server on its old version.
    """
    from core.installer_deployment import (
        IMAGE_ARTIFACTS,
        OPTIONAL_IMAGE_ARTIFACTS,
    )

    quoted = shlex.quote(target_dir)
    required = " ".join(shlex.quote(rel) for rel in IMAGE_ARTIFACTS)
    optional = " ".join(shlex.quote(rel) for rel in OPTIONAL_IMAGE_ARTIFACTS)
    # Every message goes through shlex.quote: these strings carry host paths
    # and an image name, and an apostrophe in one of them would end the shell
    # string and break the script at the worst possible moment.
    def _echo(message: str, stderr: bool = False) -> str:
        return f"echo {shlex.quote(message)}" + (" >&2" if stderr else "")

    lines = [
        _echo(f"Refreshing host artifacts from {image} into {target_dir}"),
        # A subshell, so the trap that removes the throw-away container is its
        # own. Every required step ends in `|| exit 1` rather than relying on
        # `set -e`: errexit is neutralised inside a command whose result is
        # tested, which is exactly what the forced call below does — without
        # this, a forced update would skip past failures in silence instead of
        # warning about them.
        "_pf_refresh_artifacts() (",
        "  set -eu",
        f"  mkdir -p {quoted} || exit 1",
        f'  _cid="$(docker create {shlex.quote(image)} true)" || exit 1',
        '  [ -n "$_cid" ] || exit 1',
        "  trap 'docker rm -f \"$_cid\" >/dev/null 2>&1 || true' EXIT",
        f"  for _rel in {required}; do",
        f'    mkdir -p {quoted}/"$(dirname "$_rel")" || exit 1',
        f'    rm -rf {quoted}/"$_rel" || exit 1',
        f'    docker cp "$_cid:/app/$_rel" {quoted}/"$_rel" || exit 1',
        "  done",
        f"  for _rel in {optional}; do",
        f'    mkdir -p {quoted}/"$(dirname "$_rel")"',
        f'    rm -rf {quoted}/"$_rel"',
        f'    docker cp "$_cid:/app/$_rel" {quoted}/"$_rel" 2>/dev/null ||',
        '      echo "WARNING this server image does not carry $_rel" >&2',
        "  done",
        f"  chmod +x {quoted}/scripts/*.sh 2>/dev/null || true",
    ]
    uid = (env.get("PAWFLOW_RUN_UID") or "").strip()
    gid = (env.get("PAWFLOW_RUN_GID") or "").strip()
    if uid and gid:
        warn = _echo(f"WARNING could not chown {target_dir}", stderr=True)
        lines.append(
            f"  chown -R {shlex.quote(uid)}:{shlex.quote(gid)} {quoted} "
            f"2>/dev/null || {warn}")
    lines.append(")")
    if force:
        warn = _echo(
            "WARNING host artifacts were not refreshed; the server is being "
            "started with the host-side files of the version it is replacing",
            stderr=True)
        lines.append(f"_pf_refresh_artifacts || {warn}")
    else:
        lines.append("_pf_refresh_artifacts")
    return lines


def _installer_updater_script(info: Dict[str, Any], image: str,
                              pull_source: bool, artifact_dir: str = "",
                              force_artifacts: bool = False) -> str:
    """Shell run by the updater for a ``docker run`` (installer) deployment.

    It does what the installer does, in the installer's own order: pull the new
    server image first, refresh the host-side artifacts it carries, then hand
    over to ``run-pawflow-docker.sh``, which is the single source of truth for
    how a PawFlow container is started and which already recreates an existing
    container in place.

    The pull comes before the start script for the same reason the compose path
    checks compose first: a failed pull must leave the server running. The
    artifact refresh sits in the same window, and for the same reason.

    ``artifact_dir`` empty means no refresh: a git checkout carries its own
    host-side files and ``git pull`` is what moves them, so copying image
    contents over a tracked tree would dirty it.
    """
    from core.installer_deployment import RESET_ON_UPDATE, start_script_env

    work_dir = artifact_dir or info.get("host_app_dir", "")
    env = start_script_env(info, image, source_dir=artifact_dir)
    # Empty values are dropped, except the first-run flags: those must reach
    # the script *as* empty to override what the running container carries.
    assignments = " ".join(
        f"{k}={shlex.quote(v)}" for k, v in sorted(env.items())
        if v != "" or k in RESET_ON_UPDATE)
    lines = [
        "set -eu",
        # The updater image is docker:cli, which is Alpine and ships ash; the
        # start script is bash (arrays, [[ ]]).
        "command -v bash >/dev/null 2>&1 || apk add --no-cache bash",
        f"cd {shlex.quote(info.get('host_app_dir', ''))}",
    ]
    if pull_source:
        # Same rule as the compose path: a dirty or diverged tree stops the
        # update instead of being merged behind the operator's back.
        lines.append("git pull --ff-only")
    lines.append(f"docker pull {shlex.quote(image)}")
    if artifact_dir:
        lines.extend(_artifact_refresh_lines(image, artifact_dir, env,
                                             force=force_artifacts))
    lines.append(f"cd {shlex.quote(work_dir)}")
    lines.append(f"{assignments} bash scripts/run-pawflow-docker.sh")
    return "\n".join(lines)


def running_agent_count() -> int:
    """Agent turns currently in flight across the whole server.

    Reported, not enforced: restarting kills them, exactly as it would from the
    command line. The operator is told what it costs and decides.
    """
    try:
        from tasks.ai.agent_loop import AgentLoopTask
        inst = AgentLoopTask._live_instance
        if not inst:
            return 0
        with inst._active_contexts_lock:
            return len(inst._active_contexts)
    except Exception:
        logger.debug("Could not count active agents", exc_info=True)
        return 0


def _probe(image: str, host_dir: str, script: str) -> subprocess.CompletedProcess:
    """Run a check inside the updater image, with the host directory mounted.

    The directory is a *host* path, so the server cannot stat it directly; only
    a container carrying that bind mount can. It is mounted at its own path,
    because both compose and the start script resolve relative paths against it
    and hand the result to the daemon as host paths.
    """
    return subprocess.run(  # nosec B603
        docker_cmd() + [
            "run", "--rm",
            "--volume", "/var/run/docker.sock:/var/run/docker.sock",
            "--volume", f"{host_dir}:{host_dir}",
            "--workdir", host_dir,
            "--entrypoint", "sh", image, "-c", script,
        ],
        capture_output=True, text=True, timeout=300)


def _compose_preflight(compose: Dict[str, Any]) -> Dict[str, Any]:
    working_dir = compose.get("working_dir", "")
    image = updater_image()
    # One probe, three answers: the image can run, it carries a working
    # `docker compose`, and the project directory really is where compose says
    # it is — mounted the same way the updater will mount it.
    probe_script = (
        "set -e\n"
        "docker compose version\n"
        "test -d .git && echo PAWFLOW_GIT=1 || true\n"
    )
    try:
        probe = _probe(image, working_dir, probe_script)
    except Exception as exc:
        return {"ok": False, "compose": compose,
                "reason": f"Could not run the updater image '{image}': {exc}"}
    if probe.returncode != 0:
        return {"ok": False, "compose": compose,
                "reason": f"Updater preflight failed with image '{image}': "
                          f"{(probe.stderr or probe.stdout).strip()[:300]}"}

    stdout = probe.stdout or ""
    return {"ok": True, "deployment": "compose", "compose": compose,
            "updater_image": image,
            "working_dir": working_dir,
            "is_git_checkout": "PAWFLOW_GIT=1" in stdout,
            "running_agents": running_agent_count(),
            "compose_version": stdout.splitlines()[0].strip() if stdout.strip() else ""}


def _artifact_mount_dir(app_dir: str, artifact_dir: str) -> str:
    """Host directory the updater must carry to reach both install directories.

    When the update moves to a new per-version directory, the updater has to
    create a *sibling* of the current one, so mounting the current directory
    alone would leave it writing into its own filesystem instead of the host's.
    Their common parent covers both, and it is still mounted at its own host
    path -- the start script resolves ``$PAWFLOW_HOME`` against it and hands the
    result to the daemon as a host path, so any other mount point would
    silently produce wrong bind mounts.
    """
    import posixpath

    if not artifact_dir or artifact_dir == app_dir:
        return app_dir
    return posixpath.dirname(artifact_dir.rstrip("/")) or app_dir


def _installer_preflight(installer: Dict[str, Any]) -> Dict[str, Any]:
    """The installer deployment's equivalent: can we re-run the start script?"""
    from core.installer_deployment import artifact_dir_for_update

    app_dir = installer.get("host_app_dir", "")
    image = updater_image()
    target = published_server_image(installer.get("image", ""))
    if not target:
        return {"ok": False, "installer": installer,
                "reason": "Could not resolve the published server image to "
                          "update to: GitHub did not answer with a release "
                          "tag, or this container runs an unnamed image."}
    if not installer.get("container_name"):
        return {"ok": False, "installer": installer,
                "reason": "This container has no name, so the update could not "
                          "recreate it in place."}
    # Refused, never defaulted: run-pawflow-docker.sh falls back to
    # `$HOME/pawflow` when PAWFLOW_HOME is empty, which inside the updater
    # container is a fresh empty directory. The server would come back up on
    # blank data with the old container already gone.
    if not installer.get("pawflow_home"):
        return {"ok": False, "installer": installer,
                "reason": "Could not tell where this server's data directory "
                          "lives on the host (no /app/data bind mount and no "
                          "PAWFLOW_HOST_DATA_DIR), so the update would recreate "
                          "it on an empty one."}
    if not installer.get("port"):
        return {"ok": False, "installer": installer,
                "reason": "Could not tell which port this server was started "
                          "on, so the update could not restart it on the same "
                          "one."}

    # The artifacts the installer put on the host are refreshed from the new
    # image, so the directory they go to -- and the directory that holds it --
    # must be writable before anything is pulled or stopped.
    artifact_dir = artifact_dir_for_update(app_dir, installer.get("image", ""),
                                           target)
    mount_dir = _artifact_mount_dir(app_dir, artifact_dir)
    probe_script = (
        "set -e\n"
        f"test -f {shlex.quote(app_dir)}/scripts/run-pawflow-docker.sh\n"
        f"test -w {shlex.quote(mount_dir)}\n"
        f"test -d {shlex.quote(app_dir)}/.git && echo PAWFLOW_GIT=1 || true\n"
    )
    try:
        probe = _probe(image, mount_dir, probe_script)
    except Exception as exc:
        return {"ok": False, "installer": installer,
                "reason": f"Could not run the updater image '{image}': {exc}"}
    if probe.returncode != 0:
        return {"ok": False, "installer": installer,
                "reason": f"The install directory '{app_dir}' does not carry "
                          "scripts/run-pawflow-docker.sh, or "
                          f"'{mount_dir}' is not writable, so the update "
                          "cannot start the server the way the installer did: "
                          f"{(probe.stderr or probe.stdout).strip()[:200]}"}

    # A git checkout carries its own host-side files and `git pull` is what
    # moves them; copying image contents over a tracked tree would dirty it.
    is_git = "PAWFLOW_GIT=1" in (probe.stdout or "")
    return {"ok": True, "deployment": "installer", "installer": installer,
            "updater_image": image, "working_dir": app_dir,
            "target_image": target,
            "artifact_dir": "" if is_git else artifact_dir,
            "mount_dir": mount_dir,
            "is_git_checkout": is_git,
            "running_agents": running_agent_count(),
            "container": installer.get("container_name", "")}


def server_update_preflight() -> Dict[str, Any]:
    """Everything that must hold before the server may be told to die.

    Checked here rather than inside the updater because a failure here is
    recoverable: the server is still running and can report why.

    Two deployments can update themselves, and they are asked in that order: a
    compose stack, and an installer ``docker run`` — the shape
    ``install-pawflow.sh`` produces, which used to be refused outright.
    """
    from core.compose_deployment import compose_info
    from core.installer_deployment import installer_info

    compose = compose_info()
    if compose:
        return _compose_preflight(compose)

    installer = installer_info()
    if installer:
        return _installer_preflight(installer)

    return {"ok": False, "compose": {}, "installer": {},
            "reason": "This server was not started by Docker Compose and "
                      "carries no installer markers (no compose labels, no "
                      "PAWFLOW_HOST_APP_DIR), so it cannot update itself."}


def update_server(pull_source: bool = False,
                  force_artifacts: bool = False) -> Dict[str, Any]:
    """Launch the detached updater that restarts this server.

    ``force_artifacts`` downgrades a failed host-artifact refresh from an abort
    to a warning. Off by default: the refreshed files include the start script
    the update is about to run, so proceeding without them silently starts the
    new server image with the previous version's host side.

    A container cannot replace itself: ``docker restart`` would come back on the
    old image, and ``rm -f`` kills the process issuing the command. So the work
    is handed to a short-lived container that survives the server's death, has
    the socket, and drives the deployment from the project directory — compose
    for a compose stack, ``run-pawflow-docker.sh`` for an installer deployment.

    The project directory is mounted **at its own host path** — compose resolves
    the relative paths in the compose file (``./data``, ``build: .``) against it
    and hands the result to the daemon as host paths, and the start script does
    the same with ``$PAWFLOW_HOME``, so mounting it anywhere else would silently
    produce wrong bind mounts.

    Returns immediately. The caller has seconds, not minutes, before the server
    stops answering.
    """
    check = server_update_preflight()
    if not check.get("ok"):
        return {"ok": False, "started": False, "reason": check.get("reason", "")}

    working_dir = check["working_dir"]
    image = check["updater_image"]
    deployment = check.get("deployment", "compose")
    artifact_dir = check.get("artifact_dir", "")
    # The updater has to reach the directory the artifacts go to, which may be
    # a sibling of the current one; the compose path has nothing to extract.
    mount_dir = (check.get("mount_dir") or working_dir
                 if deployment == "installer" else working_dir)
    script = (_installer_updater_script(check["installer"], check["target_image"],
                                        pull_source, artifact_dir=artifact_dir,
                                        force_artifacts=force_artifacts)
              if deployment == "installer"
              else _updater_script(working_dir, pull_source))

    # Drop the previous updater so its name is free; its logs have served
    # their purpose by now.
    try:
        subprocess.run(  # nosec B603
            docker_cmd() + ["rm", "-f", UPDATER_CONTAINER],
            capture_output=True, timeout=30)
    except Exception:
        logger.debug("Could not remove the previous updater", exc_info=True)

    args = [
        "run", "--detach", "--name", UPDATER_CONTAINER, "--restart", "no",
        "--volume", "/var/run/docker.sock:/var/run/docker.sock",
        "--volume", f"{mount_dir}:{mount_dir}",
        "--workdir", working_dir,
        "--entrypoint", "sh",
        image, "-c", script,
    ]
    try:
        result = subprocess.run(  # nosec B603
            docker_cmd() + args, capture_output=True, text=True, timeout=60)
    except Exception as exc:
        return {"ok": False, "started": False,
                "reason": f"Could not start the updater: {exc}"}
    if result.returncode != 0:
        return {"ok": False, "started": False,
                "reason": f"Updater refused to start: {result.stderr.strip()[:300]}"}

    logger.warning("[update] Updater launched (%s) — this server is about to "
                   "be recreated from %s", UPDATER_CONTAINER, working_dir)
    return {"ok": True, "started": True,
            "container": UPDATER_CONTAINER,
            "updater_image": image,
            "working_dir": working_dir,
            "artifact_dir": artifact_dir,
            "pull_source": bool(pull_source),
            "force_artifacts": bool(force_artifacts),
            "deployment": deployment,
            "target_image": check.get("target_image", ""),
            "project": check.get("compose", {}).get("project", ""),
            "service": check.get("compose", {}).get("service", "")}
