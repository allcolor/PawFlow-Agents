"""RelayService request transport and filesystem/git/exec operations.

_RelayFsOpsMixin: the _send_to_pool/_request* transport plus the public
filesystem, git, exec and http operations. Split out of filesystem_service.py
for the <=800-line rule; mixed into RelayService (one MRO, shared self state).
"""

import asyncio
import base64
import json
import logging
import threading
import uuid
from typing import Any, Dict, List

from services._relay_ws import (
    _is_relay_disconnect_error,
    _ws_send_frame,
    _RELAY_RETRY_ATTEMPTS,
    _RELAY_RETRY_DELAY_SECONDS,
    _RELAY_RETRY_EXHAUSTED_MARKER,
)

logger = logging.getLogger(__name__)


class _RelayFsOpsMixin:
    """Request transport + filesystem/git/exec/http operations for RelayService."""

    def supports_capability(self, capability: str) -> bool:
        """Return whether the connected relay advertised one exact capability."""
        capabilities = self._relay_info.get("capabilities", [])
        return str(capability or "") in capabilities

    def scratchdir_ensure(self, **kwargs):
        return self._request("scratchdir_ensure", ".", **kwargs)

    def scratchdir_status(self, **kwargs):
        return self._request("scratchdir_status", ".", **kwargs)

    def scratchdir_renew(self, **kwargs):
        return self._request("scratchdir_renew", ".", **kwargs)

    def scratchdir_clear(self, **kwargs):
        return self._request("scratchdir_clear", ".", **kwargs)

    def scratchdir_reconcile(self, **kwargs):
        return self._request("scratchdir_reconcile", ".", **kwargs)

    def _server_local_requested(self, kwargs: Dict[str, Any]) -> bool:
        return bool(kwargs.get("local") and self.config.get("server_managed"))

    def _request_server_local(self, action: str, path: str,
                              on_output=None, **kwargs) -> Any:
        if not self.config.get("server_local_exec"):
            raise PermissionError(
                "Server-local execution is disabled for this managed relay. "
                "An administrator can enable it in Server settings > Server relays.")
        kwargs.pop("_request_timeout", None)
        kwargs.pop("_retry_on_disconnect", None)
        from services._server_local_exec import execute_server_local
        return execute_server_local(action, path, kwargs, on_output=on_output)

    def _send_to_pool(self, pool: List[Dict], payload: bytes,
                      request_id: str = "", fence_guard=None):
        """Send `payload` over the WS pool, most-recently-connected first.

        Returns None on success, the last exception on total failure.
        Round-robin would be incoherent here — the pool only ever holds
        more than one entry during a reconnect overlap (a dying old WS
        plus the freshly attached new one), so splitting traffic across
        the two would route some requests to the dying socket. Multi-
        relay is handled at the conversation level via core/relay_bindings
        (link_relay + set_default_relay), not inside this pool.

        ``fence_guard`` is ``(lock, check)`` (B1-O): the frame is
        SCHEDULED under ``lock`` and only if ``check()`` returns falsy —
        the same lock the fence bump holds when it raises the watermark.
        A bump therefore either wins the lock first (``check`` refuses,
        nothing is scheduled) or loses it (the frame was already decided
        before the bump). The blocking wait happens OUTSIDE the lock.
        """
        last_err = None
        for conn in reversed(pool):
            writer, loop = conn["writer"], conn["loop"]
            send_lock = conn.get("send_lock")

            async def _send(w=writer, lk=send_lock):
                if lk is not None:
                    async with lk:
                        await _ws_send_frame(w, payload)
                else:
                    await _ws_send_frame(w, payload)

            if request_id:
                with self._pending_lock:
                    entry = self._pending.get(request_id)
                    if not entry:
                        return Exception("Relay disconnected")
                    entry[1]["_relay_reader"] = conn["reader"]
            try:
                if fence_guard is not None:
                    _guard_lock, _guard_check = fence_guard
                    with _guard_lock:
                        _stale = _guard_check()
                        if _stale:
                            return _stale
                        # Scheduling the send while holding the bump's
                        # own lock makes check-and-schedule atomic.
                        _future = asyncio.run_coroutine_threadsafe(
                            _send(), loop)
                    _future.result(timeout=10)
                else:
                    asyncio.run_coroutine_threadsafe(
                        _send(), loop).result(timeout=10)
                return None
            except Exception as e:
                last_err = e
                continue
        return last_err

    def _request(self, action: str, path: str = ".", **kwargs) -> Any:
        """Send a command to the relay and wait for the result (sync).

        Uses the pool's most-recently-connected entry first, falling
        back to older entries only on send failure.
        """
        wait_timeout = kwargs.pop("_request_timeout", None)
        retry_on_disconnect = kwargs.pop("_retry_on_disconnect", True)
        attempts = (_RELAY_RETRY_ATTEMPTS if retry_on_disconnect else 1)
        retry_request_id = uuid.uuid4().hex[:12]
        last_exc = None
        for attempt in range(attempts):
            try:
                return self._request_once(
                    action, path, _request_timeout=wait_timeout,
                    _request_id=retry_request_id, **kwargs)
            except Exception as exc:
                last_exc = exc
                if not retry_on_disconnect or not _is_relay_disconnect_error(exc):
                    raise
                if attempt >= attempts - 1:
                    break
                logger.warning(
                    "Relay request %s on %s lost connection; waiting up to %.0fs "
                    "for reconnection "
                    "(attempt %d/%d): %s",
                    action, self._service_id, _RELAY_RETRY_DELAY_SECONDS,
                    attempt + 2, attempts, exc)
                # Retrying only helps while there is something to reconnect.
                # A managed container that died (or was removed by hand) is
                # never re-created by anything else, so every retry — and every
                # later request — would fail until the server itself restarts.
                # No-op unless this service owns a container and that container
                # is really gone.
                self.ensure_managed_relay_alive()
                self._relay_available.wait(_RELAY_RETRY_DELAY_SECONDS)
        if last_exc is not None:
            raise Exception(
                f"{_RELAY_RETRY_EXHAUSTED_MARKER} for {action}: {last_exc}")
        raise Exception(f"Relay request failed for {action}")

    def _request_once(self, action: str, path: str = ".", **kwargs) -> Any:
        wait_timeout = kwargs.pop("_request_timeout", None)
        request_id = kwargs.pop("_request_id", "") or uuid.uuid4().hex[:12]
        if self._server_local_requested(kwargs):
            kwargs["request_id"] = request_id
            return self._request_server_local(action, path, **kwargs)
        with self._relay_pool_lock:
            pool = self._relay_pool[:]
        if not pool:
            if self.config.get("server_managed"):
                raise Exception(
                    f"Managed relay '{self._service_id}' is reconnecting.")
            raise Exception(
                f"Relay not connected to '{self._service_id}'. "
                f"Start: python tools/pawflow_relay.py "
                f"--server wss://<server_host>:<server_port>/ws/relay/{self._service_id} "
                f"--relay-id {self._service_id} --token <token> --dir <path>"
            )

        evt = threading.Event()
        holder: Dict[str, Any] = {}
        holder["_action"] = action

        # Run fence (B1-O): the dispatching agent thread's identity rides
        # on the command, checked server-side BEFORE the send and again
        # relay-side at dispatch — a fenced-out zombie's command is
        # refused at both places.
        _fence_fields: Dict[str, Any] = {}
        _fence_conv = _fence_agent = ""
        _fence_token = None
        try:
            from services._tool_relay_base import current_run_identity
            _identity = current_run_identity()
        except Exception:
            _identity = None
        if _identity and _identity[1] is not None:
            _run_handle, _fence_token, _fence_conv, _fence_agent = _identity
            _fence_fields = {
                "fence_key": f"{_fence_conv}:{_fence_agent}",
                "fence_token": int(_fence_token),
                "run_handle": str(_run_handle),
            }

        def _fence_lost() -> bool:
            if _fence_token is None:
                return False
            try:
                from services.tool_relay_service import ToolRelayService
                return not ToolRelayService.fence_highwater_allows(
                    _fence_conv, _fence_agent, int(_fence_token))
            except Exception:
                return False

        if _fence_lost():
            raise Exception(
                f"run superseded (fence lost) — relay action "
                f"'{action}' was NOT sent.")

        with self._pending_lock:
            self._pending[request_id] = (evt, holder)

        payload = json.dumps({
            "type": "command",
            "request_id": request_id,
            "action": action,
            "path": path,
            **_fence_fields,
            **kwargs,
        }).encode("utf-8")

        # The final check and the send are made ATOMIC with the fence
        # bump: the frame is scheduled under the SAME lock the bump holds
        # to raise the watermark (B1-O). Either the bump wins the lock —
        # this check refuses, nothing is scheduled — or it loses it — the
        # frame was decided before the bump. No check-then-send window
        # remains.
        _fence_guard = None
        if _fence_token is not None:
            try:
                from services.tool_relay_service import ToolRelayService
                _hw_key = f"{_fence_conv}:{_fence_agent}"

                def _guard_check():
                    # Called WHILE holding _fence_highwater_lock — read
                    # the map DIRECTLY, never re-enter the lock.
                    if int(_fence_token) < ToolRelayService._fence_highwater.get(
                            _hw_key, 0):
                        with self._pending_lock:
                            self._pending.pop(request_id, None)
                        return Exception(
                            f"run superseded (fence lost) — relay action "
                            f"'{action}' was NOT sent.")
                    return None

                _fence_guard = (
                    ToolRelayService._fence_highwater_lock, _guard_check)
            except Exception:
                _fence_guard = None

        last_err = self._send_to_pool(
            pool, payload, request_id=request_id, fence_guard=_fence_guard)

        if last_err:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            # A fence refusal is surfaced verbatim (it already reads
            # "fence lost"), not wrapped as a transport failure.
            if "fence lost" in str(last_err):
                raise Exception(str(last_err))
            raise Exception(f"Failed to send to relay: {last_err}")

        # Register a kill hook so a FORCE STOP at the tool-relay layer
        # propagates all the way down to the relay's subprocess: the
        # hook calls cancel_pending(rid) which both unblocks our local
        # evt.wait() below AND pushes a cancel_request envelope so the
        # relay terminates its Popen.
        try:
            from services.tool_relay_service import register_kill_hook
            register_kill_hook(lambda rid=request_id: self.cancel_pending(rid))
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        if not evt.wait(timeout=wait_timeout):
            self.cancel_pending(request_id)
            raise Exception(f"Relay timeout for {action} on {self._service_id}")

        if "error" in holder:
            raise Exception(holder["error"])

        data = holder.get("data")
        # Check for relay-level errors
        if isinstance(data, dict) and data.get("ok") is False:
            raise Exception(data.get("error", "Relay error"))
        return data

    def _request_with_progress(self, action: str, on_progress=None,
                               timeout=None, **kwargs) -> Any:
        """Like _request but supports progress callbacks.

        Progress messages arriving before the final result are dispatched
        to on_progress(data_dict) via _dispatch_progress.
        """
        with self._relay_pool_lock:
            pool = self._relay_pool[:]
        if not pool:
            raise Exception(f"Relay not connected to '{self._service_id}'.")

        request_id = uuid.uuid4().hex[:12]
        evt = threading.Event()
        holder: Dict[str, Any] = {}
        holder["_action"] = action
        if on_progress:
            holder["_on_progress"] = on_progress

        with self._pending_lock:
            self._pending[request_id] = (evt, holder)

        payload = json.dumps({
            "type": "command",
            "request_id": request_id,
            "action": action,
            **kwargs,
        }).encode("utf-8")

        last_err = self._send_to_pool(pool, payload, request_id=request_id)

        if last_err:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise Exception(f"Failed to send to relay: {last_err}")

        # Same kill-hook registration as `_request` — see comment there.
        try:
            from services.tool_relay_service import register_kill_hook
            register_kill_hook(lambda rid=request_id: self.cancel_pending(rid))
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        evt.wait(timeout=timeout)

        if not evt.is_set():
            self.cancel_pending(request_id)
            raise Exception("Timeout waiting for relay response")

        with self._pending_lock:
            self._pending.pop(request_id, None)
        if "error" in holder:
            raise Exception(holder["error"])

        data = holder.get("data")
        if isinstance(data, dict) and data.get("ok") is False:
            raise Exception(data.get("error", "Relay error"))
        return data

    def _request_stream(self, action: str, path: str = ".",
                        on_output=None, **kwargs) -> Any:
        """Like _request but registers an on_output callback for streaming.

        exec_output messages arriving before the final result are dispatched
        to on_output(stream, data) via _dispatch_exec_output.
        """
        wait_timeout = kwargs.pop("_request_timeout", None)
        if self._server_local_requested(kwargs):
            return self._request_server_local(
                action, path, on_output=on_output, **kwargs)
        with self._relay_pool_lock:
            pool = self._relay_pool[:]
        if not pool:
            raise Exception(f"Relay not connected to '{self._service_id}'.")

        request_id = uuid.uuid4().hex[:12]
        evt = threading.Event()
        holder: Dict[str, Any] = {}
        holder["_action"] = action
        if on_output:
            holder["_on_output"] = on_output

        with self._pending_lock:
            self._pending[request_id] = (evt, holder)

        payload = json.dumps({
            "type": "command",
            "request_id": request_id,
            "action": action,
            "path": path,
            **kwargs,
        }).encode("utf-8")

        last_err = self._send_to_pool(pool, payload, request_id=request_id)

        if last_err:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise Exception(f"Failed to send to relay: {last_err}")

        # Same kill-hook registration as `_request` — see comment there.
        try:
            from services.tool_relay_service import register_kill_hook
            register_kill_hook(lambda rid=request_id: self.cancel_pending(rid))
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        # Command-level timeout belongs to the relay action (for example socket
        # inactivity or subprocess execution). Only the private transport
        # timeout limits how long the server waits for that action to report.
        if not evt.wait(timeout=wait_timeout):
            self.cancel_pending(request_id)
            raise Exception(f"Relay timeout for {action} on {self._service_id}")

        if "error" in holder:
            raise Exception(holder["error"])

        data = holder.get("data")
        if isinstance(data, dict) and data.get("ok") is False:
            raise Exception(data.get("error", "Relay error"))
        return data

    # ── Filesystem interface ──

    def list_dir(self, path: str = ".", local: bool = False,
                 recursive: bool = False, max_entries: int = 0):
        from core.filesystem import FilesystemEntry
        data = self._request(
            "list_dir", path, local=local,
            recursive=bool(recursive), max_entries=int(max_entries or 0))
        return [FilesystemEntry(**e) if isinstance(e, dict) else e for e in data]

    def read_file(self, path: str, local: bool = False) -> bytes:
        data = self._request("read_file", path, local=local)
        if isinstance(data, dict) and "content" in data:
            return base64.b64decode(data["content"])
        return data.encode("utf-8") if isinstance(data, str) else data

    def hash_file(self, path: str, local: bool = False) -> dict:
        """Return a relay-computed SHA-256 without transferring file bytes."""

        result = self._request("hash_file", path, local=local)
        return {
            "bytes": int((result or {}).get("bytes") or 0),
            "sha256": str((result or {}).get("sha256") or ""),
        }

    def iter_file_chunks(self, path: str, local: bool = False,
                         chunk_size: int = 4 * 1024 * 1024):
        """Yield one relay file incrementally with a bounded decoded buffer."""
        first = self._request(
            "read_file_chunked", path, local=local,
            chunk_size=chunk_size)
        total_chunks = int(first.get("total_chunks", 1) or 1)
        actual_chunk_size = int(first.get("chunk_size", chunk_size) or chunk_size)
        yield base64.b64decode(first.get("data") or "")
        for index in range(1, total_chunks):
            chunk = self._request(
                "read_chunk", path, index=index,
                chunk_size=actual_chunk_size, local=local)
            yield base64.b64decode(chunk.get("data") or "")
            if chunk.get("done"):
                break

    def copy_file_to_local(self, path: str, local_path: str,
                           local: bool = False) -> dict:
        """Copy a relay file to a server-local path without holding it in RAM."""
        from pathlib import Path

        target = Path(local_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        written = 0
        temporary = target.with_name(
            f".{target.name}.pawflow-download-{uuid.uuid4().hex}.part")
        try:
            with temporary.open("wb") as handle:
                for data in self.iter_file_chunks(path, local=local):
                    if not data:
                        continue
                    handle.write(data)
                    written += len(data)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return {"path": str(target), "written": written}

    def copy_file(self, source_path: str, dest_path: str,
                  local: bool = False) -> dict:
        """Copy within one relay/host without transferring file bytes."""
        return self._request(
            "copy_file", source_path, dest_path=dest_path, local=local)

    def extract_zip_subtree(
        self, path: str, dest_path: str, *, artifact_root: str,
        local: bool = False,
    ) -> dict:
        """Validate and atomically materialize one ZIP artifact subtree."""

        return self._request(
            "extract_zip_subtree",
            path,
            dest_path=dest_path,
            artifact_root=artifact_root,
            local=local,
        )

    def website_browser_start(
        self, *, run_id: str, url: str, approved_origin: str,
        profile_path: str, timeout: int = 10, local: bool = False,
    ) -> dict:
        """Start or reuse one run-scoped fixed-script Chromium session."""

        return self._request(
            "website_browser_start", ".",
            run_id=run_id,
            url=url,
            approved_origin=approved_origin,
            profile_path=profile_path,
            timeout=timeout,
            local=local,
        )

    def website_browser_stop(self, session_id: str, *, local: bool = False) -> dict:
        """Stop one run-scoped Website Creator Chromium session."""

        return self._request(
            "website_browser_stop", ".", session_id=session_id, local=local,
        )

    def write_file(self, path: str, content: bytes, local: bool = False):
        if len(content) > 50 * 1024 * 1024:  # > 50MB → chunked
            self._write_chunked(path, content, local=local)
        else:
            self._request("write_file", path,
                           content=base64.b64encode(content).decode("ascii"),
                           base64=True, local=local)

    def atomic_write_file(self, path: str, content: bytes,
                          local: bool = False) -> dict:
        """Atomically replace one bounded relay file."""
        return self._request(
            "atomic_write_file", path,
            content=base64.b64encode(content).decode("ascii"),
            base64=True, local=local,
        )

    def append_file(self, path: str, content: bytes, expected_size: int,
                    local: bool = False) -> dict:
        """Append bytes at an exact checkpointed relay-file offset."""
        return self._request(
            "append_file", path,
            content=base64.b64encode(content).decode("ascii"),
            base64=True, expected_size=expected_size, local=local,
        )

    def truncate_file(self, path: str, size: int, *, expected_size: int | None = None,
                      local: bool = False) -> dict:
        """Truncate a relay file without allowing implicit extension."""
        kwargs = {"size": size, "local": local}
        if expected_size is not None:
            kwargs["expected_size"] = expected_size
        return self._request("truncate_file", path, **kwargs)

    def _write_chunked(self, path: str, content: bytes, local: bool = False):
        """Write a large file in chunks via the relay."""
        chunk_size = 1024 * 1024  # 1MB
        total = len(content)
        for i in range(0, total, chunk_size):
            chunk = content[i:i + chunk_size]
            done = (i + chunk_size) >= total
            self._request("write_file_chunked", path,
                           index=i // chunk_size,
                           data=base64.b64encode(chunk).decode("ascii"),
                           done=done, local=local)

    def write_file_stream(self, path: str, chunks, expected_size: int,
                          local: bool = False) -> int:
        """Write bounded chunks without ever joining the complete file."""
        written = 0
        index = 0
        for chunk in chunks:
            if not chunk:
                continue
            written += len(chunk)
            if written > expected_size:
                raise ValueError("Upload stream exceeded Content-Length")
            self._request(
                "write_file_chunked", path, index=index,
                data=base64.b64encode(chunk).decode("ascii"),
                done=written == expected_size, local=local,
                # This action appends every index after zero. If the relay wrote
                # a chunk but its response was lost, replaying it after reconnect
                # would silently duplicate bytes. Fail the temporary upload
                # instead; its caller removes it and never publishes corruption.
                _retry_on_disconnect=False)
            index += 1
        if written != expected_size:
            raise ValueError(
                f"Upload stream size mismatch: {written} != {expected_size}")
        if expected_size == 0:
            self._request("write_file", path, content="", base64=True,
                          local=local)
        return written

    def delete_file(self, path: str, local: bool = False):
        self._request("delete_file", path, local=local)

    def mkdir(self, path: str, local: bool = False):
        self._request("mkdir", path, local=local)

    def stat(self, path: str, local: bool = False):
        from core.filesystem import FilesystemEntry
        import dataclasses
        data = self._request("stat", path, local=local)
        if isinstance(data, dict):
            # Filter to known fields only (relay may return extra like 'created')
            valid = {f.name for f in dataclasses.fields(FilesystemEntry)}
            return FilesystemEntry(**{k: v for k, v in data.items() if k in valid})
        return data

    def exists(self, path: str, local: bool = False) -> bool:
        data = self._request("exists", path, local=local)
        return data.get("exists", False) if isinstance(data, dict) else bool(data)

    def search(self, path: str, pattern: str, recursive: bool = True,
               local: bool = False, limit: int = 500):
        return self._request("search", path, pattern=pattern,
                             recursive=recursive, local=local, limit=limit)

    def grep(self, path: str, regex: str, recursive: bool = True, **kwargs):
        return self._request("grep", path, regex=regex, recursive=recursive, **kwargs)

    def find_replace(self, path: str, pattern: str, replacement: str,
                     local: bool = False, multiline: bool = False):
        return self._request("find_replace", path, pattern=pattern,
                             replacement=replacement, local=local,
                             multiline=multiline)

    def edit(self, path: str, old_string: str, new_string: str,
             replace_all: bool = False, local: bool = False,
             fuzzy: bool = False, fuzzy_threshold=None):
        kwargs = {
            "old_string": old_string,
            "new_string": new_string,
            "replace_all": replace_all,
            "local": local,
        }
        if fuzzy:
            kwargs["fuzzy"] = True
        if fuzzy_threshold is not None:
            kwargs["fuzzy_threshold"] = fuzzy_threshold
        return self._request("edit", path, **kwargs)

    def batch_edit(self, edits: list, replace_all: bool = False,
                   local: bool = False, fuzzy: bool = False,
                   fuzzy_threshold=None):
        kwargs = {"edits": edits, "replace_all": replace_all, "local": local}
        if fuzzy:
            kwargs["fuzzy"] = True
        if fuzzy_threshold is not None:
            kwargs["fuzzy_threshold"] = fuzzy_threshold
        return self._request("batch_edit", ".", **kwargs)

    def apply_patch(self, patch: str, local: bool = False):
        return self._request("apply_patch", ".", patch=patch, local=local)

    def edit_notebook(self, path: str, cell_index: int, new_source: str = "",
                      cell_type: str = "", operation: str = "edit",
                      local: bool = False):
        return self._request("edit_notebook", path, cell_index=cell_index,
                              new_source=new_source, cell_type=cell_type,
                              operation=operation, local=local)

    def exec(self, path: str, command: str, timeout=None, shell: str = "", env: dict = None,
             local: bool = False):
        kwargs = {"command": command}
        request_timeout = None
        if timeout is not None:
            kwargs["timeout"] = timeout
            try:
                request_timeout = max(1.0, float(timeout)) + 5.0
            except (TypeError, ValueError):
                request_timeout = None
        if shell:
            kwargs["shell"] = shell
        if env:
            kwargs["env"] = env
        if local:
            kwargs["local"] = True
        if request_timeout is not None:
            kwargs["_request_timeout"] = request_timeout
        return self._request("exec", path, **kwargs)

    def exec_argv(self, path: str, argv: list[str], timeout=None,
                  env: dict = None, local: bool = False):
        """Execute an explicit argument vector without invoking a shell."""
        kwargs = {"argv": list(argv)}
        request_timeout = None
        if timeout is not None:
            kwargs["timeout"] = timeout
            try:
                request_timeout = max(1.0, float(timeout)) + 5.0
            except (TypeError, ValueError):
                request_timeout = None
        if env:
            kwargs["env"] = env
        if local:
            kwargs["local"] = True
        if request_timeout is not None:
            kwargs["_request_timeout"] = request_timeout
        return self._request("exec", path, **kwargs)

    def exec_stream(self, path: str, command: str, timeout=None,
                    shell: str = "", on_output=None):
        """Execute a command with streaming output via on_output(stream, data).

        Returns the final result dict (stdout, stderr, returncode).
        on_output is called for each line as it arrives from the relay.
        """
        kwargs = {"command": command, "timeout": timeout}
        if shell:
            kwargs["shell"] = shell
        return self._request_stream("exec_stream", path, on_output=on_output, **kwargs)

    def http_fetch(self, url: str, method: str = "GET",
                    headers: dict = None, body: bytes = b"",
                    timeout: int = 300, local: bool = False,
                    max_bytes: int = 0, public_only: bool = False) -> dict:
        """Sync HTTP fetch via the relay container.

        Returns {ok, status, headers, body_bytes} — body is decoded
        from the relay's base64 wire format. Use this when PawFlow's
        own HTTP stack is fingerprint-blocked (Cloudflare on Windows
        Python urllib) but the relay's Linux stack works.

        `local=True` forwards to the user's host helper (PAWFLOW_HOST_HELPER),
        same semantic as the screen / desktop actions.
        """
        import base64 as _b64
        _body = body if isinstance(body, (bytes, bytearray)) else (body or b"")
        result = self._request(
            "http_fetch", ".",
            local=local,
            url=url,
            method=method,
            headers=headers or {},
            body=_b64.b64encode(bytes(_body)).decode("ascii") if _body else "",
            timeout=timeout,
            max_bytes=max(0, int(max_bytes or 0)),
            public_only=bool(public_only),
        )
        if not isinstance(result, dict) or not result.get("ok"):
            err = (result or {}).get("error", "http_fetch returned no result")
            raise Exception(f"relay http_fetch failed: {err}")
        b64 = result.get("body", "")
        body_bytes = _b64.b64decode(b64) if b64 else b""
        return {
            "ok": True,
            "status": int(result.get("status", 0)),
            "headers": result.get("headers") or {},
            "body_bytes": body_bytes,
            "url": str(result.get("url") or url),
        }

    def http_fetch_to_file(
        self,
        url: str,
        path: str,
        *,
        headers: dict | None = None,
        timeout: int = 300,
        max_bytes: int,
        public_only: bool = True,
        expected_kind: str = "",
        local: bool = False,
    ) -> dict:
        """Stream a bounded public response to one atomically replaced relay file."""

        result = self._request(
            "http_fetch_to_file",
            path,
            local=local,
            url=url,
            method="GET",
            headers=headers or {},
            timeout=timeout,
            max_bytes=int(max_bytes),
            public_only=bool(public_only),
            expected_kind=str(expected_kind or ""),
        )
        if not isinstance(result, dict) or not result.get("ok"):
            error = (result or {}).get(
                "error", "http_fetch_to_file returned no result",
            )
            raise Exception(f"relay http_fetch_to_file failed: {error}")
        return {
            "status": int(result.get("status") or 0),
            "headers": dict(result.get("headers") or {}),
            "url": str(result.get("url") or url),
            "bytes": int(result.get("bytes") or 0),
            "sha256": str(result.get("sha256") or ""),
            "content_type": str(result.get("content_type") or ""),
            "saved": bool(result.get("saved")),
        }

    def http_fetch_stream(self, url: str, method: str = "GET",
                           headers: dict = None, body: bytes = b"",
                           timeout: int = 300, local: bool = True,
                           on_output=None):
        """Fetch an HTTP URL through the relay with streaming response.

        on_output(kind, data) is called with kind in {"start", "chunk", "end"}.
        Used by the /relay-proxy/ route to pipe Anthropic API calls through
        a user-local endpoint (llama-server, etc.).

        local=True ensures the request is executed on the user's host (via
        PawCode CLI), not inside the relay container — so 'localhost' in
        the target URL means the user's actual localhost.
        """
        import base64 as _b64
        _body = body if isinstance(body, (bytes, bytearray)) else (body or b"")
        return self._request_stream(
            "http_fetch", ".",
            on_output=on_output,
            local=local,
            url=url,
            method=method,
            headers=headers or {},
            body=_b64.b64encode(bytes(_body)).decode("ascii") if _body else "",
            timeout=timeout,
        )

    def http_proxy_stream(self, port: int, method: str = "GET",
                          req_path: str = "/", req_headers: dict = None,
                          body: bytes = b"", timeout: int = 300,
                          on_output=None):
        """Stream one relay-container localhost HTTP response.

        This is used for code-server and capability port forwards. Unlike the
        removed inline ``http_proxy`` contract, no complete response body is
        materialized or base64-encoded as one JSON value.
        """
        import base64 as _b64
        _body = body if isinstance(body, (bytes, bytearray)) else (body or b"")
        return self._request_stream(
            "http_proxy", ".", on_output=on_output,
            port=int(port), method=method, req_path=req_path,
            req_headers=req_headers or {},
            req_body=_b64.b64encode(bytes(_body)).decode("ascii")
            if _body else "",
            timeout=int(timeout),
        )

    # ── Git ──

    # ── Aliases (LLMs often drop the _file suffix) ──

    read = read_file
    write = write_file
    delete = delete_file

    # ── Git ──

    def git_status(self, path="."): return self._request("git_status", path)
    def git_log(self, path=".", count=10): return self._request("git_log", path, count=count)
    def git_diff(self, path=".", ref=""): return self._request("git_diff", path, ref=ref)
    def git_commit(self, path=".", message="", files=None, amend=False): return self._request("git_commit", path, message=message, files=files or [], amend=amend)
    def git_pull(self, path="."): return self._request("git_pull", path)
    def git_push(self, path="."): return self._request("git_push", path)
    def git_checkout(self, path=".", ref=""): return self._request("git_checkout", path, ref=ref)
    def git_add(self, path=".", files=None): return self._request("git_add", path, files=files or [])
    def git_reset(self, path=".", files=None, ref="", mode="mixed"): return self._request("git_reset", path, files=files or [], ref=ref, mode=mode)
    def git_stash(self, path=".", operation="push", message="", index=0): return self._request("git_stash", path, operation=operation, message=message, index=index)
    def git_branch(self, path=".", operation="list", branch="", base="", force=False): return self._request("git_branch", path, operation=operation, branch=branch, base=base, force=force)
    def git_merge(self, path=".", branch="", no_ff=False): return self._request("git_merge", path, branch=branch, no_ff=no_ff)
    def git_rebase(self, path=".", onto="", operation="start"): return self._request("git_rebase", path, onto=onto, operation=operation)
    def git_cherry_pick(self, path=".", commits=None): return self._request("git_cherry_pick", path, commits=commits or [])
    def git_tag(self, path=".", operation="list", tag="", message=""): return self._request("git_tag", path, operation=operation, tag=tag, message=message)
    def git_blame(self, path=".", file="", start_line=0, end_line=0): return self._request("git_blame", path, file=file, start_line=start_line, end_line=end_line)
    def project_init(self, path=".", force=False): return self._request("project_init", path, force=force)

    def git_worktree_list(self, path="."):
        return self._request("git_worktree_list", path)

    def git_worktree_add(self, path=".", branch="", worktree_path="", create_new_branch=False):
        return self._request("git_worktree_add", path, branch=branch,
                              worktree_path=worktree_path, create_new_branch=create_new_branch)

    def git_worktree_remove(self, path=".", worktree_path=""):
        return self._request("git_worktree_remove", path, worktree_path=worktree_path)
