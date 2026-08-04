"""ServePfpExtensionAssets Task — serve static assets for installed PFP UI extensions.

Route pattern: `/chat/ext/<package_id>/<asset_hash>/<file_path>` where:
  - `<package_id>` matches an installed `ui_extension` in the requesting user's
    scope (or conversation scope when a conversation cookie is present);
  - `<asset_hash>` is the SHA-256 prefix recorded at install time for this
    specific asset — the immutable cache key;
  - `<file_path>` is the asset path declared in the package manifest.

Security:
  - whitelist: file must be listed in the install record's assets array;
  - integrity: the file content's SHA-256 is recomputed and must match the
    install-time digest. A tampered file refuses to serve.
  - path containment: the resolved file must live under the package's
    content_dir (no symlink/parent traversal can escape).
  - cache: `Cache-Control: public, max-age=31536000, immutable` (hash in URL).
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Tuple

from core import FlowFile, TaskFactory
from core.base_task import BaseTask
from core.pfp_package._pp_base import _UI_ASSET_EXTENSIONS

logger = logging.getLogger(__name__)

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("model/gltf+json", ".gltf")
mimetypes.add_type("application/octet-stream", ".vrm")
mimetypes.add_type("application/octet-stream", ".bin")
mimetypes.add_type("image/ktx2", ".ktx2")
mimetypes.add_type("application/octet-stream", ".basis")
mimetypes.add_type("application/octet-stream", ".fbx")
mimetypes.add_type("audio/mpeg", ".mp3")
mimetypes.add_type("audio/wav", ".wav")
mimetypes.add_type("audio/ogg", ".ogg")
mimetypes.add_type("audio/mp4", ".m4a")
mimetypes.add_type("audio/aac", ".aac")
mimetypes.add_type("audio/flac", ".flac")


# `.html` removed: a same-origin HTML page served from /chat/ext/... could
# run inline <script> under the user's session even though the runtime
# auto-loader only fetches .js/.css. The matching whitelist in core.pfp_package
# (_UI_ASSET_EXTENSIONS) refuses to install a package declaring .html assets;
# this server-side allow-list is the second layer.
_ALLOWED_EXTENSIONS = _UI_ASSET_EXTENSIONS
_BASE_PATH = "/chat/ext"


class _BoundedReader:
    """Read at most ``remaining`` bytes from an already-positioned file."""

    def __init__(self, source: BinaryIO, remaining: int):
        self.source = source
        self.remaining = remaining

    def read(self, size: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        if size < 0 or size > self.remaining:
            size = self.remaining
        chunk = self.source.read(size)
        self.remaining -= len(chunk)
        return chunk


class ServePfpExtensionAssetsTask(BaseTask):
    """Serve assets for installed PFP UI extensions."""

    TYPE = "servePfpExtensionAssets"
    VERSION = "1.0.0"
    NAME = "Serve PFP Extension Assets"
    DESCRIPTION = (
        "Serve JS/CSS/JSON assets for installed PFP `ui_extension` objects "
        "via /chat/ext/<package>/<asset_hash>/<file>."
    )
    ICON = "package"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "cache_control": {
                "type": "string",
                "required": False,
                "default": "public, max-age=31536000, immutable",
                "description": (
                    "Cache-Control header for asset responses. The default "
                    "is immutable because the URL embeds the file SHA-256."
                ),
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        http_path = (flowfile.get_attribute("http.path") or "").split("?", 1)[0]
        if not http_path.startswith(_BASE_PATH + "/"):
            return self._not_found(flowfile, "invalid extension asset path")
        rest = http_path[len(_BASE_PATH) + 1:]
        parts = rest.split("/", 2)
        if len(parts) != 3 or not all(parts):
            return self._not_found(flowfile, "expected /chat/ext/<package>/<hash>/<file>")
        package_id, asset_hash, file_path = parts
        file_path = file_path.lstrip("/")
        if ".." in file_path.split("/") or file_path.startswith("/") or "\x00" in file_path:
            return self._not_found(flowfile, "invalid file path")
        ext = Path(file_path).suffix.lower()
        if ext not in _ALLOWED_EXTENSIONS:
            return self._not_found(flowfile, f"unsupported asset type: {ext}")

        user_id = (flowfile.get_attribute("http.auth.principal") or "").strip()
        if not user_id:
            return self._not_found(flowfile, "authentication required")
        conversation_id = (flowfile.get_attribute("http.cookie.pawflow_conv") or "").strip()

        # Kill switch and per-conversation toggle: a disabled package must
        # not be servable at all. Returning 404 (rather than 403) hides the
        # presence of the package from a malicious page in another tab.
        from core.tool_mcp_filters import (
            _ui_extensions_globally_disabled, is_extension_enabled,
        )
        if _ui_extensions_globally_disabled():
            return self._not_found(flowfile, "ui extensions are disabled")
        if conversation_id and not is_extension_enabled(conversation_id, package_id):
            return self._not_found(flowfile, "extension disabled for this conversation")

        # Look up the asset across user + (optionally) conversation scope.
        from core.pfp_package import list_installed_ui_extensions
        scope = "conversation" if conversation_id else "user"
        records = list_installed_ui_extensions(
            user_id=user_id, conversation_id=conversation_id, scope=scope)
        match = None
        for rec in records:
            if rec.get("package") != package_id:
                continue
            for asset in rec.get("assets") or []:
                if asset.get("path") != file_path:
                    continue
                if not _asset_hash_matches(asset.get("sha256", ""), asset_hash):
                    continue
                match = (rec, asset)
                break
            if match:
                break
        if not match:
            return self._not_found(flowfile, "asset not found")
        rec, asset = match

        content_dir = Path(str(rec.get("content_dir") or "")).resolve()
        target = (content_dir / asset["path"]).resolve()
        try:
            target.relative_to(content_dir)
        except ValueError:
            return self._not_found(flowfile, "asset escapes content directory")
        if not target.is_file():
            return self._not_found(flowfile, "asset missing on disk")

        try:
            total_size = target.stat().st_size
            with target.open("rb") as source:
                flowfile.set_content_from_stream(source, total_size)
        except OSError as err:
            logger.warning("PFP asset read failed: %s", err)
            return self._not_found(flowfile, "asset read failed")

        expected = str(asset.get("sha256") or "").lower().replace("sha256:", "")
        actual_hash = hashlib.sha256()
        with flowfile.get_content_stream() as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                actual_hash.update(chunk)
        actual = actual_hash.hexdigest()
        if expected and actual != expected:
            logger.warning(
                "PFP asset hash mismatch %s/%s: expected=%s actual=%s",
                package_id, asset["path"], expected, actual)
            return self._not_found(flowfile, "asset integrity check failed")

        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "application/octet-stream"

        range_header = flowfile.get_attribute("http.header.range") or ""
        try:
            byte_range = _parse_byte_range(range_header, total_size)
        except ValueError:
            return self._range_not_satisfiable(flowfile, total_size)

        if byte_range is None:
            start, end = 0, max(total_size - 1, 0)
            status = "200"
            response_size = total_size
        else:
            start, end = byte_range
            status = "206"
            response_size = end - start + 1
        if byte_range is not None:
            try:
                with flowfile.get_content_stream() as source:
                    source.seek(start)
                    flowfile.set_content_from_stream(
                        _BoundedReader(source, response_size), response_size)
            except OSError as err:
                logger.warning("PFP asset range stream failed: %s", err)
                return self._not_found(flowfile, "asset read failed")

        flowfile.set_attribute("http.response.status", status)
        flowfile.set_attribute("http.response.header.Content-Type", mime_type)
        flowfile.set_attribute("http.response.header.Accept-Ranges", "bytes")
        flowfile.set_attribute("http.response.header.Content-Length", str(response_size))
        if byte_range is not None:
            flowfile.set_attribute(
                "http.response.header.Content-Range",
                f"bytes {start}-{end}/{total_size}")
        cache_control = self.config.get("cache_control",
                                         "public, max-age=31536000, immutable")
        if cache_control:
            flowfile.set_attribute("http.response.header.Cache-Control", cache_control)
        # Same-origin only — belt-and-suspenders against accidental embeds.
        flowfile.set_attribute("http.response.header.X-Content-Type-Options", "nosniff")
        return [flowfile]

    @staticmethod
    def _range_not_satisfiable(flowfile: FlowFile, size: int) -> List[FlowFile]:
        flowfile.set_content(b'{"error":"range not satisfiable"}')
        flowfile.set_attribute("http.response.status", "416")
        flowfile.set_attribute("http.response.header.Content-Type", "application/json")
        flowfile.set_attribute("http.response.header.Content-Range", f"bytes */{size}")
        flowfile.set_attribute("http.response.header.Accept-Ranges", "bytes")
        return [flowfile]

    @staticmethod
    def _not_found(flowfile: FlowFile, reason: str) -> List[FlowFile]:
        flowfile.set_content(f'{{"error":"{reason}"}}'.encode("utf-8"))
        flowfile.set_attribute("http.response.status", "404")
        flowfile.set_attribute("http.response.header.Content-Type",
                               "application/json")
        return [flowfile]


def _asset_hash_matches(stored: str, url_value: str) -> bool:
    """The URL may carry the full hex digest or a short prefix; both must match."""
    expected = (stored or "").lower().replace("sha256:", "")
    candidate = (url_value or "").lower().replace("sha256:", "")
    if not expected or not candidate:
        return False
    if len(candidate) < 12 or len(candidate) > len(expected):
        return False
    return expected.startswith(candidate)


def _parse_byte_range(value: str, size: int) -> Optional[Tuple[int, int]]:
    """Parse one RFC 7233 byte range; reject malformed or multipart ranges."""
    value = (value or "").strip()
    if not value:
        return None
    if not value.startswith("bytes=") or "," in value or size <= 0:
        raise ValueError("invalid byte range")
    spec = value[6:].strip()
    if "-" not in spec:
        raise ValueError("invalid byte range")
    start_text, end_text = spec.split("-", 1)
    if not start_text:
        if not end_text.isdigit() or int(end_text) <= 0:
            raise ValueError("invalid suffix range")
        length = min(int(end_text), size)
        return size - length, size - 1
    if not start_text.isdigit():
        raise ValueError("invalid range start")
    start = int(start_text)
    if start >= size:
        raise ValueError("range starts after content")
    if end_text:
        if not end_text.isdigit():
            raise ValueError("invalid range end")
        end = min(int(end_text), size - 1)
        if end < start:
            raise ValueError("range end precedes start")
    else:
        end = size - 1
    return start, end


TaskFactory.register(ServePfpExtensionAssetsTask)
