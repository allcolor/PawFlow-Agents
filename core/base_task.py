# Base Task Implementation

"""
Base implementation for all tasks.
Provides common functionality and the standard structure.
"""

from typing import Dict, Any, List, Optional
from abc import ABC
from core import Task, TaskError, FlowFile
from core.variable_resolver import VariableResolverMixin
from core.bulletin import BulletinBoard
import json


class BaseTask(VariableResolverMixin, Task, ABC):
    """
    Base implementation for all tasks.

    Handles validation, variable resolution, and utility functions.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the task with its configuration.

        Args:
            config: Task configuration
        """
        # ALWAYS wrap config in LazyResolveDict — every .get() resolves
        # expressions automatically. No task needs manual resolution.
        from core.expression import LazyResolveDict
        self._original_config = config if isinstance(config, dict) else {}
        if not isinstance(config, LazyResolveDict):
            config = LazyResolveDict(config or {})
        super().__init__(config)
        self.config = config

        # Controller services (injected by the executor)
        self._services: Dict[str, Any] = {}

        # Parameter context (injected by the executor at runtime)
        self._parameter_context = None

        # Flow source directory (injected by the executor for asset resolution)
        self._flow_source_dir: str = ""

    def log(self, level: str = "INFO", message: str = "", **kwargs):
        """
        Log a message.
        
        Args:
            level: Log level (DEBUG, INFO, WARNING, ERROR)
            message: Message to log
            **kwargs: Attributes to include in the log
        """
        import logging
        logger = logging.getLogger(self.__class__.__name__)
        
        log_message = message
        if kwargs:
            log_message += f" | Attributes: {json.dumps(kwargs)}"
        
        if level == "DEBUG":
            logger.debug(log_message)
        elif level == "INFO":
            logger.info(log_message)
        elif level == "WARNING":
            logger.warning(log_message)
        elif level == "ERROR":
            logger.error(log_message)
    
    def get_attribute(self, flowfile: FlowFile, key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Get a FlowFile attribute with variable resolution.
        
        Args:
            flowfile: Source FlowFile
            key: Attribute key
            default: Default value
            
        Returns:
            Attribute value or default value
        """
        value = flowfile.get_attribute(key, default)
        
        if value and '${' in value:
            # Resolve variables in the attribute
            return self._resolve_string(value)
        
        return value
    
    def set_attribute(self, flowfile: FlowFile, key: str, value: str):
        """
        Set an attribute on the FlowFile.
        
        Args:
            flowfile: Target FlowFile
            key: Attribute key
            value: Attribute value
        """
        resolved_value = self._resolve_string(str(value))
        flowfile.set_attribute(key, resolved_value)
    
    def create_flowfile(
        self,
        content: bytes,
        attributes: Optional[Dict[str, str]] = None,
        parent_flowfile: Optional[FlowFile] = None
    ) -> FlowFile:
        """
        Create a new FlowFile.
        
        Args:
            content: FlowFile content
            attributes: Optional attributes
            parent_flowfile: Parent FlowFile used to inherit attributes
            
        Returns:
            New FlowFile
        """
        new_attributes = attributes.copy() if attributes else {}
        
        # Inherit attributes from the parent if specified
        if parent_flowfile:
            for key, value in parent_flowfile.get_attributes().items():
                if key not in new_attributes:
                    new_attributes[key] = value
        
        return FlowFile(
            content=content,
            attributes=new_attributes
        )
    
    def read_content(self, flowfile: FlowFile) -> bytes:
        """
        Read a FlowFile's content.
        
        Args:
            flowfile: Source FlowFile
            
        Returns:
            Binary content
        """
        return flowfile.get_content()
    
    def write_content(self, flowfile: FlowFile, content: bytes):
        """
        Write content into a FlowFile.
        
        Args:
            flowfile: Target FlowFile
            content: Content to write
        """
        flowfile.set_content(content)
    
    def split_content(self, content: bytes, split_by: bytes = b"\n") -> List[bytes]:
        """
        Split content into multiple parts.
        
        Args:
            content: Content to split
            split_by: Separator bytes
            
        Returns:
            List of split content chunks
        """
        if isinstance(content, str):
            content = content.encode('utf-8')
        
        parts = content.split(split_by)
        return [p for p in parts if p]  # Filter out empty parts
    
    def merge_content(self, contents: List[bytes], separator: bytes = b"\n") -> bytes:
        """
        Merge multiple content chunks.
        
        Args:
            contents: List of content chunks
            separator: Separator between chunks, as bytes
            
        Returns:
            Merged content
        """
        return separator.join(contents)
    
    def validate_json(self, content: str) -> bool:
        """
        Validate JSON content.
        
        Args:
            content: JSON content to validate
            
        Returns:
            True if valid
        """
        try:
            json.loads(content)
            return True
        except json.JSONDecodeError:
            return False
    
    def parse_json(self, content: str) -> Dict[str, Any]:
        """
        Parse JSON content.
        
        Args:
            content: JSON content
            
        Returns:
            Parsed JSON object
            
        Raises:
            TaskError: If the JSON is invalid
        """
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise TaskError(f"JSON invalide: {e}")
    
    def serialize_json(self, data: Any) -> str:
        """
        Serialize an object to JSON.
        
        Args:
            data: Object to serialize
            
        Returns:
            JSON string
            
        Raises:
            TaskError: If serialization fails
        """
        try:
            return json.dumps(data, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as e:
            raise TaskError(f"Erreur de sérialisation JSON: {e}")
    
    def copy_flowfile_attributes(self, source: FlowFile, target: FlowFile, exclude: Optional[List[str]] = None):
        """
        Copy attributes from one FlowFile to another.
        
        Args:
            source: Source FlowFile
            target: Target FlowFile
            exclude: List of attributes to exclude
        """
        exclude = exclude or []
        
        for key, value in source.get_attributes().items():
            if key not in exclude:
                target.set_attribute(key, value)
    
    def get_service(self, service_id: str) -> Optional[Any]:
        """Get a controller service by ID.

        Services are injected by the executor from Flow.services.
        Tasks reference services by the ID defined in the flow JSON.

        Args:
            service_id: The service identifier

        Returns:
            The service instance, or None if not found
        """
        svc = self._services.get(service_id)
        if svc is None:
            # Also try resolving from config (e.g. "service_id": "my_cache")
            config_svc_id = self.config.get("service_id", "")
            if config_svc_id and config_svc_id in self._services:
                return self._services[config_svc_id]
        return svc

    def set_services(self, services: Dict[str, Any]):
        """Inject controller services (called by executor)."""
        self._services = services

    def set_parameter_context(self, ctx):
        """Inject the flow's ParameterContext (called by executor).

        Once injected, ${X} in the original config are
        re-resolved with actual values, and resolve_value() becomes available.
        """
        self._parameter_context = ctx
        # Re-resolve config from the original (unresolved) config + parameter context
        if ctx:
            self.config = ctx.resolve_config(self._original_config)

    @property
    def parameter_context(self):
        """Access the flow's ParameterContext (may be None if not injected)."""
        return self._parameter_context

    def resolve_value(self, value: str, flowfile: Optional[FlowFile] = None) -> str:
        """Resolve a string at runtime via unified cascade.

        Cascade: secrets (conv→user→global) → params (flow attrs→flow params→conv→user→global→env).
        """
        if not isinstance(value, str) or '${' not in value:
            return value
        from core.expression import resolve_expression
        flow_params = self._parameter_context.parameters if self._parameter_context else {}
        attrs = flowfile.get_attributes() if flowfile else {}
        # Merge: flow params first, FlowFile attrs win (more specific)
        merged = {**flow_params, **attrs}
        return resolve_expression(value, parameters=merged)

    def bulletin(self, level: str, message: str):
        """Post a message to the bulletin board."""
        BulletinBoard.get_instance().post(level, self.__class__.__name__, message)

    def initialize(self):
        """Called once after services are injected and connected.

        Override in tasks that need setup before scheduling begins
        (e.g. registering HTTP routes, opening sockets).
        """
        pass

    def reset(self):
        """Reset internal state. Called when queues are cleared.

        Override in stateful tasks (e.g. mergeContent) to clear
        internal buffers/bins. Also used to re-arm one-shot tasks
        like generateFlowFile.
        """
        pass

    def prioritize(self, flowfile) -> int:
        """Return priority for a FlowFile entering this task's input queue.

        Higher number = more urgent. Override for custom rules.
        Default: read from 'priority' attribute or 0.

        Convention: 0=normal, 5=elevated, 10=urgent, -5=low/batch.
        """
        val = flowfile.get_attribute("priority")
        try:
            return int(val) if val else 0
        except (ValueError, TypeError):
            return 0

    def has_pending_input(self) -> bool:
        """Whether this task has self-generated input ready.

        Override in self-triggering tasks (e.g. httpReceiver) to return True
        when the task has data to produce without needing an incoming connection.
        The continuous executor scheduler checks this for root tasks.
        """
        return False

    @property
    def is_persistent_source(self) -> bool:
        """Whether this task is a persistent/recurring source (listener, poller).

        Override to return True in tasks that listen for external events
        (HTTP receiver, Telegram receiver, etc.). A flow with only non-persistent
        sources will auto-stop when all queues are empty and no workers are active.
        """
        return False

    # ── Asset resolution ─────────────────────────────────────────────

    def set_flow_source_dir(self, path: str):
        """Inject the flow's source directory (called by executor)."""
        self._flow_source_dir = path

    def _resolve_asset_path(self, relative_path: str):
        """Resolve an asset path relative to the flow's source directory.

        Search order:
        1. flow_source_dir / assets / relative_path
        2. flow_source_dir / relative_path
        3. Task's own module directory / relative_path

        Returns:
            pathlib.Path if found, None otherwise.
        """
        from pathlib import Path
        candidates = []
        if self._flow_source_dir:
            flow_dir = Path(self._flow_source_dir)
            candidates.append(flow_dir / "assets" / relative_path)
            candidates.append(flow_dir / relative_path)
        # Fallback: relative to the task's own Python file
        try:
            import sys as _sys
            task_mod = _sys.modules.get(self.__class__.__module__)
            if task_mod and hasattr(task_mod, '__file__') and task_mod.__file__:
                task_dir = Path(task_mod.__file__).parent
                candidates.append(task_dir / relative_path)
        except Exception:
            pass
        for p in candidates:
            if p.is_file():
                return p
        return None

    def get_asset(self, path: str) -> bytes:
        """Load a binary asset relative to the flow's assets directory.

        Args:
            path: Relative path (e.g. "chat_ui/i18n.js", "images/logo.png")

        Returns:
            File content as bytes.

        Raises:
            FileNotFoundError: If the asset is not found.
        """
        resolved = self._resolve_asset_path(path)
        if resolved is None:
            raise FileNotFoundError(
                f"Asset '{path}' not found (flow_dir={self._flow_source_dir})")
        return resolved.read_bytes()

    def get_asset_text(self, path: str, encoding: str = "utf-8") -> str:
        """Load a text asset relative to the flow's assets directory.

        Args:
            path: Relative path (e.g. "chat_ui/sse.js")
            encoding: Text encoding (default: utf-8)

        Returns:
            File content as string.
        """
        resolved = self._resolve_asset_path(path)
        if resolved is None:
            raise FileNotFoundError(
                f"Asset '{path}' not found (flow_dir={self._flow_source_dir})")
        return resolved.read_text(encoding=encoding)

    def list_assets(self, prefix: str = "") -> List[str]:
        """List available asset paths under a prefix.

        Args:
            prefix: Directory prefix (e.g. "chat_ui", "images")

        Returns:
            List of relative paths.
        """
        from pathlib import Path
        results = []
        if self._flow_source_dir:
            assets_dir = Path(self._flow_source_dir) / "assets" / prefix
            if assets_dir.is_dir():
                for p in sorted(assets_dir.rglob("*")):
                    if p.is_file():
                        results.append(str(p.relative_to(
                            Path(self._flow_source_dir) / "assets")))
        return results

    def get_task_id(self) -> str:
        """
        Return the task ID.
        
        Returns:
            Task ID, based on the class name
        """
        # This method is populated by the parent flow
        return self.__class__.__name__