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
from core.tool_registry import ToolRegistry, create_default_registry, load_agent_tools

logger = logging.getLogger(__name__)



def _resolve_extra(store, conv_id: str, key: str, user_id: str = ""):
    """Read a conv extra and resolve ${...} expressions."""
    from core.expression import resolve_value
    return resolve_value(store.get_extra(conv_id, key), owner=user_id)


def _resolve_extra_dict(store, conv_id: str, key: str, user_id: str = ""):
    """Read a conv extra dict and resolve ${...} expressions in all values."""
    from core.expression import resolve_value
    raw = store.get_extra(conv_id, key) or {}
    return resolve_value(raw, owner=user_id)


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
            client = LLMClient(
                provider=self.config.get("provider", "openai"),
                api_key=self.config["api_key"],
                base_url=self.config.get("base_url", ""),
                default_model=default_model,
                timeout=int(self.config.get("timeout", 120)),
            )
            svc = None
        if not client and raise_on_missing:
            raise ValueError(
                f"LLM service '{service_id}' not found. "
                f"Define it in global services or set 'llm.default.service' "
                f"in config/global_parameters.json."
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


    def _resolve_llm_service(self, service_id: str, user_id: str):
        """Resolve an LLM service by ID. Returns (LLMClient, service) or (None, None).

        Resolution order: flow services → UserServiceRegistry → GlobalServiceRegistry.
        """
        if not service_id:
            return None, None
        # 1. Flow-level services (defined in flow JSON)
        if self._services:
            svc = self._services.get(service_id)
            if svc and hasattr(svc, 'get_client'):
                return svc.get_client(), svc
        # 2. User-scoped services
        try:
            from gui.services.user_service_registry import UserServiceRegistry
            svc = UserServiceRegistry.get_instance().get_live_instance(user_id, service_id)
            if svc and hasattr(svc, 'get_client'):
                return svc.get_client(), svc
        except Exception as e:
            logger.debug("User service '%s' for '%s': %s", service_id, user_id, e)
        # 3. Global services
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            svc = GlobalServiceRegistry.get_instance().get_live_instance(service_id)
            if svc and hasattr(svc, 'get_client'):
                return svc.get_client(), svc
        except Exception as e:
            logger.warning("Global service '%s' resolution failed: %s", service_id, e)
        return None, None


    def _resolve_agent_client(self, agent_name: str, user_id: str,
                              conversation_id: str = ""):
        """Resolve an agent's LLM client by following the override chain.

        Resolution order:
        1. Per-conversation LLM override (agent_llm_overrides extra)
        2. Agent definition's llm_service (with expression resolution)
        3. Task-level llm_service default

        Returns (client, service_id, resolved_svc) or (None, "", None).
        """
        svc_id = ""
        # 1. Per-conversation override (values may be expressions)
        if conversation_id:
            try:
                from core.conversation_store import ConversationStore
                overrides = _resolve_extra_dict(
                    ConversationStore.instance(), conversation_id,
                    "agent_llm_overrides", user_id)
                svc_id = overrides.get(agent_name, "")
            except Exception:
                pass
        # 2. Agent definition
        if not svc_id and agent_name:
            try:
                from core.resource_store import ResourceStore
                from core.expression import resolve_value
                adef = ResourceStore.instance().get_any(
                    "agent", agent_name, user_id,
                    conversation_id=conversation_id)
                if adef:
                    svc_id = resolve_value(adef.get("llm_service", ""),
                                           owner=user_id) or ""
            except Exception:
                pass
        # 3. Task default
        if not svc_id:
            svc_id = self._resolve_service_param("llm_service", user_id) or "default"
        client, svc = self._resolve_llm_service(svc_id, user_id)
        return client, svc_id, svc

    def _resolve_service_param(self, param_name: str, user_id: str = "") -> str:
        """Resolve a service parameter that may contain ${...} expressions.

        If not in task config, falls back to schema default (lazy eval).
        Returns the resolved service ID string, or "" if not configured.
        """
        svc_id = self.config.get(param_name, "")
        # If not in config, try schema default (e.g. "${conv.summarizer_service}")
        if not svc_id:
            schema = {}
            if hasattr(self, 'get_parameter_schema'):
                schema = self.get_parameter_schema() or {}
            default = (schema.get(param_name) or {}).get("default", "")
            if default:
                svc_id = default
        from core.expression import resolve_value
        return resolve_value(svc_id, owner=user_id) or ""

    def _get_summarizer_client(self, user_id: str = ""):
        """Resolve a dedicated summarizer LLM service for compaction/summary.

        Returns (service_or_client, max_context_tokens, service_id) or (None, 0, "").
        The returned object has .complete() — prefer service (has _apply_defaults).
        """
        svc_id = self._resolve_service_param("summarizer_service", user_id)
        if not svc_id:
            return None, 0, ""
        logger.debug(f"[summarizer] resolved to '{svc_id}'")
        client, svc = self._resolve_llm_service(svc_id, user_id)
        if svc and hasattr(svc, 'complete'):
            # Return the SERVICE (has _apply_defaults for temperature etc.)
            ctx_max = int((getattr(svc, 'config', {}) or {}).get("max_context_size", 0))
            return svc, ctx_max, svc_id
        if client:
            ctx_max = 0
            return client, ctx_max, svc_id
        return None, 0, ""

    # ── Media service discovery (generic for image/video) ───────────


    @staticmethod
    def _get_media_types(base_class) -> set:
        """Get all registered service_type strings that inherit from base_class."""
        try:
            from tasks import _register_all_services
            _register_all_services()
        except Exception:
            pass
        from core import ServiceFactory
        types = set()
        for stype, sclass in ServiceFactory._services.items():
            try:
                if issubclass(sclass, base_class):
                    types.add(stype)
            except TypeError:
                pass
        return types


    def _discover_media_services(self, user_id: str, base_class) -> list:
        """Discover all deployed and enabled services of a given type.

        Uses the service definitions from global + user registries.
        Matches service_type against known types for the base_class.
        Rechecked every time (services can be added at runtime).

        Returns list of (service_id, service_type, scope) tuples.
        """
        valid_types = self._get_media_types(base_class)

        results = []
        seen = set()
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            for sid, sdef in greg.get_all_definitions().items():
                if not getattr(sdef, "enabled", True):
                    continue
                stype = getattr(sdef, "service_type", "") or ""
                if stype in valid_types:
                    results.append((sid, stype, "global"))
                    seen.add(sid)
        except Exception as e:
            logger.error("Global service discovery failed: %s", e, exc_info=True)
        if user_id:
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                ureg = UserServiceRegistry.get_instance()
                for sid, sdef in ureg.get_all_for_user(user_id).items():
                    if sid in seen:
                        continue
                    if not getattr(sdef, "enabled", True):
                        continue
                    stype = getattr(sdef, "service_type", "") or ""
                    if stype in valid_types:
                        results.append((sid, stype, "user"))
            except Exception as e:
                logger.error("User service discovery failed: %s", e, exc_info=True)
        return results


    @staticmethod
    def _resolve_media_service_by_id(service_id: str, user_id: str):
        """Resolve a media service by ID. Returns instance or None."""
        if not service_id:
            return None
        try:
            from gui.services.user_service_registry import UserServiceRegistry
            svc = UserServiceRegistry.get_instance().get_live_instance(user_id, service_id)
            if svc and hasattr(svc, 'generate'):
                return svc
        except Exception:
            pass
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            svc = GlobalServiceRegistry.get_instance().get_live_instance(service_id)
            if svc and hasattr(svc, 'generate'):
                return svc
        except Exception:
            pass
        return None


    def _make_media_resolver(self, user_id: str, conversation_id: str,
                             agent_name: str, base_class,
                             extra_key: str, label: str, command: str):
        """Build a generic resolver closure for any media service type."""
        _self = self
        def resolver():
            available = _self._discover_media_services(user_id, base_class)
            if not available:
                return None, f"No {label} service deployed"
            if len(available) == 1:
                svc = _self._resolve_media_service_by_id(available[0][0], user_id)
                if svc:
                    return svc, None
                return None, f"{label.title()} service '{available[0][0]}' failed to connect"
            # Multiple → check per-agent preference, then wildcard
            if conversation_id:
                from core.conversation_store import ConversationStore
                prefs = _resolve_extra_dict(
                    ConversationStore.instance(), conversation_id,
                    extra_key, user_id)
                preferred = prefs.get(agent_name or "agent") or prefs.get("*")
                if preferred:
                    svc = _self._resolve_media_service_by_id(preferred, user_id)
                    if svc:
                        return svc, None
            names = [s[0] for s in available]
            return None, (
                f"Multiple {label} services available: {', '.join(names)}. "
                f"Use {command} select <name> to choose one for this "
                f"conversation, or {command} select <name> <agent> for "
                f"a specific agent."
            )
        return resolver


    def _make_image_resolver(self, user_id, conversation_id, agent_name):
        from services.base_image_generation import BaseImageGenerationService
        return self._make_media_resolver(
            user_id, conversation_id, agent_name,
            BaseImageGenerationService, "image_services",
            "image generation", "/imgservice",
        )


    def _make_video_resolver(self, user_id, conversation_id, agent_name):
        from services.base_video_generation import BaseVideoGenerationService
        return self._make_media_resolver(
            user_id, conversation_id, agent_name,
            BaseVideoGenerationService, "video_services",
            "video generation", "/vidservice",
        )

    def _make_audio_resolver(self, user_id, conversation_id, agent_name):
        from services.base_audio_generation import BaseAudioGenerationService
        return self._make_media_resolver(
            user_id, conversation_id, agent_name,
            BaseAudioGenerationService, "audio_services",
            "audio generation", "/audioservice",
        )


    def _decrement_active(self, conversation_id: str, ctx: dict = None):
        """Decrement the active-conversation refcount and clean up tracking.

        Also refreshes the poll cooldown so that agent-generated messages
        don't trigger other agents to wake up (only user messages should).
        """
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
        if ctx:
            gen_key = ctx.get("_gen_key", conversation_id)
            with self._interactions_lock:
                self._active_interactions.pop(gen_key, None)
        # Clean up active claude-code client reference
        if hasattr(self, '_active_claude_client'):
            self._active_claude_client.pop(conversation_id, None)


    def _calibrate_cpt(self, service_id: str, total_chars: int,
                       actual_tokens: int):
        """Update the calibrated chars-per-token ratio from actual API usage.

        Uses exponential moving average (alpha=0.3) so the ratio adapts
        quickly but doesn't swing wildly on a single outlier.
        """
        if not service_id or actual_tokens <= 0 or total_chars <= 0:
            return
        measured = total_chars / actual_tokens
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
        cal = self._calibrated_cpt.get(service_id)
        if cal and cal > 0:
            return cal
        return fallback if fallback > 0 else 2.0


    @staticmethod
    def _track_tokens(user_id: str, tokens_in: int, tokens_out: int,
                      model: str, agent_name: str = "",
                      llm_service: str = ""):
        """Track token usage via TokenTracker (best-effort)."""
        try:
            from core.token_tracker import TokenTracker
            TokenTracker.instance().track(
                user_id, tokens_in, tokens_out,
                model=model, agent_name=agent_name,
                llm_service=llm_service,
            )
            TokenTracker.instance().flush()
        except Exception:
            pass


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
    def _deflate_image_messages(messages: List[LLMMessage], keep_last: bool = False):
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
                    p.get("type") == "image_url" for p in m.content
                ):
                    last_img_idx = i
        for idx, m in enumerate(messages):
            if not isinstance(m.content, list):
                continue
            has_images = any(
                p.get("type") == "image_url" for p in m.content
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
                                    fname, _b64d.b64decode(b64), mime)
                                _base = FileStore.instance().get_base_url() if hasattr(FileStore.instance(), 'get_base_url') else ""
                                img_url = f"{_base}/files/{fid}/{fname}" if _base else f"/files/{fid}/{fname}"
                                img_refs.append(img_url)
                        except Exception:
                            img_refs.append("(image)")
                    elif "/files/" in url:
                        img_refs.append(url)
                    elif url.startswith(("http://", "https://")):
                        # Keep full URL — small compared to base64, allows re-fetch
                        img_refs.append(url)
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
    _TOOL_RESULT_CLEAR_THRESHOLD = 300


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
        _FS_REF = _re_fs.compile(r'/files/[a-f0-9]{12}/')
        cleared = 0

        # Clear ALL tool results > threshold — recent or not.
        # The LLM can use read(path=url, source='filestore', offset, limit) to paginate.
        for i in range(1, len(messages)):
            m = messages[i]
            if m.role != "tool" or not isinstance(m.content, str):
                continue
            content = m.content
            content_len = len(content)

            # Skip small results
            if content_len <= self._TOOL_RESULT_CLEAR_THRESHOLD:
                continue
            # Skip already cleared
            if "[Result cleared" in content or ("[Full result" in content and _FS_REF.search(content)):
                continue

            # Strip TOOL OUTPUT wrapper for storage
            _inner = content
            if _inner.startswith("[TOOL OUTPUT"):
                _nl = _inner.find("\n")
                if _nl >= 0:
                    _inner = _inner[_nl + 1:]
                if _inner.endswith("[/TOOL OUTPUT]"):
                    _inner = _inner[:-len("[/TOOL OUTPUT]")].rstrip()

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
                url = f"/files/{fid}/{fname}"
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
                         chars_per_token: float = 0) -> int:
        """Estimate token count for messages + tool definitions.

        *chars_per_token* controls the conversion ratio.  Default (0) uses
        a conservative 2 chars/token.  Multilingual models or models with
        byte-level tokenizers may need 1.0–1.5.  The service config key
        ``chars_per_token`` can override this per-LLM.
        """
        # Precise counting via tiktoken — strip image data first
        try:
            from core.token_counter import count_messages_tokens
            _stripped = []
            for m in messages:
                c = m.content if hasattr(m, 'content') else str(m)
                if isinstance(c, list):
                    # Replace image_url parts with a small placeholder
                    c = " ".join(
                        p.get("text", "") if p.get("type") == "text"
                        else "[image]" if p.get("type") == "image_url"
                        else p.get("text", "") if p.get("type") == "document"
                        else ""
                        for p in c
                    )
                _stripped.append({"content": c})
            return count_messages_tokens(_stripped)
        except Exception:
            pass
        # Fallback to character estimation
        # Modern tokenizers average ~3.5 chars/token (was 2.0, too conservative)
        cpt = chars_per_token if chars_per_token > 0 else 3.5
        total_chars = 0
        for m in messages:
            total_chars += 12  # message overhead (role, separators)
            if isinstance(m.content, str):
                total_chars += len(m.content)
            elif isinstance(m.content, list):
                for part in m.content:
                    if part.get("type") == "text":
                        total_chars += len(part.get("text", ""))
                    elif part.get("type") == "document":
                        total_chars += len(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        # Images are handled separately by the API (not counted as text tokens).
                        # Don't count them — they inflate the estimate and trigger unnecessary compaction.
                        total_chars += 85  # ~85 tokens per image tile in OpenAI/Anthropic
            if m.tool_calls:
                for tc in m.tool_calls:
                    total_chars += len(tc.name) + len(json.dumps(tc.arguments))
        # Tool definitions (JSON schemas) are sent with every request
        if tool_defs:
            for td in tool_defs:
                total_chars += len(getattr(td, 'name', '') or '')
                total_chars += len(getattr(td, 'description', '') or '')
                params = getattr(td, 'parameters', None)
                if params:
                    total_chars += len(json.dumps(params) if isinstance(params, dict) else str(params))
        return int(total_chars / cpt)


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
            from core.dynamic_tool_store import DynamicToolStore
            DynamicToolStore.instance().cleanup_conversation(conversation_id)
        except Exception as e:
            logger.warning(f"[cleanup] dynamic tool cleanup failed: {e}")
        # Stop and undeploy conversation-scoped flow instances
        try:
            from gui.services.deployment_registry import DeploymentRegistry
            from gui.services.executor_registry import ExecutorRegistry
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
        pattern = re.compile(r'/files/([a-f0-9]{12})/')
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
        load_agent_tools from the flow config).  If not set, the tool is
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


    def _list_available_services(self, user_id: str, service_type: str) -> list:
        """Plan D: list all available services of a type for the user."""
        result = []
        # Flow services
        services = getattr(self, '_services', {})
        for sid, svc in services.items():
            svc_type = getattr(svc, 'TYPE', '')
            if service_type == "remoteExecutor" and svc_type == "remoteExecutor":
                info = svc.get_relay_info() if hasattr(svc, 'get_relay_info') else {}
                result.append({"id": sid, "type": svc_type, "root": info.get("root", "?")})
            elif service_type == "filesystem" and svc_type in (
                "relay", "googleDrive", "oneDrive",
            ):
                result.append({"id": sid, "type": svc_type, "root": "?"})
        # User services
        if user_id:
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                all_defs = registry.get_all_for_user(user_id)
                for sid, sdef in all_defs.items():
                    if not sdef.enabled:
                        continue
                    if service_type == "remoteExecutor" and sdef.service_type == "remoteExecutor":
                        if not any(s["id"] == sid for s in result):
                            result.append({
                                "id": sid, "type": sdef.service_type,
                                "root": sdef.description or "?",
                            })
                    elif service_type == "filesystem" and sdef.service_type in (
                        "relay", "googleDrive", "oneDrive",
                    ):
                        if not any(s["id"] == sid for s in result):
                            result.append({
                                "id": sid, "type": sdef.service_type,
                                "root": sdef.description or "?",
                            })
            except Exception:
                pass
        return result


    def _find_filesystem_service(self, user_id: str = ""):
        """Find the first available filesystem service.

        Search order: flow services → UserServiceRegistry (Plan B cross-channel).
        """
        services = getattr(self, '_services', {})
        fs_types = ("relay", "filesystem", "googleDrive", "oneDrive")
        for svc in services.values():
            svc_type = getattr(svc, 'TYPE', '')
            if svc_type in fs_types:
                return svc
        # Check GlobalServiceRegistry
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            for sid, sdef in greg.get_all_definitions().items():
                if not getattr(sdef, "enabled", True):
                    continue
                if getattr(sdef, "service_type", "") in fs_types:
                    svc = greg.get_live_instance(sid)
                    if svc:
                        return svc
        except Exception:
            pass
        # Check UserServiceRegistry
        if user_id:
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                for fs_type in fs_types:
                    compatible = registry.get_compatible(fs_type, user_id)
                    for sdef in compatible:
                        if sdef.enabled:
                            svc = registry.get_live_instance(user_id, sdef.service_id)
                            if svc:
                                return svc
            except Exception:
                pass
        # Plan B: check RelayConnectionManager for WS relays
        if user_id:
            try:
                from core.relay_manager import RelayConnectionManager
                mgr = RelayConnectionManager.instance()
                conn = mgr.get(user_id, relay_type="filesystem")
                if conn:
                    from gui.services.user_service_registry import UserServiceRegistry
                    registry = UserServiceRegistry.get_instance()
                    svc = registry.get_live_instance(user_id, conn.relay_id)
                    if svc:
                        return svc
            except Exception:
                pass
        return None


    def _find_executor_service(self, user_id: str = ""):
        """Find the first available remote executor service.

        Search order: flow services → UserServiceRegistry (Plan B cross-channel).
        """
        services = getattr(self, '_services', {})
        for svc in services.values():
            svc_type = getattr(svc, 'TYPE', '')
            if svc_type == "remoteExecutor":
                return svc
        # Plan B: fallback to user-installed services
        if user_id:
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                compatible = registry.get_compatible("remoteExecutor", user_id)
                for sdef in compatible:
                    if sdef.enabled:
                        svc = registry.get_live_instance(user_id, sdef.service_id)
                        if svc:
                            return svc
            except Exception:
                pass
        # Plan B: check RelayConnectionManager for WS relays
        if user_id:
            try:
                from core.relay_manager import RelayConnectionManager
                mgr = RelayConnectionManager.instance()
                conn = mgr.get(user_id, relay_type="executor")
                if conn:
                    from gui.services.user_service_registry import UserServiceRegistry
                    registry = UserServiceRegistry.get_instance()
                    svc = registry.get_live_instance(user_id, conn.relay_id)
                    if svc:
                        return svc
            except Exception:
                pass
        return None


    def _wire_embed_fn(
        self, registry: ToolRegistry, client: LLMClient,
    ) -> None:
        """Wire embedding function into RememberHandler and SemanticRecallHandler."""
        from core.tool_registry import RememberHandler, SemanticRecallHandler

        if not client.api_key:
            return  # No API key, can't embed

        _api_key = client.api_key
        _base_url = client.base_url

        def embed_fn(text: str) -> List[float]:
            from core.embeddings import EmbeddingProvider
            results = EmbeddingProvider.instance().embed(
                [text], provider="auto", api_key=_api_key, base_url=_base_url,
            )
            return results[0] if results else []

        for h in registry.list_tools():
            if isinstance(h, RememberHandler):
                h.set_embed_fn(embed_fn)
            elif isinstance(h, SemanticRecallHandler):
                h.set_embed_fn(embed_fn)

