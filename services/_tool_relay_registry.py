"""ToolRelayService tool registry build + media resolver."""

import logging
import threading
import time

from core import ServiceFactory

logger = logging.getLogger(__name__)
# Split out of tool_relay_service.py for the <=800-line rule; composed back
# into ToolRelayService (invariant 2: MRO/shared class-state on the host).


class _ToolRelayRegistryMixin:
    """tool registry build + media resolver."""

    def _get_registry(self, user_id: str = "", conversation_id: str = "",
                       agent_name: str = ""):
        """Get a configured tool registry for this request context.

        CRITICAL: injects the live filesystem service instance (the one
        with the relay connection) into the handler. Without this, the
        handler creates a new disconnected instance.

        Also loads dynamic tools (per-conversation) and MCP server tools
        (per-agent) so they are available via the MCP bridge.
        """
        cache_key = (
            self._service_id, user_id or "", conversation_id or "",
            agent_name or "", self.config.get("file_base_url", "") or "")
        cache_started = time.perf_counter()
        build_owner = False
        with self._registry_cache_lock:
            cached = self._registry_cache.get(cache_key)
            if cached is not None:
                tool_count = self._registry_cache_tool_counts.get(cache_key, 0)
                logger.debug(
                    "[tool-relay] timing get_registry_cache user=%s conv=%s "
                    "agent=%s total_ms=%.1f tools=%d",
                    user_id, (conversation_id or "")[:8], agent_name,
                    (time.perf_counter() - cache_started) * 1000,
                    tool_count)
                return cached
            build_evt = self._registry_building.get(cache_key)
            if build_evt is None:
                build_evt = threading.Event()
                self._registry_building[cache_key] = build_evt
                build_owner = True

        if not build_owner:
            build_evt.wait()
            with self._registry_cache_lock:
                cached = self._registry_cache.get(cache_key)
                tool_count = self._registry_cache_tool_counts.get(cache_key, 0)
            if cached is not None:
                logger.debug(
                    "[tool-relay] timing get_registry_cache user=%s conv=%s "
                    "agent=%s total_ms=%.1f tools=%d waited_for_build=yes",
                    user_id, (conversation_id or "")[:8], agent_name,
                    (time.perf_counter() - cache_started) * 1000,
                    tool_count)
                return cached
            return self._get_registry(user_id, conversation_id, agent_name)

        registry_total_started = time.perf_counter()
        dynamic_ms = 0.0
        mcp_ms = 0.0
        filter_ms = 0.0
        fs_find_ms = 0.0
        context_ms = 0.0
        spawn_ms = 0.0
        media_ms = 0.0
        fs_available_ms = 0.0
        from core.tool_registry import create_default_registry
        default_started = time.perf_counter()
        registry = create_default_registry()
        default_ms = (time.perf_counter() - default_started) * 1000

        # Load dynamic tools (global + user + conv) for this user/conv.
        if user_id:
            dynamic_started = time.perf_counter()
            try:
                from core.tool_loader import load_tools_into_registry
                _parent_cid = conversation_id or ""
                for _sep in ("::task::", "::task_verify::", "::delegate::"):
                    if _sep in _parent_cid:
                        _parent_cid = _parent_cid.split(_sep, 1)[0]
                        break
                load_tools_into_registry(
                    registry, user_id, _parent_cid)
            except Exception as e:
                logger.warning("[tool-relay] Failed to load dynamic tools: %s", e)
            dynamic_ms = (time.perf_counter() - dynamic_started) * 1000

        # Load MCP server tools for the active agent
        if conversation_id and user_id:
            mcp_started = time.perf_counter()
            self._load_mcp_tools(registry, user_id, conversation_id, agent_name)
            mcp_ms = (time.perf_counter() - mcp_started) * 1000

        if conversation_id:
            filter_started = time.perf_counter()
            try:
                from core.tool_mcp_filters import get_filters, is_tool_enabled_from_filters
                _filters = get_filters(conversation_id)
                for _handler in list(registry.list_tools()):
                    if not is_tool_enabled_from_filters(
                            _filters, _handler.name, agent_name,
                            getattr(_handler, "_origin", "builtin"),
                            getattr(_handler, "_origin_scope", "")):
                        registry.unregister(_handler.name)
            except Exception as e:
                logger.debug("[tool-relay] tool availability filter failed: %s", e)
            filter_ms = (time.perf_counter() - filter_started) * 1000

        available_fs = None
        # Find the default linked filesystem service for this conversation.
        fs_find_started = time.perf_counter()
        if conversation_id:
            available_fs = self._list_available_filesystem_services(
                user_id, conversation_id, agent_name)
            fs_svc = self._filesystem_service_from_available(
                available_fs, user_id, conversation_id, agent_name)
        else:
            fs_svc = self._find_filesystem_service(
                user_id, conversation_id, agent_name)
        fs_resolver = self._make_filesystem_resolver(
            user_id, conversation_id, agent_name, default_service=fs_svc)
        fs_find_ms = (time.perf_counter() - fs_find_started) * 1000

        tool_result_max_chars = self._active_tool_result_max_chars(
            user_id, conversation_id, agent_name)

        # Configure ALL handlers that need user/filesystem context
        context_started = time.perf_counter()
        for h in registry.list_tools():
            if (tool_result_max_chars is not None and
                    hasattr(h, '_tool_result_max_chars')):
                h._tool_result_max_chars = tool_result_max_chars
            # Set user_id on any handler that supports it
            if hasattr(h, 'set_user_id') and user_id:
                h.set_user_id(user_id)
            if hasattr(h, 'set_conversation_id') and conversation_id:
                h.set_conversation_id(conversation_id)
            if hasattr(h, 'set_agent_name') and agent_name:
                h.set_agent_name(agent_name)
            if hasattr(h, '_user_id'):
                h._user_id = user_id
            if hasattr(h, '_conversation_id'):
                h._conversation_id = conversation_id
            # Inject live filesystem service where needed
            if fs_svc or fs_resolver:
                if hasattr(h, 'set_fs_resolver') and fs_resolver:
                    h.set_fs_resolver(fs_resolver)
                if hasattr(h, 'set_fs_service'):
                    h.set_fs_service(fs_svc)
                if hasattr(h, '_fs_service') and not getattr(h, '_fs_service', None):
                    h._fs_service = fs_svc
                if hasattr(h, 'set_service'):
                    try:
                        h.set_service(fs_svc)
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        context_ms = (time.perf_counter() - context_started) * 1000

        # Configure SpawnAgentsHandler (delegate) — needs a client_resolver
        # to look up per-agent LLM services. Without this, delegate fails
        # with "Agent executor not configured (missing client_resolver)".
        spawn_started = time.perf_counter()
        try:
            from core.handlers.resource_agent import SpawnAgentsHandler
            from core.service_registry import ServiceRegistry as _SR

            def _client_resolver(svc_id, uid):
                _reg = _SR.get_instance()
                _tried = []
                # Canonical scope chain first (conv > user > global, parent
                # conversations included) so conv-scoped LLM services resolve.
                try:
                    _live = _reg.resolve(svc_id, user_id=uid,
                                         conv_id=conversation_id)
                    if _live and hasattr(_live, "get_client"):
                        return _live.get_client(), _live
                    _tried.append("resolve:no-live-instance")
                except Exception as _re:
                    _tried.append(f"resolve:{type(_re).__name__}:{_re}")
                for _scope, _sid in (("user", uid), ("global", "")):
                    try:
                        _svc_def = _reg.get_definition(_scope, _sid, svc_id)
                        if not _svc_def:
                            _tried.append(f"{_scope}/{_sid}:missing")
                            continue
                        _live = _reg.get_live_instance(_scope, _sid, svc_id)
                        if _live and hasattr(_live, "get_client"):
                            return _live.get_client(), _live
                        _tried.append(f"{_scope}/{_sid}:no-live-instance")
                    except Exception as _re:
                        _tried.append(f"{_scope}/{_sid}:{type(_re).__name__}:{_re}")
                logger.warning(
                    "[tool-relay] could not resolve llm_service '%s' "
                    "for user '%s' (tried: %s)",
                    svc_id, uid, ", ".join(_tried) or "none")
                return None, None

            # NO default LLM client. An agent's llm_service is always
            # resolved per-task via _client_resolver (from the conv_agents
            # link of the delegate target). If resolution fails, the
            # sub-agent errors out — never silently falls back to
            # "whatever LLM was enabled first".

            # Bridge sub-agent events to the conversation SSE bus so the
            # webchat can render delegate blocks live (mirrors the wiring in
            # tasks/ai/agent_context.py for non-CC agents).
            from core.service_registry import _parent_conversation_id
            _parent_cid_for_events = (
                _parent_conversation_id(conversation_id or "")
                or (conversation_id or ""))

            def _sub_on_event(event_type, data):
                if not _parent_cid_for_events:
                    return
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(
                        _parent_cid_for_events, event_type, data)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

            for h in registry.list_tools():
                if isinstance(h, SpawnAgentsHandler):
                    h.set_spawn_deps(None, _client_resolver,
                                      on_event=_sub_on_event, registry=registry)
        except Exception as _e:
            logger.warning("[tool-relay] SpawnAgents wiring failed: %s", _e)
        spawn_ms = (time.perf_counter() - spawn_started) * 1000

        # Configure media service resolvers (image/video/audio/capabilities)
        media_started = time.perf_counter()
        from core.handlers.media import EditImageHandler, ImageGenerationHandler, ImageModelInfoHandler
        from core.handlers.media import VideoGenerationHandler, AudioGenerationHandler
        file_base_url = self.config.get("file_base_url", "") or ""
        for h in registry.list_tools():
            if isinstance(h, (ImageGenerationHandler, EditImageHandler,
                              ImageModelInfoHandler)):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                image_methods = ("generate",)
                if isinstance(h, EditImageHandler):
                    image_methods = ("edit_image",)
                elif isinstance(h, ImageModelInfoHandler):
                    image_methods = ("get_model_info",)
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "image", image_methods))
            elif isinstance(h, VideoGenerationHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(user_id, conversation_id, "video"))
            elif isinstance(h, AudioGenerationHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "audio", ("generate",)))
            elif h.name in ("generate_3d",):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "3d", ("generate_3d",)))
            elif h.name in ("upscale_image",):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "upscale", ("upscale",)))
            elif h.name in ("try_on",):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "tryon", ("try_on",)))
            elif h.name in ("lipsync",):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "lipsync", ("lipsync",)))
            elif h.name in ("train_image_model",):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "trainer", ("train",)))
            elif h.name in ("clone_voice", "speak", "delete_voice"):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                if hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
                voice_methods = {
                    "clone_voice": ("clone_speak",),
                    "speak": ("speak",),
                    "delete_voice": ("delete_voice_id",),
                }[h.name]
                media_type = "tts" if h.name == "speak" else "voice"
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, media_type, voice_methods))
            elif h.name in ("describe_image", "remix_image"):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                if hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
                image_methods = {
                    "describe_image": ("describe_image",),
                    "remix_image": ("remix_image",),
                }[h.name]
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "image", image_methods))
            elif h.name in ("speech_to_video",):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                if hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "speech_to_video",
                        ("speech_to_video",)))
            elif h.name in ("upscale_video", "remove_background"):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                if hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
                upscale_methods = {
                    "upscale_video": ("upscale_video",),
                    "remove_background": ("remove_background",),
                }[h.name]
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "upscale", upscale_methods))
        media_ms = (time.perf_counter() - media_started) * 1000

        # Populate conversation-linked filesystems on all BaseFsHandler instances.
        from core.handlers._fs_base import BaseFsHandler, _FS_TYPES
        _fs_handlers = [h for h in registry.list_tools() if isinstance(h, BaseFsHandler)]
        if _fs_handlers:
            fs_available_started = time.perf_counter()
            try:
                available = available_fs
                if available is None:
                    available = self._list_available_filesystem_services(
                        user_id, conversation_id, agent_name, fs_types=_FS_TYPES)
                default_fs_id = self._default_filesystem_id(
                    available, conversation_id, agent_name) if conversation_id else ""
                for h in _fs_handlers:
                    h.set_available_services(available, default_fs_id)
                logger.debug("Filesystem services for user '%s': %s",
                             user_id, [s["id"] for s in available])
            except Exception as e:
                logger.error("Failed to enumerate filesystem services: %s", e)
            fs_available_ms = (time.perf_counter() - fs_available_started) * 1000

        tool_count = len(registry.list_tools())
        total_ms = (time.perf_counter() - registry_total_started) * 1000
        if total_ms >= 100.0:
            logger.debug(
                "[tool-relay] timing get_registry user=%s conv=%s agent=%s "
                "total_ms=%.1f default_ms=%.1f dynamic_ms=%.1f "
                "mcp_ms=%.1f filter_ms=%.1f fs_find_ms=%.1f "
                "context_ms=%.1f spawn_ms=%.1f media_ms=%.1f "
                "fs_available_ms=%.1f tools=%d",
                user_id, (conversation_id or "")[:8], agent_name,
                total_ms, default_ms, dynamic_ms, mcp_ms, filter_ms,
                fs_find_ms, context_ms, spawn_ms, media_ms,
                fs_available_ms, tool_count)

        with self._registry_cache_lock:
            self._registry_cache[cache_key] = registry
            self._registry_cache_tool_counts[cache_key] = tool_count
            evt = self._registry_building.pop(cache_key, None)
            if evt:
                evt.set()
        return registry

    @staticmethod
    def _make_media_resolver(user_id: str, conversation_id: str, media_type: str,
                             required_methods=()):
        """Build a resolver closure for image/video/audio services."""
        required_methods = tuple(required_methods or ())
        def resolver(required_methods_override=()):
            type_map = {
                "image": ("base_image_generation", "BaseImageGenerationService"),
                "video": ("base_video_generation", "BaseVideoGenerationService"),
                "speech_to_video": ("base_video_generation", "BaseVideoGenerationService"),
                "audio": ("base_audio_generation", "BaseAudioGenerationService"),
                "tts": ("base_tts", "BaseTTSService"),
                "3d": ("base_capabilities", "BaseImage3DService"),
                "upscale": ("base_capabilities", "BaseImageUpscaleService"),
                "tryon": ("base_capabilities", "BaseTryOnService"),
                "lipsync": ("base_capabilities", "BaseLipsyncService"),
                "trainer": ("base_capabilities", "BaseImageTrainerService"),
                "voice": ("base_voice_clone", "BaseVoiceCloneService"),
            }
            mod_name, cls_name = type_map[media_type]
            import importlib
            mod = importlib.import_module(f"services.{mod_name}")
            base_class = getattr(mod, cls_name)

            # Discover valid service types
            try:
                from tasks import _register_all_services
                _register_all_services()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            valid_types = set()
            for stype, sclass in ServiceFactory._services.items():
                try:
                    if issubclass(sclass, base_class):
                        valid_types.add(stype)
                except TypeError:
                    pass

            # Find deployed services
            # Find all matching services across scopes (conv > user > global)
            from core.service_registry import ServiceRegistry
            _sreg = ServiceRegistry.get_instance()
            matching = []
            for vtype in valid_types:
                matching.extend(_sreg.resolve_by_type(
                    vtype, user_id=user_id, conv_id=conversation_id))
            pfp_capabilities = {
                "image": {"media.image_generation"},
                "video": {"media.video_generation"},
                "speech_to_video": {"media.video_generation", "media.lipsync"},
                "audio": {"media.audio_generation"},
                "tts": {"media.tts", "media.audio_generation", "media.voice_clone"},
                "3d": {"media.3d_generation"},
                "upscale": {
                    "media.image_upscale", "media.video_upscale",
                    "media.background_removal",
                },
                "tryon": {"media.try_on"},
                "lipsync": {"media.lipsync"},
                "trainer": {"media.image_training"},
                "voice": {"media.voice_clone"},
            }.get(media_type, set())
            if pfp_capabilities:
                for sdef in _sreg.resolve_by_type(
                        "packageRuntime", user_id=user_id,
                        conv_id=conversation_id):
                    runtime = (sdef.config or {}).get("package_runtime") or {}
                    provides = set(runtime.get("provides") or [])
                    if provides.intersection(pfp_capabilities):
                        matching.append(sdef)
            matching = [
                sdef for _idx, sdef in sorted(
                    enumerate(matching),
                    key=lambda item: (_ToolRelayRegistryMixin._service_scope_rank(item[1]), item[0]),
                )
            ]

            if not matching:
                return None, f"No {media_type} generation service deployed"
            method_map = {
                "image": ("generate",),
                "video": (
                    "generate", "frame_to_video", "image_to_video",
                    "reference_to_video", "video_edit"),
                "speech_to_video": ("speech_to_video",),
                "audio": ("generate",),
                "tts": ("speak",),
                "3d": ("generate_3d",),
                "upscale": (
                    "upscale", "upscale_video", "remove_background"),
                "tryon": ("try_on",),
                "lipsync": ("lipsync",),
                "trainer": ("train",),
                "voice": ("clone_speak",),
            }
            required = tuple(
                required_methods_override or required_methods
                or method_map.get(media_type, ()))
            def _service_supports_required_methods(svc):
                if not svc:
                    return False
                native_proxy_methods = {"get_model_info"}
                if any(method in native_proxy_methods
                       and callable(getattr(svc, method, None))
                       for method in required):
                    return True
                operation_getter = getattr(svc, "get_operations", None)
                if callable(operation_getter):
                    operations = operation_getter() or {}
                    if isinstance(operations, dict):
                        operation_names = set(operations)
                        if not operation_names:
                            return False
                    elif isinstance(operations, (list, tuple, set)):
                        operation_names = {
                            str(name) for name in operations if str(name or "")}
                        if not operation_names:
                            return False
                    else:
                        operation_names = set()
                    if operation_names and not any(method in operation_names for method in required):
                        return False
                return any(hasattr(svc, method) for method in required)

            first_sid = matching[0].service_id
            op_desc = ", ".join(required) or media_type
            any_resolved = False
            for sdef in matching:
                svc = _ToolRelayRegistryMixin._resolve_service_definition(
                    _sreg, sdef, user_id=user_id,
                    conversation_id=conversation_id)
                if svc is None:
                    continue
                any_resolved = True
                if _service_supports_required_methods(svc):
                    return svc, None
            # Distinguish 'no service could be reached' from 'the deployed
            # service(s) are up but don't implement this operation'.
            if any_resolved:
                deployed = ", ".join(s.service_id for s in matching)
                return None, (
                    f"No deployed {media_type} service supports this operation "
                    f"({op_desc}); available: {deployed}")
            return None, f"{media_type.title()} service '{first_sid}' failed to connect"
        return resolver

    @staticmethod
    def _resolve_service_definition(registry, service_def, *, user_id: str,
                                    conversation_id: str):
        service_id = str(getattr(service_def, "service_id", "") or "")
        if not service_id:
            return None
        scoped_getter = getattr(registry, "get_live_instance", None)
        if callable(scoped_getter):
            scope = str(getattr(service_def, "scope", "") or "")
            scope_id = str(getattr(service_def, "scope_id", "") or "")
            if scope and scope_id:
                return scoped_getter(scope, scope_id, service_id)
        return registry.resolve(
            service_id, user_id=user_id, conv_id=conversation_id)

    @staticmethod
    def _service_scope_rank(service_def) -> int:
        scope = str(getattr(service_def, "scope", "") or "").lower()
        if scope in {"conv", "conversation"}:
            return 0
        if scope == "user":
            return 1
        return 2
