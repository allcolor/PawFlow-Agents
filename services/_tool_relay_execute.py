"""ToolRelayService tool execute / do_execute / secrets env."""

import logging
import json
import threading
import time


logger = logging.getLogger(__name__)
# Split out of tool_relay_service.py for the <=800-line rule; composed back
# into ToolRelayService (invariant 2: MRO/shared class-state on the host).

import services._tool_relay_base as _trb  # noqa: E402
from services._tool_relay_base import _RELAY_TRANSPORT_RETRY_ATTEMPTS, _RELAY_TRANSPORT_RETRY_DELAY_SECONDS, _is_relay_transport_error, _is_relay_transport_result, _redact_secrets, _resolve_vars_in_args, _set_current_cancel_event, _set_current_kill_hooks  # noqa: F401,E402

class _ToolRelayExecuteMixin:
    """tool execute / do_execute / secrets env."""

    def _handle_execute(self, request_id: str, tool_name: str,
                        arguments, user_id: str,
                        conversation_id: str, agent_name: str,
                        relay_received_at: float = 0.0,
                        dispatch_started_at: float = 0.0) -> dict:
        handle_started = dispatch_started_at or time.perf_counter()
        relay_received_at = relay_received_at or handle_started
        # Defensive: arguments may arrive as JSON string (double-encoded by LLM)
        for _ in range(3):
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (json.JSONDecodeError, TypeError):
                    break
            else:
                break
        # Idempotent: if this request_id was already executed, return cached result
        with self._cache_lock:
            if request_id in self._result_cache:
                logger.info("[tool-relay] returning cached result for %s", request_id)
                return self._result_cache[request_id]
            if request_id in self._executing:
                # Another connection is executing this — wait for it
                evt = self._executing[request_id]
        if request_id in self._executing:
            logger.info("[tool-relay] waiting for in-flight request %s", request_id)
            evt.wait()
            with self._cache_lock:
                if request_id in self._result_cache:
                    return self._result_cache[request_id]
            return {"type": "result", "request_id": request_id,
                    "data": "Error: in-flight request completed without a cached result"}

        # Match CC tool_use id (enqueued by claude_code provider when it
        # emitted the tool_call SSE event). Matching lets background /
        # kill actions, keyed by UI-visible tc_id, reach this request.
        #
        # Race: the MCP bridge can forward execute_tool before the provider
        # stream exposes the UI-visible tool_call id. In that case the request
        # starts with request_id as its background id; enqueue_cc_tc can still
        # late-bind the provider id through bind_pending_cc_tc.
        #
        # Sentinel conversations (_compact, _memory_extract, …) never
        # push cc_tc — they have no UI subscribers, tool_call SSE is a
        # no-op, and they can't be backgrounded or killed per-tool
        # anyway (the whole sentinel session is the unit of cancel).
        # Skip the MISS log for them.
        cc_tc_id = ""
        _is_sentinel = bool(conversation_id) and conversation_id.startswith("_")
        try:
            from core.background_tool import pop_cc_tc, _args_hash
            _ah = _args_hash(arguments)
            cc_tc_id = pop_cc_tc(
                conversation_id, agent_name, tool_name, _ah)
            if not cc_tc_id and not _is_sentinel:
                # This can be a healthy relay-first race. The provider stream
                # may late-bind the tc_id once its tool_call item arrives.
                from core.background_tool import snapshot_cc_pending
                _pending_now = snapshot_cc_pending(conversation_id, agent_name)
                logger.debug(
                    "[tool-relay] cc_tc pending provider id conv=%s agent=%s "
                    "tool=%s args_hash=%s pending=%s",
                    conversation_id[:8], agent_name, tool_name, _ah,
                    _pending_now or "[]")
            elif cc_tc_id:
                logger.debug(
                    "[tool-relay] cc_tc matched tc_id=%s (tool=%s)",
                    cc_tc_id, tool_name)
        except Exception as _me:
            logger.debug("[tool-relay] cc_tc match skipped: %s", _me)

        # Mark as executing — cancel_event can abort, background_event
        # detaches the call (returns placeholder, thread keeps running).
        # Use the provider-visible tool id when available; otherwise fall
        # back to request_id so MCP calls without a mapped tool_call can
        # still use explicit auto-background.
        bg_tc_id = cc_tc_id or request_id
        evt = threading.Event()
        cancel_event = threading.Event()
        background_event = threading.Event()
        wake_event = threading.Event()
        started_at = time.time()
        # Shared mutable list — the exec thread populates it via
        # register_kill_hook(); cancel_agent reads + invokes each hook.
        kill_hooks: list = []
        with self._cache_lock:
            self._executing[request_id] = evt
        with self._inflight_lock:
            self._inflight[request_id] = {
                "conv": conversation_id,
                "agent": agent_name,
                "cancel": cancel_event,
                "background": background_event,
                "wake": wake_event,
                "cc_tc_id": cc_tc_id,
                "bg_tc_id": bg_tc_id,
                "tool_name": tool_name,
                "args_hash": _ah,
                "started_at": started_at,
                "kill_hooks": kill_hooks,
            }

        if not cc_tc_id and not _is_sentinel:
            try:
                from core.background_tool import pop_cc_tc
                cc_tc_id = pop_cc_tc(
                    conversation_id, agent_name, tool_name, _ah)
            except Exception as _me:
                logger.debug("[tool-relay] late cc_tc pop skipped: %s", _me)
            if cc_tc_id:
                bg_tc_id = cc_tc_id
                with self._inflight_lock:
                    info = self._inflight.get(request_id)
                    if info:
                        info["cc_tc_id"] = cc_tc_id
                        info["bg_tc_id"] = cc_tc_id

        # Execute in a daemon thread so cancel/background can let it run on.
        _result_holder = [None]

        def _exec():
            # Expose the cancel event + kill-hook registry to the tool's
            # call stack via thread-local — long-running tools (Pixazo
            # poll loops, browser automation, anything with its own
            # retry/wait) can read the event and abort early instead of
            # hammering the remote API after the user clicked Kill.
            # Tools that spawn subprocesses MUST also call
            # register_kill_hook(proc.terminate) so FORCE STOP can
            # actually tear them down.
            _set_current_cancel_event(cancel_event)
            _set_current_kill_hooks(kill_hooks)
            try:
                for attempt in range(1, _RELAY_TRANSPORT_RETRY_ATTEMPTS + 1):
                    try:
                        _result_holder[0] = self._do_execute(
                            request_id, tool_name, arguments,
                            user_id, conversation_id, agent_name)
                        break
                    except Exception as e:
                        if (not _is_relay_transport_error(e)
                                or attempt >= _RELAY_TRANSPORT_RETRY_ATTEMPTS):
                            _result_holder[0] = {
                                "type": "result", "request_id": request_id,
                                "data": f"Error: {e}"}
                            break
                        logger.warning(
                            "[tool-relay] relay transport error during %s "
                            "request=%s; retrying in %.1fs (attempt %d/%d): %s",
                            tool_name, request_id,
                            _RELAY_TRANSPORT_RETRY_DELAY_SECONDS,
                            attempt, _RELAY_TRANSPORT_RETRY_ATTEMPTS, e)
                        time.sleep(_RELAY_TRANSPORT_RETRY_DELAY_SECONDS)
            except Exception as e:
                _result_holder[0] = {"type": "result", "request_id": request_id,
                                      "data": f"Error: {e}"}
            finally:
                _set_current_cancel_event(None)
                _set_current_kill_hooks(None)
                evt.set()
                wake_event.set()

        exec_thread = threading.Thread(target=_exec, daemon=True)
        exec_thread.start()

        # Wait for completion, cancel, or explicit background. Optional auto-BG
        # is disabled by default: there is no implicit timeout/backgrounding.
        _auto_bg_after = max(0.0, float(getattr(self, "_auto_bg_after_seconds", 0.0) or 0.0))
        auto_bg_timer = None
        if _auto_bg_after > 0:
            def _auto_background():
                if evt.is_set() or cancel_event.is_set():
                    return
                logger.info("[tool-relay] auto-background after %ds for tc_id=%s",
                            int(_auto_bg_after), bg_tc_id)
                background_event.set()
                wake_event.set()

            auto_bg_timer = threading.Timer(_auto_bg_after, _auto_background)
            auto_bg_timer.daemon = True
            auto_bg_timer.start()

        while not evt.is_set():

            if background_event.is_set():
                # Return placeholder now; spawn a watcher to inject the
                # real result (or kill notice) as a user message when
                # the daemon thread finishes.
                placeholder = (
                    f"[Running in background (tc_id={bg_tc_id})]\n"
                    f"The actual result will be delivered in a separate "
                    f"user message once the tool completes. Continue your "
                    f"work — do not wait for it."
                )
                result = {"type": "result", "request_id": request_id,
                          "data": placeholder}

                def _watch_bg_completion(_evt, _holder, _tc, _conv, _agent,
                                         _tool, _uid, _cancel):
                    _bg_wake = threading.Event()

                    def _relay_event(_src):
                        _src.wait()
                        _bg_wake.set()

                    threading.Thread(
                        target=_relay_event, args=(_evt,), daemon=True).start()
                    threading.Thread(
                        target=_relay_event, args=(_cancel,), daemon=True).start()
                    _bg_wake.wait()
                    _was_cancelled = _cancel.is_set() and not _evt.is_set()
                    _res = _holder[0] or {}
                    _payload = _res.get("data", "") if isinstance(_res, dict) else str(_res)
                    if _was_cancelled and not _payload:
                        _payload = "[Cancelled before any output]"
                    try:
                        import core.background_tool as _bg
                        # Register lazily so _inject_result has context
                        # (we don't use a real Future here — the exec is
                        # already captured in _holder).
                        with _bg._lock:
                            _bg._backgrounded[_tc] = {
                                "future": None,
                                "conversation_id": _conv,
                                "agent_name": _agent,
                                "tool_name": _tool,
                                "user_id": _uid,
                                "is_claude_code": True,
                                "started_at": started_at,
                                "status": "cancelled" if _was_cancelled else "done",
                                "result": _payload,
                            }
                        _bg._inject_result(_tc, _payload, is_cancel=_was_cancelled)
                    except Exception as _ie:
                        logger.error("[tool-relay] bg inject failed for %s: %s",
                                     _tc, _ie)

                threading.Thread(
                    target=_watch_bg_completion,
                    args=(evt, _result_holder, bg_tc_id, conversation_id,
                          agent_name, tool_name, user_id, cancel_event),
                    daemon=True,
                    name=f"bg-watch-{bg_tc_id[:12]}",
                ).start()

                with self._cache_lock:
                    self._result_cache[request_id] = result
                    self._executing.pop(request_id, None)
                with self._inflight_lock:
                    self._inflight.pop(request_id, None)
                if auto_bg_timer:
                    auto_bg_timer.cancel()
                logger.debug(
                    "[tool-relay] timing execute_background request=%s tool=%s "
                    "relay_queue_ms=%.1f total_ms=%.1f cc_tc=%s",
                    request_id, tool_name,
                    (handle_started - relay_received_at) * 1000,
                    (time.perf_counter() - handle_started) * 1000,
                    cc_tc_id or "")
                return result

            if cancel_event.is_set():
                # Cancelled — return interrupt result immediately. The
                # daemon thread is abandoned; best-effort subprocess kill
                # is the relay's responsibility.
                result = {"type": "result", "request_id": request_id,
                          "data": "[Interrupted by user — stop current work and respond to the new message]"}
                with self._cache_lock:
                    self._result_cache[request_id] = result
                    self._executing.pop(request_id, None)
                with self._inflight_lock:
                    self._inflight.pop(request_id, None)
                if auto_bg_timer:
                    auto_bg_timer.cancel()
                logger.debug(
                    "[tool-relay] timing execute_cancelled request=%s tool=%s "
                    "relay_queue_ms=%.1f total_ms=%.1f cc_tc=%s",
                    request_id, tool_name,
                    (handle_started - relay_received_at) * 1000,
                    (time.perf_counter() - handle_started) * 1000,
                    cc_tc_id or "")
                return result

            wake_event.wait()
            wake_event.clear()

        result = _result_holder[0]
        # If cancelled while executing, check cache
        with self._cache_lock:
            if request_id in self._result_cache:
                result = self._result_cache[request_id]

        try:
            pass
        finally:
            with self._cache_lock:
                self._result_cache[request_id] = result
                self._executing.pop(request_id, None)
                evt.set()
            with self._inflight_lock:
                self._inflight.pop(request_id, None)
            if auto_bg_timer:
                auto_bg_timer.cancel()
            # Cleanup old cache entries (keep last 100)
            with self._cache_lock:
                if len(self._result_cache) > 100:
                    oldest = list(self._result_cache.keys())[:50]
                    for k in oldest:
                        self._result_cache.pop(k, None)

        data = result.get("data") if isinstance(result, dict) else result
        try:
            result_len = len(data) if isinstance(data, str) else len(json.dumps(data, default=str))
        except Exception:
            result_len = len(str(data))
        logger.debug(
            "[tool-relay] timing execute_done request=%s tool=%s "
            "relay_queue_ms=%.1f total_ms=%.1f result_len=%d cc_tc=%s",
            request_id, tool_name,
            (handle_started - relay_received_at) * 1000,
            (time.perf_counter() - handle_started) * 1000,
            result_len, cc_tc_id or "")
        return result

    def _do_execute(self, request_id, tool_name, arguments,
                    user_id, conversation_id, agent_name):
        total_started = time.perf_counter()
        registry_started = time.perf_counter()
        registry = self._get_registry(user_id, conversation_id, agent_name)
        registry_ms = (time.perf_counter() - registry_started) * 1000
        pre_hook_ms = 0.0
        approval_ms = 0.0
        secrets_ms = 0.0
        tool_exec_ms = 0.0
        post_hook_ms = 0.0
        _hook_runner = None
        _hook_enabled = False
        _perm_cid = self._root_conversation_id(conversation_id)

        try:
            hook_started = time.perf_counter()
            try:
                _hook_enabled = self._conversation_has_hooks(_perm_cid, user_id)
            except Exception as _detect_error:
                logger.warning(
                    "pre_tool_call hook detection failed; approval gate will decide: %s",
                    _detect_error)
                _hook_enabled = False
            if _hook_enabled:
                from core.agent_hooks import AgentHookRunner
                _hook_runner = AgentHookRunner(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    agent_name=agent_name,
                )
                _pre = _hook_runner.run("pre_tool_call", {
                    "tool_call_id": request_id,
                    "tool_name": tool_name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                }, fail_policy="closed")
                if _pre.get("decision") == "block":
                    reason = _pre.get("reason") or "blocked by hook"
                    return {"type": "result", "request_id": request_id,
                            "data": f"Blocked by hook: {reason}"}
                if _pre.get("decision") == "replace":
                    _payload = _pre.get("payload") or {}
                    tool_name = str(_payload.get("tool_name") or tool_name)
                    _new_args = _payload.get("arguments")
                    arguments = _new_args if isinstance(_new_args, dict) else {}
            pre_hook_ms = (time.perf_counter() - hook_started) * 1000
        except Exception as _he:
            logger.error("pre_tool_call hook failed; denying relay tool: %s", _he,
                         exc_info=True)
            return {"type": "result", "request_id": request_id,
                    "data": f"Error: pre_tool_call hook failed: {_he}"}

        # Tool Approval Gate — reads permission_mode from conversation
        # For task sub-conversations (conv::task::tid), inherit parent's permissions
        try:
            approval_started = time.perf_counter()
            _perm_mode = "default"
            _tool_perm = ""
            if _perm_cid:
                _perm_mode = self._conversation_extra_fast(
                    _perm_cid, "permission_mode", "default") or "default"
                _tperms = self._conversation_extra_fast(
                    _perm_cid, "tool_permissions", {}) or {}
                _tool_perm = _tperms.get(tool_name, "")

            # read_only mode takes precedence over EVERY per-tool
            # override — a stale `allow` permission left from a
            # previous mode must not let a write tool through once the
            # conversation has been switched to read_only. The
            # allowlist is fail-closed (anything not classified is
            # denied).
            if _perm_mode == "read_only":
                from core.tool_approval import ToolApprovalGate
                if not ToolApprovalGate.is_read_only_allowed(
                        tool_name,
                        arguments if isinstance(arguments, dict) else None):
                    return {"type": "result", "request_id": request_id,
                            "data": f"Error: tool '{tool_name}' is not allowed in read-only mode."}
                # Allowed by read_only — fall through, but skip the
                # per-tool override below (it would be redundant for
                # an allowlisted read tool).
                _tool_perm = ""

            # Per-tool override (only consulted outside read_only).
            if _tool_perm == "deny":
                return {"type": "result", "request_id": request_id,
                        "data": f"Error: Tool '{tool_name}' is denied by permission settings."}
            elif _tool_perm == "allow":
                pass  # explicitly allowed — skip further checks
            elif _tool_perm == "confirm":
                from core.tool_approval import ToolApprovalGate
                _path = arguments.get("path", "") if isinstance(arguments, dict) else ""
                action_summary = f"{tool_name}({_path})" if _path else tool_name
                approval = ToolApprovalGate.check(
                    tool_name, action_summary, _perm_cid, user_id, arguments)
                if approval != "approved":
                    return {"type": "result", "request_id": request_id,
                            "data": f"Error: Tool '{tool_name}' was {approval} by the user."}
            elif _perm_mode == "auto":
                # Auto mode: approve everything EXCEPT catastrophic patterns → always ask
                from core.tool_approval import ToolApprovalGate
                if tool_name in ("bash", "execute_script") and isinstance(arguments, dict):
                    _cmd = arguments.get("command", "") or arguments.get("code", "")
                    if ToolApprovalGate._is_catastrophic_command(_cmd):
                        action_summary = f"\u26a0\ufe0f CATASTROPHIC: {tool_name}({_cmd[:100]})"
                        approval = ToolApprovalGate.check(
                            tool_name, action_summary, _perm_cid, user_id, arguments)
                        if approval != "approved":
                            return {"type": "result", "request_id": request_id,
                                    "data": f"Error: Command rejected by user: {_cmd[:100]}"}
            else:
                # default / approve_edits — use approval gate
                from core.tool_approval import ToolApprovalGate
                _path = arguments.get("path", "") if isinstance(arguments, dict) else ""
                action_summary = f"{tool_name}({_path})" if _path else tool_name
                approval = ToolApprovalGate.check(
                    tool_name, action_summary, _perm_cid, user_id, arguments)
                if approval != "approved":
                    return {"type": "result", "request_id": request_id,
                            "data": f"Error: Tool '{tool_name}' was {approval} by the user."}
            approval_ms = (time.perf_counter() - approval_started) * 1000
        except Exception as e:
            logger.error("Tool approval check failed; denying tool for safety: %s", e,
                         exc_info=True)
            return {"type": "result", "request_id": request_id,
                    "data": "Error: tool approval check failed; denied for safety."}

        # Resolve env vars (all variables + secrets) and secret values (for redaction)
        _secret_values = set()
        _secret_names = {}
        _all_env = {}
        _secret_cid = _perm_cid
        if user_id and isinstance(arguments, dict):
            try:
                secrets_started = time.perf_counter()
                _needs_env = (tool_name in self._ENV_SECRET_TOOLS
                              or self._args_reference_env(arguments))
                if _needs_env:
                    _all_env = self._cached_secrets_env(user_id, _secret_cid)
                if _needs_env and _all_env:
                    # Inject as process env vars for shell tools
                    if tool_name in {"bash", "execute_script"}:
                        arguments["_secret_env"] = _all_env
                    # Resolve $VAR / ${VAR} in string arguments
                    # bash: skip 'command' (shell resolves $VAR itself)
                    # execute_script: skip 'code' (Python uses os.environ)
                    _skip = set()
                    if tool_name == "bash":
                        _skip = {"command"}
                    elif tool_name == "execute_script":
                        _skip = {"code"}
                    _resolve_vars_in_args(arguments, _all_env, skip_keys=_skip)
                # Only secrets → redaction
                _secret_values, _secret_names = self._cached_secret_values(
                    user_id, _secret_cid)
                secrets_ms = (time.perf_counter() - secrets_started) * 1000
            except Exception as _se:
                logger.warning("[tool-relay] failed to resolve env/secrets: %s", _se)

        # For delegate calls, set thread-local source_agent + delegate_tc_id
        # on the SpawnAgentsHandler so sub_agent_* SSE events carry the
        # delegate_tc_id that the chat UI uses to render delegate-blocks
        # (otherwise the events fall back to a generic task-block).
        # flash_delegate uses the same caller context by design: flash agents
        # inherit the calling agent identity and llm_service.
        if tool_name in {"delegate", "flash_delegate"}:
            try:
                from core.handlers.resource_agent import SpawnAgentsHandler
                _src_svc = ""
                try:
                    from core.service_registry import _parent_conversation_id
                    _parent_cid = (_parent_conversation_id(conversation_id or "")
                                   or conversation_id)
                    if _parent_cid and agent_name:
                        from core.conv_agent_config import get_agent_config as _gac
                        _src_svc = (_gac(_parent_cid, agent_name) or {}).get("llm_service", "") or ""
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                for _h in registry.list_tools():
                    if isinstance(_h, SpawnAgentsHandler):
                        _h.set_source_agent(agent_name or "", _src_svc)
                        _h.set_delegate_tc_id(request_id)
            except Exception as _de:
                logger.debug("[tool-relay] failed to set delegate ctx: %s", _de)

        try:
            logger.debug("[tool-relay] execute %s [req=%s]", tool_name, request_id)
            try:
                handler = registry.get(tool_name)
                from core.handlers.meta_tools import _normalize_tool_args
                if handler and isinstance(arguments, dict):
                    arguments = _normalize_tool_args(tool_name, arguments)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            tool_exec_started = time.perf_counter()
            result = registry.execute(tool_name, arguments)
            tool_exec_ms = (time.perf_counter() - tool_exec_started) * 1000
            if tool_name in {
                    "create_tool", "delete_tool", "manage_resource",
                    "link_resource", "manage_package"}:
                self.clear_registry_cache(
                    conversation_id=conversation_id, user_id=user_id,
                    agent_name=agent_name)
            result_str = str(result) if result is not None else "(no output)"
            if _is_relay_transport_result(result_str):
                raise RuntimeError(result_str)
            if tool_name in self._SECRET_MUTATION_TOOLS:
                self.clear_runtime_caches(
                    conversation_id=conversation_id, user_id=user_id)
        except Exception as e:
            tool_exec_ms = (time.perf_counter() - tool_exec_started) * 1000 if 'tool_exec_started' in locals() else 0.0
            if _is_relay_transport_error(e):
                logger.warning("Tool relay transport failure in '%s': %s", tool_name, e)
                raise
            result_str = f"Error: {e}"
            logger.error("Tool relay execute '%s' failed: %s", tool_name, e)

        # Sanitize tool result to strip invisible/malicious unicode
        from core.sanitization import sanitize_unicode
        result_str = sanitize_unicode(result_str)

        # Redact secret values from tool output
        if _secret_values:
            result_str = _redact_secrets(result_str, _secret_values,
                                         secret_names=_secret_names)

        try:
            post_hook_started = time.perf_counter()
            if _hook_enabled and _hook_runner is not None:
                _post = _hook_runner.run("post_tool_call", {
                    "tool_call_id": request_id,
                    "tool_name": tool_name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                    "result": result_str,
                })
                if _post.get("decision") == "replace":
                    _payload = _post.get("payload") or {}
                    if "result" in _payload:
                        result_str = str(_payload.get("result") or "")
                elif _post.get("decision") == "block":
                    reason = _post.get("reason") or "blocked by hook"
                    result_str = f"Blocked by hook: {reason}"
            post_hook_ms = (time.perf_counter() - post_hook_started) * 1000
        except Exception as _he:
            logger.warning("post_tool_call hook failed: %s", _he, exc_info=True)

        logger.debug(
            "[tool-relay] timing do_execute request=%s tool=%s "
            "total_ms=%.1f registry_ms=%.1f pre_hook_ms=%.1f "
            "approval_ms=%.1f secrets_ms=%.1f exec_ms=%.1f "
            "post_hook_ms=%.1f result_len=%d",
            request_id, tool_name,
            (time.perf_counter() - total_started) * 1000,
            registry_ms, pre_hook_ms, approval_ms, secrets_ms,
            tool_exec_ms, post_hook_ms, len(result_str))

        # Convert __image_data__: markers into MCP content blocks server-side,
        # gated on the handler's _returns_images flag. Without this gate, a
        # grep result matching the literal marker string would be wrongly
        # split into separate text/image blocks by the bridge.
        from core.handlers.meta_tools import resolve_result_shape_handler
        _h_for_img = resolve_result_shape_handler(
            registry, tool_name, arguments)
        _returns_images = bool(getattr(_h_for_img, '_returns_images', False)) if _h_for_img else False
        if _returns_images and "__image_data__:" in result_str:
            blocks = []
            for rline in result_str.split("\n"):
                if rline.startswith("__image_data__:"):
                    parts = rline.split(":", 2)
                    if len(parts) == 3:
                        blocks.append({"type": "image",
                                       "data": parts[2],
                                       "mimeType": parts[1]})
                elif rline.strip():
                    blocks.append({"type": "text", "text": rline})
            if blocks:
                return {"type": "result", "request_id": request_id,
                        "data": blocks}

        return {"type": "result", "request_id": request_id, "data": result_str}

    def _resolve_secrets_env(self, user_id: str, conversation_id: str) -> dict:
        return _trb.resolve_secrets_env(user_id, conversation_id)
