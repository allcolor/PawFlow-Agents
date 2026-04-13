# PawFlow Tasks Module

"""
Module principal des tâches PawFlow.
Fournit et enregistre toutes les tâches disponibles.
"""

from typing import Dict, Any, List
from core import TaskFactory
from core import FlowFile


def _register_all_services():
    """Import all service modules to trigger ServiceFactory registration."""
    from core import ServiceFactory
    # Check a late-registered type to know if ALL modules are loaded
    # (llmConnection alone is not enough — image/video may be missing)
    if "pixazoImageGeneration" in ServiceFactory.list_types():
        return  # All modules already registered

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
        pass  # whatsapp service load failed
    try:
        import services.slack_bot_service     # noqa: F401
    except ImportError:
        pass  # slack_sdk not installed

    # Image generation services
    import services.pixazo_image_service      # noqa: F401
    import services.grok_image_service        # noqa: F401
    import services.openai_image_service      # noqa: F401

    # Video generation services
    import services.kling_video_service       # noqa: F401
    import services.pixazo_video_service      # noqa: F401
    import services.grok_video_service        # noqa: F401
    import services.sora_video_service        # noqa: F401

    # Relay & filesystem services
    import services.filesystem_service         # noqa: F401  — relay service (WS)
    import services.tool_relay_service         # noqa: F401  — MCP bridge tool relay
    import services.server_filesystem_service  # noqa: F401
    import services.gdrive_filesystem_service  # noqa: F401
    import services.onedrive_filesystem_service  # noqa: F401


def register_all_tasks():
    """Enregistrer toutes les tâches et services disponibles."""
    # Services first (no guard — they have their own idempotent registration)
    _register_all_services()

    if "log" in TaskFactory.list_types():
        return  # Tasks déjà enregistrées

    # Tâches système
    from tasks.system import register_system_tasks
    register_system_tasks()

    # Tâches d'IO
    from tasks.io import GetFileTask, PutFileTask, FetchHTTPTask
    from tasks.io.listen_http import ListenHTTPTask

    # Tâches de données
    from tasks.data import TransformJSONTask
    from tasks.data.evaluate_jsonpath import EvaluateJSONPathTask
    from tasks.data.extract_text import ExtractTextTask
    from tasks.data.compress_content import CompressContentTask
    from tasks.data.validate_json import ValidateJSONTask
    from tasks.data.convert_charset import ConvertCharsetTask
    from tasks.data.filter_content import FilterContentTask
    from tasks.data.base64_encode import Base64EncodeTask
    from tasks.data.count_text import CountTextTask

    # Tâches de contrôle
    from tasks.control import RouteOnAttributeTask, SplitContentTask, MergeContentTask
    from tasks.control.duplicate_content import DuplicateContentTask
    from tasks.control.ports import InputPortTask, OutputPortTask
    from tasks.control.funnel import FunnelTask
    from tasks.control.control_rate import ControlRateTask

    # Tâches de données supplémentaires
    from tasks.data.convert_csv import ConvertCSVToJSONTask, ConvertJSONToCSVTask
    from tasks.data.execute_sql import ExecuteSQLTask, PutSQLTask
    from tasks.data.cache_tasks import PutCacheTask, GetCacheTask
    from tasks.data.dist_cache_tasks import FetchDistributedMapCacheTask, PutDistributedMapCacheTask
    from tasks.data.detect_duplicate import DetectDuplicateTask
    from tasks.data.attributes_to_json import AttributesToJSONTask
    from tasks.data.split_json import SplitJSONTask
    from tasks.data.infer_llm import InferLLMTask

    # Tâches IO supplémentaires
    from tasks.io.send_email import SendEmailTask
    from tasks.io.notify_slack import NotifySlackTask
    from tasks.io.sftp_tasks import GetSFTPTask, PutSFTPTask
    from tasks.io.ftp_tasks import GetFTPTask, PutFTPTask
    from tasks.io.kafka_tasks import PublishKafkaTask, ConsumeKafkaTask
    from tasks.io.s3_tasks import GetS3Task, PutS3Task
    from tasks.io.gcs_tasks import GetGCSTask, PutGCSTask
    from tasks.io.azure_tasks import GetAzureBlobTask, PutAzureBlobTask

    # Tâches XML
    from tasks.data.parse_xml import ParseXMLTask, TransformXMLTask

    # Tâches Avro / Parquet
    from tasks.data.convert_avro_parquet import (
        ConvertAvroToJSONTask, ConvertJSONToAvroTask,
        ConvertParquetToJSONTask, ConvertJSONToParquetTask,
    )

    # Tâches MQTT
    from tasks.io.mqtt_tasks import PublishMQTTTask, ConsumeMQTTTask

    # List SFTP
    from tasks.io.list_sftp import ListSFTPTask

    # HTTP Listener tasks
    from tasks.io.http_receiver import HTTPReceiverTask
    from tasks.io.handle_http_response import HandleHTTPResponseTask
    from tasks.io.validate_http_auth import ValidateHTTPAuthTask

    # Tâches de synchronisation
    from tasks.control.wait_notify import WaitTask, NotifyTask

    # Tâches système supplémentaires
    from tasks.system.generate_flowfile import GenerateFlowFileTask
    from tasks.system.hash_content import HashContentTask
    from tasks.system.list_files import ListFilesTask
    from tasks.system.execute_script import ExecuteScriptTask
    from tasks.system.reporting_task import ReportingTask

    # Scrapling fetch
    from tasks.io.scrapling_fetch import ScraplingFetchTask

    # File serving + Chat UI + Assets + Admin UI
    from tasks.io.serve_file import ServeFileTask
    from tasks.io.serve_chat_ui import ServeChatUITask
    from tasks.io.serve_assets import ServeAssetsTask
    from tasks.io.serve_admin_ui import ServeAdminUITask
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

    # Tâches AI
    from tasks.ai.agent_loop import AgentLoopTask

    # Auto-register ToolHandlers as flow tasks (tool.*)
    from core.tool_task_adapter import register_tool_tasks
    register_tool_tasks()




# Export des tâches enregistrées
def get_available_tasks() -> List[Dict[str, Any]]:
    """
    Obtenir la liste des tâches disponibles.
    
    Returns:
        Liste de dictionnaires avec les informations sur chaque tâche
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