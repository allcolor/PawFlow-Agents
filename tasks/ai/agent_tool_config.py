"""AgentLoopTask mixin — AgentContext methods

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



class AgentContextMixin:
    """Methods extracted from AgentLoopTask."""



class AgentToolConfigMixin:
    """Tool handler configuration."""

    def _configure_tool_handlers(
        self, registry: ToolRegistry,
        conversation_id: str = "", user_id: str = "",
        llm_client=None, llm_model: str = "",
        agent_name: str = "", agent_svc: str = "",
    ) -> None:
        """Configure tool handlers with runtime settings (base_url, API keys, TTL)."""
        from core.tool_registry import (
            AskUserHandler, BrowserActionHandler,
            CreateFileHandler,
            ApprovePlanHandler,
            AssignPlanHandler,
            CancelPlanHandler,
            CreatePlanHandler,
            DeletePlanHandler,
            CreateToolHandler, ExecuteScriptHandler,
            FlowManagerHandler,
            ForgetHandler,
            ImageGenerationHandler, EditImageHandler, ImageModelInfoHandler,
            VideoGenerationHandler, AudioGenerationHandler,
            LinkIdentityHandler, ManageResourceHandler,
            NotifyUserHandler,
            RecallHandler, RememberHandler,
            SemanticRecallHandler,
            AssignTaskHandler, CompleteTaskHandler, VerifyTaskHandler,
            ListSecretsHandler,
            ScheduleRecheckHandler, ShowFileHandler, SpawnAgentsHandler,
            StoreSecretHandler, UpdatePlanHandler,
            VerifyPlanStepHandler,
            SecurityScanHandler,
        )
        from core.handlers._fs_base import BaseFsHandler
        from core.handlers.compact_result import CompactResultHandler
        from core.handlers.diary import DiaryWriteHandler, DiaryReadHandler
        from core.handlers.knowledge_graph import _KgBaseHandler
        from core.handlers.learn import LearnHandler
        from core.handlers.memory import CheckDuplicateHandler
        from core.handlers.project_graph import ProjectGraphHandler

        file_base_url = self.config.get("file_base_url", "")
        # file_ttl is set per-request to match conversation TTL
        # (see _prepare_agent_context and _build_poll_context)
        # Resolve any remaining expressions (e.g. ${api_key} from cascade)
        from core.expression import resolve_value as _rv
        file_base_url = _rv(file_base_url) or ""

        for h in registry.list_tools():
            if isinstance(h, CreateFileHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
                if user_id:
                    h.set_user_id(user_id)
            elif isinstance(h, ExecuteScriptHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
                # Inject filesystem service resolver for fs:// URLs in scripts
                def _fs_resolver(svc_id):
                    try:
                        from core.service_registry import ServiceRegistry
                        return ServiceRegistry.get_instance().resolve(svc_id, user_id=user_id)
                    except Exception:
                        return None
                h.set_fs_resolver(_fs_resolver)
            elif isinstance(h, (ImageGenerationHandler, EditImageHandler,
                                ImageModelInfoHandler)):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                if user_id and hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if conversation_id and hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
                h.set_service_resolver(self._make_image_resolver(
                    user_id, conversation_id, agent_name,
                ))
            elif isinstance(h, VideoGenerationHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
                if user_id:
                    h.set_user_id(user_id)
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                h.set_service_resolver(self._make_video_resolver(
                    user_id, conversation_id, agent_name,
                ))
            elif isinstance(h, AudioGenerationHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
                if user_id:
                    h.set_user_id(user_id)
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                h.set_service_resolver(self._make_audio_resolver(
                    user_id, conversation_id, agent_name,
                ))
            elif h.name in ("generate_3d", "upscale_image", "upscale_video",
                             "remove_background", "try_on",
                             "lipsync", "train_image_model"):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                if user_id and hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if conversation_id and hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
                _maker = {
                    "generate_3d": self._make_3d_resolver,
                    "upscale_image": self._make_upscale_resolver,
                    "upscale_video": self._make_upscale_resolver,
                    "remove_background": self._make_upscale_resolver,
                    "try_on": self._make_tryon_resolver,
                    "lipsync": self._make_lipsync_resolver,
                    "train_image_model": self._make_trainer_resolver,
                }[h.name]
                h.set_service_resolver(_maker(
                    user_id, conversation_id, agent_name))
            elif h.name in ("describe_image", "remix_image"):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                if user_id and hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if conversation_id and hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
                h.set_service_resolver(self._make_image_resolver(
                    user_id, conversation_id, agent_name))
            elif h.name == "speech_to_video":
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                if user_id and hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if conversation_id and hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
                h.set_service_resolver(self._make_video_resolver(
                    user_id, conversation_id, agent_name))
            elif isinstance(h, ScheduleRecheckHandler):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                if user_id:
                    h.set_user_id(user_id)
            elif hasattr(h, '_is_dynamic') or h.name in ('create_tool', 'delete_tool'):
                if hasattr(h, 'set_conversation_id') and conversation_id:
                    h.set_conversation_id(conversation_id)
                if hasattr(h, 'set_user_id') and user_id:
                    h.set_user_id(user_id)
            elif isinstance(h, (RememberHandler, RecallHandler, SemanticRecallHandler,
                                  ForgetHandler, CheckDuplicateHandler)):
                h.set_user_id(user_id)
                if hasattr(h, 'set_agent_name'):
                    h.set_agent_name(agent_name)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, _KgBaseHandler):
                h.set_user_id(user_id)
                if hasattr(h, 'set_agent_name'):
                    h.set_agent_name(agent_name)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, (DiaryWriteHandler, DiaryReadHandler, LearnHandler)):
                h.set_user_id(user_id)
                h.set_agent_name(agent_name)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, ProjectGraphHandler):
                # ProjectGraphHandler extends BaseFsHandler — wire FS + agent_name
                h.set_user_id(user_id)
                h.set_conversation_id(conversation_id)
                h.set_agent_name(agent_name)
                # Wire relay/filesystem service (same as BaseFsHandler clause below)
                _agent_name_pg = agent_name
                _relay_svc_pg = None
                if conversation_id:
                    try:
                        from core.relay_bindings import get_default
                        _default_relay_pg = get_default(conversation_id, agent=_agent_name_pg)
                        if _default_relay_pg:
                            from core.service_registry import ServiceRegistry
                            _relay_svc_pg = ServiceRegistry.get_instance().resolve(_default_relay_pg, user_id=user_id)
                    except Exception:
                        pass
                fs_svc_pg = _relay_svc_pg or self._find_filesystem_service(user_id)
                if fs_svc_pg:
                    h.set_fs_service(fs_svc_pg)
            elif isinstance(h, (AssignTaskHandler, CompleteTaskHandler, VerifyTaskHandler)):
                h.set_conversation_id(conversation_id)
                h.set_agent_name(agent_name)
                if hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if hasattr(h, 'set_agent_name'):
                    h.set_agent_name(agent_name)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, BrowserActionHandler):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, LinkIdentityHandler):
                if user_id:
                    h.set_user_id(user_id)
            elif isinstance(h, (CreatePlanHandler, UpdatePlanHandler, ApprovePlanHandler,
                                 AssignPlanHandler, CancelPlanHandler, DeletePlanHandler,
                                 VerifyPlanStepHandler)):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                if hasattr(h, 'set_agent_name') and agent_name:
                    h.set_agent_name(agent_name)
            elif isinstance(h, NotifyUserHandler):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                if user_id:
                    h.set_user_id(user_id)
            elif isinstance(h, AskUserHandler):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                if user_id:
                    h.set_user_id(user_id)
            elif isinstance(h, CreateToolHandler):
                if user_id:
                    h.set_user_id(user_id)
                if conversation_id:
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, FlowManagerHandler):
                if user_id:
                    h.set_user_id(user_id)
                if conversation_id:
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, StoreSecretHandler):
                if user_id:
                    h.set_user_id(user_id)
                if conversation_id:
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, ListSecretsHandler):
                if user_id:
                    h.set_user_id(user_id)
            elif isinstance(h, ManageResourceHandler):
                h.set_user_id(user_id)
                h.set_conversation_id(conversation_id)
                h.set_agent_name(agent_name)
                h.set_llm_service(agent_svc)
            elif isinstance(h, SpawnAgentsHandler):
                if user_id:
                    h.set_user_id(user_id)
                if isinstance(h, SpawnAgentsHandler):
                    if conversation_id:
                        h.set_conversation_id(conversation_id)
                    if agent_name:
                        h.set_source_agent(agent_name, agent_svc)
                # SubAgentExecutor is set up lazily in _prepare_agent_context
            elif isinstance(h, ShowFileHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
                if user_id:
                    h.set_user_id(user_id)
            elif hasattr(h, 'name') and h.name == 'screen':
                fs_svc = self._find_filesystem_service(user_id)
                if fs_svc:
                    h.set_service(fs_svc)
                if user_id:
                    h.set_user_id(user_id)
                if file_base_url:
                    h.set_base_url(file_base_url)
            elif hasattr(h, 'name') and h.name == 'read_history':
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                if user_id:
                    h.set_user_id(user_id)
            elif hasattr(h, 'name') and h.name == 'read_parent_context':
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                if user_id:
                    h.set_user_id(user_id)
            elif isinstance(h, BaseFsHandler):
                if user_id:
                    h.set_user_id(user_id)
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                # Agent name scopes the Read-before-Edit guard — each agent
                # has its own "has read" view, reads by other agents don't
                # count.
                if agent_name and hasattr(h, 'set_agent_name'):
                    h.set_agent_name(agent_name)
                # Try conversation-scoped relay bindings first (per-agent)
                _agent_name = agent_name
                _relay_svc = None
                if conversation_id:
                    try:
                        from core.relay_bindings import get_default
                        _default_relay = get_default(conversation_id, agent=_agent_name)
                        if _default_relay:
                            from core.service_registry import ServiceRegistry
                            _relay_svc = ServiceRegistry.get_instance().resolve(_default_relay, user_id=user_id)
                    except Exception:
                        pass
                fs_svc = _relay_svc or self._find_filesystem_service(user_id)
                if fs_svc:
                    if hasattr(fs_svc, 'set_user_id') and user_id:
                        fs_svc.set_user_id(user_id)
                    h.set_fs_service(fs_svc)
                # Build available services from relay bindings (per-agent scope)
                fs_services = []
                if conversation_id:
                    try:
                        from core.relay_bindings import get_linked
                        for _rid in get_linked(conversation_id, agent=_agent_name):
                            fs_services.append({"id": _rid, "type": "relay", "root": "?"})
                    except Exception:
                        pass
                if not fs_services:
                    fs_services = self._list_available_services(user_id, "filesystem")
                if fs_services:
                    h.set_available_services(fs_services)
                # Set default_local for tool argument injection
                if conversation_id and _default_relay:
                    try:
                        from core.relay_bindings import get_default_local
                        _dl = get_default_local(conversation_id, relay_id=_default_relay, agent=_agent_name)
                        if _dl is not None:
                            h._default_local = _dl
                    except Exception:
                        pass
            elif isinstance(h, SecurityScanHandler):
                if user_id:
                    h.set_user_id(user_id)

