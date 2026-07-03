"""AgentLoopTask mixin — AgentUtils methods

Auto-extracted from tasks/ai/agent_loop.py.
All methods access self (AgentLoopTask instance).
"""
import logging
from typing import List, Optional


from core.llm_client import (
    LLMClient,
)
from core.tool_registry import ToolRegistry
from tasks.ai._agent_media import _AgentMediaMixin
from tasks.ai._agent_msg_proc import _AgentMsgProcMixin

logger = logging.getLogger(__name__)


def _resolve_extra(store, conv_id: str, key: str, user_id: str = ""):
    """Read a conv extra and resolve ${...} expressions."""
    from core.expression import resolve_value
    return resolve_value(store.get_extra(conv_id, key), owner=user_id,
                         conversation_id=conv_id)


class AgentUtilsMixin(_AgentMediaMixin, _AgentMsgProcMixin):
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
            # CLI-backed providers resolve OAuth pools from the logical LLM
            # service id. Main-agent setup sets this later, but delegates get
            # their client directly from this resolver. Keep the id attached
            # here so delegate / flash_delegate share the same credential pool
            # as the parent instance instead of falling through to defaults.
            try:
                client._agent_service = service_id
                client._user_id = user_id or ""
                client._conversation_id = conversation_id or ""
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
                                       owner=user_id,
                                       conversation_id=conversation_id) or ""
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
                "llm_service", user_id, conversation_id)
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

    def _resolve_service_param(self, param_name: str, user_id: str = "",
                               conversation_id: str = "") -> str:
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
        return resolve_value(svc_id, owner=user_id,
                             conversation_id=conversation_id) or ""

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

    def _get_title_client(self, user_id: str = "",
                          conversation_id: str = ""):
        """Resolve a dedicated LLM service for conversation title generation.

        Same pattern as _get_summarizer_client. When configured, the agent
        loop generates a short title after the first done event.

        Returns (service_or_client, service_id) or (None, "").
        """
        svc_id = self._resolve_service_param("title_llm_service", user_id,
                                             conversation_id)
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

