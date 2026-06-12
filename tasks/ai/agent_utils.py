"""AgentLoopTask mixin — AgentUtils methods

Auto-extracted from tasks/ai/agent_loop.py.
All methods access self (AgentLoopTask instance).
"""
import json
import logging
import threading
import time
from typing import Dict, Any, List, Optional


from core import FlowFile
from core.llm_client import (
    LLMClient, LLMMessage, LLMResponse, LLMToolDefinition,
    LLMToolCall, LLMToolResult, LLMClientError,
)
from core.tool_registry import ToolRegistry, create_default_registry

logger = logging.getLogger(__name__)


def _estimate_content_tokens(content: str, default_cpt: float = 3.5) -> int:
    """Estimate token count for a content string, aware of content type.

    JSON content (starts with { or [) uses 2 chars/token — it's denser due to
    brackets, quoted keys, and punctuation. Natural language uses the default
    ~3.5 chars/token ratio. Matches Claude Code's approach.
    """
    if not isinstance(content, str) or not content:
        return 0
    stripped = content.lstrip()
    if stripped.startswith('{') or stripped.startswith('['):
        return int(len(content) / 2.0)
    return int(len(content) / default_cpt)


def _resolve_extra(store, conv_id: str, key: str, user_id: str = ""):
    """Read a conv extra and resolve ${...} expressions."""
    from core.expression import resolve_value
    return resolve_value(store.get_extra(conv_id, key), owner=user_id)


def _resolve_extra_dict(store, conv_id: str, key: str, user_id: str = ""):
    """Read a conv extra dict and resolve ${...} expressions in all values."""
    from core.expression import resolve_value
    raw = store.get_extra(conv_id, key) or {}
    return resolve_value(raw, owner=user_id)


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


class AgentUtilsMixin:
    """Methods extracted from AgentLoopTask."""


    def _resolve_client(self, service_id: str, user_id: str, *,
                        raise_on_missing: bool = False,
                        default_model: str = "",
                        **_compat):
        """Unified LLM client resolution.

        service_id is ALREADY resolved (caller uses resolve_value/resolve_service_param).
        Returns (LLMClient | None, service | None).
        """
        svc_id = service_id or ""
        client, svc = self._resolve_llm_service(svc_id, user_id)
        if not client and self.config.get("api_key"):
            _fallback_cfg = {
                "api_key": self.config["api_key"],
                "base_url": self.config.get("base_url", ""),
                "model": default_model,
                "timeout": self.config.get("timeout", 0),
            }
            client = LLMClient(
                provider=self.config.get("provider", "openai"),
                config=_fallback_cfg,
            )
            svc = None
        if not client and raise_on_missing:
            raise ValueError(
                f"LLM service '{service_id}' not found. "
                f"Define it in services and reference it explicitly."
            )
        return client, svc


    def _get_default_client(self, user_id: str = ""):
        """Get the task's default LLM client (for compaction/summarization).

        Always uses the task-level llm_service, never the agent-switched one.
        """
        client, _ = self._resolve_client(
            self.config.get("llm_service", ""), user_id,
            resolve_expressions=True,
        )
        return client


    def _resolve_llm_service(self, service_id: str, user_id: str,
                             conversation_id: str = ""):
        """Resolve an LLM service by ID. Returns (LLMClient, service) or (None, None).

        Resolution order: flow services → ServiceRegistry (user) → ServiceRegistry (global).
        If the service has an API key pool, uses conversation affinity.
        """
        if not service_id:
            return None, None

        def _get_client_with_pool(svc):
            """Get client with pool_index from conversation affinity."""
            pool_idx = -1
            if conversation_id and hasattr(svc, 'get_pool_size') and svc.get_pool_size() > 0:
                try:
                    from core.conversation_store import ConversationStore
                    pool_idx = int(ConversationStore.instance().get_extra(
                        conversation_id, f"llm_api_key_idx:{service_id}") or -1)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            client = svc.get_client(pool_index=pool_idx)
            # Store the pool index for this conversation (first use)
            if conversation_id and hasattr(client, '_active_pool_index'):
                _pidx = client._active_pool_index
                try:
                    from core.conversation_store import ConversationStore
                    ConversationStore.instance().set_extra(
                        conversation_id, f"llm_api_key_idx:{service_id}", _pidx)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            return client

        # 1. Flow-level services (defined in flow JSON)
        if self._services:
            svc = self._services.get(service_id)
            if svc and hasattr(svc, 'get_client'):
                return _get_client_with_pool(svc), svc
        # 2. Resolve across scopes (conv > user > global)
        try:
            from core.service_registry import ServiceRegistry
            svc = ServiceRegistry.get_instance().resolve(
                service_id, user_id=user_id, conv_id=conversation_id)
            if svc and hasattr(svc, 'get_client'):
                return _get_client_with_pool(svc), svc
        except Exception as e:
            logger.warning("Service '%s' resolution failed: %s", service_id, e)
        return None, None


    def _resolve_agent_client(self, agent_name: str, user_id: str,
                              conversation_id: str = ""):
        """Resolve an agent's LLM client by following the override chain.

        Resolution order:
        1. conv_agents runtime config (llm_service for this agent in this conv)
        2. Task-level llm_service default

        Returns (client, service_id, resolved_svc) or (None, "", None).
        """
        svc_id = ""
        # Diagnostic state: populated during conv-level lookup so a
        # failed resolve can report *why* instead of just "no service".
        # Without this, silent exceptions and empty configs were
        # indistinguishable in the error trace.
        _diag_all_agents: list = []
        _diag_agent_found = False
        _diag_conv_svc_raw = ""
        _diag_conv_exc: Optional[str] = None
        # 1. Conv-level agent config
        if conversation_id and agent_name:
            try:
                from core.conv_agent_config import (
                    get_agent_config, get_all_agent_configs)
                from core.expression import resolve_value
                _all_cfgs = get_all_agent_configs(conversation_id) or {}
                _diag_all_agents = list(_all_cfgs.keys())
                # Case-insensitive membership check — get_agent_config
                # does the same, but we want it for diagnostics even if
                # the subsequent resolve_value raises.
                _needle = (agent_name or "").lower()
                _diag_agent_found = any(
                    isinstance(_k, str) and _k.lower() == _needle
                    for _k in _diag_all_agents)
                acfg = get_agent_config(conversation_id, agent_name)
                _diag_conv_svc_raw = acfg.get("llm_service", "") or ""
                svc_id = resolve_value(_diag_conv_svc_raw,
                                       owner=user_id) or ""
            except Exception as _cvr_err:
                # Don't swallow silently — a broken ${…} expression in
                # the agent's llm_service or a malformed conv_agents
                # entry used to disappear here, leaving only the "no
                # service resolved" error with no clue as to cause.
                _diag_conv_exc = f"{type(_cvr_err).__name__}: {_cvr_err}"
                logger.warning(
                    "[agent-resolve] conv-level lookup failed for "
                    "agent '%s' in conv %s: %s",
                    agent_name, (conversation_id or "")[:8],
                    _diag_conv_exc, exc_info=True)
        # 2. Task default
        _diag_task_svc = ""
        if not svc_id:
            _diag_task_svc = self._resolve_service_param(
                "llm_service", user_id)
            svc_id = _diag_task_svc
            if not svc_id:
                # Hard fail with a self-contained diagnosis so the log
                # line is enough to see what went wrong (previously the
                # user had to instrument the code to find out whether
                # the agent was missing from conv_agents, had an empty
                # llm_service, or had an unresolvable ${…} expression).
                raise RuntimeError(
                    "No llm_service resolved for agent {!r} in conv "
                    "{!r} (user {!r}). "
                    "conv_agents keys={!r}; "
                    "agent_found_in_conv_agents={}; "
                    "conv_level_llm_service_raw={!r}; "
                    "conv_lookup_exc={!r}; "
                    "task_default_llm_service={!r}. "
                    "Check conv_agents config, flow params, or global "
                    "parameters.".format(
                        agent_name, (conversation_id or "")[:8],
                        user_id or "",
                        _diag_all_agents, _diag_agent_found,
                        _diag_conv_svc_raw, _diag_conv_exc,
                        _diag_task_svc))
        client, svc = self._resolve_llm_service(svc_id, user_id, conversation_id)
        return client, svc_id, svc

    def _resolve_service_param(self, param_name: str, user_id: str = "") -> str:
        """Resolve a service parameter that may contain ${...} expressions.

        If not in task config, falls back to schema default (lazy eval).
        Returns the resolved service ID string, or "" if not configured.
        """
        svc_id = self.config.get(param_name, "")
        # If not in config, try schema default (e.g. "${summarizer_service}")
        if not svc_id:
            schema = {}
            if hasattr(self, 'get_parameter_schema'):
                schema = self.get_parameter_schema() or {}
            default = (schema.get(param_name) or {}).get("default", "")
            if default:
                svc_id = default
        from core.expression import resolve_value
        return resolve_value(svc_id, owner=user_id) or ""

    def _get_summarizer_client(self, user_id: str = "", conversation_id: str = ""):
        """Resolve the effective summarizer service and its LLM service.

        Returns (service_or_client, max_context_tokens, llm_service_id) or
        (None, 0, ""). A conversation can explicitly bind one summarizer;
        otherwise the first enabled summarizer service wins in scope order:
        conversation -> user -> global.
        """
        from core.summarizer_bindings import resolve_service
        summarizer, sdef, explicit = resolve_service(user_id, conversation_id)
        if not summarizer:
            return None, 0, ""
        client, ctx_max, llm_service = summarizer.resolve_llm_service(
            user_id=user_id, conversation_id=conversation_id)
        if client:
            logger.debug(
                "[summarizer] resolved service='%s' scope=%s explicit=%s llm='%s'",
                getattr(sdef, "service_id", ""), getattr(sdef, "scope", ""),
                explicit, llm_service)
            return client, ctx_max, llm_service
        logger.warning(
            "[summarizer] service '%s' resolved but LLM service '%s' is unavailable",
            getattr(sdef, "service_id", ""),
            (getattr(summarizer, "config", {}) or {}).get("llm_service", ""))
        return None, 0, ""

    def _get_title_client(self, user_id: str = ""):
        """Resolve a dedicated LLM service for conversation title generation.

        Same pattern as _get_summarizer_client. When configured, the agent
        loop generates a short title after the first done event.

        Returns (service_or_client, service_id) or (None, "").
        """
        svc_id = self._resolve_service_param("title_llm_service", user_id)
        if not svc_id:
            return None, ""
        logger.debug(f"[title_llm] resolved to '{svc_id}'")
        client, svc = self._resolve_llm_service(svc_id, user_id)
        if svc and hasattr(svc, 'complete'):
            return svc, svc_id
        if client:
            return client, svc_id
        return None, ""

    # ── Media service discovery (generic for image/video) ───────────


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

    def _calibrate_cpt(self, service_id: str, total_chars: int,
                       actual_tokens: int):
        """Update the calibrated chars-per-token ratio from actual API usage.

        Uses exponential moving average (alpha=0.3) so the ratio adapts
        quickly but doesn't swing wildly on a single outlier.
        """
        if not service_id or actual_tokens <= 0 or total_chars <= 0:
            return
        measured = total_chars / actual_tokens
        with self._calibrated_cpt_lock:
            old = self._calibrated_cpt.get(service_id)
            if old is None:
                self._calibrated_cpt[service_id] = measured
            else:
                alpha = 0.3
                self._calibrated_cpt[service_id] = old * (1 - alpha) + measured * alpha


    def _get_cpt(self, service_id: str, fallback: float = 0) -> float:
        """Get the best chars-per-token ratio for a service.

        Priority: calibrated (learned) → service config → default (2.0).
        """
        with self._calibrated_cpt_lock:
            cal = self._calibrated_cpt.get(service_id)
        if cal and cal > 0:
            return cal
        return fallback if fallback > 0 else 2.0


    @staticmethod
    def _track_tokens(user_id: str, tokens_in: int, tokens_out: int,
                      model: str, agent_name: str = "",
                      llm_service: str = "", cache_read: int = 0,
                      cache_write: int = 0):
        """Track token usage via TokenTracker (best-effort)."""
        try:
            from core.token_tracker import TokenTracker
            TokenTracker.instance().track(
                user_id, tokens_in, tokens_out,
                model=model, agent_name=agent_name,
                llm_service=llm_service, cache_read=cache_read,
                cache_write=cache_write,
            )
            TokenTracker.instance().flush()
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)


    @staticmethod
    def _strip_echo_prefix(text: str) -> str:
        """Strip identity prefix that the LLM may echo back (e.g. '[agent]: ...')."""
        if not text:
            return text
        stripped = text.lstrip()
        if stripped.startswith("["):
            import re
            return re.sub(r'^\[[^\]]+\]:\s*', '', stripped)
        return text


    @staticmethod
    def _deflate_image_messages(messages: List[LLMMessage], keep_last: bool = False,
                                 user_id: str = "", conversation_id: str = ""):
        """Replace multimodal image content with text-only references in-place.

        Called after the LLM has seen the images so base64 data doesn't
        persist in the conversation context.  The LLM can use view_image
        or show_file to re-request an image if needed.

        If keep_last=True, the last message with images is preserved
        (for pre-send compaction where the LLM hasn't seen them yet).
        """
        if keep_last:
            # Find the last message with images and skip it
            last_img_idx = -1
            for i, m in enumerate(messages):
                if isinstance(m.content, list) and any(
                    p.get("type") in ("image_url", "image_ref", "image")
                    for p in m.content
                ):
                    last_img_idx = i
        for idx, m in enumerate(messages):
            if not isinstance(m.content, list):
                continue
            has_images = any(
                p.get("type") in ("image_url", "image_ref", "image")
                for p in m.content
            )
            if not has_images:
                continue
            if keep_last and idx == last_img_idx:
                continue
            # Keep text parts, save images to FileStore and keep references
            text_parts = []
            img_refs = []
            for part in m.content:
                if part.get("type") == "text":
                    text_parts.append(part["text"])
                elif part.get("type") == "image_url":
                    url = (part.get("image_url", {}).get("url", "") or "")
                    if url.startswith("data:"):
                        # Save base64 data URI to FileStore
                        try:
                            import base64 as _b64d, re as _re_d
                            _m = _re_d.match(r'data:([^;]+);base64,(.+)', url)
                            if _m:
                                mime, b64 = _m.group(1), _m.group(2)
                                ext = {"image/png": "png", "image/jpeg": "jpg",
                                       "image/webp": "webp", "image/gif": "gif"}.get(mime, "png")
                                from core.file_store import FileStore
                                import time as _t
                                fname = f"image_{int(_t.time())}_{len(img_refs)}.{ext}"
                                fid = FileStore.instance().store(
                                    fname, _b64d.b64decode(b64), mime,
                                    user_id=user_id, conversation_id=conversation_id)
                                img_refs.append(f"fs://filestore/{fid}/{fname}")
                        except Exception:
                            img_refs.append("(image)")
                    elif "/files/" in url:
                        img_refs.append(url)
                    elif url.startswith(("http://", "https://")):
                        # Keep full URL — small compared to base64, allows re-fetch
                        img_refs.append(url)
                    else:
                        img_refs.append("(image)")
                elif part.get("type") == "image_ref":
                    fid = part.get("file_id", "")
                    fname = part.get("filename", "image") or "image"
                    img_refs.append(
                        f"fs://filestore/{fid}/{fname}" if fid else "(image)")
                elif part.get("type") == "image":
                    source = part.get("source") if isinstance(part.get("source"), dict) else {}
                    data_b64 = source.get("data") or part.get("data") or ""
                    if data_b64:
                        try:
                            import base64 as _b64d
                            mime = (source.get("media_type") or part.get("mimeType")
                                    or part.get("mime_type") or "image/png")
                            ext = {"image/png": "png", "image/jpeg": "jpg",
                                   "image/webp": "webp", "image/gif": "gif"}.get(mime, "png")
                            from core.file_store import FileStore
                            import time as _t
                            fname = part.get("filename") or f"image_{int(_t.time())}_{len(img_refs)}.{ext}"
                            fid = FileStore.instance().store(
                                fname, _b64d.b64decode(data_b64), mime,
                                user_id=user_id, conversation_id=conversation_id)
                            img_refs.append(f"fs://filestore/{fid}/{fname}")
                        except Exception:
                            img_refs.append("(image)")
                    else:
                        img_refs.append("(image)")
            text = "\n".join(text_parts)
            if img_refs:
                refs_text = "\n".join(f"  - {ref}" for ref in img_refs)
                m.content = f"{text}\n[{len(img_refs)} image(s) — saved to FileStore:\n{refs_text}\n  Use show_file to view again]"
            else:
                m.content = f"{text}\n[images deflated]"

    # ── Tool result size management ──────────────────────────────────

    # TTL for tool result files in FileStore (seconds). Default 1h.
    _TOOL_RESULT_TTL = 3600
    # Threshold for clearing tool results (chars). Results over this get
    # saved to FileStore and replaced with a reference after the LLM has seen them.
    _TOOL_RESULT_CLEAR_THRESHOLD = 5000  # only store results > 5KB to FileStore


    @staticmethod
    def _detect_base64_blob(text: str) -> bool:
        """Check if text contains a large base64 blob (data URI or raw).

        Avoids false positives on minified code which also has long
        alphanumeric stretches but contains (){}[].:; characters.
        """
        if "data:" in text and ";base64," in text:
            return True
        # Raw base64: 1000+ chars of base64 alphabet WITHOUT code punctuation
        import re
        match = re.search(r'[A-Za-z0-9+/=]{1000,}', text)
        if not match:
            return False
        # Verify it's actual base64 (no code-like chars mixed in)
        blob = match.group(0)
        # Real base64 has very few + and / relative to alphanumerics
        # and NEVER has (){}[].;: inside it
        code_chars = sum(1 for c in blob if c in '(){}[].:;,!@#$%^&*<>?~`')
        return code_chars == 0


    def _clear_seen_tool_results(self, messages, keep_recent: int = 4,
                                  conversation_id: str = "",
                                  user_id: str = "",
                                  agent_name: str = ""):
        """Clear old tool results that the LLM has already seen.

        Called AFTER the LLM has responded. Saves large results to FileStore
        and replaces them with a short reference in the context.

        Only clears results NOT in the last `keep_recent` messages.
        The LLM can use read(path=url, source='filestore') to retrieve them if needed.
        """
        import re as _re_fs
        _FS_REF = _re_fs.compile(r'/files/[a-f0-9]{12}(?:/|$)')
        cleared = 0

        # Clear old tool results > threshold, skipping the last `keep_recent` messages.
        _cutoff = max(1, len(messages) - keep_recent) if keep_recent > 0 else len(messages)
        for i in range(1, _cutoff):
            m = messages[i]
            if m.role != "tool" or not isinstance(m.content, str):
                continue
            content = m.content
            content_len = len(content)

            # Skip small results
            if content_len <= self._TOOL_RESULT_CLEAR_THRESHOLD:
                continue
            # Has a FileStore ref but still has content → shrink to ref only
            _ref_match = _re_fs.search(r'(\[Result cleared[^\]]*\])', content)
            if _ref_match:
                m.content = _ref_match.group(1)
                continue

            # Strip outer <tool_output tool="..."> wrapper for storage.
            _inner = content
            if _inner.startswith("<tool_output tool="):
                _nl = _inner.find("\n")
                if _nl >= 0:
                    _inner = _inner[_nl + 1:]
                _close = _inner.rfind("</tool_output>")
                if _close >= 0:
                    _inner = _inner[:_close].rstrip()

            # Save to FileStore
            try:
                from core.file_store import FileStore
                store = FileStore.instance()
                fname = f"tool_result_{cleared}.txt"
                fid = store.store(
                    fname, _inner.encode("utf-8"),
                    conversation_id=conversation_id,
                    user_id=user_id,
                    ttl=self._TOOL_RESULT_TTL,
                    agent_name=agent_name,
                    category="tool_result",
                )
                url = f"fs://filestore/{fid}/{fname}"
                # Keep a short summary so the LLM knows what happened
                _first_line = _inner.split("\n", 1)[0][:200]
                m.content = (
                    f"{_first_line}\n"
                    f"[Result cleared — {content_len:,} chars. "
                    f"Full output: read(path=\"{fid}\", source=\"filestore\")]"
                )
                cleared += 1
            except Exception:
                # Fallback: keep first line + truncate
                _first_line = content.split("\n", 1)[0][:200]
                m.content = f"{_first_line}\n[...{content_len - len(_first_line):,} chars cleared]"
                cleared += 1

        if cleared:
            logger.info(f"[clear_tool_results] Cleared {cleared} old tool result(s)")

    @staticmethod
    def _cleanup_tool_result_files(conversation_id: str = "",
                                    agent_name: str = ""):
        """Delete tool result files from FileStore after the agent's final response.

        Uses metadata filters (category + conversation_id + agent_name) —
        safe for multi-agent parallel execution.
        """
        try:
            from core.file_store import FileStore
            count = FileStore.instance().delete_by(
                category="tool_result",
                conversation_id=conversation_id,
                agent_name=agent_name,
            )
            if count:
                logger.info(f"[cleanup] Deleted {count} tool result file(s) "
                            f"for {agent_name or 'unknown'}@{conversation_id[:8]}")
        except Exception as e:
            logger.debug(f"[cleanup] Tool result file cleanup failed: {e}")

    @staticmethod
    def _estimate_tokens(messages: List[LLMMessage],
                         tool_defs: list = None,
                         chars_per_token: float = 0,
                         token_multiplier: float = 1.0) -> int:
        """Estimate token count for messages + tool definitions.

        Uses content-aware estimation: JSON content (starts with { or [)
        uses 2 chars/token (denser due to brackets, keys, punctuation),
        while natural language uses the default ~3.5 chars/token.

        *chars_per_token* controls the default ratio for natural language.
        Default (0) uses 3.5. The service config key ``chars_per_token``
        can override this per-LLM.

        *token_multiplier* scales the tiktoken (cl100k_base) count up to
        the real tokenizer of the target model — e.g. Opus 4.7 costs
        ~1.6x more tokens than cl100k for the same text. Compact thresh-
        old checks and the gauge both need REAL tokens, not raw.
        """
        # Precise counting via tiktoken — strip image data first
        try:
            from core.token_counter import count_messages_tokens
            from tasks.ai.context_usage_cache import _scrub_image_payloads
            _stripped = []
            for m in messages:
                c = m.content if hasattr(m, 'content') else str(m)
                if isinstance(c, list):
                    # Replace image parts with a small placeholder.
                    c = " ".join(
                        p.get("text", "") if p.get("type") == "text"
                        else "[image]" if p.get("type") in ("image_url", "image_ref", "image")
                        else p.get("text", "") if p.get("type") == "document"
                        else ""
                        for p in c
                    )
                elif isinstance(c, str):
                    c = _scrub_image_payloads(c)
                _stripped.append({"content": c})
            return count_messages_tokens(_stripped, multiplier=token_multiplier)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        # Fallback to character estimation
        # Modern tokenizers average ~3.5 chars/token for natural language.
        # JSON is denser (brackets, keys, less natural language) — use 2 chars/token.
        cpt = chars_per_token if chars_per_token > 0 else 3.5
        total_tokens = 0
        for m in messages:
            total_tokens += int(12 / cpt)  # message overhead (role, separators)
            if isinstance(m.content, str):
                total_tokens += _estimate_content_tokens(m.content, cpt)
            elif isinstance(m.content, list):
                for part in m.content:
                    if part.get("type") == "text":
                        total_tokens += _estimate_content_tokens(
                            part.get("text", ""), cpt)
                    elif part.get("type") == "document":
                        total_tokens += _estimate_content_tokens(
                            part.get("text", ""), cpt)
                    elif part.get("type") == "image_url":
                        # Images are handled separately by the API (not counted as text tokens).
                        # Don't count them — they inflate the estimate and trigger unnecessary compaction.
                        total_tokens += 85  # ~85 tokens per image tile in OpenAI/Anthropic
            if m.tool_calls:
                for tc in m.tool_calls:
                    # Tool call arguments are JSON — use 2 chars/token
                    _tc_chars = len(tc.name) + len(json.dumps(tc.arguments))
                    total_tokens += int(_tc_chars / 2.0)
        # Tool definitions (JSON schemas) are sent with every request
        if tool_defs:
            for td in tool_defs:
                _td_chars = len(getattr(td, 'name', '') or '')
                _td_chars += len(getattr(td, 'description', '') or '')
                params = getattr(td, 'parameters', None)
                if params:
                    _td_chars += len(json.dumps(params) if isinstance(params, dict) else str(params))
                # Tool defs are JSON schemas — use 2 chars/token
                total_tokens += int(_td_chars / 2.0)
        if token_multiplier and token_multiplier != 1.0:
            total_tokens = int(total_tokens * token_multiplier)
        return total_tokens


    @staticmethod
    def _cleanup_conversation_resources(conversation_id: str):
        """Cascade-delete all resources tied to a conversation: flows, tools, secrets."""
        from core.tool_registry import FlowManagerHandler, StoreSecretHandler
        try:
            FlowManagerHandler.cleanup_conversation(conversation_id)
        except Exception as e:
            logger.warning(f"[cleanup] flow cleanup failed: {e}")
        try:
            StoreSecretHandler.cleanup_conversation(conversation_id)
        except Exception as e:
            logger.warning(f"[cleanup] secret cleanup failed: {e}")
        try:
            from core.conversation_store import ConversationStore
            from core.tool_loader import cleanup_conversation_tools
            uid = ConversationStore.instance()._cid_user.get(conversation_id, "")
            if uid:
                cleanup_conversation_tools(uid, conversation_id)
        except Exception as e:
            logger.warning(f"[cleanup] dynamic tool cleanup failed: {e}")
        # Stop and undeploy conversation-scoped flow instances
        try:
            from core.deployment_registry import DeploymentRegistry
            from core.executor_registry import ExecutorRegistry
            dr = DeploymentRegistry.get_instance()
            er = ExecutorRegistry.get_instance()
            for iid, inst in list(dr.list_all().items()):
                if getattr(inst, "conversation_id", None) == conversation_id:
                    ex = er.get(iid)
                    if ex and ex.is_running:
                        ex.stop()
                    er.unregister(iid)
                    dr.undeploy(iid)
                    logger.info(f"[cleanup] Stopped conv-scoped flow {iid}")
        except Exception as e:
            logger.warning(f"[cleanup] conv-scoped flow cleanup failed: {e}")


    @staticmethod
    def _cleanup_conversation_files(messages: List[Dict[str, Any]]):
        """Delete files referenced in conversation messages (on conv delete)."""
        import re
        from core.file_store import FileStore
        store = FileStore.instance()
        file_ids = set()
        # Scan for /files/{file_id}/ patterns in message content
        pattern = re.compile(r'/files/([a-f0-9]{12})(?:/|$)')
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                for match in pattern.finditer(content):
                    file_ids.add(match.group(1))
        for fid in file_ids:
            store.delete(fid)
        if file_ids:
            logger.info(f"[cleanup] deleted {len(file_ids)} files from conversation")


    def _filter_tools_by_role(self, registry: ToolRegistry,
                              user_role: str) -> ToolRegistry:
        """Return a filtered registry containing only tools the user can access.

        Each tool handler may have an ``allowed_roles`` attribute (set by
        create_default_registry).  If not set, the tool is
        accessible to everyone.
        """
        filtered = ToolRegistry()
        for handler in registry.list_tools():
            allowed = getattr(handler, "allowed_roles", None)
            if allowed is None or user_role in allowed:
                filtered.register(handler)
        return filtered

    # ── Context rebuild ─────────────────────────────────────────────

    # ── Context compaction ────────────────────────────────────────────


    def _list_available_services(self, user_id: str, service_type: str,
                                 conversation_id: str = "") -> list:
        """List all available services of a type for the user."""
        _types = {
            "filesystem": ("relay", "googleDrive", "oneDrive"),
            "relay": ("relay",),
        }
        match_types = _types.get(service_type, (service_type,))

        result = []
        # Flow services
        services = getattr(self, '_services', {})
        for sid, svc in services.items():
            if getattr(svc, 'TYPE', '') in match_types:
                result.append({"id": sid, "type": getattr(svc, 'TYPE', ''), "root": "?"})
        # Registry services (conv > user > global)
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            for mtype in match_types:
                for sdef in reg.resolve_by_type(mtype, user_id=user_id,
                                                conv_id=conversation_id):
                    if not any(s["id"] == sdef.service_id for s in result):
                        result.append({
                            "id": sdef.service_id, "type": sdef.service_type,
                            "root": sdef.description or "?",
                        })
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return result


    def _find_filesystem_service(self, user_id: str = "", conversation_id: str = ""):
        """Find the first available filesystem service.

        Search order: flow services → registry (conv > user > global).
        """
        services = getattr(self, '_services', {})
        fs_types = ("relay", "filesystem", "googleDrive", "oneDrive")
        for svc in services.values():
            svc_type = getattr(svc, 'TYPE', '')
            if svc_type in fs_types:
                return svc
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            for fs_type in fs_types:
                for sdef in reg.resolve_by_type(
                        fs_type, user_id=user_id, conv_id=conversation_id):
                    svc = reg.resolve(
                        sdef.service_id, user_id=user_id, conv_id=conversation_id)
                    if svc:
                        return svc
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return None


    def _find_executor_service(self, user_id: str = ""):
        """Find the first available executor service (relay with exec support).

        Search order: flow services → registry (conv > user > global).
        """
        services = getattr(self, '_services', {})
        for svc in services.values():
            svc_type = getattr(svc, 'TYPE', '')
            if svc_type == "relay" and getattr(svc, 'is_connected', lambda: False)():
                return svc
        try:
            from core.service_registry import ServiceRegistry
            for sdef in ServiceRegistry.get_instance().resolve_by_type(
                    "relay", user_id=user_id):
                svc = ServiceRegistry.get_instance().resolve(
                    sdef.service_id, user_id=user_id)
                if svc and getattr(svc, 'is_connected', lambda: False)():
                    return svc
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return None


    def _wire_embed_fn(
        self, registry: ToolRegistry, client: LLMClient,
        user_id: str = "", conversation_id: str = "",
    ) -> None:
        """Wire embedding function into RememberHandler and SemanticRecallHandler.

        `${embedding_llm_service}` wins when it points to an embedding-capable
        LLM service. Otherwise PawFlow keeps the local sentence-transformer
        fallback so `semantic_recall` and embedded `remember` still work.
        Handlers MUST stay registered or the agent never learns the tool exists.
        """
        from core.tool_registry import RememberHandler, SemanticRecallHandler

        from core.embeddings import build_memory_embed_fn
        _embed = build_memory_embed_fn(
            user_id=user_id, conversation_id=conversation_id)

        def embed_fn(text: str) -> List[float]:
            return _embed(text)

        for h in registry.list_tools():
            if isinstance(h, RememberHandler):
                h.set_embed_fn(embed_fn)
            elif isinstance(h, SemanticRecallHandler):
                h.set_embed_fn(embed_fn)

        if user_id:
            import threading
            from core.memory_store import MemoryStore

            def _backfill():
                try:
                    n = MemoryStore.instance().ensure_embeddings(user_id, embed_fn)
                    if n:
                        logger.info(
                            "[memory] backfilled %d embedding(s) for user %s",
                            n, user_id,
                        )
                except Exception:
                    logger.exception("[memory] ensure_embeddings failed")

            threading.Thread(
                target=_backfill, name="memory-embed-backfill", daemon=True,
            ).start()

