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
from core.tool_registry import ToolRegistry, create_default_registry, load_agent_tools

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
            AskAgentHandler, AskUserHandler, BrowserActionHandler,
            CreateFileHandler,
            ApprovePlanHandler,
            AssignPlanHandler,
            CancelPlanHandler,
            CreatePlanHandler,
            DeletePlanHandler,
            CreateToolHandler, ExecuteScriptHandler, FilesystemToolHandler,
            FlowManagerHandler,
            ForgetHandler, GetAgentResultsHandler,
            ImageGenerationHandler, ImageModelInfoHandler, VideoGenerationHandler,
            LinkIdentityHandler, LocalFilesHandler, ManageResourceHandler,
            NotifyUserHandler,
            RecallHandler, RememberHandler, RemoteExecutorHandler,
            SemanticRecallHandler,
            AssignTaskHandler, CompleteTaskHandler, VerifyTaskHandler,
            ListSecretsHandler,
            ScheduleRecheckHandler, ShowFileHandler, SpawnAgentsHandler,
            StoreSecretHandler, UpdatePlanHandler, UseSkillHandler,
            GitHubHandler, SecurityScanHandler,
        )

        file_base_url = self.config.get("file_base_url", "")
        # file_ttl is set per-request to match conversation TTL
        # (see _prepare_agent_context and _build_poll_context)
        # Resolve any remaining expressions (e.g. ${secrets.*} from cascaded ${flow.parameters.*})
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
                        from gui.services.user_service_registry import UserServiceRegistry
                        svc = UserServiceRegistry.get_instance().get_live_instance(user_id, svc_id)
                        if svc:
                            return svc
                    except Exception:
                        pass
                    try:
                        from gui.services.global_service_registry import GlobalServiceRegistry
                        return GlobalServiceRegistry.get_instance().get_live_instance(svc_id)
                    except Exception:
                        return None
                h.set_fs_resolver(_fs_resolver)
            elif isinstance(h, (ImageGenerationHandler, ImageModelInfoHandler)):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                if user_id and hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                h.set_service_resolver(self._make_image_resolver(
                    user_id, conversation_id, agent_name,
                ))
            elif isinstance(h, VideoGenerationHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
                if user_id:
                    h.set_user_id(user_id)
                h.set_service_resolver(self._make_video_resolver(
                    user_id, conversation_id, agent_name,
                ))
                if conversation_id or user_id:
                    h.set_service_resolver(self._make_video_resolver(
                        user_id, conversation_id, agent_name,
                    ))
            elif isinstance(h, ScheduleRecheckHandler):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                if user_id:
                    h.set_user_id(user_id)
            elif isinstance(h, LocalFilesHandler):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, (RememberHandler, RecallHandler, SemanticRecallHandler, ForgetHandler)):
                h.set_user_id(user_id)
                if hasattr(h, 'set_agent_name'):
                    h.set_agent_name(agent_name)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
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
                                 AssignPlanHandler, CancelPlanHandler, DeletePlanHandler)):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
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
            elif isinstance(h, AskAgentHandler):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                if user_id:
                    h.set_user_id(user_id)
                if llm_client:
                    h.set_llm_client(llm_client, llm_model)
                h.set_client_resolver(
                    lambda svc, uid: self._resolve_llm_service(svc, uid))
            elif isinstance(h, ManageResourceHandler):
                h.set_user_id(user_id)
                h.set_conversation_id(conversation_id)
                h.set_agent_name(agent_name)
                h.set_llm_service(agent_svc)
            elif isinstance(h, (SpawnAgentsHandler, UseSkillHandler)):
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
            elif isinstance(h, RemoteExecutorHandler):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                if user_id:
                    h.set_user_id(user_id)
                exec_svc = self._find_executor_service(user_id)
                if exec_svc:
                    h.set_service(exec_svc)
                # Plan D: pass available services list
                exec_services = self._list_available_services(user_id, "remoteExecutor")
                if exec_services:
                    h.set_available_services(exec_services)
            elif isinstance(h, FilesystemToolHandler):
                if user_id:
                    h.set_user_id(user_id)
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                # Try to inject filesystem service (Plan B: cross-channel)
                fs_svc = self._find_filesystem_service(user_id)
                if fs_svc:
                    if hasattr(fs_svc, 'set_user_id') and user_id:
                        fs_svc.set_user_id(user_id)
                    h.set_fs_service(fs_svc)
                # Plan D: pass available services list
                fs_services = self._list_available_services(user_id, "filesystem")
                if fs_services:
                    h.set_available_services(fs_services)
            elif isinstance(h, (GitHubHandler, SecurityScanHandler)):
                if user_id:
                    h.set_user_id(user_id)

