"""ToolRelayService runtime/secret caches + request routing/cancel."""

import logging
import hashlib
import json
import time
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)
# Split out of tool_relay_service.py for the <=800-line rule; composed back
# into ToolRelayService (invariant 2: MRO/shared class-state on the host).

import services._tool_relay_base as _trb  # noqa: E402

class _ToolRelayCacheReqMixin:
    """runtime/secret caches + request routing/cancel."""

    @staticmethod
    def _root_conversation_id(conversation_id: str) -> str:
        conversation_id = str(conversation_id or "")
        for marker in ("::task::", "::task_verify::", "::delegate::"):
            if marker in conversation_id:
                return conversation_id.split(marker, 1)[0]
        return conversation_id

    @staticmethod
    def _args_reference_env(arguments: Any) -> bool:
        if isinstance(arguments, str):
            return "$" in arguments
        if isinstance(arguments, dict):
            return any(not str(k).startswith("_")
                       and _ToolRelayCacheReqMixin._args_reference_env(v)
                       for k, v in arguments.items())
        if isinstance(arguments, list):
            return any(_ToolRelayCacheReqMixin._args_reference_env(v)
                       for v in arguments)
        return False

    @staticmethod
    def _conversation_extra_fast(conversation_id: str, key: str,
                                 default: Any = None) -> Any:
        if not conversation_id:
            return default
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        sentinel = object()
        try:
            value = store.get_extra_snapshot(conversation_id, key, sentinel)
            if value is not sentinel:
                return value
            # A warm conversation cache knows the key is absent. Avoid falling
            # back to extras.json on hot tool paths for normal missing keys
            # such as conversation_hooks/tool_permissions.
            try:
                with store._cache_lock:
                    if conversation_id in store._cache:
                        return default
            except Exception:
                logging.getLogger(__name__).debug(
                    "extra cache warm check failed", exc_info=True)
        except Exception:
            logging.getLogger(__name__).debug("extra snapshot failed", exc_info=True)
        return store.get_extra(conversation_id, key, default)

    @staticmethod
    def _stable_config_fingerprint(value: Any) -> tuple:
        try:
            payload = json.dumps(value or {}, sort_keys=True, default=str,
                                 separators=(",", ":"))
        except Exception:
            payload = str(value or {})
        digest = hashlib.sha1(
            payload.encode("utf-8", "ignore"), usedforsecurity=False,
        ).hexdigest()
        return (len(payload), digest)

    @classmethod
    def _conversation_has_hooks(cls, conversation_id: str, user_id: str) -> bool:
        raw = cls._conversation_extra_fast(
            conversation_id, "conversation_hooks", [],)
        if isinstance(raw, dict):
            raw = raw.get("hooks") if isinstance(raw.get("hooks"), list) else list(raw.values())
        return bool(raw)

    @classmethod
    def clear_runtime_caches(cls, conversation_id: str = "", user_id: str = ""):
        conv = cls._root_conversation_id(conversation_id)
        uid = user_id or ""
        with cls._runtime_cache_lock:
            if not conv and not uid:
                cls._secret_env_cache.clear()
                cls._secret_values_cache.clear()
                return
            keys = (set(cls._secret_env_cache.keys()) |
                    set(cls._secret_values_cache.keys()))
            for key in list(keys):
                _uid, _conv = key
                if uid and _uid != uid:
                    continue
                if conv and _conv != conv:
                    continue
                cls._secret_env_cache.pop(key, None)
                cls._secret_values_cache.pop(key, None)

    @staticmethod
    def _path_fingerprint(path) -> tuple:
        try:
            st = path.stat()
            return (str(path), st.st_size, st.st_mtime_ns)
        except OSError:
            return (str(path), -1, -1)

    @classmethod
    def _secret_config_fingerprint(cls, user_id: str, conversation_id: str) -> tuple:
        from core.paths import GLOBAL_PARAMS_FILE, GLOBAL_SECRETS_FILE, USER_CONFIG_DIR
        conv = cls._root_conversation_id(conversation_id)
        parts = [
            cls._path_fingerprint(GLOBAL_PARAMS_FILE),
            cls._path_fingerprint(GLOBAL_SECRETS_FILE),
        ]
        if user_id:
            user_dir = USER_CONFIG_DIR / user_id
            parts.extend((
                cls._path_fingerprint(user_dir / "params.json"),
                cls._path_fingerprint(user_dir / "secrets.json"),
            ))
        if conv:
            parts.extend((
                ("conv_params", cls._stable_config_fingerprint(
                    cls._conversation_extra_fast(conv, "conv_params", {}) or {})),
                ("conv_secrets", cls._stable_config_fingerprint(
                    cls._conversation_extra_fast(conv, "conv_secrets", {}) or {})),
            ))
        return tuple(parts)

    @classmethod
    def _cached_secrets_env(cls, user_id: str, conversation_id: str) -> dict:
        if not user_id:
            return {}
        conv = cls._root_conversation_id(conversation_id)
        key = (user_id or "", conv)
        with cls._runtime_cache_lock:
            cached = cls._secret_env_cache.get(key)
            if cached:
                return dict(cached[1])
        fingerprint = cls._secret_config_fingerprint(user_id, conv)
        env = _trb.resolve_secrets_env(user_id, conv)
        with cls._runtime_cache_lock:
            cls._secret_env_cache[key] = (fingerprint, dict(env))
        return env

    @classmethod
    def _cached_secret_values(cls, user_id: str, conversation_id: str) -> tuple:
        if not user_id:
            return set(), {}
        conv = cls._root_conversation_id(conversation_id)
        key = (user_id or "", conv)
        with cls._runtime_cache_lock:
            cached = cls._secret_values_cache.get(key)
            if cached:
                return set(cached[1]), dict(cached[2])
        fingerprint = cls._secret_config_fingerprint(user_id, conv)
        values, names = _trb.resolve_secret_values(user_id, conv)
        with cls._runtime_cache_lock:
            cls._secret_values_cache[key] = (fingerprint, set(values), dict(names))
        return values, names

    @classmethod
    def clear_registry_cache(cls, conversation_id: str = "",
                             user_id: str = "", agent_name: str = ""):
        """Invalidate cached per-agent tool registries."""
        cls.clear_runtime_caches(conversation_id=conversation_id, user_id=user_id)
        conv = conversation_id or ""
        uid = user_id or ""
        agent = agent_name or ""
        with cls._registry_cache_lock:
            if not any((conv, uid, agent)):
                cls._registry_cache.clear()
                cls._registry_cache_tool_counts.clear()
                for evt in cls._registry_building.values():
                    evt.set()
                cls._registry_building.clear()
                return
            keys = set(cls._registry_cache.keys()) | set(cls._registry_building.keys())
            for key in list(keys):
                _service_id, _uid, _conv, _agent, _file_base = key
                if conv and _conv != conv:
                    continue
                if uid and _uid != uid:
                    continue
                if agent and _agent != agent:
                    continue
                cls._registry_cache.pop(key, None)
                cls._registry_cache_tool_counts.pop(key, None)
                evt = cls._registry_building.pop(key, None)
                if evt:
                    evt.set()

    @classmethod
    def cancel_request(cls, request_id: str) -> bool:
        """Cancel a single in-flight tool request by its request/tc id.

        `request_id` may be the MCP request_id (internal) OR the CC
        tool_use id (UI-visible) — we try both so kill works regardless
        of which one the caller knows. Returns True if a matching
        in-flight entry was found and cancelled.
        """
        with cls._inflight_lock:
            info = cls._inflight.get(request_id)
            if info is None:
                # Fallback: search by cc_tc_id (what the UI sends)
                for _rid, _info in cls._inflight.items():
                    if isinstance(_info, dict) and _info.get("cc_tc_id") == request_id:
                        info = _info
                        break
        if info and isinstance(info, dict):
            cancel_evt = info.get("cancel")
            if cancel_evt:
                cancel_evt.set()
            wake_evt = info.get("wake")
            if wake_evt:
                wake_evt.set()
            _hooks = list(info.get("kill_hooks") or [])
            _success = 0
            _failure = 0
            for hook in _hooks:
                try:
                    hook()
                    _success += 1
                except Exception as _he:
                    _failure += 1
                    logger.warning(
                        "[tool-relay] kill_hook failed for targeted %s tool=%s: %s",
                        request_id, info.get("tool_name"), _he)
            logger.info(
                "[tool-relay] targeted cancel request=%s tool=%s "
                "kill_hook_count=%d kill_hook_success=%d kill_hook_failed=%d",
                request_id, info.get("tool_name", "?"),
                len(_hooks), _success, _failure)
            if cancel_evt:
                logger.info("[tool-relay] cancelled request (cc_tc=%s)",
                            info.get("cc_tc_id") or request_id)
                return True
        return False

    @classmethod
    def background_by_tc_id(cls, tc_id: str) -> bool:
        """Flag an in-flight tool call for backgrounding by its CC tc_id.

        Sets the per-inflight background_event; the wait loop in
        _handle_execute returns the placeholder to CC immediately and
        lets the daemon thread continue. When the thread finishes,
        _inject_result publishes the actual result as a user message.
        """
        with cls._inflight_lock:
            for _rid, info in cls._inflight.items():
                if isinstance(info, dict) and info.get("cc_tc_id") == tc_id:
                    bg_evt = info.get("background")
                    if bg_evt and not bg_evt.is_set():
                        bg_evt.set()
                        wake_evt = info.get("wake")
                        if wake_evt:
                            wake_evt.set()
                        logger.info("[tool-relay] backgrounded tc_id=%s (request_id=%s)",
                                    tc_id, _rid)
                        return True
                    elif bg_evt and bg_evt.is_set():
                        logger.info(
                            "[tool-relay] tc_id=%s already backgrounded (request_id=%s)",
                            tc_id, _rid)
                        return True
                if isinstance(info, dict) and (_rid == tc_id or info.get("bg_tc_id") == tc_id):
                    bg_evt = info.get("background")
                    if bg_evt and not bg_evt.is_set():
                        bg_evt.set()
                        wake_evt = info.get("wake")
                        if wake_evt:
                            wake_evt.set()
                        logger.info("[tool-relay] backgrounded request_id=%s", _rid)
                        return True
                    elif bg_evt and bg_evt.is_set():
                        logger.info("[tool-relay] request_id=%s already backgrounded", _rid)
                        return True
            # No match — report the available cc_tc_ids so we can see whether
            # the in-flight request registered a different id, or none at all.
            _inflight_snap = [
                (_rid, (info or {}).get("cc_tc_id", ""),
                 (info or {}).get("tool_name", ""))
                for _rid, info in cls._inflight.items()
                if isinstance(info, dict)
            ]
        logger.info(
            "[tool-relay] bg MISS tc_id=%s — in-flight=%s",
            tc_id, _inflight_snap)
        return False

    @classmethod
    def bind_pending_cc_tc(cls, conversation_id: str, agent_name: str,
                           tc_id: str, tool_name: str,
                           args_hash: str) -> bool:
        """Attach a provider tool_call id to an already in-flight relay request.

        Codex/Gemini app-server can dispatch the MCP execute request before
        the provider stream publishes the UI-visible tool_call event. This
        late bind repairs that ordering so background/kill still targets the
        running request.
        """
        with cls._inflight_lock:
            for rid, info in cls._inflight.items():
                if not isinstance(info, dict):
                    continue
                if info.get("conv") != conversation_id:
                    continue
                if info.get("agent") != agent_name:
                    continue
                if info.get("tool_name") != tool_name:
                    continue
                if info.get("args_hash") != args_hash:
                    continue
                info["cc_tc_id"] = tc_id
                info["bg_tc_id"] = tc_id
                bg_evt = info.get("background")
                try:
                    from core.background_tool import is_backgrounded
                    if bg_evt and is_backgrounded(tc_id):
                        bg_evt.set()
                        wake_evt = info.get("wake")
                        if wake_evt:
                            wake_evt.set()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                logger.debug(
                    "[tool-relay] late-bound cc_tc=%s to request_id=%s tool=%s",
                    tc_id, rid, tool_name)
                return True
        return False

    @classmethod
    def cancel_agent(cls, conversation_id: str, agent_name: str):
        """Cancel all in-flight tool calls for a (conv, agent).

        Two-phase: 1) set cancel_event so cooperative loops abort,
        2) invoke every registered kill_hook so subprocesses, sockets,
        and other non-cooperative resources are torn down. Without
        phase 2 the daemon exec thread keeps running after FORCE STOP
        — a real risk for tools with side effects (HTTP writes, file
        writes, spawned processes).

        Does NOT reject future requests — only kills current in-flight ones.
        """
        with cls._inflight_lock:
            to_cancel = [(rid, info) for rid, info in cls._inflight.items()
                         if isinstance(info, dict)
                         and info.get("conv") == conversation_id
                         and (not agent_name or info.get("agent") == agent_name)]
        _hook_total = 0
        _hook_failed = 0
        for rid, info in to_cancel:
            cancel_evt = info.get("cancel")
            cancel_evt_set = False
            if cancel_evt:
                cancel_evt.set()
                cancel_evt_set = True
            wake_evt = info.get("wake")
            if wake_evt:
                wake_evt.set()
            _hooks = list(info.get("kill_hooks") or [])
            _success = 0
            _failure = 0
            for hook in _hooks:
                try:
                    hook()
                    _success += 1
                except Exception as _he:
                    _failure += 1
                    logger.warning(
                        "[tool-relay] kill_hook failed for %s tool=%s: %s",
                        rid, info.get("tool_name"), _he)
            _hook_total += _success + _failure
            _hook_failed += _failure
            # Per-request structured trace so the cancellation path is
            # observable in production logs without enabling debug.
            logger.info(
                "[tool-relay] cancel rid=%s tool=%s "
                "cancel_event_set=%s kill_hook_count=%d kill_hook_success=%d "
                "kill_hook_failed=%d",
                rid, info.get("tool_name", "?"),
                cancel_evt_set, len(_hooks), _success, _failure)
        if to_cancel:
            logger.info(
                "[tool-relay] cancelled %d in-flight request(s) for %s/%s "
                "(kill_hooks total=%d failed=%d)",
                len(to_cancel), conversation_id, agent_name,
                _hook_total, _hook_failed)

    @classmethod
    def uncancel_agent(cls, conversation_id: str, agent_name: str):
        """Clear cancelled state (new request starting)."""
        cls._cancelled.discard((conversation_id, agent_name))

    def handle_tool_request(self, msg: dict, user_id: str = "",
                            conversation_id: str = "",
                            agent_name: str = "") -> dict:
        """Handle a tool request from the MCP bridge."""
        method = msg.get("method", "")
        request_id = msg.get("request_id", "")
        relay_received_at = float(msg.get("_relay_received_perf") or 0.0)
        dispatch_started_at = time.perf_counter()

        if method == "list_tools":
            return self._handle_list_tools(request_id, user_id, conversation_id)
        elif method == "get_tool_schema":
            return self._handle_get_schema(request_id, msg.get("tool_name", ""),
                                           user_id=user_id,
                                           conversation_id=conversation_id)
        elif method == "execute_tool":
            _raw_args = msg.get("arguments", {})
            _tool = msg.get("tool_name", "")
            # Single canonical parser (same as mcp_bridge + meta_tools) so a
            # call decodes identically on every route. Idempotent on dicts:
            # a bridge-decoded dict passes straight through (no double decode).
            from core.tool_json import (
                parse_tool_arguments, tool_argument_parse_error)
            _raw_args = parse_tool_arguments(
                _raw_args, tool_name=_tool, provider="tool-relay", log=logger)
            _perr = tool_argument_parse_error(_raw_args)
            if _perr:
                logger.warning("[tool-relay] %s", _perr)
                return {"type": "response", "request_id": request_id,
                        "result": _perr}
            return self._handle_execute(
                request_id, _tool, _raw_args,
                user_id, conversation_id, agent_name,
                relay_received_at=relay_received_at,
                dispatch_started_at=dispatch_started_at,
            )
        elif method == "execute_pfp_host_call":
            return self._handle_pfp_host_call(
                request_id,
                msg.get("invocation", {}),
                msg.get("host_call", {}),
                user_id,
                conversation_id,
                agent_name,
            )
        else:
            return {"type": "error", "request_id": request_id,
                    "error": f"Unknown method: {method}"}

    def _handle_pfp_host_call(self, request_id: str, invocation: Dict[str, Any],
                              host_call: Dict[str, Any], user_id: str,
                              conversation_id: str, agent_name: str) -> dict:
        from core import pfp_runtime
        try:
            from core.service_registry import ServiceRegistry
            self._validate_pfp_host_call_context(
                invocation, user_id, conversation_id, agent_name)
            registry = self._get_registry(user_id, conversation_id, agent_name)
            host = pfp_runtime.runtime_host_from_invocation(
                invocation,
                tool_registry=registry,
                service_registry=ServiceRegistry.get_instance(),
            )
            result = host.handle_host_call(host_call)
            payload = {
                "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
                "ok": True,
                "result": result,
            }
        except Exception as exc:
            payload = {
                "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
                "ok": False,
                "error": str(exc),
            }
        return {"type": "result", "request_id": request_id, "data": payload}

    @staticmethod
    def _validate_pfp_host_call_context(invocation: Dict[str, Any], user_id: str,
                                      conversation_id: str, agent_name: str) -> None:
        context = invocation.get("context") if isinstance(invocation, dict) else {}
        if not isinstance(context, dict):
            raise ValueError("invalid PFP invocation context")
        expected = {
            "user_id": user_id or "",
            "conversation_id": conversation_id or "",
            "agent_name": agent_name or "",
        }
        for key, value in expected.items():
            actual = str(context.get(key) or "")
            if actual != value:
                raise ValueError(f"PFP host-call context mismatch: {key}")

    @classmethod
    def _active_tool_result_max_chars(cls, user_id: str, conversation_id: str,
                                      agent_name: str) -> Optional[int]:
        if not (user_id and conversation_id and agent_name):
            return None
        conv_id = cls._root_conversation_id(conversation_id)
        from core.conv_agent_config import get_agent_config
        cfg = get_agent_config(conv_id, agent_name)
        llm_service = str(cfg.get("llm_service") or "").strip()
        if not llm_service:
            return None
        from core.service_registry import ServiceRegistry
        sdef = ServiceRegistry.get_instance().resolve_definition(
            llm_service, user_id=user_id, conv_id=conv_id)
        if not sdef:
            return None
        value = (getattr(sdef, "config", {}) or {}).get("tool_result_max_chars", 0)
        max_chars = int(value or 0)
        return max_chars if max_chars > 0 else None
