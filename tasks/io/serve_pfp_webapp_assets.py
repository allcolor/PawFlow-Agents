"""ServePfpWebAppAssets Task — serve standalone PFP `web_app` pages.

Route patterns:
  - `/apps/<package_id>/<name>/` (bare, no trailing hash) serves the
    declared `entry` file (typically an .html page) for that web_app object.
  - `/apps/<package_id>/<name>/<asset_hash>/<file_path>` serves any other
    declared asset (js/css/images/...), same immutable-cache pattern as
    `/chat/ext/<package>/<hash>/<file>`.

A `web_app` is a distinct object type from `ui_extension`: it is not
injected into the chat page DOM, it is served at its own route, so unlike
`/chat/ext/...` this allow-list includes `.html`. The page still runs on
the same origin and under the same authenticated session as `/chat` — it
can read/write same-origin state and call PawFlow APIs with the user's
ambient session cookie. That is the same shared-trust-domain model already
documented for `ui_extension`: install consent is the security gate, not
runtime isolation (real isolation would need a sandboxed origin).

Security:
  - authentication: `http.auth.principal` must be set (same gate as
    `/chat` and `/chat/ext/...`) — no anonymous access to any web_app page.
  - whitelist: the requested file must be listed in the install record's
    assets array for that specific web_app object.
  - integrity: for hashed asset requests, the file content's SHA-256 is
    recomputed and must match the install-time digest.
  - path containment: the resolved file must live under the package's
    content_dir (no symlink/parent traversal can escape).
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
from pathlib import Path
from typing import Any, Dict, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/woff2", ".woff2")

# Second layer allow-list (core.pfp_package._WEBAPP_ASSET_EXTENSIONS is the
# install-time whitelist). `.html` is intentionally included here, unlike
# the `/chat/ext/...` allow-list — this route is not injected into the
# chat page DOM, so a same-origin HTML page here does not run inside the
# chat shell's DOM/window.
_ALLOWED_EXTENSIONS = {".html", ".js", ".css", ".json", ".svg",
                       ".png", ".jpg", ".jpeg", ".webp",
                       ".woff", ".woff2"}
_BASE_PATH = "/apps"


class ServePfpWebAppAssetsTask(BaseTask):
    """Serve standalone pages/assets for installed PFP `web_app` objects."""

    TYPE = "servePfpWebAppAssets"
    VERSION = "1.0.0"
    NAME = "Serve PFP Web App Assets"
    DESCRIPTION = (
        "Serve the standalone page and assets for installed PFP `web_app` "
        "objects via /apps/<package>/<name>/ and "
        "/apps/<package>/<name>/<asset_hash>/<file>."
    )
    ICON = "package"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "asset_cache_control": {
                "type": "string",
                "required": False,
                "default": "public, max-age=31536000, immutable",
                "description": (
                    "Cache-Control header for hashed asset responses. The "
                    "default is immutable because the URL embeds the file "
                    "SHA-256."
                ),
            },
            "entry_cache_control": {
                "type": "string",
                "required": False,
                "default": "no-cache",
                "description": (
                    "Cache-Control header for the bare entry page — not "
                    "hash-keyed, so it must revalidate."
                ),
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        http_path = (flowfile.get_attribute("http.path") or "").split("?", 1)[0]
        if not http_path.startswith(_BASE_PATH + "/"):
            return self._not_found(flowfile, "invalid web app path")

        user_id = (flowfile.get_attribute("http.auth.principal") or "").strip()
        if not user_id:
            return self._not_found(flowfile, "authentication required")
        conversation_id = (flowfile.get_attribute("http.cookie.pawflow_conv") or "").strip()

        rest = http_path[len(_BASE_PATH) + 1:]
        entry_segments = rest.rstrip("/").split("/")
        if len(entry_segments) == 2 and all(entry_segments):
            package_id, name = entry_segments
            return self._serve_entry(flowfile, package_id, name, user_id, conversation_id)

        asset_parts = rest.split("/", 3)
        if len(asset_parts) == 4 and all(asset_parts[:3]):
            package_id, name, asset_hash, file_path = asset_parts
            return self._serve_asset(
                flowfile, package_id, name, asset_hash, file_path,
                user_id, conversation_id)

        return self._not_found(
            flowfile,
            "expected /apps/<package>/<name>/ or "
            "/apps/<package>/<name>/<hash>/<file>")

    def _lookup(self, package_id: str, name: str, user_id: str,
                conversation_id: str):
        from core.pfp_package import list_installed_web_apps
        scope = "conversation" if conversation_id else "user"
        records = list_installed_web_apps(
            user_id=user_id, conversation_id=conversation_id, scope=scope)
        for rec in records:
            if rec.get("package") == package_id and rec.get("name") == name:
                return rec
        return None

    def _serve_entry(self, flowfile: FlowFile, package_id: str, name: str,
                     user_id: str, conversation_id: str) -> List[FlowFile]:
        rec = self._lookup(package_id, name, user_id, conversation_id)
        if not rec:
            return self._not_found(flowfile, "web app not found")
        entry_path = str(rec.get("entry") or "")
        asset = next(
            (a for a in rec.get("assets") or [] if a.get("path") == entry_path),
            None)
        if not asset:
            return self._not_found(flowfile, "web app entry asset missing")
        content = self._read_verified(rec, asset)
        if content is None:
            return self._not_found(flowfile, "web app entry read failed")
        cache_control = self.config.get("entry_cache_control", "no-cache")
        return self._respond(flowfile, entry_path, content, cache_control)

    def _serve_asset(self, flowfile: FlowFile, package_id: str, name: str,
                     asset_hash: str, file_path: str, user_id: str,
                     conversation_id: str) -> List[FlowFile]:
        file_path = file_path.lstrip("/")
        if ".." in file_path.split("/") or file_path.startswith("/") or "\x00" in file_path:
            return self._not_found(flowfile, "invalid file path")
        ext = Path(file_path).suffix.lower()
        if ext not in _ALLOWED_EXTENSIONS:
            return self._not_found(flowfile, f"unsupported asset type: {ext}")

        rec = self._lookup(package_id, name, user_id, conversation_id)
        if not rec:
            return self._not_found(flowfile, "web app not found")
        asset = None
        for candidate in rec.get("assets") or []:
            if (candidate.get("path") == file_path
                    and _asset_hash_matches(candidate.get("sha256", ""), asset_hash)):
                asset = candidate
                break
        if not asset:
            return self._not_found(flowfile, "asset not found")
        content = self._read_verified(rec, asset)
        if content is None:
            return self._not_found(flowfile, "asset read failed")
        cache_control = self.config.get(
            "asset_cache_control", "public, max-age=31536000, immutable")
        return self._respond(flowfile, file_path, content, cache_control)

    @staticmethod
    def _read_verified(rec: Dict[str, Any], asset: Dict[str, Any]) -> bytes | None:
        content_dir = Path(str(rec.get("content_dir") or "")).resolve()
        target = (content_dir / asset["path"]).resolve()
        try:
            target.relative_to(content_dir)
        except ValueError:
            logger.warning("PFP web app asset escapes content directory: %s", asset["path"])
            return None
        if not target.is_file():
            return None
        try:
            content = target.read_bytes()
        except OSError as err:
            logger.warning("PFP web app asset read failed: %s", err)
            return None
        expected = str(asset.get("sha256") or "").lower().replace("sha256:", "")
        actual = hashlib.sha256(content).hexdigest()
        if expected and actual != expected:
            logger.warning(
                "PFP web app asset hash mismatch %s: expected=%s actual=%s",
                asset["path"], expected, actual)
            return None
        return content

    @staticmethod
    def _respond(flowfile: FlowFile, file_path: str, content: bytes,
                 cache_control: str) -> List[FlowFile]:
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "application/octet-stream"
        flowfile.set_content(content)
        flowfile.set_attribute("http.response.status", "200")
        flowfile.set_attribute("http.response.header.Content-Type", mime_type)
        if cache_control:
            flowfile.set_attribute("http.response.header.Cache-Control", cache_control)
        flowfile.set_attribute("http.response.header.X-Content-Type-Options", "nosniff")
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


TaskFactory.register(ServePfpWebAppAssetsTask)
