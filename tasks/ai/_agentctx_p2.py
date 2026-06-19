"""AgentContextMixin phase 2 (split from agent_context.py for <=800 lines)."""
import json
import logging


from core.llm_client import (
    LLMMessage, LLMToolDefinition,
)

from tasks.ai._agentctx_base import _find_agent_md

logger = logging.getLogger(__name__)


class _PACPhase2Mixin:
    def _pac_p2(self, st):
        # Resolve max_context early (needed for compact-if-not-fit decision)
        st._svc_cfg_early = (getattr(st.resolved_svc, 'config', {}) or {})
        st._max_ctx = int(st._svc_cfg_early.get("max_context_size", 0) or 0) or 200000
        st._max_budget = float(st._svc_cfg_early.get("max_budget_usd", 0) or 0)

        st._context_diverged = False
        st._uses_pawflow_initial = False
        st._cold_cli_initial_source = ""
        if st.preloaded_messages is not None:
            # Caller provided messages (e.g. poller task sub-conversation)
            try:
                st._preloaded_cid = st.preloaded_conversation_id or st.conversation_id
                st.messages = self._deserialize_messages(
                    st.preloaded_messages, conversation_id=st._preloaded_cid)
                # display_only messages already filtered by _deserialize_messages
                logger.info(f"[context:{(st.conversation_id or '?')[:8]}] using preloaded messages: "
                            f"{len(st.messages)} messages")
            except (KeyError, TypeError) as e:
                logger.error(f"[context] preloaded messages deser failed: {e}")
            # Auto-compact on preloaded messages (skip for stateful CLI
            # providers with active sessions; their resume path sends only the
            # live delta, not the full PawFlow context).
            if st.messages and not st._cli_has_session:
                st._uid_pl = st.flowfile.get_attribute("http.auth.principal") or ""
                st.messages = self._auto_compact_messages(
                    st.messages, st.preloaded_conversation_id or st.conversation_id or "",
                    st._context_agent, st._uid_pl, max_context=st._max_ctx,
                    independent_context=st.independent_context)
        elif st.use_conv_store and st.conversation_id:
            if st._cli_has_session:
                # Stateful CLI has an active session — it already has the
                # context. User message is appended later; provider-only
                # prompt state is reconstructed per call and must not enter
                # stored context.
                st.messages = []
                st.base_message_count = 0
                st._context_diverged = True  # skip compact
                logger.info(
                    f"[context:{st.conversation_id[:8]}] CLI session active — skipping context load")
            else:
                from core.conversation_store import ConversationStore
                st.store = ConversationStore.instance()

                def _load_pawflow_initial_context():
                    """Build the canonical PawFlow start context for a cold CLI session.

                    The source is the personalized shared context. If it is too
                    large, the normal compactor below is responsible for using
                    the shared pyramid/buckets and preserving the recent tail.
                    Do not pre-collapse to a pyramid header here: small shared
                    contexts must be injected in full.
                    """
                    existing = st.store.load_shared_for_agent(
                        st.conversation_id, st._context_agent)
                    if not existing:
                        return None, ""
                    try:
                        shared_msgs = self._deserialize_messages(
                            existing, conversation_id=st.conversation_id)
                    except (KeyError, TypeError) as deser_err:
                        logger.error(
                            f"[context:{st.conversation_id[:8]}] shared load failed: {deser_err}")
                        return None, ""
                    return shared_msgs, "shared"

                st.context_data = st.store.load_agent_context(st.conversation_id, st._context_agent)
                st._uses_pawflow_initial = False
                if st.context_data is not None:
                    # Agent context exists: use it as the PawFlow agent
                    # context. For CLI providers, a valid session means the
                    # provider resume path sends only the delta; no valid
                    # session means the new CLI process receives this full
                    # PawFlow agent context.
                    try:
                        st.messages = self._deserialize_messages(
                            st.context_data, conversation_id=st.conversation_id)
                        st._context_diverged = True
                        logger.info(f"[context:{st.conversation_id[:8]}] loaded diverged context: "
                                    f"{len(st.messages)} messages")
                        # Cold CLI start: the loaded agent context is the
                        # full PawFlow transcript. Run the real compactor on
                        # it so an oversized context.jsonl is rewritten
                        # compacted. The CLI resume path only ever sends the
                        # live delta, so without this the stored context
                        # never crosses the in-loop compaction trigger and
                        # grows unbounded.
                        st._uid_dv = st.flowfile.get_attribute("http.auth.principal") or ""
                        st.messages = self._auto_compact_messages(
                            st.messages, st.conversation_id, st._context_agent, st._uid_dv,
                            max_context=st._max_ctx)
                    except (KeyError, TypeError) as deser_err:
                        logger.error(f"[context:{st.conversation_id[:8]}] context load failed: {deser_err}")
                else:
                    # No established agent context: build it from PawFlow
                    # shared context. _auto_compact_messages decides whether
                    # buckets are needed to fit the provider context window.
                    st.messages, st._cold_cli_initial_source = _load_pawflow_initial_context()
                    if st.messages:
                        st._uses_pawflow_initial = True
                        logger.info(
                            f"[context:{st.conversation_id[:8]}] loaded PawFlow initial "
                            f"{st._cold_cli_initial_source or 'shared'} context: {len(st.messages)} messages")
                        st._uid2 = st.flowfile.get_attribute("http.auth.principal") or ""
                        st.messages = self._auto_compact_messages(
                            st.messages, st.conversation_id, st._context_agent, st._uid2,
                            max_context=st._max_ctx)
                    else:
                        logger.warning(f"[context:{st.conversation_id[:8]}] store.load() returned None — "
                                       f"starting fresh conversation")

        elif st.conv_attr:
            st.existing = st.flowfile.get_attribute(st.conv_attr)
            if st.existing:
                try:
                    st._raw = json.loads(st.existing)
                    # Ingress from an external flow attribute: the caller
                    # doesn't know about seq/ts, so we stamp each entry
                    # here before deserialization. This is the system
                    # boundary where "outside message" becomes "PawFlow
                    # message" with the invariant (ts+seq+msg_id set).
                    from core.llm_client import stamp_message as _stamp
                    for st._e in st._raw:
                        if isinstance(st._e, dict):
                            _stamp(st._e, st.conversation_id)
                    st.messages = self._deserialize_messages(st._raw, conversation_id=st.conversation_id)
                except (json.JSONDecodeError, KeyError):
                    pass

        if st.messages and st.messages[0].role == "system":
            # Persisted agent context must contain only compact summary +
            # current messages. System/memory/skills are provider-only and
            # rebuilt below on every call.
            st.messages = st.messages[1:]
        if not st.messages:
            st.base_message_count = 0
        else:
            # Loaded from store — these messages are already persisted
            st.base_message_count = len(st.messages)

        # Inject {agent_name}.md project instructions if available
        # Try instance name first, then definition name as fallback
        st._agent_md_content = ""
        if st._active_agent_name and st.conversation_id and not st._cli_has_session:
            st._agent_md = _find_agent_md(st._active_agent_name, st._user_id_for_svc,
                                       st.conversation_id)
            if not st._agent_md:
                from core.conv_agent_config import get_definition_name as _gdn
                st._def_n = _gdn(st.conversation_id, st._active_agent_name)
                if st._def_n != st._active_agent_name:
                    st._agent_md = _find_agent_md(st._def_n, st._user_id_for_svc,
                                               st.conversation_id)
            if st._agent_md:
                st._agent_md_content = st._agent_md[1]
                st._agent_md_content = (
                    f"\n\n## Project instructions from {st._agent_md[0]}\n\n"
                    f"{st._agent_md[1]}"
                )

        # cid was generated early (above) so any downstream
        # LLMMessage already has it. Defensive check only.
        if st.use_conv_store and not st.conversation_id:
            raise ValueError(
                "BUG: no conversation_id after generate_id() — this should never happen"
            )

        # NOTE: no auto-link of relays here. The user decides what to link
        # via /relay link or the [+] button in the resource panel.
        # Server relays spawned via /workspace auto-link in server_relay_manager.

        # target_agent: temporary agent override for /agent msg (not persisted)
        st._target_agent = st.body_json.get("target_agent", "") if st.body_json else ""
        if st._target_agent and st.conversation_id:
            st._target_agent = self._resolve_agent_name(st._target_agent, st.conversation_id)

        # Apply pending_agent from the first message (agent selected before conversation existed)
        st._pending_agent = st.body_json.get("pending_agent", "") if st.body_json else ""
        if st._pending_agent and st.use_conv_store and st.conversation_id:
            try:
                from core.conversation_store import ConversationStore
                st.store = ConversationStore.instance()
                # Ensure conversation entry exists (save minimal data)
                if not st.store.load(st.conversation_id):
                    st._uid = st.flowfile.get_attribute("http.auth.principal") or ""
                    st.store.save(st.conversation_id, [], user_id=st._uid)
                st.active = st.store.get_extra(st.conversation_id, "active_resources") or {}
                st.active["agent"] = st._pending_agent
                st.store.set_extra(st.conversation_id, "active_resources", st.active)
                logger.info("Applied pending agent '%s' on new conversation %s",
                            st._pending_agent, st.conversation_id[:8])
            except Exception as e:
                logger.warning("Failed to apply pending agent '%s': %s", st._pending_agent, e)

        # Store channel chat_id for cross-channel notifications
        if st.use_conv_store and st.conversation_id and getattr(self, '_pending_channel_chat_id', ''):
            try:
                from core.conversation_store import ConversationStore
                st.ch_name = getattr(self, '_pending_channel_name', 'telegram')
                ConversationStore.instance().set_extra(
                    st.conversation_id, f"{st.ch_name}_chat_id",
                    self._pending_channel_chat_id,
                )
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            self._pending_channel_chat_id = ""
            self._pending_channel_name = ""

        # Check for selected agent persona and assigned skills
        st._selected_agent_def = None
        st.selected = st._target_agent or st._active_agent_name or st._context_agent or ""
        if st.use_conv_store and st.conversation_id and not st._cli_has_session:
            try:
                from core.conversation_store import ConversationStore
                from core.resource_store import ResourceStore
                st.cstore = ConversationStore.instance()
                st.rs = ResourceStore.instance()
                st.active_res = st.cstore.get_extra(st.conversation_id, "active_resources") or {}
                st._uid = st.flowfile.get_attribute("http.auth.principal") or ""
                st.active_res = self._ensure_active_agent(st.conversation_id, st.active_res, st._uid)

                # Active agent overrides system prompt (target_agent takes priority)
                st.selected = st._target_agent or st.active_res.get("agent", "")
                st.agent_def = None
                if st.selected:
                    # Resolve definition name from conv_agents config
                    from core.conv_agent_config import (
                        get_agent_config as _gac_sel,
                        flatten_agent_params,
                    )
                    st._inst_cfg = _gac_sel(st.conversation_id, st.selected)
                    st._def_name = st._inst_cfg["definition"]
                    st.agent_def = st.rs.get_any("agent", st._def_name, st._uid,
                                           conversation_id=st.conversation_id)
                    if not st.agent_def and st._target_agent:
                        # /agent msg <name> with unknown agent — reject early
                        raise ValueError(f"Agent '{st._target_agent}' not found")
                    if st.agent_def:
                        st._selected_agent_def = st.agent_def
                        # Resolve expressions in prompt with instance params
                        st._raw_prompt = st.agent_def["prompt"]
                        st._inst_params = st._inst_cfg.get("params") or {}
                        if st._inst_params:
                            from core.expression import resolve_expression
                            st._flat = flatten_agent_params(st.selected, st._inst_params)
                            st.system_prompt = resolve_expression(
                                st._raw_prompt, parameters=st._flat,
                                owner=st._uid,
                                conversation_id=st.conversation_id)
                        else:
                            st.system_prompt = st._raw_prompt
                        # Identity is injected later (with nickname awareness)

                        # Date/time NOT in system prompt (KV cache killer)
                        # List other agent instances in this conversation
                        from core.conv_agent_config import get_all_agent_configs as _gall
                        st._conv_members = list(_gall(st.conversation_id).keys())
                        st.others = [n for n in st._conv_members if n != st.selected]
                        if st.others:
                            st.system_prompt += (
                                f"\n\nOther agents available: "
                                f"{', '.join(st.others)}. Use delegate or "
                                f"manage_resource to work with them."
                            )

                if st._agent_md_content:
                    st.system_prompt += st._agent_md_content

                # Advertise assigned skills without loading their full prompts.
                # Active CLI sessions receive assignment deltas via context;
                # cold/rebuilt contexts get this lightweight manifest.
                st._agent_skills = (st.agent_def or {}).get("assigned_skills") or []
                if st._agent_skills:
                    from core.skill_resolver import inject_available_skills_into_prompt
                    st.system_prompt = inject_available_skills_into_prompt(
                        st.system_prompt, st._agent_skills, st._uid,
                        conversation_id=st.conversation_id)
                # Auto-load tools from all MCP servers accessible in scope
                # (global + user + conversation). No linking needed: any MCP
                # visible via rs.list_all is automatically active in this conv.
                st._all_mcps = st.rs.list_all("mcp", st._uid, conversation_id=st.conversation_id) or []
                st.active_mcps = [m.get("name", "") for m in st._all_mcps if m.get("name")]
                if st.active_mcps:
                    for st.mcp_name in st.active_mcps:
                        try:
                            from core.tool_mcp_filters import is_enabled
                            if not is_enabled(st.conversation_id, st.mcp_name, st.selected, kind="mcps"):
                                continue
                            st.raw_def = st.rs.get_any("mcp", st.mcp_name, st._uid,
                                                 conversation_id=st.conversation_id)
                            if not st.raw_def:
                                continue
                            # Resolve ALL expressions at point of use
                            from core.expression import resolve_value
                            st.mcp_def = resolve_value(st.raw_def, owner=st._uid,
                                                     conversation_id=st.conversation_id)
                            st.transport = st.mcp_def.get("transport", "http")
                            st.via = st.mcp_def.get("via", "") or (
                                "relay" if st.transport == "stdio" else "direct")
                            st.auth = st.mcp_def.get("auth", {})
                            if isinstance(st.auth, str):
                                st.auth = {"Authorization": st.auth}

                            st.disc_tools = []
                            st.relay_svc = None

                            if st.via == "relay":
                                # Resolve relay service (already expression-resolved)
                                st._rsid = st.mcp_def.get("relay_service", "")
                                if st._rsid:
                                    st.relay_svc = self._resolve_media_service_by_id(st._rsid, st._uid)
                                    if not st.relay_svc:
                                        # Try filesystem service registries
                                        try:
                                            from core.service_registry import ServiceRegistry
                                            st.relay_svc = ServiceRegistry.get_instance().resolve(
                                                st._rsid, user_id=st._uid, conv_id=st.conversation_id)
                                        except Exception:
                                            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                                if not st.relay_svc:
                                    st.relay_svc = self._find_filesystem_service(st._uid)
                                if not st.relay_svc:
                                    logger.warning(f"[mcp] No relay for '{st.mcp_name}'")
                                    continue
                                # Start stdio server on relay
                                if st.transport == "stdio":
                                    try:
                                        st.relay_svc._request("mcp_start", ".", **{
                                            "server_id": st.mcp_name,
                                            "command": st.mcp_def.get("command", ""),
                                            "args": st.mcp_def.get("args", []),
                                            "env": st.mcp_def.get("env", {}),
                                            "local": bool(st.mcp_def.get("local")),
                                        })
                                    except Exception as e:
                                        if "already_running" not in str(e):
                                            logger.error(f"[mcp] Start failed '{st.mcp_name}': {e}")
                                            continue
                                # Discover tools via relay
                                try:
                                    st.disc = st.relay_svc._request("mcp_discover", ".",
                                                              server_id=st.mcp_name,
                                                              local=bool(st.mcp_def.get("local")))
                                    st.disc_tools = (st.disc.get("tools", [])
                                                  if isinstance(st.disc, dict) else [])
                                except Exception as e:
                                    logger.error(f"[mcp] Discovery failed '{st.mcp_name}': {e}")
                            else:
                                # Direct HTTP
                                st.url = st.mcp_def.get("url", "")
                                if not st.url:
                                    continue
                                try:
                                    from core.relay_proxy_url import maybe_transform_relay_proxy_url
                                    st.url = maybe_transform_relay_proxy_url(st.url, user_id=st._uid) or st.url
                                except Exception:
                                    logger.debug("mcp relay-proxy URL transform failed", exc_info=True)
                                from core.tool_registry import discover_mcp_tools
                                st.disc_tools = discover_mcp_tools(
                                    st.url, headers=st.auth, timeout=10)

                            # Register discovered tools
                            from core.handlers.agent_tools import MCPToolHandler
                            for st.mt in st.disc_tools:
                                st.h = MCPToolHandler(
                                    tool_name=st.mt["name"],
                                    tool_description=st.mt.get("description", ""),
                                    tool_parameters=st.mt.get("inputSchema", {
                                        "type": "object", "properties": {}}),
                                    server_url=st.url if st.via != "relay" else st.mcp_def.get("url", ""),
                                    mcp_tool_name=st.mt["name"],
                                    headers=st.auth,
                                    transport=st.transport if st.via == "relay" else "http",
                                    server_id=st.mcp_name,
                                    relay_service=st.relay_svc,
                                    local=bool(st.mcp_def.get("local")),
                                )
                                st.registry.register(st.h)
                            if st.disc_tools:
                                logger.info(f"[mcp] Loaded {len(st.disc_tools)} tools "
                                            f"from '{st.mcp_name}' ({st.via}/{st.transport})")
                        except Exception as _mcp_err:
                            logger.warning(f"[mcp] Failed to load '{st.mcp_name}': {_mcp_err}")

            except Exception as e:
                logger.error("Error loading agent persona/skills: %s", e, exc_info=True)

        # Rebuild tool_defs from registry (now includes MCP + dynamic tools)
        # then apply agent's allowlist/denylist filter.
        # Skip rebuild if custom tools were provided via JSON config.
        if not st.custom_tools_json and not st._cli_has_session:
            from core.tool_mcp_filters import is_tool_enabled as _tool_enabled
            st.tool_defs = [
                LLMToolDefinition(
                    name=h.name, description=h.description,
                    parameters=h.parameters_schema,
                )
                for h in st.registry.list_tools()
                if not st.conversation_id or _tool_enabled(
                    st.conversation_id, h.name, st.selected,
                    getattr(h, "_origin", "builtin"),
                    getattr(h, "_origin_scope", ""))
            ]
        if st._selected_agent_def and st.conversation_id and not st._cli_has_session:
            from core.conv_agent_config import get_agent_config as _gac
            # Use the instance name (selected), not the definition name
            st._agent_tools_cfg = _gac(st.conversation_id, st.selected
                                     ).get("tools") or []
            if st._agent_tools_cfg and isinstance(st._agent_tools_cfg, list):
                st._allow = {t for t in st._agent_tools_cfg if not str(t).startswith("!")}
                st._deny  = {t[1:] for t in st._agent_tools_cfg if str(t).startswith("!")}
                if st._allow:
                    st.tool_defs = [td for td in st.tool_defs if td.name in st._allow]
                elif st._deny:
                    st.tool_defs = [td for td in st.tool_defs if td.name not in st._deny]
        if st.conversation_id and not st._cli_has_session:
            try:
                from core.tool_mcp_filters import disabled_names
                st._disabled_tools = disabled_names(
                    st.conversation_id, st.selected, kind="tools")
                if st._disabled_tools:
                    st.tool_defs = [td for td in st.tool_defs if td.name not in st._disabled_tools]
            except Exception:
                logger.debug("tool availability filter failed", exc_info=True)

        # NOTE: the fully-built system_prompt is stored separately below as
        # provider-only state. It must not be inserted into messages, because
        # messages are the persisted agent context.

        st.model_name = self.config.get("model", "")
        st.user_id = st.flowfile.get_attribute("http.auth.principal")

        # Check for cancel checkpoint — inject resume context if present
        if st.use_conv_store and st.conversation_id:
            try:
                from core.conversation_store import ConversationStore
                st._cp_store = ConversationStore.instance()
                st._cp_key = f"cancel_checkpoint:{st._early_agent or 'assistant'}"
                st._checkpoint = st._cp_store.get_extra(st.conversation_id, st._cp_key)
                if st._checkpoint and isinstance(st._checkpoint, dict):
                    st._cp_tools = st._checkpoint.get("tools_called", [])
                    st._cp_partial = st._checkpoint.get("partial_response", "")
                    st._resume_parts = ["[System: Resuming after cancellation."]
                    if st._cp_tools:
                        st._resume_parts.append(
                            f"Tools used before cancel: {', '.join(st._cp_tools[-10:])}.")
                    if st._cp_partial:
                        st._resume_parts.append(
                            f"Partial progress: {st._cp_partial}")
                    st._resume_parts.append(
                        "Continue from where you left off. "
                        "Do NOT restart work that was already done.]")
                    st.messages.append(LLMMessage(
                        role="user", content=" ".join(st._resume_parts),
                        conversation_id=st.conversation_id))
                    # Clear checkpoint after injection
                    st._cp_store.set_extra(st.conversation_id, st._cp_key, None)
                    logger.info(f"[context:{st.conversation_id[:8]}] injected resume from cancel checkpoint")
            except Exception as _cp_err:
                logger.warning(f"[context] cancel checkpoint check failed: {_cp_err}")
