# PawFlow Core

from __future__ import annotations

"""
PawFlow core module.
Defines the framework interfaces and abstract classes.
"""

from typing import Dict, Any, List, Optional, BinaryIO
from dataclasses import dataclass, field
from datetime import datetime
import uuid
import io

__version__ = "1.0.0"
__author__ = "PawFlow Team"


# ============================================================================
# Exceptions
# ============================================================================

class PawFlowError(Exception):
    """Base exception for PawFlow."""
    pass


class TaskError(PawFlowError):
    """Error while executing a task."""
    pass


class ServiceError(PawFlowError):
    """Error while using a service."""
    pass


class FlowError(PawFlowError):
    """Flow configuration or execution error."""
    pass


class ValidationError(PawFlowError):
    """Validation error."""
    pass


class VariableResolutionError(PawFlowError):
    """Variable resolution error."""
    pass


# ============================================================================
# Utilities
# ============================================================================

def safe_float(val, default: float = 0.0) -> float:
    """Parse a float from user input, handling French comma decimal separator.

    Supports: '0.05', '0,05', 3.5, 0, None, ''.
    """
    if val is None or val == "":
        return default
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).replace(",", "."))
    except (ValueError, TypeError):
        return default


# ============================================================================
# FlowFile
# ============================================================================

class FlowFile:
    """Represents a data unit moving through the pipeline.

    Supports both in-memory and disk-backed content transparently.
    Small content stays in RAM; large content (> SPILL_THRESHOLD) is
    automatically spilled to disk. All existing code using get_content()
    and set_content() continues to work unchanged.
    """

    __slots__ = ('_content_ref', 'attributes', 'process_id', 'created_at',
                 '_raw_content', '_sse_stream')

    def __init__(self, content: bytes = b'',
                 attributes: Optional[Dict[str, str]] = None,
                 process_id: Optional[str] = None,
                 created_at: Optional[datetime] = None,
                 _content_ref=None):
        from core.stream import ContentReference

        self.attributes: Dict[str, str] = dict(attributes) if attributes else {}
        self.process_id: str = process_id or str(uuid.uuid4())
        self.created_at: datetime = created_at or datetime.now()
        self._raw_content = None  # backward compat cache
        self._sse_stream = None   # SSE streaming iterator (non-serializable)

        if _content_ref is not None:
            self._content_ref = _content_ref
        else:
            self._content_ref = ContentReference(data=content)

    # -- Attribute API (unchanged) --

    def get_attribute(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.attributes.get(key, default)

    def set_attribute(self, key: str, value: str):
        self.attributes[key] = str(value)

    def delete_attribute(self, key: str):
        self.attributes.pop(key, None)

    def get_attributes(self) -> Dict[str, str]:
        return dict(self.attributes)

    def set_attributes(self, attributes: Dict[str, str]):
        self.attributes = dict(attributes)

    # -- Content API (backward compatible) --

    @property
    def content(self) -> bytes:
        """Backward-compatible property. Loads all content into memory."""
        return self._content_ref.get_bytes()

    @content.setter
    def content(self, value: bytes):
        """Backward-compatible setter."""
        from core.stream import ContentReference
        old = self._content_ref
        self._content_ref = ContentReference(data=value)
        old.release()

    def get_content(self) -> bytes:
        """Get full content as bytes (loads from disk if spilled)."""
        return self._content_ref.get_bytes()

    def set_content(self, content: bytes):
        """Set content from bytes. Auto-spills to disk if large."""
        from core.stream import ContentReference
        old = self._content_ref
        self._content_ref = ContentReference(data=content)
        old.release()

    # -- Streaming API (new) --

    def get_content_stream(self) -> BinaryIO:
        """Get a readable stream over the content.

        Returns io.BytesIO for in-memory content, or an open file handle
        for disk-backed content. Caller should close the stream.
        """
        return self._content_ref.get_stream()

    def set_content_from_stream(self, stream: BinaryIO, size_hint: int = 0):
        """Set content by reading from a stream.

        If size_hint > SPILL_THRESHOLD, streams directly to disk
        without buffering the entire content in memory.
        """
        from core.stream import ContentReference
        old = self._content_ref
        self._content_ref = ContentReference.from_stream(stream, size_hint)
        old.release()

    @property
    def is_content_on_disk(self) -> bool:
        """Check if content is spilled to disk."""
        return self._content_ref.is_on_disk

    # -- Size & status --

    def size(self) -> int:
        """Content size in bytes (without loading content into memory)."""
        return self._content_ref.size

    def is_empty(self) -> bool:
        return self._content_ref.size == 0

    # -- Clone --

    def clone(self, deep: bool = True) -> 'FlowFile':
        """Clone this FlowFile.

        Args:
            deep: If True (default), creates independent copy of content.
                  If False, shares content via reference counting (zero-copy).
        """
        if deep:
            ref = self._content_ref.clone_data()
        else:
            self._content_ref.increment_ref()
            ref = self._content_ref

        return FlowFile(
            attributes=dict(self.attributes),
            process_id=str(uuid.uuid4()),
            created_at=datetime.now(),
            _content_ref=ref,
        )

    # -- Serialization --

    def to_dict(self) -> Dict[str, Any]:
        return {
            'process_id': self.process_id,
            'size': self._content_ref.size,
            'attributes': dict(self.attributes),
            'created_at': self.created_at.isoformat(),
            'on_disk': self._content_ref.is_on_disk,
        }

    def __repr__(self):
        loc = "disk" if self._content_ref.is_on_disk else "mem"
        return (f"FlowFile(id={self.process_id[:8]}..., "
                f"size={self._content_ref.size}, "
                f"attrs={len(self.attributes)}, {loc})")

    def __del__(self):
        """Release content reference on garbage collection."""
        try:
            if hasattr(self, '_content_ref') and self._content_ref is not None:
                self._content_ref.release()
        except Exception:
            pass


# ============================================================================
# Flow Class
# ============================================================================

class Flow:
    """Task orchestration for building a processing pipeline."""

    def __init__(self, config: Dict[str, Any]):
        from core.process_group import ProcessGroup

        self.id = config.get('id', str(uuid.uuid4()))
        self.name = config.get('name', 'Unnamed Flow')
        self.version = config.get('version', '1.0.0')
        self.description = config.get('description', '')
        self.author = config.get('author', '')
        self.parameters = config.get('parameters', {})
        self.entries = config.get('entries', [])
        self.exits = config.get('exits', [])
        self.tasks: Dict[str, Task] = {}
        self.services: Dict[str, Service] = {}
        # Groups: Dict[str, ProcessGroup] — parsed from config in FlowParser
        self.groups: Dict[str, ProcessGroup] = {}
        self.relations = config.get('relations', [])
        self.variables = config.get('variables', {})

        # Runtime scope: "independent" (default), "user", or "conversation"
        # Declares what runtime data the flow needs (not permissions).
        self.scope: str = config.get('scope', 'independent')
        # Directory containing the flow definition file (for asset resolution)
        self.source_dir: str = config.get('_source_dir', '')
    
    def add_task(self, task_id: str, task: Task):
        self.tasks[task_id] = task
    
    def add_service(self, service_id: str, service: Service):
        self.services[service_id] = service
    
    def get_task(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)
    
    def get_service(self, service_id: str) -> Optional[Service]:
        return self.services.get(service_id)
    
    def to_dict(self) -> Dict[str, Any]:
        return {'id': self.id, 'name': self.name, 'tasks': list(self.tasks.keys())}


# ============================================================================
# Task Interface
# ============================================================================

class Task:
    """Abstract interface for all tasks."""
    
    TYPE: str = ""
    VERSION: str = "1.0.0"
    NAME: str = ""
    DESCRIPTION: str = ""
    ICON: str = "default"
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._validate_config()
    
    def _validate_config(self):
        errors = self.validate()
        if errors:
            raise ValueError("; ".join(errors))
    
    def validate(self) -> List[str]:
        errors = []
        schema = self.get_parameter_schema()
        
        for param_name, param_schema in schema.items():
            if param_schema.get('required', False):
                if param_name not in self.config:
                    errors.append(f"Paramètre requis manquant: {param_name}")
        return errors
    
    def get_parameter_schema(self) -> Dict[str, Any]:
        return {}
    
    def execute(self, flowfile: 'FlowFile') -> List['FlowFile']:
        return [flowfile]
    
    def get_type(self) -> str:
        return self.TYPE
    
    def get_name(self) -> str:
        return self.NAME
    
    def get_version(self) -> str:
        return self.VERSION


# ============================================================================
# Service Interface
# ============================================================================

class Service:
    """Abstract interface for all services."""
    
    TYPE: str = ""
    VERSION: str = "1.0.0"
    NAME: str = ""
    DESCRIPTION: str = ""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._connection = None
        self._validated = False
    
    def _validate_config(self):
        errors = self.validate()
        if errors:
            raise ValueError("; ".join(errors))
    
    def validate(self) -> List[str]:
        errors = []
        schema = self.get_parameter_schema()
        
        for param_name, param_schema in schema.items():
            if param_schema.get('required', False):
                if param_name not in self.config:
                    errors.append(f"Paramètre requis manquant: {param_name}")
        return errors
    
    def get_parameter_schema(self) -> Dict[str, Any]:
        return {}

    def get_parameter_rules(self) -> list:
        """Declarative rules for conditional visibility, required, defaults, options.

        Each rule: {"when": {"field": ["val1", "val2"]}, "set": {"other_field": {"visible": bool, "required": bool, "default": val, "options": [...]}}}
        Rules evaluated in order — last match wins per field.
        """
        return []

    def get_service_actions(self) -> list:
        """Custom actions (buttons in the edit form).

        Each: {"id": str, "label": str, "icon": str, "when": {field: [vals]}, "server_action": str, "flow": "simple"|"confirm"|"oauth_code"}
        """
        return []

    def connect(self):
        pass

    def disconnect(self):
        pass
    
    def get_type(self) -> str:
        return self.TYPE
    
    def get_name(self) -> str:
        return self.NAME


# ============================================================================
# Factories
# ============================================================================

class TaskFactory:
    """Factory for creating tasks."""
    
    _tasks: Dict[str, type] = {}
    
    @classmethod
    def register(cls, task_class: type):
        if hasattr(task_class, 'TYPE') and task_class.TYPE:
            cls._tasks[task_class.TYPE] = task_class
    
    @classmethod
    def get(cls, task_type: str) -> type:
        if task_type not in cls._tasks:
            raise TaskError(f"Task non trouvée: {task_type}")
        return cls._tasks[task_type]
    
    @classmethod
    def list_types(cls) -> List[str]:
        return list(cls._tasks.keys())


class ServiceFactory:
    """Factory for creating services."""
    
    _services: Dict[str, type] = {}
    
    @classmethod
    def register(cls, service_class: type):
        if hasattr(service_class, 'TYPE') and service_class.TYPE:
            cls._services[service_class.TYPE] = service_class
    
    @classmethod
    def get(cls, service_type: str) -> type:
        if service_type not in cls._services:
            raise ServiceError(f"Service non trouvé: {service_type}")
        return cls._services[service_type]
    
    @classmethod
    def list_types(cls) -> List[str]:
        return list(cls._services.keys())


# ============================================================================
# Constants
# ============================================================================

STANDARD_ATTRIBUTES = {
    'filename': 'Nom du fichier original',
    'fileSize': 'Taille en octets',
    'timestamp': 'Timestamp d\'entrée (ISO8601)',
    'uuid': 'UUID unique du FlowFile',
    'batch.id': 'ID du batch',
    'process.id': 'ID du processus',
    'error.message': 'Message d\'erreur',
    'error.count': 'Nombre d\'erreurs',
}

VariableType = type('VariableType', (), {
    'STRING': "string",
    'INTEGER': "integer",
    'FLOAT': "float",
    'BOOLEAN': "boolean",
    'SECRET': "secret",
    'REFERENCE': "reference",
    'JSON': "json",
})

# Import StorageManager for accessibility
from core.storage import StorageManager, StorageInterface
from core.variable_resolver import VariableResolverMixin