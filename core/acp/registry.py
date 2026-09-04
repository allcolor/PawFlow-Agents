"""Client for the public Agent Client Protocol registry.

The registry (https://agentclientprotocol.com/registry) lists agents that
speak ACP and how to obtain them. This module turns one entry into the
configuration of an ``llmConnection`` service using the generic ``acp``
provider (``core/llm_providers/acp.py``), so any listed agent can be imported
without hand-writing ``acp_command``/``acp_args``.

Trust model: the registry is community curated, its download URLs are
untrusted, and an entry's version pins what was imported.

- The index, the quarantine list and the protocol matrix are fetched over
  HTTPS only and cached for :data:`CACHE_TTL_SECONDS` under
  ``data/runtime/acp_registry/``. A failed fetch falls back to the cached copy
  and is reported as stale; without a cache it is an error.
- Every entry is validated against the vendored ``registry_schema.json``.
  Quarantined entries stay visible but cannot be imported.
- ``binary`` archives are verified against the registry ``sha256`` when one
  is published; the digest actually observed is always recorded. Installer
  formats (``.dmg``, ``.pkg``, ``.deb``, ``.rpm``, ``.msi``, ``.appimage``)
  are refused; the archive is extracted, never executed.
- Nothing is auto-upgraded: :func:`check_update` only reports a newer
  registry version for a pinned import.

The ``antigravity-acp`` entry has its own provider
(``core/llm_providers/antigravity_acp.py``); the registry client is for the
other entries.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform as _platform
import re
import shutil
import stat
import tarfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlsplit

import jsonschema

import core.paths as _paths

logger = logging.getLogger(__name__)

REGISTRY_INDEX_URL = "https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json"
QUARANTINE_URL = "https://raw.githubusercontent.com/agentclientprotocol/registry/main/quarantine.json"
PROTOCOL_MATRIX_URL = (
    "https://raw.githubusercontent.com/agentclientprotocol/registry/main/.protocol-matrix/latest.json"
)

#: 24 h: the registry bot bumps versions hourly, but an import pins a version
#: anyway, so a day-old catalogue only delays *seeing* an update.
CACHE_TTL_SECONDS = 24 * 60 * 60

#: Documented size guard for a fetched document (index is ~50 KB, matrix ~80 KB).
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024

#: Largest archive the importer will download (Antigravity's is 682 MB and
#: ships in the image instead; the next largest registry archive is < 200 MB).
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024

PLATFORMS = (
    "darwin-aarch64", "darwin-x86_64",
    "linux-aarch64", "linux-x86_64",
    "windows-aarch64", "windows-x86_64",
)

_INSTALLER_SUFFIXES = (".dmg", ".pkg", ".deb", ".rpm", ".msi", ".appimage")
_ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".zip")
_SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SCHEMA_PATH = Path(__file__).with_name("registry_schema.json")

#: Registry auth method types (``.protocol-matrix``) mapped to what PawFlow can
#: drive. ``agent``: the agent runs its own browser OAuth; ``terminal``: the
#: agent expects an interactive TUI; ``env``: credentials come from ``acp_env``.
AUTH_TYPES = ("agent", "terminal", "env")


class RegistryError(ValueError):
    """A registry document, entry or download could not be trusted."""


class RegistryUnavailable(RegistryError):
    """The registry could not be fetched and no cached copy exists."""


# -- documents ------------------------------------------------------------------


@dataclass(frozen=True)
class RegistryDocument:
    """One cached registry document and how fresh it is."""

    name: str
    data: Any
    fetched_at: float
    stale: bool


def cache_dir() -> Path:
    return _paths.RUNTIME_DIR / "acp_registry"


def agents_dir() -> Path:
    """Host directory that materialised binary distributions live under."""
    return _paths.RUNTIME_DIR / "acp_agents"


def _require_https(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        raise RegistryError(f"registry URLs must be https, got {url!r}")
    return url


def _default_fetch(url: str, limit: int) -> bytes:
    import requests

    response = requests.get(_require_https(url), timeout=30, stream=True)
    response.raise_for_status()
    chunks = []
    size = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        size += len(chunk)
        if size > limit:
            raise RegistryError(f"{url} exceeds {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


Fetcher = Callable[[str, int], bytes]


class RegistryCache:
    """Fetch registry documents with a TTL cache and offline fallback."""

    def __init__(self, directory: Optional[Path] = None, *,
                 fetch: Optional[Fetcher] = None,
                 ttl_seconds: float = CACHE_TTL_SECONDS,
                 now: Callable[[], float] = time.time) -> None:
        self.directory = Path(directory) if directory is not None else cache_dir()
        self._fetch = fetch or _default_fetch
        self.ttl_seconds = float(ttl_seconds)
        self._now = now

    def _paths_for(self, name: str) -> tuple[Path, Path]:
        return self.directory / f"{name}.json", self.directory / f"{name}.fetched_at"

    def _read_cached(self, name: str) -> Optional[tuple[Any, float]]:
        body, stamp = self._paths_for(name)
        if not body.is_file() or not stamp.is_file():
            return None
        try:
            data = json.loads(body.read_text(encoding="utf-8"))
            fetched_at = float(stamp.read_text(encoding="utf-8").strip())
        except (ValueError, OSError) as exc:
            logger.warning("[acp-registry] unreadable cache %s: %s", body, exc)
            return None
        return data, fetched_at

    def _store(self, name: str, data: Any, fetched_at: float) -> None:
        body, stamp = self._paths_for(name)
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = body.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, body)
        stamp.write_text(repr(fetched_at), encoding="utf-8")

    def get(self, name: str, url: str, *, refresh: bool = False) -> RegistryDocument:
        _require_https(url)
        cached = self._read_cached(name)
        now = self._now()
        if cached is not None and not refresh and now - cached[1] < self.ttl_seconds:
            return RegistryDocument(name, cached[0], cached[1], stale=False)
        try:
            raw = self._fetch(url, MAX_DOCUMENT_BYTES)
            data = json.loads(raw.decode("utf-8"))
        except RegistryError:
            raise
        except Exception as exc:  # network, HTTP, decoding: fall back to cache
            if cached is None:
                raise RegistryUnavailable(
                    f"cannot fetch {name} from {url} and no cached copy exists: {exc}"
                ) from exc
            logger.warning("[acp-registry] %s fetch failed, using cache from %s: %s",
                           name, time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(cached[1])), exc)
            return RegistryDocument(name, cached[0], cached[1], stale=True)
        self._store(name, data, now)
        return RegistryDocument(name, data, now, stale=False)


# -- entries ----------------------------------------------------------------------


@dataclass(frozen=True)
class BinaryTarget:
    platform: str
    archive: str
    cmd: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    sha256: str = ""


@dataclass(frozen=True)
class PackageDistribution:
    kind: str  # "npx" | "uvx"
    package: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RegistryEntry:
    id: str
    name: str
    version: str
    description: str
    license: str = ""
    license_url: str = ""
    repository: str = ""
    website: str = ""
    authors: tuple[str, ...] = ()
    icon: str = ""
    binaries: Mapping[str, BinaryTarget] = field(default_factory=dict)
    packages: Mapping[str, PackageDistribution] = field(default_factory=dict)
    quarantine_reason: str = ""
    auth_types: tuple[str, ...] = ()
    load_session: Optional[bool] = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def quarantined(self) -> bool:
        return bool(self.quarantine_reason)

    @property
    def proprietary(self) -> bool:
        return self.license.strip().lower() in {"", "proprietary"}

    def binary_for(self, platform_id: str) -> Optional[BinaryTarget]:
        return self.binaries.get(platform_id)

    def summary(self) -> dict[str, Any]:
        """Catalogue row for the UI: no download URLs, only what a user decides on."""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "license": self.license or "proprietary",
            "license_url": self.license_url,
            "repository": self.repository,
            "website": self.website,
            "authors": list(self.authors),
            "icon": self.icon,
            "distributions": sorted(list(self.packages) + (["binary"] if self.binaries else [])),
            "platforms": sorted(self.binaries),
            "auth_types": list(self.auth_types),
            "load_session": self.load_session,
            "quarantined": self.quarantined,
            "quarantine_reason": self.quarantine_reason,
        }


def _load_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


_VALIDATOR = jsonschema.Draft7Validator(_load_schema())


def validate_entry(raw: Mapping[str, Any]) -> None:
    """Raise :class:`RegistryError` unless ``raw`` matches the vendored schema."""
    errors = sorted(_VALIDATOR.iter_errors(raw), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "<root>"
        raise RegistryError(
            f"registry entry {raw.get('id', '?')!r} is invalid at {where}: {first.message}"
        )
    distribution = raw["distribution"]
    env_maps = []
    for target in (distribution.get("binary") or {}).values():
        archive = str(target.get("archive", ""))
        if urlsplit(archive).scheme != "https":
            raise RegistryError(f"binary archive must be https: {archive}")
        env_maps.append(target.get("env") or {})
    for kind in ("npx", "uvx"):
        env_maps.append((distribution.get(kind) or {}).get("env") or {})
    for env in env_maps:
        for name in env:
            if not _ENV_NAME.fullmatch(name):
                raise RegistryError(f"invalid environment name in registry entry: {name!r}")


def parse_entry(raw: Mapping[str, Any], *, quarantine: Mapping[str, str] = {},
                matrix: Mapping[str, Mapping[str, Any]] = {}) -> RegistryEntry:
    validate_entry(raw)
    dist = raw["distribution"]
    binaries = {}
    for platform_id, target in (dist.get("binary") or {}).items():
        binaries[platform_id] = BinaryTarget(
            platform=platform_id,
            archive=target["archive"],
            cmd=target["cmd"],
            args=tuple(target.get("args") or ()),
            env=dict(target.get("env") or {}),
            sha256=str(target.get("sha256") or "").lower(),
        )
    packages = {}
    for kind in ("npx", "uvx"):
        pkg = dist.get(kind)
        if pkg:
            packages[kind] = PackageDistribution(
                kind=kind, package=pkg["package"],
                args=tuple(pkg.get("args") or ()), env=dict(pkg.get("env") or {}))
    probe = matrix.get(raw["id"]) or {}
    auth_types = tuple(t for t in (probe.get("authMethods") or ()) if t in AUTH_TYPES)
    capabilities = probe.get("capabilities") or {}
    load_session = capabilities.get("loadSession")
    return RegistryEntry(
        id=raw["id"], name=raw["name"], version=raw["version"],
        description=raw["description"],
        license=str(raw.get("license") or ""),
        license_url=str(raw.get("license_url") or ""),
        repository=str(raw.get("repository") or ""),
        website=str(raw.get("website") or ""),
        authors=tuple(raw.get("authors") or ()),
        icon=str(raw.get("icon") or ""),
        binaries=binaries, packages=packages,
        quarantine_reason=str(quarantine.get(raw["id"]) or ""),
        auth_types=auth_types,
        load_session=load_session if isinstance(load_session, bool) else None,
        raw=dict(raw),
    )


@dataclass(frozen=True)
class Catalogue:
    entries: tuple[RegistryEntry, ...]
    registry_version: str
    fetched_at: float
    stale: bool

    def get(self, agent_id: str) -> RegistryEntry:
        for entry in self.entries:
            if entry.id == agent_id:
                return entry
        raise RegistryError(f"unknown ACP registry agent: {agent_id!r}")


def load_catalogue(cache: Optional[RegistryCache] = None, *,
                   refresh: bool = False) -> Catalogue:
    """Fetch (or reuse) the index, quarantine and matrix and parse every entry.

    Entries that fail schema validation are skipped with a warning: one bad
    upstream row must not hide the whole catalogue.
    """
    cache = cache or RegistryCache()
    index = cache.get("registry", REGISTRY_INDEX_URL, refresh=refresh)
    if not isinstance(index.data, dict) or not isinstance(index.data.get("agents"), list):
        raise RegistryError("registry index has no 'agents' list")
    quarantine: dict[str, str] = {}
    matrix: dict[str, Mapping[str, Any]] = {}
    stale = index.stale
    try:
        doc = cache.get("quarantine", QUARANTINE_URL, refresh=refresh)
        if isinstance(doc.data, dict):
            quarantine = {str(k): str(v) for k, v in doc.data.items()}
        stale = stale or doc.stale
    except RegistryUnavailable as exc:
        # Without the quarantine list we must not offer withdrawn entries as
        # importable: treat the whole catalogue as read-only until it loads.
        raise RegistryUnavailable(f"quarantine list unavailable: {exc}") from exc
    try:
        doc = cache.get("protocol_matrix", PROTOCOL_MATRIX_URL, refresh=refresh)
        for probe in (doc.data.get("agents") or []) if isinstance(doc.data, dict) else []:
            if isinstance(probe, dict) and probe.get("id"):
                matrix[str(probe["id"])] = probe
        stale = stale or doc.stale
    except RegistryUnavailable as exc:
        # The matrix only pre-fills auth/capability hints; importing works without it.
        logger.warning("[acp-registry] protocol matrix unavailable: %s", exc)
    entries = []
    for raw in index.data["agents"]:
        try:
            entries.append(parse_entry(raw, quarantine=quarantine, matrix=matrix))
        except RegistryError as exc:
            logger.warning("[acp-registry] skipping entry: %s", exc)
    return Catalogue(
        entries=tuple(entries),
        registry_version=str(index.data.get("version") or ""),
        fetched_at=index.fetched_at, stale=stale,
    )


# -- platform -----------------------------------------------------------------------


def platform_for(system: str, machine: str) -> str:
    """Registry platform id for a ``uname -s`` / ``uname -m`` pair."""
    system = system.strip().lower()
    machine = machine.strip().lower()
    if system.startswith("linux"):
        os_id = "linux"
    elif system.startswith("darwin"):
        os_id = "darwin"
    elif system.startswith(("windows", "msys", "mingw", "cygwin")):
        os_id = "windows"
    else:
        raise RegistryError(f"unsupported operating system for ACP binaries: {system!r}")
    if machine in {"x86_64", "amd64"}:
        arch = "x86_64"
    elif machine in {"aarch64", "arm64"}:
        arch = "aarch64"
    else:
        raise RegistryError(f"unsupported architecture for ACP binaries: {machine!r}")
    return f"{os_id}-{arch}"


def host_platform() -> str:
    return platform_for(_platform.system(), _platform.machine())


# -- binary materialisation ------------------------------------------------------------


@dataclass(frozen=True)
class Materialised:
    """A binary distribution extracted on disk, ready to be launched."""

    directory: Path
    command: Path
    args: tuple[str, ...]
    env: Mapping[str, str]
    archive_sha256: str
    verified: bool  # True when the registry published a digest and it matched


def archive_kind(url: str) -> str:
    """``zip`` / ``tar`` / ``raw`` for an archive URL; installers are refused."""
    name = urlsplit(url).path.lower()
    if name.endswith(_INSTALLER_SUFFIXES):
        raise RegistryError(
            f"installer formats are not supported, refusing {os.path.basename(name)}")
    if name.endswith(".zip"):
        return "zip"
    if name.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tbz2")):
        return "tar"
    return "raw"


def _safe_extract_zip(archive: Path, target: Path) -> None:
    root = target.resolve()
    with zipfile.ZipFile(archive) as zf:
        expanded = 0
        for info in zf.infolist():
            dest = (target / info.filename).resolve()
            if dest != root and root not in dest.parents:
                raise RegistryError(f"archive entry escapes target: {info.filename}")
            expanded += max(0, info.file_size)
            if expanded > MAX_ARCHIVE_BYTES:
                raise RegistryError(
                    f"expanded archive exceeds {MAX_ARCHIVE_BYTES} bytes")
            if info.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, dest.open("wb") as output:
                shutil.copyfileobj(source, output, length=64 * 1024)
        # zipfile drops POSIX modes; restore the executable bit that the
        # publisher set so ``cmd`` runs without a chmod round trip.
        for info in zf.infolist():
            mode = (info.external_attr >> 16) & 0o777
            if mode & 0o111:
                path = target / info.filename
                if path.is_file():
                    path.chmod(path.stat().st_mode | 0o111)


def _safe_extract_tar(archive: Path, target: Path) -> None:
    root = target.resolve()
    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            dest = (target / member.name).resolve()
            if dest != root and root not in dest.parents:
                raise RegistryError(f"archive entry escapes target: {member.name}")
            if member.issym() or member.islnk():
                link = (Path(os.path.dirname(dest)) / member.linkname).resolve()
                if root not in link.parents and link != root:
                    raise RegistryError(f"archive link escapes target: {member.name}")
        # The "data" filter (Python 3.12+) additionally strips setuid bits,
        # device nodes and absolute links; the loop above is the readable rule.
        tf.extractall(target, filter="data")


def _resolve_cmd(target: Path, cmd: str) -> Path:
    rel = cmd.replace("\\", "/")
    candidate = (target / rel).resolve()
    if target.resolve() not in candidate.parents:
        raise RegistryError(f"registry cmd escapes the extracted directory: {cmd!r}")
    if not candidate.is_file():
        raise RegistryError(f"registry cmd not found after extraction: {cmd!r}")
    return candidate


def materialise_binary(entry: RegistryEntry, platform_id: str, *,
                       base_dir: Optional[Path] = None,
                       fetch: Optional[Fetcher] = None) -> Materialised:
    """Download, verify and extract ``entry``'s binary for ``platform_id``.

    Layout: ``<base_dir>/<id>/<version>/<platform>/`` with ``archive.sha256``
    recording the digest that was observed. An existing complete directory is
    reused without a download.
    """
    if entry.quarantined:
        raise RegistryError(
            f"{entry.id} is quarantined by the registry: {entry.quarantine_reason}")
    target_spec = entry.binary_for(platform_id)
    if target_spec is None:
        raise RegistryError(
            f"{entry.id} {entry.version} has no binary for {platform_id} "
            f"(available: {', '.join(sorted(entry.binaries)) or 'none'})")
    kind = archive_kind(target_spec.archive)
    base = Path(base_dir) if base_dir is not None else agents_dir()
    directory = base / entry.id / entry.version / platform_id
    digest_file = directory / "archive.sha256"
    if digest_file.is_file():
        observed = digest_file.read_text(encoding="utf-8").strip()
        command = _resolve_cmd(directory, target_spec.cmd)
        return Materialised(directory, command, target_spec.args, dict(target_spec.env),
                            observed, verified=bool(target_spec.sha256))

    fetch = fetch or _default_fetch
    raw = fetch(_require_https(target_spec.archive), MAX_ARCHIVE_BYTES)
    observed = hashlib.sha256(raw).hexdigest()
    if target_spec.sha256 and observed != target_spec.sha256:
        raise RegistryError(
            f"{entry.id} archive digest mismatch: registry {target_spec.sha256}, "
            f"downloaded {observed}")

    staging = directory.with_name(directory.name + ".partial")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        if kind == "raw":
            cmd_name = target_spec.cmd.replace("\\", "/").lstrip("./") or "agent"
            binary = staging / cmd_name
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_bytes(raw)
        else:
            archive = staging / ("archive.zip" if kind == "zip" else "archive.tar")
            archive.write_bytes(raw)
            try:
                if kind == "zip":
                    _safe_extract_zip(archive, staging)
                else:
                    _safe_extract_tar(archive, staging)
            except (zipfile.BadZipFile, tarfile.TarError) as exc:
                raise RegistryError(f"{entry.id} archive is not a valid {kind}: {exc}") from exc
            archive.unlink()
        command = _resolve_cmd(staging, target_spec.cmd)
        command.chmod(command.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        (staging / "archive.sha256").write_text(observed + "\n", encoding="utf-8")
        if directory.exists():
            shutil.rmtree(directory)
        os.replace(staging, directory)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    command = _resolve_cmd(directory, target_spec.cmd)
    return Materialised(directory, command, target_spec.args, dict(target_spec.env),
                        observed, verified=bool(target_spec.sha256))


# -- service configuration ---------------------------------------------------------------


_PACKAGE_RUNNERS = {"npx": ("npx", ("--yes",)), "uvx": ("uvx", ())}


def _auth_method_hint(entry: RegistryEntry) -> str:
    """Pre-fill ``acp_auth_method_id`` only when the matrix leaves no choice.

    The matrix publishes method *types*, not ids; when exactly one type is
    advertised the generic provider's ``acp_auto_auth_single_method`` picks the
    single advertised id at runtime, so the hint is the type name for display.
    """
    return entry.auth_types[0] if len(entry.auth_types) == 1 else ""


def _base_service(entry: RegistryEntry, distribution: str, cwd: str) -> dict[str, Any]:
    return {
        "provider": "acp",
        "auth_mode": "none",
        "acp_cwd": cwd,
        "acp_mcp_mode": "pawflow",
        "acp_load_session": bool(entry.load_session),
        "acp_auto_auth_single_method": len(entry.auth_types) == 1,
        "acp_auth_method_id": "",
        "acp_title_override": entry.name,
        "acp_registry": {
            "id": entry.id,
            "version": entry.version,
            "distribution": distribution,
            "license": entry.license or "proprietary",
            "license_url": entry.license_url,
            "auth_types": list(entry.auth_types),
            "auth_hint": _auth_method_hint(entry),
        },
    }


def service_config_for_package(entry: RegistryEntry, kind: str, *, cwd: str,
                               which: Callable[[str], Optional[str]] = shutil.which,
                               ) -> dict[str, Any]:
    """``acp`` service config that runs ``entry`` through ``npx`` or ``uvx``.

    The runner must exist where the ``acp`` provider will launch it; a missing
    runtime is a :class:`RegistryError` naming the tool rather than a service
    that fails on its first turn.
    """
    if entry.quarantined:
        raise RegistryError(
            f"{entry.id} is quarantined by the registry: {entry.quarantine_reason}")
    if kind not in _PACKAGE_RUNNERS:
        raise RegistryError(f"unknown package distribution kind: {kind!r}")
    dist = entry.packages.get(kind)
    if dist is None:
        raise RegistryError(f"{entry.id} {entry.version} has no {kind} distribution")
    runner, runner_args = _PACKAGE_RUNNERS[kind]
    resolved = which(runner)
    if not resolved:
        raise RegistryError(
            f"{entry.id} needs '{runner}' to run its {kind} distribution, and it is "
            f"not installed where ACP agents are launched")
    config = _base_service(entry, kind, cwd)
    config.update({
        "acp_command": resolved,
        "acp_args": [*runner_args, dist.package, *dist.args],
        "acp_env": dict(dist.env),
    })
    return config


def service_config_for_binary(entry: RegistryEntry, materialised: Materialised, *,
                              cwd: str) -> dict[str, Any]:
    config = _base_service(entry, "binary", cwd)
    config.update({
        "acp_command": str(materialised.command),
        "acp_args": list(materialised.args),
        "acp_env": dict(materialised.env),
    })
    config["acp_registry"].update({
        "archive_sha256": materialised.archive_sha256,
        "archive_verified": materialised.verified,
        "platform": materialised.directory.name,
    })
    return config


def check_update(service_config: Mapping[str, Any], catalogue: Catalogue) -> dict[str, Any]:
    """Report whether the registry lists a newer version than the pinned import.

    Never mutates anything: upgrading is a deliberate re-import.
    """
    pinned = service_config.get("acp_registry") or {}
    if isinstance(pinned, str):
        # The service form stores the record as JSON text.
        try:
            pinned = json.loads(pinned) if pinned.strip() else {}
        except json.JSONDecodeError as exc:
            raise RegistryError(f"acp_registry is not valid JSON: {exc}") from exc
    if not isinstance(pinned, Mapping):
        raise RegistryError("acp_registry must be a JSON object")
    agent_id = str(pinned.get("id") or "")
    if not agent_id:
        raise RegistryError("service was not imported from the ACP registry")
    current = str(pinned.get("version") or "")
    entry = catalogue.get(agent_id)
    return {
        "id": agent_id,
        "pinned": current,
        "latest": entry.version,
        "update_available": entry.version != current,
        "quarantined": entry.quarantined,
        "quarantine_reason": entry.quarantine_reason,
        "stale_catalogue": catalogue.stale,
    }


__all__ = [
    "AUTH_TYPES",
    "BinaryTarget",
    "CACHE_TTL_SECONDS",
    "Catalogue",
    "Materialised",
    "PLATFORMS",
    "PROTOCOL_MATRIX_URL",
    "PackageDistribution",
    "QUARANTINE_URL",
    "REGISTRY_INDEX_URL",
    "RegistryCache",
    "RegistryDocument",
    "RegistryEntry",
    "RegistryError",
    "RegistryUnavailable",
    "agents_dir",
    "archive_kind",
    "cache_dir",
    "check_update",
    "host_platform",
    "load_catalogue",
    "materialise_binary",
    "parse_entry",
    "platform_for",
    "service_config_for_binary",
    "service_config_for_package",
    "validate_entry",
]
