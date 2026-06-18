"""Media-service discovery and per-capability resolver factories for
AgentUtilsMixin (image/video/audio/tts/stt/3d/upscale/tryon/lipsync/etc.).

Split out of agent_utils.py as a leaf mixin so the file stays <= 800 lines.
Methods rely on AgentUtilsMixin host state/methods via the MRO.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _resolve_extra_dict(store, conv_id: str, key: str, user_id: str = ""):
    """Read a conv extra dict and resolve ${...} expressions in all values."""
    from core.expression import resolve_value
    raw = store.get_extra(conv_id, key) or {}
    return resolve_value(raw, owner=user_id, conversation_id=conv_id)


def _service_scope_rank(scope: str) -> int:
    scope = str(scope or "").lower()
    if scope in {"conv", "conversation"}:
        return 0
    if scope == "user":
        return 1
    return 2


class _MediaServiceRef(tuple):
    """Tuple-compatible service reference carrying the exact ServiceDef."""

    def __new__(cls, service_def):
        return super().__new__(cls, (
            service_def.service_id, service_def.service_type, service_def.scope))

    def __init__(self, service_def):
        self.service_def = service_def




class _AgentMediaMixin:
    """Media-service discovery + capability resolver factories."""

    @staticmethod
    def _get_media_types(base_class) -> set:
        """Get all registered service_type strings that inherit from base_class."""
        try:
            from tasks import _register_all_services
            _register_all_services()
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        from core import ServiceFactory
        types = set()
        for stype, sclass in ServiceFactory._services.items():
            try:
                if issubclass(sclass, base_class):
                    types.add(stype)
            except TypeError:
                pass
        return types

    @staticmethod
    def _get_package_runtime_capabilities(base_class) -> set:
        """Return PFP `provides` capabilities compatible with a media base."""
        from services.base_image_generation import BaseImageGenerationService
        from services.base_video_generation import BaseVideoGenerationService
        from services.base_audio_generation import BaseAudioGenerationService
        from services.base_tts import BaseTTSService
        from services.base_capabilities import (
            BaseImage3DService, BaseImageUpscaleService,
            BaseTryOnService, BaseLipsyncService, BaseImageTrainerService,
        )
        from services.base_voice_clone import BaseVoiceCloneService

        mapping = (
            (BaseImageGenerationService, {"media.image_generation"}),
            (BaseVideoGenerationService, {"media.video_generation"}),
            (BaseAudioGenerationService, {"media.audio_generation"}),
            (BaseTTSService, {"media.tts", "media.audio_generation", "media.voice_clone"}),
            (BaseImage3DService, {"media.3d_generation"}),
            (BaseImageUpscaleService, {
                "media.image_upscale", "media.video_upscale",
                "media.background_removal",
            }),
            (BaseTryOnService, {"media.try_on"}),
            (BaseLipsyncService, {"media.lipsync"}),
            (BaseImageTrainerService, {"media.image_training"}),
            (BaseVoiceCloneService, {"media.voice_clone"}),
        )
        caps = set()
        for candidate, provides in mapping:
            try:
                if issubclass(candidate, base_class) or issubclass(base_class, candidate):
                    caps.update(provides)
            except TypeError:
                continue
        return caps


    def _discover_media_services(self, user_id: str, base_class,
                                 conversation_id: str = "") -> list:
        """Discover all deployed and enabled services of a given type.

        Uses the service definitions from global + user registries.
        Matches service_type against known types for the base_class.
        Rechecked every time (services can be added at runtime).

        Returns list of (service_id, service_type, scope) tuples.
        """
        definitions = self._discover_media_service_definitions(
            user_id, base_class, conversation_id)
        return [_MediaServiceRef(sdef) for sdef in definitions]

    def _discover_media_service_definitions(self, user_id: str, base_class,
                                            conversation_id: str = "") -> list:
        """Discover deployed service definitions for a media base class."""
        valid_types = self._get_media_types(base_class)
        pfp_capabilities = self._get_package_runtime_capabilities(base_class)

        results = []
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            for vtype in valid_types:
                for sdef in reg.resolve_by_type(
                        vtype, user_id=user_id, conv_id=conversation_id):
                    results.append(sdef)
            if pfp_capabilities:
                for sdef in reg.resolve_by_type(
                        "packageRuntime", user_id=user_id, conv_id=conversation_id):
                    runtime = (sdef.config or {}).get("package_runtime") or {}
                    provides = set(runtime.get("provides") or [])
                    if provides.intersection(pfp_capabilities):
                        results.append(sdef)
            results = [
                item for _idx, item in sorted(
                    enumerate(results),
                    key=lambda entry: (_service_scope_rank(entry[1].scope), entry[0]),
                )
            ]
        except Exception as e:
            logger.error("Service discovery failed: %s", e, exc_info=True)
        return results


    @staticmethod
    def _resolve_media_service_by_id(service_id: str, user_id: str,
                                     conversation_id: str = ""):
        """Resolve a media/capability service by ID. Returns instance or None."""
        if not service_id:
            return None
        try:
            from core.service_registry import ServiceRegistry
            return ServiceRegistry.get_instance().resolve(
                service_id, user_id=user_id, conv_id=conversation_id)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return None

    @staticmethod
    def _resolve_media_service_definition(service_def, user_id: str,
                                          conversation_id: str = ""):
        service_def = getattr(service_def, "service_def", service_def)
        service_id = str(getattr(service_def, "service_id", "") or "")
        if not service_id:
            return None
        try:
            from core.service_registry import ServiceRegistry
            registry = ServiceRegistry.get_instance()
            scoped_getter = getattr(registry, "get_live_instance", None)
            if callable(scoped_getter):
                scope = str(getattr(service_def, "scope", "") or "")
                scope_id = str(getattr(service_def, "scope_id", "") or "")
                if scope and scope_id:
                    return scoped_getter(scope, scope_id, service_id)
            return registry.resolve(
                service_id, user_id=user_id, conv_id=conversation_id)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return None


    def _make_media_resolver(self, user_id: str, conversation_id: str,
                             agent_name: str, base_class,
                             extra_key: str, label: str, command: str,
                             required_methods=(), require_preference=False):
        """Build a generic resolver closure for any media service type."""
        _self = self
        required_methods = tuple(required_methods or ())

        def _service_supports_required_methods(svc, active_required=None):
            if not svc:
                return False
            required = tuple(active_required or required_methods)
            if not required:
                return True
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
                    operation_names = {str(name) for name in operations if str(name or "")}
                    if not operation_names:
                        return False
                else:
                    operation_names = set()
                if operation_names and not any(method in operation_names for method in required):
                    return False
            return any(hasattr(svc, method) for method in required)

        def resolver(required_methods_override=()):
            active_required = tuple(required_methods_override or required_methods)
            available = _self._discover_media_services(
                user_id, base_class, conversation_id)
            if not available:
                return None, f"No {label} service deployed"
            if len(available) == 1:
                service_def = getattr(available[0], "service_def", None)
                if service_def is not None:
                    svc = _self._resolve_media_service_definition(
                        service_def, user_id, conversation_id)
                else:
                    svc = _self._resolve_media_service_by_id(
                        available[0][0], user_id, conversation_id)
                if _service_supports_required_methods(svc, active_required):
                    return svc, None
                return None, f"{label.title()} service '{available[0][0]}' failed to connect"
            # Multiple → check per-agent preference, then wildcard, then
            # deterministic first available service. A tool call can still
            # override per-call with service=<name>.
            if conversation_id:
                from core.conversation_store import ConversationStore
                prefs = _resolve_extra_dict(
                    ConversationStore.instance(), conversation_id,
                    extra_key, user_id)
                preferred = prefs.get(agent_name or "agent") or prefs.get("*")
                if preferred:
                    svc = _self._resolve_media_service_by_id(
                        preferred, user_id, conversation_id)
                    if _service_supports_required_methods(svc, active_required):
                        return svc, None
            if require_preference:
                return None, f"Multiple {label} services available; choose a conversation default"
            for service_ref in available:
                service_def = getattr(service_ref, "service_def", None)
                if service_def is not None:
                    svc = _self._resolve_media_service_definition(
                        service_def, user_id, conversation_id)
                else:
                    svc = _self._resolve_media_service_by_id(
                        service_ref[0], user_id, conversation_id)
                if _service_supports_required_methods(svc, active_required):
                    return svc, None
            return None, f"{label.title()} service '{available[0][0]}' failed to connect"
        return resolver


    def _make_image_resolver(self, user_id, conversation_id, agent_name,
                             required_methods=("generate",)):
        from services.base_image_generation import BaseImageGenerationService
        return self._make_media_resolver(
            user_id, conversation_id, agent_name,
            BaseImageGenerationService, "image_services",
            "image generation", "/imgservice", required_methods,
        )


    def _make_video_resolver(self, user_id, conversation_id, agent_name,
                             required_methods=(
                                 "generate", "frame_to_video", "image_to_video",
                                 "reference_to_video", "video_edit")):
        from services.base_video_generation import BaseVideoGenerationService
        return self._make_media_resolver(
            user_id, conversation_id, agent_name,
            BaseVideoGenerationService, "video_services",
            "video generation", "/vidservice", required_methods,
        )

    def _make_audio_resolver(self, user_id, conversation_id, agent_name,
                             required_methods=("generate",)):
        from services.base_audio_generation import BaseAudioGenerationService
        return self._make_media_resolver(
            user_id, conversation_id, agent_name,
            BaseAudioGenerationService, "audio_services",
            "audio generation", "/audioservice", required_methods,
        )

    def _make_tts_resolver(self, user_id, conversation_id, agent_name,
                           required_methods=("speak",)):
        from services.base_tts import BaseTTSService
        return self._make_media_resolver(
            user_id, conversation_id, agent_name,
            BaseTTSService, "audio_services",
            "text-to-speech", "/audioservice", required_methods,
            require_preference=True,
        )

    def _make_stt_resolver(self, user_id, conversation_id, agent_name,
                           required_methods=("transcribe",)):
        from services.base_stt import BaseSTTService
        return self._make_media_resolver(
            user_id, conversation_id, agent_name,
            BaseSTTService, "stt_services",
            "speech-to-text", "/sttservice", required_methods,
            require_preference=True,
        )

    def _make_3d_resolver(self, user_id, conversation_id, agent_name,
                          required_methods=("generate_3d",)):
        from services.base_capabilities import BaseImage3DService
        return self._make_media_resolver(
            user_id, conversation_id, agent_name,
            BaseImage3DService, "threed_services",
            "3D generation", "/threedservice", required_methods,
        )

    def _make_upscale_resolver(self, user_id, conversation_id, agent_name,
                               required_methods=(
                                   "upscale", "upscale_video", "remove_background")):
        from services.base_capabilities import BaseImageUpscaleService
        return self._make_media_resolver(
            user_id, conversation_id, agent_name,
            BaseImageUpscaleService, "upscale_services",
            "image upscaling", "/upscaleservice", required_methods,
        )

    def _make_tryon_resolver(self, user_id, conversation_id, agent_name,
                             required_methods=("try_on",)):
        from services.base_capabilities import BaseTryOnService
        return self._make_media_resolver(
            user_id, conversation_id, agent_name,
            BaseTryOnService, "tryon_services",
            "virtual try-on", "/tryonservice", required_methods,
        )

    def _make_lipsync_resolver(self, user_id, conversation_id, agent_name,
                               required_methods=("lipsync",)):
        from services.base_capabilities import BaseLipsyncService
        return self._make_media_resolver(
            user_id, conversation_id, agent_name,
            BaseLipsyncService, "lipsync_services",
            "lipsync", "/lipsyncservice", required_methods,
        )

    def _make_speech_to_video_resolver(self, user_id, conversation_id, agent_name):
        from services.base_video_generation import BaseVideoGenerationService
        _self = self

        def resolver():
            available = list(_self._discover_media_service_definitions(
                user_id, BaseVideoGenerationService, conversation_id))
            try:
                from core.service_registry import ServiceRegistry
                reg = ServiceRegistry.get_instance()
                for sdef in reg.resolve_by_type(
                        "packageRuntime", user_id=user_id,
                        conv_id=conversation_id):
                    runtime = (sdef.config or {}).get("package_runtime") or {}
                    provides = set(runtime.get("provides") or [])
                    operations = (sdef.config or {}).get("operations") or {}
                    if ("speech_to_video" in operations
                            and provides.intersection({"media.video_generation", "media.lipsync"})):
                        available.append(sdef)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

            available = [
                item for _idx, item in sorted(
                    enumerate(available),
                    key=lambda entry: (_service_scope_rank(entry[1].scope), entry[0]),
                )
            ]
            seen = set()
            ordered = []
            for item in available:
                runtime = (getattr(item, "config", {}) or {}).get("package_runtime") or {}
                key = (
                    item.service_id, item.scope,
                    getattr(item, "scope_id", ""),
                    runtime.get("package", ""), runtime.get("object_id", ""),
                )
                if item.service_id and key not in seen:
                    seen.add(key)
                    ordered.append(item)
            if not ordered:
                return None, "No speech-to-video service deployed"

            preferred_ids = []
            if conversation_id:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                for key in ("video_services", "lipsync_services"):
                    prefs = _resolve_extra_dict(store, conversation_id, key, user_id)
                    preferred = prefs.get(agent_name or "agent") or prefs.get("*")
                    if preferred and preferred not in preferred_ids:
                        preferred_ids.append(preferred)

            for sid in preferred_ids:
                svc = _self._resolve_media_service_by_id(
                    sid, user_id, conversation_id)
                if svc and hasattr(svc, "speech_to_video"):
                    return svc, None
            for service_def in ordered:
                svc = _self._resolve_media_service_definition(
                    service_def, user_id, conversation_id)
                if svc and hasattr(svc, "speech_to_video"):
                    return svc, None
            return None, f"Speech-to-video service '{ordered[0].service_id}' failed to connect"

        return resolver

    def _make_trainer_resolver(self, user_id, conversation_id, agent_name,
                               required_methods=("train",)):
        from services.base_capabilities import BaseImageTrainerService
        return self._make_media_resolver(
            user_id, conversation_id, agent_name,
            BaseImageTrainerService, "trainer_services",
            "image-model training", "/trainerservice", required_methods,
        )

    def _make_voice_clone_resolver(self, user_id, conversation_id, agent_name,
                                   required_methods=("clone_speak",)):
        from services.base_voice_clone import BaseVoiceCloneService
        return self._make_media_resolver(
            user_id, conversation_id, agent_name,
            BaseVoiceCloneService, "voice_clone_services",
            "voice cloning", "/voicecloneservice", required_methods,
        )


    def _decrement_active(self, conversation_id: str, ctx: dict = None):
        """Decrement the active-conversation refcount and clean up tracking.

        Also refreshes the poll cooldown so that agent-generated messages
        don't trigger other agents to wake up (only user messages should).
        """
        if ctx and ctx.get("_active_cleanup_done"):
            return
        with self._active_lock:
            rc = self._active_conversations.get(conversation_id, 1) - 1
            if rc <= 0:
                self._active_conversations.pop(conversation_id, None)
            else:
                self._active_conversations[conversation_id] = rc
            if ctx and not ctx.get("is_poll"):
                self._user_active_conversations.discard(conversation_id)
            if ctx:
                _tk = ctx.get("_thought_key")
                if _tk:
                    self._active_thoughts.discard(_tk)
        # Clean up provider-agnostic active turn + live client references.
        _agent_n = ctx.get("active_agent_name", "") if ctx else ""
        _cc_key = f"{conversation_id}:{_agent_n}" if _agent_n else conversation_id
        _turn_key = (ctx or {}).get("_active_turn_key") or _cc_key
        _released_turn = False
        with self._active_contexts_lock:
            _turn = self._active_turns.get(_turn_key)
            _ctx_gen = (ctx or {}).get("_generation")
            _turn_gen = _turn.get("generation") if isinstance(_turn, dict) else None
            if _turn is None or _turn_gen is None or _ctx_gen is None or _turn_gen == _ctx_gen:
                self._active_turns.pop(_turn_key, None)
                _released_turn = True
            self._active_claude_client.pop(_cc_key, None)
        if ctx and _released_turn:
            ctx["_active_cleanup_done"] = True

