"""Run-scoped Chromium CDP-pipe sessions for Website Creator extraction."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import select
import shutil
import subprocess  # nosec B404 - launches one reviewed Chromium binary
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


logger = logging.getLogger(__name__)


MAX_EXTRACTION_BYTES = 32 * 1024 * 1024
MAX_CDP_MESSAGE_BYTES = 2 * 1024 * 1024
CDP_CHUNK_CHARS = 256 * 1024
_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")
_SCRIPT_SCHEMAS = {
    "rendered_inventory_v1": "rendered_inventory.v1",
    "dom_outline_v1": "dom_outline.v1",
    "computed_assets_v1": "computed_assets.v1",
}

_SCRIPT_BODIES = {
    "rendered_inventory_v1": """
const selectors = 'a[href],img,source[srcset],link[href],script[src],video[src],audio[src]';
const items = Array.from(document.querySelectorAll(selectors)).slice(0, options.max_items).map((node) => ({
  tag: node.tagName.toLowerCase(), href: node.href || '', src: node.currentSrc || node.src || '',
  srcset: node.srcset || '', rel: node.rel || '', type: node.type || ''
}));
return {schema_version:'rendered_inventory.v1',script_id:'rendered_inventory_v1',url:location.href,items,counts:{items:items.length}};
""",
    "dom_outline_v1": """
const selectors = 'h1,h2,h3,h4,h5,h6,main,nav,header,footer,section,article,form';
const items = Array.from(document.querySelectorAll(selectors)).slice(0, options.max_items).map((node) => ({
  tag: node.tagName.toLowerCase(), id: node.id || '', role: node.getAttribute('role') || '',
  text: (node.innerText || '').trim().slice(0, 500)
}));
return {schema_version:'dom_outline.v1',script_id:'dom_outline_v1',url:location.href,items,counts:{items:items.length}};
""",
    "computed_assets_v1": """
const items = [];
for (const node of Array.from(document.querySelectorAll('*'))) {
  if (items.length >= options.max_items) break;
  const style = getComputedStyle(node);
  const background = style.backgroundImage || '';
  const src = node.currentSrc || node.src || '';
  if (src || (background && background !== 'none')) items.push({tag:node.tagName.toLowerCase(),src,background});
}
return {schema_version:'computed_assets.v1',script_id:'computed_assets_v1',url:location.href,items,counts:{items:items.length}};
""",
}


@dataclass
class BrowserSession:
    session_id: str
    target_id: str
    cdp_session_id: str
    approved_origin: str
    profile_path: Path
    transport: Any
    process: Any
    run_id: str = ""


class CdpPipeTransport:
    """Minimal bounded request/response transport for Chromium's NUL-framed pipe."""

    def __init__(self, read_fd: int, write_fd: int):
        self.read_fd = int(read_fd)
        self.write_fd = int(write_fd)
        self._next_id = 1
        self._buffer = bytearray()
        self._lock = threading.Lock()

    def close(self) -> None:
        for descriptor in (self.read_fd, self.write_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass

    def _message(self, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            marker = self._buffer.find(0)
            if marker >= 0:
                raw = bytes(self._buffer[:marker])
                del self._buffer[:marker + 1]
                if len(raw) > MAX_CDP_MESSAGE_BYTES:
                    raise ValueError("Chromium CDP message exceeds configured maximum")
                value = json.loads(raw.decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("Chromium CDP message must be an object")
                return value
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Chromium CDP response timed out")
            ready, _, _ = select.select([self.read_fd], [], [], remaining)
            if not ready:
                raise TimeoutError("Chromium CDP response timed out")
            chunk = os.read(self.read_fd, 64 * 1024)
            if not chunk:
                raise ConnectionError("Chromium CDP pipe disconnected")
            self._buffer.extend(chunk)
            if len(self._buffer) > MAX_CDP_MESSAGE_BYTES:
                raise ValueError("Chromium CDP message exceeds configured maximum")

    def request(
        self, method: str, params: dict[str, Any], *, session_id: str = "", timeout: float = 10,
    ) -> dict[str, Any]:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            message: dict[str, Any] = {"id": request_id, "method": method, "params": params}
            if session_id:
                message["sessionId"] = session_id
            raw = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\0"
            if len(raw) > MAX_CDP_MESSAGE_BYTES:
                raise ValueError("Chromium CDP request exceeds configured maximum")
            os.write(self.write_fd, raw)
            while True:
                response = self._message(float(timeout))
                if response.get("id") != request_id:
                    continue
                if response.get("error"):
                    raise RuntimeError(f"Chromium CDP error: {response['error']}")
                return response


def _origin(value: str) -> str:
    parsed = urlsplit(str(value or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Website Creator browser requires a public HTTP(S) origin")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Website Creator browser URL credentials are prohibited")
    host = parsed.hostname or ""
    port = parsed.port
    default = (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
    return f"{parsed.scheme}://{host}" + (f":{port}" if port and not default else "")


def _resolve_path(root_dir: str, value: str) -> Path:
    root = Path(root_dir).resolve()
    raw = str(value or "")
    if raw.startswith("/workspace/"):
        raw = raw[len("/workspace/"):]
    elif raw == "/workspace":
        return root
    candidate = Path(raw)
    target = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Website Creator browser path escapes the workspace") from exc
    return target


def _options(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"max_items", "include_hidden"}:
        raise ValueError("browser extraction options contain unsupported fields")
    max_items = value.get("max_items", 1000)
    if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= 5000:
        raise ValueError("browser extraction max_items must be between 1 and 5000")
    include_hidden = value.get("include_hidden", False)
    if not isinstance(include_hidden, bool):
        raise ValueError("browser extraction include_hidden must be boolean")
    return {"max_items": max_items, "include_hidden": include_hidden}


def _value(response: dict[str, Any]) -> Any:
    try:
        remote = response["result"]["result"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Chromium CDP response is malformed") from exc
    if remote.get("exceptionDetails"):
        raise ValueError("Chromium fixed extraction script failed")
    return remote.get("value")


def _initial_expression(script_id: str, options: dict[str, Any]) -> str:
    body = _SCRIPT_BODIES[script_id]
    return (
        "(() => { /*__PAWFLOW_EXTRACT_INIT__*/ const options="
        + json.dumps(options, separators=(",", ":"))
        + "; const result=(() => {"
        + body
        + "})(); const serialized=JSON.stringify(result);"
        + "globalThis.__pawflowWebsiteCreatorExtract=serialized;"
        + "return {chars:serialized.length,bytes:new TextEncoder().encode(serialized).length,"
        + "preview:serialized.slice(0,4000),counts:result.counts||{}}; })()"
    )


def _chunk_expression(index: int, start: int, end: int) -> str:
    return (
        "(() => { /*__PAWFLOW_EXTRACT_CHUNK__*/ const value="
        "globalThis.__pawflowWebsiteCreatorExtract||''; return {index:"
        f"{index},text:value.slice({start},{end})"
        "}; })()"
    )


def extract_session(
    session: BrowserSession,
    message: dict[str, Any],
    *,
    root_dir: str,
    chunk_chars: int = CDP_CHUNK_CHARS,
) -> dict[str, Any]:
    """Execute one fixed script and stream its serialized result atomically."""

    if session.process.poll() is not None:
        raise ConnectionError("Website Creator Chromium process is not running")
    if str(message.get("target_id") or "") != session.target_id:
        raise ValueError("browser extraction target_id is not bound to this session")
    script_id = str(message.get("script_id") or "")
    if script_id not in _SCRIPT_BODIES:
        raise ValueError("browser extraction script_id is unknown")
    timeout = message.get("timeout", 10)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 30:
        raise ValueError("browser extraction timeout must be between 1 and 30 seconds")
    max_bytes = message.get("max_bytes", MAX_EXTRACTION_BYTES)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_EXTRACTION_BYTES:
        raise ValueError("browser extraction maximum size is invalid")
    expected_origin = str(message.get("approved_origin") or session.approved_origin)
    if _origin(expected_origin + "/") != session.approved_origin:
        raise ValueError("browser extraction approved origin does not match the session")
    runtime = session.transport
    origin_result = runtime.request(
        "Runtime.evaluate",
        {"expression": "(() => { /*__PAWFLOW_ORIGIN__*/ return location.origin; })()", "returnByValue": True},
        session_id=session.cdp_session_id,
        timeout=float(timeout),
    )
    if str(_value(origin_result)) != session.approved_origin:
        raise ValueError("browser target final origin does not match the approved origin")
    metadata = _value(runtime.request(
        "Runtime.evaluate",
        {"expression": _initial_expression(script_id, _options(message.get("options"))), "returnByValue": True},
        session_id=session.cdp_session_id,
        timeout=float(timeout),
    ))
    if not isinstance(metadata, dict):
        raise ValueError("browser extraction metadata is malformed")
    chars = metadata.get("chars")
    byte_count = metadata.get("bytes")
    if (
        isinstance(chars, bool) or not isinstance(chars, int) or chars < 0
        or isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0
    ):
        raise ValueError("browser extraction metadata is malformed")
    if byte_count > max_bytes:
        raise ValueError("browser extraction exceeds the maximum size")
    output = _resolve_path(
        root_dir,
        str(message.get("write_to") or f"inventory/{script_id}.json"),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.extracting-{uuid.uuid4().hex}.part")
    digest = hashlib.sha256()
    written = 0
    consumed_chars = 0
    try:
        with temporary.open("xb") as handle:
            index = 0
            while consumed_chars < chars:
                end = min(chars, consumed_chars + int(chunk_chars))
                chunk_result = _value(runtime.request(
                    "Runtime.evaluate",
                    {"expression": _chunk_expression(index, consumed_chars, end), "returnByValue": True},
                    session_id=session.cdp_session_id,
                    timeout=float(timeout),
                ))
                if not isinstance(chunk_result, dict) or chunk_result.get("index") != index:
                    raise ValueError("browser extraction chunk order mismatch")
                text = chunk_result.get("text")
                if not isinstance(text, str) or not text:
                    raise ValueError("browser extraction chunk is malformed")
                consumed_chars += len(text)
                if consumed_chars > chars:
                    raise ValueError("browser extraction character count mismatch")
                raw = text.encode("utf-8")
                written += len(raw)
                if written > max_bytes:
                    raise ValueError("browser extraction exceeds the maximum size")
                digest.update(raw)
                handle.write(raw)
                index += 1
            handle.flush()
            os.fsync(handle.fileno())
        if consumed_chars != chars or written != byte_count:
            raise ValueError("browser extraction size verification failed")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    counts = metadata.get("counts") if isinstance(metadata.get("counts"), dict) else {}
    return {
        "path": str(message.get("write_to") or f"inventory/{script_id}.json"),
        "bytes": written,
        "sha256": digest.hexdigest(),
        "schema_version": _SCRIPT_SCHEMAS[script_id],
        "counts": counts,
        "preview": str(metadata.get("preview") or "")[:4000],
        "extraction_mode": "cdp_pipe",
    }


def build_chromium_command(binary: str, profile_path: Path, url: str) -> list[str]:
    """Return the reviewed visible Chromium command; never open a debug TCP port."""

    return [
        str(binary),
        "--remote-debugging-pipe",
        f"--user-data-dir={profile_path}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--new-window",
        str(url),
    ]


def _chromium_binary() -> str:
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found
    raise FileNotFoundError("Chromium is not installed on this relay")


def _launch_chromium(profile_path: Path, url: str, timeout: float) -> tuple[Any, CdpPipeTransport, str, str]:
    if os.name == "nt":
        raise RuntimeError("Chromium CDP pipe extraction requires the desktop relay container")
    from fs_screen import _display_env, _ensure_desktop
    _ensure_desktop()
    child_read, parent_write = os.pipe()
    parent_read, child_write = os.pipe()

    def _pipe_setup() -> None:
        os.dup2(child_read, 3)
        os.dup2(child_write, 4)

    command = build_chromium_command(_chromium_binary(), profile_path, url)
    try:
        process = subprocess.Popen(  # nosec B603 - fixed binary and reviewed arguments
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_display_env(),
            close_fds=True,
            pass_fds=(child_read, child_write),
            preexec_fn=_pipe_setup,  # nosec B606 - required for Chromium fd 3/4 pipe contract
        )
    except Exception:
        for descriptor in (child_read, parent_write, parent_read, child_write):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise
    os.close(child_read)
    os.close(child_write)
    transport = CdpPipeTransport(parent_read, parent_write)
    deadline = time.monotonic() + timeout
    try:
        target_id = ""
        while time.monotonic() < deadline and not target_id:
            response = transport.request(
                "Target.getTargets", {}, timeout=max(0.1, deadline - time.monotonic()),
            )
            for target in response.get("result", {}).get("targetInfos", []):
                if target.get("type") == "page":
                    target_id = str(target.get("targetId") or "")
                    break
            if not target_id:
                time.sleep(0.05)
        if not target_id:
            raise TimeoutError("Chromium did not create a page target")
        attached = transport.request(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
            timeout=max(0.1, deadline - time.monotonic()),
        )
        cdp_session_id = str(attached.get("result", {}).get("sessionId") or "")
        if not cdp_session_id:
            raise ValueError("Chromium did not return a CDP session id")
        return process, transport, target_id, cdp_session_id
    except Exception:
        transport.close()
        process.terminate()
        raise


def start_session(state, message: dict[str, Any], *, root_dir: str) -> dict[str, Any]:
    run_id = str(message.get("run_id") or "")
    if _RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError("Website Creator browser run_id is invalid")
    url = str(message.get("url") or "")
    approved_origin = _origin(str(message.get("approved_origin") or url))
    if _origin(url) != approved_origin:
        raise ValueError("Website Creator browser URL does not match approved origin")
    sessions = getattr(state, "website_browser_sessions", None)
    if sessions is None:
        sessions = {}
        state.website_browser_sessions = sessions
    for session in sessions.values():
        if session.run_id == run_id and session.process.poll() is None:
            return {
                "session_id": session.session_id,
                "target_id": session.target_id,
                "approved_origin": session.approved_origin,
                "profile_path": str(session.profile_path),
                "extraction_mode": "cdp_pipe",
                "already_running": True,
            }
    profile = _resolve_path(
        root_dir,
        str(message.get("profile_path") or f".pawflow-browser/{run_id}"),
    )
    if profile.exists():
        shutil.rmtree(profile)
    profile.mkdir(parents=True)
    process = transport = None
    try:
        process, transport, target_id, cdp_session_id = _launch_chromium(
            profile, url, float(message.get("timeout") or 10),
        )
        session_id = str(uuid.uuid4())
        sessions[session_id] = BrowserSession(
            session_id=session_id,
            target_id=target_id,
            cdp_session_id=cdp_session_id,
            approved_origin=approved_origin,
            profile_path=profile,
            transport=transport,
            process=process,
            run_id=run_id,
        )
        return {
            "session_id": session_id,
            "target_id": target_id,
            "approved_origin": approved_origin,
            "profile_path": str(profile),
            "extraction_mode": "cdp_pipe",
            "already_running": False,
        }
    except Exception:
        if transport is not None:
            transport.close()
        if process is not None and process.poll() is None:
            process.terminate()
        shutil.rmtree(profile, ignore_errors=True)
        raise


def stop_session(state, session_id: str) -> dict[str, Any]:
    sessions = getattr(state, "website_browser_sessions", {})
    session = sessions.pop(str(session_id or ""), None)
    if session is None:
        return {"stopped": False}
    session.transport.close()
    if session.process.poll() is None:
        session.process.terminate()
        try:
            session.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            session.process.kill()
    shutil.rmtree(session.profile_path, ignore_errors=True)
    return {"stopped": True, "session_id": session.session_id}


def cleanup_sessions(state) -> None:
    for session_id in list(getattr(state, "website_browser_sessions", {})):
        try:
            stop_session(state, session_id)
        except Exception:
            logger.debug(
                "Website Creator browser session cleanup failed: %s",
                session_id,
                exc_info=True,
            )


def extract_for_state(state, message: dict[str, Any], *, root_dir: str) -> dict[str, Any]:
    sessions = getattr(state, "website_browser_sessions", {})
    session_id = str(message.get("session_id") or "")
    session = sessions.get(session_id)
    if session is None:
        raise ValueError("Website Creator browser session is unavailable")
    return extract_session(session, message, root_dir=root_dir)
