"""Base messaging tasks — reusable patterns for receiver/send tasks.

Provides BaseReceiverTask and BaseSendTask that channel-specific tasks
can extend with minimal boilerplate.
"""

import logging
import queue
import re
from typing import Any, Dict, List, Optional

from core import FlowFile
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class BaseReceiverTask(BaseTask):
    """Base for self-triggering messaging receiver tasks.

    Subclasses must:
    - Set TYPE, VERSION, NAME, DESCRIPTION, ICON, TAGS
    - Override _parse_update(update) -> Optional[FlowFile]
    - Call _enqueue(flowfile) or use _on_update(update) as callback
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._queue: queue.Queue = queue.Queue(maxsize=1000)
        self._registered = False
        self._owner_id: Optional[str] = None

    def has_pending_input(self) -> bool:
        return not self._queue.empty()

    @property
    def is_persistent_source(self) -> bool:
        return True

    def _on_update(self, update: dict):
        """Default callback: parse update and enqueue."""
        ff = self._parse_update(update)
        if ff is not None:
            self._enqueue(ff)

    def _enqueue(self, ff: FlowFile):
        """Add a FlowFile to the queue."""
        try:
            self._queue.put_nowait(ff)
        except queue.Full:
            logger.warning(f"{self.TYPE} queue full, dropping message")

    def _parse_update(self, update: dict) -> Optional[FlowFile]:
        """Parse a channel update into a FlowFile. Override in subclass."""
        raise NotImplementedError

    def execute(self, flowfile: Optional[FlowFile] = None) -> List[FlowFile]:
        self.initialize()
        try:
            ff = self._queue.get_nowait()
            return [ff]
        except queue.Empty:
            return []

    def initialize(self):
        """Override to register with service."""
        pass

    def cleanup(self):
        """Unregister from service."""
        pass


class BaseSendTask(BaseTask):
    """Base for messaging send tasks.

    Subclasses must:
    - Set TYPE, VERSION, NAME, DESCRIPTION, ICON, TAGS
    - Override _get_service_type() -> str
    - Override _send(service, channel_id, text, flowfile) -> dict
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

    def _resolve_value(self, flowfile: FlowFile, value: str) -> str:
        """Resolve ${...} expressions in a config value."""
        if '${' not in value:
            return value
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        service_id = self.config.get("service_id", "")
        svc = self.get_service(service_id)
        if not svc:
            raise RuntimeError(f"Service '{service_id}' not found")
        svc.ensure_connected()

        channel_id_expr = self.config.get("channel_id", "")
        channel_id = self._resolve_value(flowfile, channel_id_expr) if channel_id_expr else ""
        if not channel_id:
            raise ValueError(f"{self.TYPE}: no channel_id configured or resolved")

        text = flowfile.get_content().decode("utf-8", errors="replace")
        if not text.strip():
            logger.warning(f"{self.TYPE}: empty message, skipping")
            return [flowfile]

        try:
            result = self._send(svc, channel_id, text, flowfile)
            flowfile.set_attribute(f"{self._channel_name()}.send_status", "sent")
            if isinstance(result, dict) and "message_id" in result:
                flowfile.set_attribute(
                    f"{self._channel_name()}.sent_message_id",
                    str(result["message_id"]),
                )
        except Exception as e:
            logger.error(f"{self.TYPE} send error: {e}")
            flowfile.set_attribute(f"{self._channel_name()}.send_status", "error")
            flowfile.set_attribute(f"{self._channel_name()}.send_error", str(e))

        return [flowfile]

    def _send(self, service, channel_id: str, text: str, flowfile: FlowFile) -> dict:
        """Send message via service. Override in subclass."""
        return service.send_message(channel_id, text)

    def _channel_name(self) -> str:
        """Channel name for attribute prefix. Override if needed."""
        return "messaging"
