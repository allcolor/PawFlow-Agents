"""RelayService connection, relay-session serving and message dispatch.

_RelayConnMixin: route registration, WS relay session loop, request/response
dispatch and pending-request tracking. Split out of filesystem_service.py for
the <=800-line rule; mixed into RelayService (one MRO, shared self state).
"""

import asyncio
import contextlib
import json
import logging
import threading
import time

from services._relay_ws import (
    _invalidate_tool_relay_registry_cache,
    _short_args,
    _sync_relay_scripts,
    _attach_sync_sock_to_loop,
    _ws_close_info,
    _ws_recv_frame,
    _ws_send_frame,
)

logger = logging.getLogger(__name__)


class _RelayConnMixin:
    """Connection, relay-session and dispatch methods for RelayService."""

    def connect(self):
        from services.http_listener_service import HTTPListenerService
        instances = HTTPListenerService.all_instances()
        if not instances:
            logger.warning('RelayService %s: no HTTPListenerService running yet, route not registered',
                           self._service_id)
            self._initialized = True
            return
        listener = next(iter(instances.values()))
        route = f'/ws/relay/{self._service_id}'
        self._route_path = route
        listener.register_route('GET', route, self._service_id, callback=None, ws_handler=self._handle_ws)
        self._connection = listener
        self._initialized = True
        logger.info('RelayService %s registered on main listener path %s', self._service_id, route)
        if self.config.get("server_managed"):
            self._start_managed_server_relay()

    def _start_managed_server_relay(self):
        token = str(self.config.get("token") or "")
        if not token:
            logger.warning("RelayService %s: managed relay has no token; cannot start container", self._service_id)
            return
        scope = str(self.config.get("server_scope") or self.config.get("_scope") or "user")
        scope_id = str(self.config.get("server_scope_id") or self.config.get("_scope_id") or "")
        user_id = str(self.config.get("server_user_id") or "")
        if not user_id and scope == "user":
            user_id = scope_id
        try:
            from core.server_relay_manager import ServerRelayManager
            ServerRelayManager.get_instance().spawn_service_relay(
                self._service_id,
                token,
                scope=scope,
                scope_id=scope_id,
                user_id=user_id,
                kind=str(self.config.get("server_kind") or "workspace"),
            )
            self._managed_container_started = True
        except Exception as e:
            logger.error("RelayService %s: failed to start managed relay container: %s",
                         self._service_id, e, exc_info=True)
            raise

    def is_connected(self) -> bool:
        with self._relay_pool_lock:
            self._relay_pool = [
                conn for conn in self._relay_pool
                if not getattr(conn.get("writer"), "_closed", False)
            ]
            return len(self._relay_pool) > 0

    def disconnect(self):
        if self._connection and getattr(self, '_route_path', ''):
            try:
                self._connection.unregister_routes(self._service_id)
            except Exception as e:
                logger.error('Failed to unregister relay route %s: %s', self._route_path, e, exc_info=True)
            self._connection = None
        # Release any open fds in the inverse-direction handlers
        with self._server_fs_lock:
            if self._server_fs is not None:
                try:
                    self._server_fs.close()
                except Exception as e:
                    logger.debug('server_fs.close failed: %s', e, exc_info=True)
                self._server_fs = None
        with self._filestore_fs_lock:
            if self._filestore_fs is not None:
                try:
                    self._filestore_fs.close()
                except Exception as e:
                    logger.debug('filestore_fs.close failed: %s', e, exc_info=True)
                self._filestore_fs = None
        with self._skills_fs_lock:
            if self._skills_fs is not None:
                try:
                    self._skills_fs.close()
                except Exception as e:
                    logger.debug('skills_fs.close failed: %s', e, exc_info=True)
                self._skills_fs = None

    def _handle_ws(self, sock, path_params, meta):
        import asyncio
        remote = meta.get('remote_addr', '?')
        try:
            loop = asyncio.new_event_loop()
            try:
                reader, writer = _attach_sync_sock_to_loop(sock, loop)
                loop.run_until_complete(
                    self._serve_relay_session(reader, writer, loop, remote))
            finally:
                with contextlib.suppress(Exception):
                    loop.run_until_complete(loop.shutdown_asyncgens())
                with contextlib.suppress(Exception):
                    try:
                        loop.run_until_complete(
                            loop.shutdown_default_executor(timeout=2.0))
                    except TypeError:
                        loop.run_until_complete(loop.shutdown_default_executor())
                loop.close()
        except Exception as e:
            logger.error('Relay WS handler error (%s): %s', remote, e, exc_info=True)

    async def _serve_relay_session(self, reader, writer, loop, remote):
        import asyncio
        service = self
        # One asyncio.Lock per WS connection — required because the
        # relay_request handler is now spawned as a task per inbound
        # frame (so a slow ffs.read doesn't block the next FUSE
        # callback in line), and concurrent tasks calling
        # writer.write()/drain() would interleave WS frames. The lock
        # is passed alongside the writer rather than attached to it
        # because StreamWriter implementations (e.g. _SockWriter on
        # Windows) can have __slots__ and refuse new attributes.
        send_lock = asyncio.Lock()
        relay_tasks = set()
        # [relay-diag] Per-connection id so the cold-start flap cycles can be
        # correlated and overlaps (two live conns at once) spotted.
        try:
            service._relay_conn_seq = getattr(service, '_relay_conn_seq', 0) + 1
            conn_id = service._relay_conn_seq
        except Exception:
            conn_id = 0
        conn_state = {
            'relay_id': '',
            'connected_at': time.time(),
            'last_msg_type': '',
            'last_request_id': '',
            'last_action': '',
            'close_info': '',
            'conn_id': conn_id,
        }
        try:
            opcode, payload = await _ws_recv_frame(reader)
            if opcode != 0x01:
                return
            reg = json.loads(payload.decode('utf-8'))
            if reg.get('type') != 'register':
                return
            relay_token = reg.get('token', '')
            if not relay_token or relay_token != service.config.get('token', ''):
                async with send_lock:
                    await _ws_send_frame(writer, json.dumps(
                        {'type': 'error', 'message': 'Token mismatch'}).encode())
                return
            relay_id = reg.get('relay_id', '')
            reg_info = reg.get('info', {})
            conn_state['relay_id'] = relay_id
            with self._relay_pool_lock:
                _alive = len(self._relay_pool)
            logger.debug('Relay connected: %s (addr=%s) conn#%d alive_before=%d',
                         relay_id, remote, conn_id, _alive)
            if reg_info.get('shells'):
                service._relay_shells = reg_info['shells']
            if reg_info:
                service._relay_info = reg_info
            service._relay_addr = remote
            async with send_lock:
                await _ws_send_frame(writer, json.dumps({
                    'type': 'registered', 'relay_id': relay_id}).encode())
            service._set_relay(reader, writer, loop, send_lock, relay_tasks)
            self._spawn_ctx_sync(reg_info, relay_id)
            try:
                from core.relay_key_integration import on_relay_connected
                on_relay_connected(service, relay_id)
            except Exception:
                logger.debug('relay key connect hook failed', exc_info=True)
            await self._relay_main_loop(
                reader, writer, service, send_lock, relay_tasks, conn_state)
            if conn_state.get('close_info'):
                logger.info(
                    'Relay disconnected: relay=%s addr=%s close_frame=%s '
                    'inflight=%d last_type=%s last_rid=%s last_action=%s',
                    conn_state.get('relay_id') or service._service_id, remote,
                    conn_state.get('close_info'), len(relay_tasks),
                    conn_state.get('last_msg_type', ''),
                    conn_state.get('last_request_id', ''),
                    conn_state.get('last_action', ''))
        except Exception as e:
            _err_str = str(e)
            # Peer-initiated close is the nominal end of a relay session:
            # clean FIN ("0 bytes read"), ECONNRESET (WinError 10054 —
            # container killed mid-read on Windows), or StreamReader's
            # IncompleteReadError. Log as info so we don't spam ERROR
            # tracebacks for routine shutdowns.
            _peer_close = (
                '0 bytes read' in _err_str
                or '10054' in _err_str
                or 'reset by peer' in _err_str.lower()
                or isinstance(e, (ConnectionResetError, asyncio.IncompleteReadError)))
            _lived = time.time() - conn_state.get('connected_at', time.time())
            if _peer_close:
                logger.info(
                    'Relay disconnected: relay=%s addr=%s conn#%s lived=%.1fs '
                    'closed_by_peer err_type=%s err=%s '
                    'inflight=%d last_type=%s last_rid=%s last_action=%s',
                    conn_state.get('relay_id') or service._service_id, remote,
                    conn_state.get('conn_id', '?'), _lived, type(e).__name__, e,
                    len(relay_tasks),
                    conn_state.get('last_msg_type', ''),
                    conn_state.get('last_request_id', ''),
                    conn_state.get('last_action', ''))
            else:
                logger.error(
                    'Relay connection error: relay=%s addr=%s err=%s '
                    'inflight=%d last_type=%s last_rid=%s last_action=%s',
                    conn_state.get('relay_id') or service._service_id, remote, e,
                    len(relay_tasks),
                    conn_state.get('last_msg_type', ''),
                    conn_state.get('last_request_id', ''),
                    conn_state.get('last_action', ''), exc_info=True)
        finally:
            try:
                service._clear_relay(reader=reader)
            except Exception as e:
                logger.debug('_clear_relay failed: %s', e, exc_info=True)
            try:
                from core.relay_key_integration import on_relay_disconnected
                on_relay_disconnected(conn_state.get('relay_id') or service._service_id)
            except Exception:
                logger.debug('relay key disconnect hook failed', exc_info=True)
            if relay_tasks:
                tasks = list(relay_tasks)
                for task in tasks:
                    task.cancel()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=2.0)
                relay_tasks.clear()
            try:
                writer.close()
            except Exception as e:
                logger.debug('writer.close failed: %s', e, exc_info=True)
    def _spawn_ctx_sync(self, reg_info, relay_id):
        service = self
        with self._ctx_sync_lock:
            if self._ctx_sync_active:
                logger.debug('Relay context sync already running for %s', relay_id)
                return
            self._ctx_sync_active = True

        def _fetch_ctx_and_sync():
            try:
                try:
                    ctx = service._request(
                        'project_context', '.', _request_timeout=30.0)
                    service._project_context = ctx
                    logger.info('Project context loaded for %s: %s',
                                 relay_id, ctx.get('project_types', []))
                except Exception as e:
                    logger.debug('Failed to load project context: %s', e, exc_info=True)
                try:
                    _sync_relay_scripts(service, reg_info)
                except Exception as e:
                    logger.debug('Relay script sync failed: %s', e, exc_info=True)
            finally:
                with service._ctx_sync_lock:
                    service._ctx_sync_active = False

        threading.Thread(target=_fetch_ctx_and_sync, daemon=True,
                         name=f'relay-ctx-{relay_id}').start()

    async def _relay_main_loop(self, reader, writer, service, send_lock,
                               relay_tasks, conn_state=None):
        import asyncio
        if conn_state is None:
            conn_state = {}
        KEEPALIVE = 120
        while True:
            try:
                opcode, payload = await asyncio.wait_for(
                    _ws_recv_frame(reader), timeout=KEEPALIVE)
            except asyncio.TimeoutError:
                reader_exception = None
                exception_getter = getattr(reader, "exception", None)
                if callable(exception_getter):
                    with contextlib.suppress(Exception):
                        reader_exception = exception_getter()
                if reader_exception is not None:
                    raise reader_exception
                async with send_lock:
                    await _ws_send_frame(
                        writer, json.dumps({'type': 'ping'}).encode())
                continue
            if opcode == 0x08:
                conn_state['close_info'] = _ws_close_info(payload)
                break
            if opcode == 0x09:
                async with send_lock:
                    await _ws_send_frame(writer, payload, opcode=0x0A)
                continue
            if opcode != 0x01:
                continue
            try:
                msg = json.loads(payload.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.warning('Ignoring malformed relay frame from %s: %s',
                               service._service_id, exc)
                continue
            conn_state['last_msg_type'] = str(msg.get('type') or '')
            conn_state['last_request_id'] = str(msg.get('request_id') or '')[:12]
            _action = msg.get('action') or msg.get('method') or msg.get('tool') or ''
            if not _action and msg.get('type') in ('result', 'error'):
                _action = service._pending_action(conn_state['last_request_id'])
            conn_state['last_action'] = str(_action)[:80]
            try:
                await self._dispatch_relay_msg(
                    msg, writer, service, send_lock, relay_tasks)
            except Exception as exc:
                logger.warning('Relay message dispatch failed for %s: %s',
                               service._service_id, exc, exc_info=True)

    async def _dispatch_relay_msg(self, msg, writer, service, send_lock, relay_tasks):
        import asyncio
        mtype = msg.get('type')
        if mtype == 'relay_request':
            # Fire-and-forget: each FUSE callback (sfs.read, ffs.getattr,
            # …) runs on the executor without blocking the WS receiver.
            # Otherwise CC reading 8 MB through 1 MB FUSE chunks holds
            # the main loop for the full sequence and every other FUSE
            # op (and any concurrent terminal/exec frame) queues up
            # behind it. The send back is serialized via send_lock so
            # concurrent tasks can't interleave frames.
            task = asyncio.create_task(
                service._handle_relay_request(msg, writer, send_lock))
            relay_tasks.add(task)
            task.add_done_callback(relay_tasks.discard)
            return
        if mtype in ('result', 'error'):
            service._resolve_pending(msg)
        elif mtype == 'progress':
            service._dispatch_progress(msg)
        elif mtype == 'exec_output':
            service._dispatch_exec_output(msg)
        elif mtype == 'http_response':
            service._dispatch_http_response(msg)
        elif mtype == 'terminal_data':
            try:
                from services.terminal_proxy import dispatch_terminal_data
                dispatch_terminal_data(msg.get('session_id', ''), msg.get('data', ''))
            except Exception as e:
                logger.debug('terminal_data dispatch failed: %s', e, exc_info=True)
        elif mtype == 'terminal_exit':
            try:
                from services.terminal_proxy import dispatch_terminal_exit
                dispatch_terminal_exit(msg.get('session_id', ''))
            except Exception as e:
                logger.debug('terminal_exit dispatch failed: %s', e, exc_info=True)
        elif mtype == 'cs_ws_data':
            try:
                from services.code_server_proxy import dispatch_cs_ws_data
                dispatch_cs_ws_data(service._service_id,
                                     msg.get('session_id', ''),
                                     msg.get('frame') or msg.get('data', ''),
                                     msg.get('opcode', 1))
            except Exception as e:
                logger.debug('cs_ws_data dispatch failed: %s', e, exc_info=True)
        elif mtype == 'cs_ws_close':
            try:
                from services.code_server_proxy import dispatch_cs_ws_close
                dispatch_cs_ws_close(service._service_id, msg.get('session_id', ''))
            except Exception as e:
                logger.debug('cs_ws_close dispatch failed: %s', e, exc_info=True)
        elif mtype == 'ping':
            async with send_lock:
                await _ws_send_frame(
                    writer, json.dumps({'type': 'pong'}).encode())

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def _get_server_fs(self):
        """Lazy-instantiate the inverse-direction FS handler.

        Returns None if no user_id is set yet — callers must reject the
        request rather than fall back to an unscoped handler.
        """
        if not self._user_id:
            return None
        with self._server_fs_lock:
            if self._server_fs is None:
                from services.relay_server_fs import RelayServerFs
                self._server_fs = RelayServerFs(self._user_id)
            return self._server_fs

    def _get_filestore_fs(self):
        """Lazy-instantiate the FileStore FUSE handler (ffs.* methods)."""
        if not self._user_id:
            return None
        with self._filestore_fs_lock:
            if self._filestore_fs is None:
                from services.relay_filestore_fs import RelayFileStoreFs
                self._filestore_fs = RelayFileStoreFs(self._user_id)
            return self._filestore_fs

    def _get_skills_fs(self):
        """Lazy-instantiate the skills-repo FUSE handler (skfs.* methods)."""
        if not self._user_id:
            return None
        with self._skills_fs_lock:
            if self._skills_fs is None:
                from services.relay_skills_fs import RelaySkillsFs
                self._skills_fs = RelaySkillsFs(self._user_id)
            return self._skills_fs

    async def _handle_relay_request(self, msg, writer, send_lock):
        """Service a relay→server FS op (the inverse direction).

        The relay's FUSE proxy forwards each FUSE callback as a
        `relay_request` over the existing tunnel. The method prefix
        selects the handler:
          - `sfs.*` → cc-sessions slot (CLAUDE_SESSIONS_DIR/<user>/)
          - `ffs.*` → virtualized FileStore view
          - `skfs.*` → virtualized Agent Skills repository view
        Anything else returns ENOSYS.
        """
        import asyncio
        import time as _time
        request_id = msg.get('request_id', '')
        method = msg.get('method', '')
        args = msg.get('args', {}) or {}
        _t0 = _time.monotonic()
        logger.debug("[server-fs] %s ENTER rid=%s args=%s",
                     method, request_id[:8], _short_args(args))
        if method.startswith('ffs.'):
            fs = self._get_filestore_fs()
        elif method.startswith('skfs.'):
            fs = self._get_skills_fs()
        elif method.startswith('sfs.'):
            fs = self._get_server_fs()
        else:
            fs = None
        if fs is None:
            if not self._user_id:
                reply = {'error': 'EACCES', 'errno': 13,
                         'message': 'relay has no owner user_id'}
            else:
                reply = {'error': 'ENOSYS', 'errno': 38,
                         'message': f'unknown method prefix: {method!r}'}
        else:
            # FS ops are sync — run on the loop's default executor so we
            # don't block other relay traffic on a slow disk. Hard 10s
            # cap: a single os.listdir/os.read on a hung WSL UNC path
            # must NOT freeze the FUSE callback indefinitely. After the
            # cap we send EIO; the kernel surfaces "Input/output error"
            # to the caller instead of blocking the whole shell.
            loop = asyncio.get_event_loop()
            try:
                reply = await asyncio.wait_for(
                    loop.run_in_executor(None, fs.handle, method, args),
                    timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "[server-fs] %s TIMEOUT rid=%s after 10s — returning EIO",
                    method, request_id[:8])
                reply = {'error': 'EIO', 'errno': 5,
                         'message': f'{method} timed out after 10s'}
            except Exception as exc:
                logger.warning(
                    "[server-fs] %s ERROR rid=%s: %s",
                    method, request_id[:8], exc, exc_info=True)
                reply = {'error': 'EIO', 'errno': 5,
                         'message': f'{method} failed: {exc}'}
        _dt = int((_time.monotonic() - _t0) * 1000)
        if 'error' in reply:
            logger.debug("[server-fs] %s EXIT rid=%s dt=%dms err=%s",
                         method, request_id[:8], _dt, reply.get('error'))
        else:
            logger.debug("[server-fs] %s EXIT rid=%s dt=%dms ok",
                         method, request_id[:8], _dt)
        envelope = {'type': 'relay_response', 'request_id': request_id, **reply}
        try:
            # Serialize so concurrent _handle_relay_request tasks (now
            # spawned via create_task) can't interleave WS frames.
            async with send_lock:
                await _ws_send_frame(writer, json.dumps(envelope).encode())
        except Exception as e:
            logger.warning('[server-fs] failed to send response for %s: %s',
                           request_id, e)

    # ── Relay connection management ──

    def _set_relay(self, reader, writer, loop, send_lock, relay_tasks=None):
        """Add a relay connection to the pool."""
        with self._relay_pool_lock:
            self._relay_pool.append({"reader": reader, "writer": writer,
                                      "loop": loop, "send_lock": send_lock,
                                      "tasks": relay_tasks})
            count = len(self._relay_pool)
        logger.debug("Relay pool: %d connection(s) for '%s'", count, self._service_id)
        _invalidate_tool_relay_registry_cache()
        self.push_remote_fs_manifest()
        # Notify all SSE clients to refresh resources (relay status changed)
        try:
            from core.conversation_event_bus import ConversationEventBus
            bus = ConversationEventBus.instance()
            with bus._lock:
                cids = list(bus._subscribers.keys())
            for cid in cids:
                bus.publish_event(cid, "relay_status_changed", {
                    "relay_id": self._service_id, "connected": True})
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def push_remote_fs_manifest(self, user_id: str = "") -> None:
        """Push the current conversation remote-FS manifest to all relay sockets."""
        owner = user_id or self._user_id
        if not owner:
            return
        try:
            from core.remote_fs_bindings import build_manifest_for_relay
            manifest = build_manifest_for_relay(self._service_id, owner)
        except Exception as exc:
            logger.debug("Remote FS manifest build failed for %s: %s",
                         self._service_id, exc, exc_info=True)
            return
        payload = json.dumps({
            "type": "remote_mount_manifest",
            "manifest": manifest,
        }).encode("utf-8")
        with self._relay_pool_lock:
            pool = self._relay_pool[:]
        for conn in pool:
            writer, loop = conn["writer"], conn["loop"]
            send_lock = conn.get("send_lock")

            async def _send(w=writer, lk=send_lock):
                if lk is not None:
                    async with lk:
                        await _ws_send_frame(w, payload)
                else:
                    await _ws_send_frame(w, payload)
            try:
                fut = asyncio.run_coroutine_threadsafe(_send(), loop)

                def _log_manifest_result(done, sid=self._service_id):
                    try:
                        done.result()
                    except Exception as exc:
                        logger.warning("[%s] remote FS manifest push failed: %s",
                                       sid, exc)

                fut.add_done_callback(_log_manifest_result)
            except Exception as exc:
                logger.warning("[%s] remote FS manifest push failed: %s",
                               self._service_id, exc)

    def _clear_relay(self, reader=None):
        """Remove a connection from the pool (by reader), or all if None."""
        removed = []
        with self._relay_pool_lock:
            if reader:
                kept = []
                for conn in self._relay_pool:
                    if conn["reader"] is reader:
                        removed.append(conn)
                    else:
                        kept.append(conn)
                self._relay_pool = kept
            else:
                removed = list(self._relay_pool)
                self._relay_pool.clear()
            alive = len(self._relay_pool)
        removed_readers = {conn.get("reader") for conn in removed}
        for conn in removed:
            for task in list(conn.get("tasks") or ()):
                task.cancel()
        cancelled_pending = 0
        with self._pending_lock:
            pending_items = list(self._pending.items())
            for rid, (evt, holder) in pending_items:
                pending_reader = holder.get("_relay_reader")
                if alive == 0 or pending_reader in removed_readers:
                    self._pending.pop(rid, None)
                    holder["error"] = "Relay disconnected"
                    evt.set()
                    cancelled_pending += 1
        logger.info(
            "Relay pool cleared for '%s': removed=%d alive=%d pending_cancelled=%d",
            self._service_id, len(removed), alive, cancelled_pending)
        _invalidate_tool_relay_registry_cache()
        # Notify SSE clients
        try:
            from core.conversation_event_bus import ConversationEventBus
            bus = ConversationEventBus.instance()
            with bus._lock:
                cids = list(bus._subscribers.keys())
            for cid in cids:
                bus.publish_event(cid, "relay_status_changed", {
                    "relay_id": self._service_id, "connected": alive > 0})
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def _resolve_pending(self, msg: dict):
        request_id = msg.get("request_id", "")
        with self._pending_lock:
            entry = self._pending.pop(request_id, None)
        if entry:
            evt, holder = entry
            if msg.get("type") == "error":
                holder["error"] = msg.get("error", "Unknown relay error")
            else:
                holder["data"] = msg.get("data", {})
            evt.set()

    def _pending_action(self, request_id: str) -> str:
        with self._pending_lock:
            entry = self._pending.get(request_id)
        if not entry:
            return ""
        return str(entry[1].get("_action") or "")

    def cancel_pending(self, request_id: str):
        """Cancel a pending request — unblock the waiting thread AND tell
        the relay to kill the underlying subprocess.

        Two-step:
          1. Push a `cancel_request` envelope to the relay so it can
             terminate the Popen registered for this request_id (see
             pawflow_relay.proc_registry).
          2. Pop the local pending entry and unblock the waiter with
             '[Interrupted by user]'. The thread that called `_request`
             returns immediately even if the relay's kill takes a moment.
        """
        if request_id:
            self._send_cancel_request_to_relay(request_id)
        with self._pending_lock:
            entry = self._pending.pop(request_id, None)
        if entry:
            evt, holder = entry
            holder["error"] = "[Interrupted by user]"
            evt.set()

    def _send_cancel_request_to_relay(self, request_id: str):
        """Broadcast a cancel_request envelope to every connected relay.

        Best-effort and non-blocking: send timeouts are absorbed silently
        because a missed cancel only means the action thread will exit
        naturally when its subprocess does. We log the failure for
        forensics but never raise — cancel_pending must always succeed
        in unblocking the local waiter.
        """
        with self._relay_pool_lock:
            pool = self._relay_pool[:]
        if not pool:
            return
        payload = json.dumps({
            "type": "cancel_request",
            "request_id": request_id,
        }).encode("utf-8")
        for conn in pool:
            writer, loop = conn["writer"], conn["loop"]
            send_lock = conn.get("send_lock")
            async def _send(w=writer, lk=send_lock):
                if lk is not None:
                    async with lk:
                        await _ws_send_frame(w, payload)
                else:
                    await _ws_send_frame(w, payload)
            try:
                asyncio.run_coroutine_threadsafe(_send(), loop).result(timeout=2)
            except Exception as e:
                logger.warning(
                    "[%s] cancel_request push failed for %s: %s",
                    self._service_id, request_id, e)

    def _dispatch_progress(self, msg: dict):
        """Forward progress messages to registered callback or terminal proxy."""
        data = msg.get("data", {})

        # Terminal data/exit from local terminal (forwarded via host helper progress)
        if isinstance(data, dict) and data.get("type") in ("terminal_data", "terminal_exit"):
            try:
                if data["type"] == "terminal_data":
                    from services.terminal_proxy import dispatch_terminal_data
                    dispatch_terminal_data(data.get("session_id", ""), data.get("data", ""))
                elif data["type"] == "terminal_exit":
                    from services.terminal_proxy import dispatch_terminal_exit
                    dispatch_terminal_exit(data.get("session_id", ""))
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            return

        request_id = msg.get("request_id", "")
        with self._pending_lock:
            entry = self._pending.get(request_id)
        if entry:
            _, holder = entry
            cb = holder.get("_on_progress")
            if cb:
                try:
                    cb(data)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def _dispatch_exec_output(self, msg: dict):
        """Forward streaming exec_output to the registered callback (if any)."""
        request_id = msg.get("request_id", "")
        with self._pending_lock:
            entry = self._pending.get(request_id)
        if entry:
            _, holder = entry
            cb = holder.get("_on_output")
            if cb:
                try:
                    cb(msg.get("stream", "stdout"), msg.get("data", ""))
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def _dispatch_http_response(self, msg: dict):
        """Forward streaming http_response chunks to the registered callback.

        Message kinds: "start", "chunk", "end" — see fs_http.action_http_fetch.
        """
        request_id = msg.get("request_id", "")
        with self._pending_lock:
            entry = self._pending.get(request_id)
        if entry:
            _, holder = entry
            cb = holder.get("_on_output")
            if cb:
                try:
                    cb(msg.get("kind", ""), msg.get("data"))
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
