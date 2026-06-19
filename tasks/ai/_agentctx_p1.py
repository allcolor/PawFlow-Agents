"""AgentContextMixin phase 1 (split from agent_context.py for <=800 lines)."""
import json
import logging
from typing import List


from core.llm_client import (
    LLMMessage, LLMToolDefinition,
)


logger = logging.getLogger(__name__)


class _PACPhase1Mixin:
    def _pac_p1(self, st):
        st.model = self.config.get("model", "")
        # LLM timeout is resolved by the LLM service/client. Missing or 0 means
        # no timeout; only an explicit positive value limits the provider call.

        # LLM service routing — all LLM access goes through services
        st._user_id_for_svc = st.flowfile.get_attribute("http.auth.principal") or ""
        if not st._user_id_for_svc:
            raise ValueError("BUG: missing http.auth.principal on flowfile — all requests require authentication")
        # Task-level llm_service is a fallback — per-agent config takes priority
        # (resolved later when active agent is known)
        st.task_llm_service = self._resolve_service_param(
            "llm_service", st._user_id_for_svc,
            st.flowfile.get_attribute("conversation_id") or "")
        st.client, st.resolved_svc = None, None
        if st.task_llm_service and not st.task_llm_service.startswith("${"):
            # Resolved service ID — try to connect
            st.client, st.resolved_svc = self._resolve_client(
                st.task_llm_service, st._user_id_for_svc,
                raise_on_missing=False, default_model=st.model,
            )
        elif not st.task_llm_service and self.config.get("api_key"):
            # Legacy inline config (api_key + provider) — no service ID
            st.client, st.resolved_svc = self._resolve_client(
                "", st._user_id_for_svc,
                raise_on_missing=False, default_model=st.model,
            )
        # _is_claude_code and _claude_has_session are set after agent resolution below

        st.registry = self.get_tool_registry()
        # Handlers are fully configured later (after conversation_id/user_id are known)

        # Wire embedding function for semantic memory handlers
        if st.client:
            self._wire_embed_fn(
                st.registry, st.client, user_id=st._user_id_for_svc,
                conversation_id="")

        # Set up SubAgentExecutor for delegate
        from core.agent_executor import SubAgentExecutor
        from core.tool_registry import SpawnAgentsHandler
        # Create a resolver closure for per-agent LLM service routing
        st._self = self
        def _client_resolver(svc_id, uid):
            return st._self._resolve_llm_service(svc_id, uid)
        # on_event callback for sub-agent visibility (SSE events)
        def _sub_on_event(event_type, data):
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(st.conversation_id, event_type, data)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        st.sub_executor = SubAgentExecutor(
            st.client, st.registry, max_workers=4,
            client_resolver=_client_resolver,
            on_event=_sub_on_event,
        )
        # Inject available agent instances into SpawnAgentsHandler for tool description
        st._uid_for_agents = st.flowfile.get_attribute("http.auth.principal") or ""
        try:
            from core.resource_store import ResourceStore
            from core.expression import resolve_value
            from core.conv_agent_config import (
                get_all_agent_configs as _gall2,
                get_agent_config as _gac2,
            )
            st._cid_for_agents = st.flowfile.get_attribute("conversation_id") or ""
            st._rs = ResourceStore.instance()
            st._agent_infos = []
            # List from conv_agents (instances), not repo definitions
            st._conv_cfgs = _gall2(st._cid_for_agents) if st._cid_for_agents else {}
            for st._inst_name, st._inst_raw in st._conv_cfgs.items():
                st._info = {"name": st._inst_name}
                # Get description from definition
                st._acfg = _gac2(st._cid_for_agents, st._inst_name)
                st._def_name = st._acfg["definition"]
                st._adef = st._rs.get_any("agent", st._def_name, st._uid_for_agents,
                                    conversation_id=st._cid_for_agents)
                if st._adef:
                    st._desc = (st._adef.get("description", "") or "").strip()[:120]
                    if not st._desc:
                        st._prompt = st._adef.get("prompt", "") or ""
                        st._desc = st._prompt.split("\n")[0].strip()[:120]
                    if st._desc:
                        st._info["description"] = st._desc
                    if st._def_name != st._inst_name:
                        st._info["definition"] = st._def_name
                st._info["llm_service"] = resolve_value(
                    st._acfg.get("llm_service", ""),
                    owner=st._uid_for_agents,
                    conversation_id=st._cid_for_agents) or ""
                if st._acfg.get("tools"):
                    st._info["tools"] = st._acfg["tools"]
                st._agent_infos.append(st._info)
        except Exception:
            st._agent_infos = []

        # Tool result size limit — configurable from LLM service
        st._svc_cfg = getattr(st.resolved_svc, 'config', {}) or {}
        st._tool_max = int(st._svc_cfg.get("tool_result_max_chars", 0) or
                        self.config.get("tool_result_max_chars", 0) or 50000)
        for st.h in st.registry.list_tools():
            if isinstance(st.h, SpawnAgentsHandler):
                st.h.set_spawn_deps(st.client, _client_resolver, _sub_on_event, registry=st.registry)
                if st._agent_infos:
                    st.h.set_available_agents(st._agent_infos)

            if hasattr(st.h, '_tool_result_max_chars'):
                st.h._tool_result_max_chars = st._tool_max

        st.user_role = st.flowfile.get_attribute("http.auth.roles") or ""
        if st.user_role:
            st.registry = self._filter_tools_by_role(st.registry, st.user_role)

        st.custom_tools_json = self.config.get("tools", "")
        if st.custom_tools_json:
            try:
                st.custom_tools = json.loads(st.custom_tools_json)
                st.tool_defs = [
                    LLMToolDefinition(
                        name=t["name"],
                        description=t.get("description", ""),
                        parameters=t.get("parameters", {"type": "object", "properties": {}}),
                    )
                    for t in st.custom_tools
                ]
            except (json.JSONDecodeError, KeyError) as e:
                raise ValueError(f"Invalid tools JSON: {e}")
        else:
            st.tool_defs = [
                LLMToolDefinition(
                    name=h.name, description=h.description, parameters=h.parameters_schema,
                )
                for h in st.registry.list_tools()
            ]

        st.system_prompt = self.config.get("system_prompt", "You are a helpful assistant.")
        # Date/time injected separately (NOT in system prompt — would break KV cache)
        from datetime import datetime
        st._datetime_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # Will be overridden below if a persona is selected (after conversation_id is known)
        st._base_system_prompt = st.system_prompt
        # Resolution order: agent config > LLM service config > defaults
        st._svc_cfg = getattr(st.resolved_svc, 'config', {}) or {}
        st._cfg = lambda key, default: self._pac_cfg(st, key, default)
        st.temperature = float(st._cfg("temperature", 0.7))
        st.max_tokens = int(self.config.get("max_context_size", 0))
        st.max_iterations = int(st._cfg("max_iterations", 1000))
        st.max_consecutive_tool_calls = int(st._cfg("max_consecutive_tool_calls", 100))
        st._resilience_style = st._cfg("resilience_style", "balanced")
        if st._resilience_style == "cautious":
            st.max_consecutive_tool_calls = min(st.max_consecutive_tool_calls, 10)
        elif st._resilience_style == "aggressive":
            st.max_consecutive_tool_calls = max(st.max_consecutive_tool_calls, 50)
        # thinking_budget: -1 = auto (10k for reasoning models, 0 for others)
        # 0 = disabled, >0 = explicit budget
        st.thinking_budget = int(st._cfg("thinking_budget", -1))

        st.use_conv_store = self.config.get("conversation_store", False)
        st.conv_ttl = int(self.config.get("conversation_ttl", 0))
        st.conv_attr = self.config.get("conversation_attribute", "")

        st.raw_body = st.flowfile.get_content().decode("utf-8", errors="replace")
        st.user_text = st.raw_body
        st.conversation_id = ""
        st.attachments = []  # list of {"type": "image"|"document", ...}
        st.body_json = None

        if st.raw_body.strip().startswith("{"):
            try:
                st.body_json = json.loads(st.raw_body)
                if isinstance(st.body_json, dict) and "message" in st.body_json:
                    st.user_text = st.body_json["message"]
                    st.conversation_id = st.body_json.get("conversation_id") or ""
                    st.attachments = st.body_json.get("attachments", [])
                    # Per-conversation TTL override from chat UI
                    if "ttl" in st.body_json:
                        st.conv_ttl = int(st.body_json["ttl"])
            except json.JSONDecodeError:
                pass

        # Reply-to context: prepend quoted message to user text
        st._reply_to = st.body_json.get("reply_to") if st.body_json else None
        if st._reply_to and isinstance(st._reply_to, dict):
            st._reply_agent = st._reply_to.get("agent", st._reply_to.get("role", ""))
            st._reply_preview = st._reply_to.get("text_preview", "")[:200]
            if st._reply_preview:
                st.user_text = (
                    f'[Replying to {st._reply_agent}: "{st._reply_preview}"]\n\n{st.user_text}'
                )

        # Sanitize user message content (strip invisible/malicious unicode)
        from core.sanitization import sanitize_unicode
        st.user_text = sanitize_unicode(st.user_text)

        # Telegram multimodal: inject image from attributes
        st.tg_image = st.flowfile.get_attribute("telegram.image_base64") or ""
        if st.tg_image:
            st.attachments.append({
                "filename": "telegram_photo.jpg",
                "mime_type": "image/jpeg",
                "data": st.tg_image,
            })

        # Cross-channel identity resolution (generic for all channels)
        st.CHANNEL_ATTRS = {
            "telegram": ("telegram.chat_id", "telegram.user_id"),
            "discord":  ("discord.channel_id", "discord.user_id"),
            "whatsapp": ("whatsapp.phone", "whatsapp.phone"),
            "slack":    ("slack.channel_id", "slack.user_id"),
        }

        st.channel = st.flowfile.get_attribute("agent.client_channel") or "web"
        st.channel_chat_id = ""
        st.channel_user_id = ""
        for st.ch, (st.chat_attr, st.user_attr) in st.CHANNEL_ATTRS.items():
            st.val = st.flowfile.get_attribute(st.chat_attr) or ""
            if st.val:
                st.channel = st.ch
                st.channel_chat_id = st.val
                st.channel_user_id = st.flowfile.get_attribute(st.user_attr) or ""
                break

        if st.channel_chat_id:
            if st.use_conv_store and st.channel_user_id:
                from core.identity_service import IdentityService
                st.ids = IdentityService.instance()
                st.resolved_user = st.ids.resolve_user(st.channel, st.channel_user_id)
                if st.resolved_user:
                    st.flowfile.set_attribute("http.auth.principal", st.resolved_user)
                    st.active = st.ids.get_active_conv(st.resolved_user, st.channel)
                    if st.active:
                        st.conversation_id = st.active
                    self._pending_channel_chat_id = st.channel_chat_id
                    self._pending_channel_name = st.channel
                else:
                    self._pending_channel_chat_id = st.channel_chat_id
                    self._pending_channel_name = st.channel
            else:
                self._pending_channel_chat_id = st.channel_chat_id
                self._pending_channel_name = st.channel

        # Every LLMMessage we're about to build requires a cid. Mint
        # one now so downstream LLMMessage constructors have a valid
        # value. Works for both persistent (use_conv_store=True) and
        # ephemeral (no store: attribute-only conv, unit tests) modes
        # — the invariant is "every message belongs to a conv", not
        # "every conv is persisted".
        if not st.conversation_id:
            from core.conversation_store import ConversationStore
            st.conversation_id = ConversationStore.instance().generate_id()

        st.messages: List[LLMMessage] = []

        # Determine active agent name early (needed for per-agent context loading)
        st._early_target = st.body_json.get("target_agent", "") if st.body_json else ""
        st._early_agent = ""
        if st.use_conv_store and st.conversation_id:
            try:
                from core.conversation_store import ConversationStore as _CSEarly
                st._early_res = _CSEarly.instance().get_extra(
                    st.conversation_id, "active_resources",
                ) or {}
                st._early_agent = st._early_target or st._early_res.get("agent", "")
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            # Load dynamic tools (global + user + conv) for this user/conv.
            from core.tool_loader import load_tools_into_registry
            st._parent_cid = st.conversation_id
            for st._sep in ("::task::", "::task_verify::", "::delegate::"):
                if st._sep in st._parent_cid:
                    st._parent_cid = st._parent_cid.split(st._sep, 1)[0]
                    break
            load_tools_into_registry(
                st.registry, st._user_id_for_svc, st._parent_cid)
        st._context_agent = st._early_agent

        # NOTE: conv_agents `max_depth` is the SUB-AGENT recursion depth only
        # (enforced in agent_executor via min(max_depth, MAX_GLOBAL_DEPTH)). It
        # must NOT touch `max_iterations` (the tool-use loop cap), which is the
        # LLM service's prerogative, resolved via _cfg("max_iterations", 1000)
        # above. Conflating the two silently throttled tool-using agents whose
        # max_depth was lowered to forbid delegation (e.g. help bots with
        # max_depth=1): the loop got 1 iteration and died after a single tool
        # call. Keep them separate.

        # ── Resolve active agent + LLM service EARLY ──
        # Needed before message loading to know if we should skip compact
        # (claude-code with existing session = append-only, no compact).
        st._active_agent_name = ""
        st._active_llm_service = st.task_llm_service
        if st.use_conv_store and st.conversation_id:
            try:
                from core.conversation_store import ConversationStore as _CSAgent
                st._ares = _CSAgent.instance().get_extra(
                    st.conversation_id, "active_resources",
                ) or {}
                st._ares = self._ensure_active_agent(
                    st.conversation_id, st._ares,
                    st.flowfile.get_attribute("http.auth.principal") or "",
                )
                st._active_agent_name = st._early_target or st._ares.get("agent", "")
                if st._active_agent_name:
                    st._rc, st._rsvc_id, st._rsvc = self._resolve_agent_client(
                        st._active_agent_name, st._user_id_for_svc, st.conversation_id)
                    if st._rc:
                        st.client = st._rc
                        st.resolved_svc = st._rsvc
                        st._active_llm_service = st._rsvc_id
                        st.model_name = ""  # Use service's default model
                        logger.info("Agent '%s' using LLM service '%s' (provider: %s)",
                                    st._active_agent_name, st._rsvc_id,
                                    getattr(st._rsvc, 'provider', '?') if st._rsvc else '?')
                    elif st._rsvc_id and st._rsvc_id != st.task_llm_service:
                        # Agent has a specific service configured but it can't be resolved
                        raise ValueError(
                            f"Agent '{st._active_agent_name}' LLM service '{st._rsvc_id}' "
                            f"not found. Check service configuration.")
                    else:
                        logger.info("Agent '%s' using task default LLM '%s'",
                                    st._active_agent_name, st.task_llm_service)
            except ValueError:
                raise  # Don't catch our own service-not-found error
            except Exception as e:
                logger.error("Error resolving agent LLM service: %s", e, exc_info=True)
        if not st._active_agent_name and st.use_conv_store and st.conversation_id:
            raise ValueError(
                "No agent configured for this conversation. "
                "Select an agent before sending a message.")

        # `_context_agent` is captured before `_ensure_active_agent()` has a
        # chance to normalize the conversation state. If that early value is
        # stale/empty, loading context with it falls back to shared context and
        # a cold CLI session loses the agent's private PawFlow history.
        if st._active_agent_name and st._context_agent != st._active_agent_name:
            if st._context_agent:
                logger.info(
                    "[context:%s] context agent corrected %s -> %s",
                    (st.conversation_id or "?")[:8], st._context_agent,
                    st._active_agent_name)
            st._context_agent = st._active_agent_name

        # Ensure we have a client (either from per-agent or task default)
        if st.client is None:
            raise ValueError(
                f"No LLM service resolved for agent '{st._active_agent_name or '?'}'. "
                f"Set llm_service in the conversation agent config.")

        # Re-wire memory embeddings now that the conversation id and the final
        # active agent client are known. This enables conv-scoped
        # `${embedding_llm_service}` overrides.
        self._wire_embed_fn(
            st.registry, st.client, user_id=st._user_id_for_svc,
            conversation_id=st.conversation_id)

        # Provider detection (now with the correct resolved service)
        st._provider_name = (
            getattr(st.resolved_svc, 'provider', "") or
            (getattr(st.resolved_svc, 'config', {}) or {}).get("provider", "") or
            getattr(st.client, 'provider', "") or ""
        )
        st._is_claude_code = (st._provider_name == "claude-code")
        st._is_claude_code_interactive = (st._provider_name == "claude-code-interactive")
        st._is_antigravity_interactive = (st._provider_name == "antigravity-interactive")
        st._is_gemini_acp = (st._provider_name == "gemini")
        st._is_codex_app_server = (st._provider_name == "codex-app-server")
        st._is_cli_provider = (
            st._is_claude_code or st._is_claude_code_interactive
            or st._is_antigravity_interactive or st._is_gemini_acp
            or st._is_codex_app_server)

        # CLI session detection (2 states):
        #   True  -> provider has prior CLI state; resume can send only delta
        #   False -> provider needs the full PawFlow initial context
        st._claude_has_session = False
        st._cli_has_session = False
        if st._is_cli_provider and st.conversation_id:
            try:
                from core.conversation_store import ConversationStore as _CSSession
                st._agent_key = st._active_agent_name or st._context_agent or 'default'
                st._store_session = _CSSession.instance()
                if st._is_claude_code:
                    st._session_key = f"claude_session:{st._agent_key}"
                    st._session_val = st._store_session.get_extra(st.conversation_id, st._session_key)
                    st._claude_has_session = bool(st._session_val)
                    st._cli_has_session = st._claude_has_session
                    if st._claude_has_session:
                        logger.info("[claude-code] active session (%s) — will resume",
                                    st._session_key)
                elif st._is_claude_code_interactive:
                    try:
                        from core.claude_code_interactive_pool import InteractiveClaudeCodePool
                        st._svc_id = getattr(st.resolved_svc, "service_id", "") or ""
                        st._state = InteractiveClaudeCodePool.instance().find_session(
                            st._user_id_for_svc, st.conversation_id, st._agent_key, st._svc_id)
                        st._cli_has_session = bool(st._state)
                        st._claude_has_session = st._cli_has_session
                    except Exception:
                        st._cli_has_session = False
                        st._claude_has_session = False
                elif st._is_antigravity_interactive:
                    try:
                        from core.antigravity_observer_pool import AntigravityObserverPool
                        st._svc_id = getattr(st.resolved_svc, "service_id", "") or ""
                        st._state = AntigravityObserverPool.instance().find_session(
                            st._user_id_for_svc, st.conversation_id, st._agent_key, st._svc_id)
                        st._cli_has_session = bool(st._state)
                    except Exception:
                        st._cli_has_session = False
                elif st._is_gemini_acp:
                    st._session_key = f"gemini_acp_session:{st._agent_key}"
                    st._session_ver_key = f"gemini_acp_session_version:{st._agent_key}"
                    st._session_val = st._store_session.get_extra(st.conversation_id, st._session_key)
                    st._session_ver = st._store_session.get_extra(st.conversation_id, st._session_ver_key)
                    st._cli_has_session = bool(st._session_val) and st._session_ver == "2"
                elif st._is_codex_app_server:
                    st._session_key = f"codex_app_server_thread:{st._agent_key}"
                    st._session_val = st._store_session.get_extra(st.conversation_id, st._session_key)
                    st._cli_has_session = bool(st._session_val)
                    if st._cli_has_session:
                        st._session_valid = False
                        st._svc_id = getattr(st.resolved_svc, "service_id", "") or ""
                        try:
                            from core.codex_live_registry import CodexLiveRegistry
                            st._pool_key = f"codex_app_pool_idx:{st._agent_key}"
                            try:
                                st._pool_idx = int(st._store_session.get_extra(
                                    st.conversation_id, st._pool_key) or -1)
                            except Exception:
                                st._pool_idx = -1
                            st._live_reg = CodexLiveRegistry.instance()
                            st._live = st._live_reg.get((
                                st._user_id_for_svc, st.conversation_id, st._agent_key,
                                st._svc_id, st._pool_idx))
                            if st._live is None:
                                st._compat = st._live_reg.get_compatible(
                                    st._user_id_for_svc, st.conversation_id,
                                    st._agent_key, st._svc_id)
                                st._live = st._compat[1] if st._compat else None
                            # A live app-server process already owns the thread
                            # state. A merely-alive container does not: the new
                            # app-server must resume from a rollout jsonl, so we
                            # still validate that path below before skipping the
                            # PawFlow context load.
                            st._session_valid = bool(
                                st._live and st._live.is_process_alive())
                        except Exception:
                            logging.getLogger(__name__).debug(
                                "Ignored codex live-session validation exception",
                                exc_info=True)
                        if not st._session_valid:
                            try:
                                import os as _os
                                from core.llm_providers.codex_session import _get_sessions_base
                                from core.llm_providers.codex_app_server import LLMCodexAppServerMixin
                                st._uid = st._user_id_for_svc or st._store_session.get_user_id(st.conversation_id) or "default"
                                st._workdir = _os.path.join(
                                    _get_sessions_base(), st._uid,
                                    st.conversation_id.replace(":", "_"), st._agent_key)
                                st._session_valid = bool(
                                    LLMCodexAppServerMixin._codex_app_rollout_path(
                                        st._workdir, str(st._session_val)))
                            except Exception:
                                logging.getLogger(__name__).debug(
                                    "Ignored codex rollout validation exception",
                                    exc_info=True)
                        if not st._session_valid:
                            st._store_session.set_extra(st.conversation_id, st._session_key, "")
                            st._store_session.set_extra(
                                st.conversation_id,
                                f"codex_app_pool_idx:{st._agent_key}", "")
                            st._cli_has_session = False
                            logger.warning(
                                "[context:%s] stale codex app-server thread %s for %s — loading PawFlow context",
                                st.conversation_id[:8], str(st._session_val)[:12],
                                st._agent_key)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
