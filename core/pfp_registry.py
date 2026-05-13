"""Decentralized PawFlow Package registry support.

A registry is a static JSON index that points to signed .pfp artifacts. The
registry is discovery metadata only; install still verifies the .pfp signature
and, when provided, the registry package SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests

import core.paths as _paths


REGISTRY_FORMAT = "pawflow.package.registry.v1"
MAX_INDEX_BYTES = 1_000_000
MAX_REGISTRY_PACKAGES = 500
MAX_SEARCH_RESULTS = 50
USER_AGENT = "PawFlow-pfp-registry/1.0"
REQUEST_TIMEOUT_SECONDS = 30

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}$")


class PfpRegistryError(ValueError):
    """Raised for invalid package registry operations."""


def add_registry(url: str, *, user_id: str, name: str = "",
                 trusted: bool = False) -> Dict[str, Any]:
    """Add a decentralized registry URL for a user after validating it."""
    if not user_id:
        raise PfpRegistryError("user_id is required")
    clean_url = _validate_registry_url(url)
    index = fetch_registry_index(clean_url)
    reg_name = name or str(index.get("registry") or _default_registry_name(clean_url))
    _validate_registry_name(reg_name)
    data = _read_registries(user_id)
    registries = [r for r in data.get("registries", []) if r.get("url") != clean_url]
    entry = {
        "name": reg_name,
        "url": clean_url,
        "package_count": len(index.get("packages") or []),
        "trusted": bool(trusted),
    }
    registries.append(entry)
    data["registries"] = registries
    _write_registries(user_id, data)
    return {"ok": True, "registry": entry}


def remove_registry(name_or_url: str, *, user_id: str) -> Dict[str, Any]:
    """Remove a user registry by name or URL."""
    data = _read_registries(user_id)
    before = data.get("registries", [])
    after = [
        row for row in before
        if row.get("name") != name_or_url and row.get("url") != name_or_url
    ]
    data["registries"] = after
    _write_registries(user_id, data)
    return {"ok": True, "removed": len(before) - len(after), "registries": after}


def list_registries(*, user_id: str) -> Dict[str, Any]:
    data = _read_registries(user_id)
    return {"ok": True, "registries": data.get("registries", [])}


def search_registries(query: str = "", *, user_id: str,
                      limit: int = 20) -> Dict[str, Any]:
    """Search all configured package registries."""
    query = (query or "").strip().lower()
    limit = max(1, min(int(limit or 20), MAX_SEARCH_RESULTS))
    rows = []
    errors = []
    for reg in _read_registries(user_id).get("registries", []):
        try:
            index = fetch_registry_index(reg.get("url", ""))
            for item in index.get("packages") or []:
                row = _normalize_package_row(item, reg, index)
                if _matches(query, row):
                    rows.append(row)
        except Exception as exc:
            errors.append({"registry": reg.get("name", ""), "error": str(exc)})
    rows = _dedupe(rows)
    if query:
        rows.sort(key=lambda row: _rank(row, query), reverse=True)
    return {"ok": True, "query": query, "count": min(len(rows), limit), "results": rows[:limit], "errors": errors}


def fetch_registry_index(url: str) -> Dict[str, Any]:
    clean_url = _validate_registry_url(url)
    response = requests.get(
        clean_url,
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise PfpRegistryError(f"Registry fetch failed {response.status_code}: {clean_url}")
    content = response.content
    if len(content) > MAX_INDEX_BYTES:
        raise PfpRegistryError("Registry index exceeds size cap")
    try:
        data = json.loads(content.decode("utf-8"))
    except Exception as exc:
        raise PfpRegistryError("Registry index must be UTF-8 JSON") from exc
    _validate_registry_index(data)
    return data


def expected_sha_for_ref(ref: str, *, user_id: str) -> str:
    """Resolve package@version or URL to a registry-pinned package SHA."""
    ref = (ref or "").strip()
    if not ref:
        return ""
    for reg in _read_registries(user_id).get("registries", []):
        index = None
        try:
            index = fetch_registry_index(reg.get("url", ""))
        except (PfpRegistryError, requests.RequestException):
            index = None
        if not index:
            continue
        for item in index.get("packages") or []:
            row = _normalize_package_row(item, reg, index)
            if ref in {row.get("ref"), row.get("package"), row.get("url")}:
                return row.get("sha256", "")
    return ""


def url_for_ref(ref: str, *, user_id: str) -> str:
    """Resolve a package ref from configured registries to its .pfp URL."""
    ref = (ref or "").strip()
    if _is_http_url(ref):
        return ref
    for reg in _read_registries(user_id).get("registries", []):
        index = fetch_registry_index(reg.get("url", ""))
        for item in index.get("packages") or []:
            row = _normalize_package_row(item, reg, index)
            if ref in {row.get("ref"), row.get("package")}:
                return row.get("url", "")
    return ref


def download_pfp(url: str, *, expected_sha256: str = "",
                 expected_size: int = 0) -> Dict[str, Any]:
    """Download a .pfp artifact to the local runtime cache."""
    clean_url = _validate_pfp_url(url)
    response = requests.get(
        clean_url,
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise PfpRegistryError(f"Package fetch failed {response.status_code}: {clean_url}")
    content = response.content
    if expected_size and len(content) != expected_size:
        raise PfpRegistryError("Downloaded package size does not match registry")
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    if expected_sha256 and _normalize_sha(expected_sha256) != digest:
        raise PfpRegistryError("Downloaded package SHA-256 does not match registry")
    cache_dir = _paths.RUNTIME_DIR / "pfp_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{digest.split(':', 1)[1]}.pfp"
    tmp = target.with_suffix(".pfp.tmp")
    tmp.write_bytes(content)
    os.replace(tmp, target)
    return {
        "ok": True,
        "path": str(target),
        "sha256": digest,
        "url": clean_url,
        "package_size": len(content),
    }


def resolve_package_path(ref: str, *, user_id: str,
                         expected_sha256: str = "",
                         confirm_download: bool = False) -> Dict[str, Any]:
    """Return a local .pfp path for a local path, URL, or registry ref."""
    value = str(ref or "").strip()
    if not value:
        raise PfpRegistryError("package path or ref is required")
    local = Path(value).expanduser()
    if local.exists():
        return {"path": str(local), "downloaded": False, "sha256": "", "url": ""}
    preview = preview_package_download(
        value, user_id=user_id, expected_sha256=expected_sha256)
    if preview["remote"] and not confirm_download:
        return preview
    expected = preview.get("sha256", "")
    url = preview.get("url", value)
    if _is_http_url(url):
        downloaded = download_pfp(
            url,
            expected_sha256=expected,
            expected_size=int(preview.get("package_size") or 0),
        )
        downloaded["downloaded"] = True
        downloaded["confirmed"] = True
        return downloaded
    return {"path": value, "downloaded": False, "sha256": "", "url": ""}


def preview_package_download(ref: str, *, user_id: str,
                             expected_sha256: str = "") -> Dict[str, Any]:
    """Return remote package metadata without downloading the .pfp payload."""
    value = str(ref or "").strip()
    if not value:
        raise PfpRegistryError("package path or ref is required")
    if _is_http_url(value):
        size = _head_package_size(value)
        sha = _normalize_sha(expected_sha256) if expected_sha256 else ""
        return _download_confirmation(value, value, sha, size, source="url")
    for reg in _read_registries(user_id).get("registries", []):
        index = fetch_registry_index(reg.get("url", ""))
        for item in index.get("packages") or []:
            row = _normalize_package_row(item, reg, index)
            if value in {row.get("ref"), row.get("package")}:
                sha = _normalize_sha(expected_sha256) if expected_sha256 else row.get("sha256", "")
                return _download_confirmation(
                    value,
                    row.get("url", ""),
                    sha,
                    int(row.get("package_size") or 0),
                    source="registry",
                    registry=row.get("registry", ""),
                    registry_url=row.get("registry_url", ""),
                )
    return {"path": value, "downloaded": False, "remote": False, "sha256": "", "url": ""}


def _validate_registry_index(data: Dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise PfpRegistryError("Registry index must be an object")
    if data.get("format") != REGISTRY_FORMAT:
        raise PfpRegistryError("Unsupported package registry format")
    packages = data.get("packages")
    if not isinstance(packages, list):
        raise PfpRegistryError("Registry packages must be a list")
    if len(packages) > MAX_REGISTRY_PACKAGES:
        raise PfpRegistryError("Registry contains too many packages")
    for item in packages:
        if not isinstance(item, dict):
            raise PfpRegistryError("Registry package entries must be objects")
        if not item.get("package") or not item.get("version") or not item.get("pfp_url"):
            raise PfpRegistryError("Registry package entries require package, version, and pfp_url")
        _validate_pfp_url(str(item.get("pfp_url") or ""))
        _package_size_from_item(item)
        if item.get("sha256"):
            _normalize_sha(str(item.get("sha256")))


def _normalize_package_row(item: Dict[str, Any], reg: Dict[str, Any],
                           index: Dict[str, Any]) -> Dict[str, Any]:
    package = str(item.get("package") or "")
    version = str(item.get("version") or "")
    return {
        "registry": reg.get("name") or index.get("registry") or "",
        "registry_url": reg.get("url", ""),
        "registry_trusted": bool(reg.get("trusted", False)),
        "package": package,
        "version": version,
        "ref": f"{package}@{version}",
        "description": str(item.get("description") or ""),
        "url": str(item.get("pfp_url") or ""),
        "sha256": _normalize_sha(str(item.get("sha256") or "")) if item.get("sha256") else "",
        "package_size": _package_size_from_item(item),
        "size_display": _format_bytes(_package_size_from_item(item)),
        "developer_key": str(item.get("developer_key") or ""),
        "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
        "objects": item.get("objects") if isinstance(item.get("objects"), list) else [],
    }


def _package_size_from_item(item: Dict[str, Any]) -> int:
    raw = item.get("package_size", item.get("size", item.get("bytes")))
    try:
        size = int(raw)
    except (TypeError, ValueError) as exc:
        raise PfpRegistryError("Registry package entries require package_size") from exc
    if size < 0:
        raise PfpRegistryError("Registry package_size must be non-negative")
    return size


def _download_confirmation(ref: str, url: str, sha256: str, package_size: int,
                           *, source: str, registry: str = "",
                           registry_url: str = "") -> Dict[str, Any]:
    if package_size <= 0:
        raise PfpRegistryError("Remote package size is required before download")
    return {
        "ok": False,
        "remote": True,
        "downloaded": False,
        "requires_confirmation": True,
        "confirmation": "download_package",
        "message": (
            f"Package {ref} is {_format_bytes(package_size)}. "
            "Confirm download to continue."
        ),
        "ref": ref,
        "url": _validate_pfp_url(url),
        "sha256": sha256,
        "package_size": package_size,
        "size_display": _format_bytes(package_size),
        "source": source,
        "registry": registry,
        "registry_url": registry_url,
    }


def _head_package_size(url: str) -> int:
    clean_url = _validate_pfp_url(url)
    response = requests.head(
        clean_url,
        headers={"User-Agent": USER_AGENT},
        allow_redirects=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise PfpRegistryError(f"Package metadata fetch failed {response.status_code}: {clean_url}")
    raw = response.headers.get("Content-Length") or ""
    if not raw:
        raise PfpRegistryError("Remote package must expose Content-Length before download")
    try:
        size = int(raw)
    except ValueError as exc:
        raise PfpRegistryError("Remote package Content-Length is not valid") from exc
    if size <= 0:
        raise PfpRegistryError("Remote package Content-Length is required before download")
    return size


def _format_bytes(size: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(max(size, 0))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024


def _read_registries(user_id: str) -> Dict[str, Any]:
    path = _registries_file(user_id)
    if not path.exists():
        return {"registries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PfpRegistryError("Package registry config is not valid JSON") from exc
    if not isinstance(data, dict):
        raise PfpRegistryError("Package registry config must be an object")
    data.setdefault("registries", [])
    return data


def _write_registries(user_id: str, data: Dict[str, Any]) -> None:
    path = _registries_file(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _registries_file(user_id: str) -> Path:
    return _paths.REPOSITORY_DIR / "packages" / "registries" / f"{_safe_component(user_id)}.json"


def _validate_registry_url(url: str) -> str:
    clean = str(url or "").strip()
    parsed = urlparse(clean)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise PfpRegistryError("Registry URL must be http(s)")
    return clean


def _validate_pfp_url(url: str) -> str:
    clean = _validate_registry_url(url)
    if not urlparse(clean).path.endswith(".pfp"):
        raise PfpRegistryError("Package URL must point to a .pfp artifact")
    return clean


def _validate_registry_name(name: str) -> None:
    if not name or not _SAFE_NAME_RE.match(name):
        raise PfpRegistryError("Registry name contains unsafe characters")


def _default_registry_name(url: str) -> str:
    parsed = urlparse(url)
    return re.sub(r"[^A-Za-z0-9_.@+-]", "_", parsed.netloc or "registry")


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@+-]", "_", str(value or "")) or "default"


def _normalize_sha(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("sha256:"):
        hex_part = text.split(":", 1)[1]
    else:
        hex_part = text
        text = "sha256:" + text
    if not re.fullmatch(r"[0-9a-fA-F]{64}", hex_part):
        raise PfpRegistryError("sha256 must be 64 hex characters")
    return "sha256:" + hex_part.lower()


def _matches(query: str, row: Dict[str, Any]) -> bool:
    if not query:
        return True
    haystack = " ".join([
        str(row.get("package") or ""),
        str(row.get("description") or ""),
        " ".join(str(t) for t in row.get("tags") or []),
        " ".join(str(o) for o in row.get("objects") or []),
    ]).lower()
    return all(term in haystack for term in query.split())


def _rank(row: Dict[str, Any], query: str) -> int:
    package = str(row.get("package") or "").lower()
    desc = str(row.get("description") or "").lower()
    score = 0
    if query == package:
        score += 100
    if query in package:
        score += 50
    if query in desc:
        score += 10
    if row.get("sha256"):
        score += 5
    return score


def _dedupe(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    seen = set()
    for row in rows:
        key = (row.get("package"), row.get("version"), row.get("url"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _is_http_url(value: str) -> bool:
    return urlparse(str(value or "")).scheme in {"http", "https"}

