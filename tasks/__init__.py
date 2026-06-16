# PawFlow Tasks Module

"""
Main PawFlow tasks module.
Provides and registers all available tasks.
"""

from typing import Dict, Any, List
import logging

from core import TaskFactory
from core import FlowFile


logger = logging.getLogger(__name__)


def _register_all_services():
    """Import all service modules to trigger ServiceFactory registration."""
    # Force project root into sys.path — always, no conditional check
    # (Windows path comparison is unreliable: C:\x vs C:/x vs c:\x)
    import sys, os
    _root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
    sys.path.insert(0, _root)

    # Each module auto-registers via ServiceFactory.register() at import time
    import services.db_connection_pool       # noqa: F401
    import services.cache_service            # noqa: F401
    import services.http_client_service      # noqa: F401
    import services.http_listener_service    # noqa: F401
    import services.http_auth_service        # noqa: F401
    import services.ssl_context_service      # noqa: F401
    import services.file_tracking_service    # noqa: F401
    import services.distributed_cache        # noqa: F401
    import services.llm_connection           # noqa: F401
    import services.summarizer_service       # noqa: F401
    import services.private_gateway          # noqa: F401
    import services.package_runtime_service  # noqa: F401
    import services.llm_credential_oauth     # noqa: F401
    import services.oauth_provider_service   # noqa: F401
    import services.auth_gateway_service    # noqa: F401
    import services.telegram_bot_service    # noqa: F401
    try:
        import services.discord_bot_service   # noqa: F401
    except ImportError:
        pass  # discord.py not installed
    try:
        import services.whatsapp_service      # noqa: F401
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    try:
        import services.slack_bot_service     # noqa: F401
    except ImportError:
        pass  # slack_sdk not installed

    # Image generation services
    import services.pixazo_image_service      # noqa: F401
    import services.wavespeed_image_service   # noqa: F401
    import services.grok_image_service        # noqa: F401
    import services.openai_image_service      # noqa: F401
    import services.codex_image_service       # noqa: F401
    import services.openai_compatible_media_service  # noqa: F401

    # Video generation services
    import services.kling_video_service       # noqa: F401
    import services.pixazo_video_service      # noqa: F401
    import services.wavespeed_video_service   # noqa: F401
    import services.grok_video_service        # noqa: F401

    # Audio generation services
    import services.pixazo_audio_service       # noqa: F401
    import services.wavespeed_audio_service    # noqa: F401
    import services.suno_audio_service         # noqa: F401  — sunoapi.org wrapper
    import services.supertonic_tts_service     # noqa: F401  — local Supertonic TTS
    import services.voicebox_service           # noqa: F401  — local Voicebox voice I/O
    import services.voxcpm_tts_service         # noqa: F401  — external VoxCPM TTS
    import services.openai_compatible_tts_service  # noqa: F401  — OpenAI-compatible TTS
    import services.openai_compatible_stt_service  # noqa: F401  — OpenAI-compatible STT
    import services.xai_tts_service               # noqa: F401  — xAI TTS
    import services.xai_stt_service               # noqa: F401  — xAI STT
    import services.luxtts_service             # noqa: F401  — local LuxTTS voice clone

    # Voice-cloning TTS services
    import services.fish_audio_voice_clone_service  # noqa: F401
    import services.elevenlabs_voice_clone_service  # noqa: F401
    import services.wavespeed_voice_clone_service   # noqa: F401

    # Extra media capability services (3D, upscale, try-on, lipsync, trainer)
    import services.pixazo_capability_services  # noqa: F401
    import services.wavespeed_capability_services  # noqa: F401

    # Relay & filesystem services
    import services.filesystem_service         # noqa: F401  — relay service (WS)
    import services.tool_relay_service         # noqa: F401  — MCP bridge tool relay
    import services.cc_interactive_event_service  # noqa: F401  — CC interactive MITM events
    import services.gdrive_filesystem_service  # noqa: F401
    import services.onedrive_filesystem_service  # noqa: F401
    import services.rclone_filesystem_service  # noqa: F401
    import services.rclone_oauth_credentials  # noqa: F401

    from core import ServiceFactory
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("services.") or module is None:
            continue
        for value in vars(module).values():
            if not isinstance(value, type):
                continue
            if value.__module__ != module_name:
                continue
            if getattr(value, "TYPE", ""):
                ServiceFactory.register(value)


def register_all_tasks():
    """Register all available tasks and services."""
    # Services first (no guard — they have their own idempotent registration)
    _register_all_services()

    if "log" in TaskFactory.list_types():
        _register_installed_package_tasks()
        return  # Already registered tasks

    # System tasks
    from tasks.system import register_system_tasks
    register_system_tasks()

    # Tasks d'IO
    from tasks.io import GetFileTask, PutFileTask, FetchHTTPTask
    from tasks.io.listen_http import ListenHTTPTask

    # Data tasks
    from tasks.data import TransformJSONTask
    from tasks.data.evaluate_jsonpath import EvaluateJSONPathTask
    from tasks.data.extract_text import ExtractTextTask
    from tasks.data.compress_content import CompressContentTask
    from tasks.data.validate_json import ValidateJSONTask
    from tasks.data.convert_charset import ConvertCharsetTask
    from tasks.data.filter_content import FilterContentTask
    from tasks.data.base64_encode import Base64EncodeTask
    from tasks.data.count_text import CountTextTask

    # Control tasks
    from tasks.control import RouteOnAttributeTask, SplitContentTask, MergeContentTask
    from tasks.control.duplicate_content import DuplicateContentTask
    from tasks.control.ports import InputPortTask, OutputPortTask
    from tasks.control.funnel import FunnelTask
    from tasks.control.control_rate import ControlRateTask

    # Additional data tasks
    from tasks.data.convert_csv import ConvertCSVToJSONTask, ConvertJSONToCSVTask
    from tasks.data.execute_sql import ExecuteSQLTask, PutSQLTask
    from tasks.data.cache_tasks import PutCacheTask, GetCacheTask
    from tasks.data.dist_cache_tasks import FetchDistributedMapCacheTask, PutDistributedMapCacheTask
    from tasks.data.detect_duplicate import DetectDuplicateTask
    from tasks.data.attributes_to_json import AttributesToJSONTask
    from tasks.data.split_json import SplitJSONTask
    from tasks.data.infer_llm import InferLLMTask

    # Additional IO tasks
    from tasks.io.send_email import SendEmailTask
    from tasks.io.notify_slack import NotifySlackTask
    from tasks.io.sftp_tasks import GetSFTPTask, PutSFTPTask
    from tasks.io.kafka_tasks import PublishKafkaTask, ConsumeKafkaTask
    from tasks.io.s3_tasks import GetS3Task, PutS3Task
    from tasks.io.gcs_tasks import GetGCSTask, PutGCSTask
    from tasks.io.azure_tasks import GetAzureBlobTask, PutAzureBlobTask

    # Tasks XML
    from tasks.data.parse_xml import ParseXMLTask, TransformXMLTask

    # Tasks Avro / Parquet
    from tasks.data.convert_avro_parquet import (
        ConvertAvroToJSONTask, ConvertJSONToAvroTask,
        ConvertParquetToJSONTask, ConvertJSONToParquetTask,
    )

    # Tasks MQTT
    from tasks.io.mqtt_tasks import PublishMQTTTask, ConsumeMQTTTask

    # List SFTP
    from tasks.io.list_sftp import ListSFTPTask

    # HTTP Listener tasks
    from tasks.io.http_receiver import HTTPReceiverTask
    from tasks.io.handle_http_response import HandleHTTPResponseTask
    from tasks.io.validate_http_auth import ValidateHTTPAuthTask

    # Tasks de synchronisation
    from tasks.control.wait_notify import WaitTask, NotifyTask

    # Additional system tasks
    from tasks.system.generate_flowfile import GenerateFlowFileTask
    from tasks.system.startup_trigger import StartupTriggerTask
    from tasks.system.install_bootstrap import InstallBootstrapTask
    from tasks.system.hash_content import HashContentTask
    from tasks.system.list_files import ListFilesTask
    from tasks.system.execute_script import ExecuteScriptTask
    from tasks.system.reporting_task import ReportingTask

    # Scrapling fetch
    from tasks.io.scrapling_fetch import ScraplingFetchTask

    # File serving + Chat UI + Assets
    from tasks.io.serve_file import ServeFileTask
    from tasks.io.serve_relay_file import ServeRelayFileTask
    from tasks.io.serve_chat_ui import ServeChatUITask
    from tasks.io.serve_assets import ServeAssetsTask
    from tasks.io.serve_pfp_ext_assets import ServePfpExtensionAssetsTask
    from tasks.io.admin_actions import AdminActionTask
    from tasks.io.relay_proxy import ServeRelayProxyTask

    # Conversation-scoped flow tasks
    from tasks.io.publish_message import PublishMessageTask
    from tasks.io.spawn_agent import SpawnAgentTask
    from tasks.io.conv_task_ops import AssignTaskToAgentTask, CancelAgentTaskTask
    from tasks.io.read_conversation import ReadConversationTask

    # Login page
    from tasks.io.serve_login import ServeLoginTask

    # Create conversation (for user-scoped flows)
    from tasks.io.create_conversation import CreateConversationTask

    # OAuth2 tasks
    from tasks.io.oauth_redirect import OAuthRedirectTask
    from tasks.io.oauth_callback import OAuthCallbackTask
    from tasks.io.oauth_logout import OAuthLogoutTask
    from tasks.io.validate_session_auth import ValidateSessionAuthTask

    # Telegram
    from tasks.io.telegram_receiver import TelegramReceiverTask
    from tasks.io.telegram_send import TelegramSendTask
    from tasks.io.telegram_api import TelegramApiTask
    from tasks.io.telegram_agent_client import (
        TelegramAgentClientTask, TelegramConversationBridgeTask)

    # Discord
    from tasks.io.discord_receiver import DiscordReceiverTask
    from tasks.io.discord_send import DiscordSendTask

    # WhatsApp
    from tasks.io.whatsapp_receiver import WhatsAppReceiverTask
    from tasks.io.whatsapp_send import WhatsAppSendTask

    # Slack (bidirectional — receiver + send)
    from tasks.io.slack_receiver import SlackReceiverTask

    # SSE streaming
    from tasks.io.agent_sse_stream import AgentSSEStreamTask

    # Filesystem operations
    from tasks.io.filesystem_ops import FilesystemOpsTask

    # Tasks AI
    from tasks.ai.agent_loop import AgentLoopTask
    from tasks.ai.agent_actions_task import AgentActionsTask

    # Auto-register ToolHandlers as flow tasks (tool.*)
    from core.tool_task_adapter import register_tool_tasks
    register_tool_tasks()

    _register_installed_package_tasks()


def _register_installed_package_tasks():
    """Reload installed PFP task proxies after builtin tasks are available."""
    try:
        from core import pfp_package
        result = pfp_package.load_all_installed_package_tasks()
        if result.get("errors"):
            logger.debug("PFP task proxy reload had errors: %s", result["errors"])
    except Exception as exc:
        logger.debug("PFP task proxy reload failed: %s", exc)




# Export registered tasks
def get_available_tasks() -> List[Dict[str, Any]]:
    """
    Get the list of available tasks.
    
    Returns:
        Liste de dictionnaires avec les informations sur chaque task
    """
    tasks = []
    task_types = TaskFactory.list_types()
    
    for task_type in task_types:
        task_class = TaskFactory.get(task_type)
        tasks.append({
            'type': task_class.TYPE,
            'version': task_class.VERSION,
            'name': task_class.NAME,
            'description': task_class.DESCRIPTION,
            'icon': task_class.ICON
        })
    
    return tasks