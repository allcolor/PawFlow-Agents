# Technical and Functional Documentation - PawFlow

## Table of Contents

1. [Overview](#1-overview)
2. [Software Architecture](#2-software-architecture)
3. [Fundamental Concepts](#3-fundamental-concepts)
4. [JSON Flow Format](#4-json-flow-format)
5. [Task Interface](#5-task-interface)
6. [Service Interface](#6-service-interface)
7. [Flow and Group Interface](#7-flow-and-group-interface)
8. [FlowFile and Attributes](#8-flowfile-and-attributes)
9. [Configuration System](#9-configuration-system)
10. [Runtime Variable Management](#10-runtime-variable-management)
11. [Complete Task Reference](#11-complete-task-reference)
12. [Complete Service Reference](#12-complete-service-reference)
13. [Execution Engine API](#13-execution-engine-api)
14. [GUI - Technical Specifications](#14-gui---technical-specifications)
15. [Security and Authentication](#15-security-and-authentication)
16. [Tests and Quality](#16-tests-and-quality)
17. [Deployment and Production](#17-deployment-and-production)

---

## 1. Overview

### 1.1. Project Objective

PawFlow is an Apache NiFi-style Python framework for creating, deploying, and monitoring complex data pipelines. It clearly separates two states:

- **Creation State**: Design and editing of flows, services, and tasks in a repository (Git/DB/File)
- **Runtime State**: Deployment, configuration, and execution of flows with variable management

### 1.2. Design Principles

1. **Separation of concerns**: Tasks, Services, Flows, and Groups are modeled independently
2. **Externalized configuration**: Parameters are stored in JSON, overrides at runtime
3. **Extensibility**: New task/service types can be added without modifying the core
4. **Flow-based programming**: Data flows via FlowFiles between components
5. **Declarative**: Flows are defined in readable and editable JSON files

### 1.3. Hierarchical Architecture

```
Repository
├── Services (reusable)
├── Tasks (unit processing)
├── Flows (task orchestration)
│   └── Groups (logical grouping)
└── Variables (runtime overrides)
```

---

## 2. Software Architecture

### 2.1. Project Structure

```
pawflow/
├── core/
│   ├── __init__.py
│   ├── interface_task.py        # Abstract Task interface
│   ├── interface_service.py     # Abstract Service interface
│   ├── interface_flow.py        # Abstract Flow interface
│   ├── interface_group.py       # Abstract Group interface
│   ├── flowfile.py              # FlowFile and Attributes class
│   ├── config_manager.py        # Configuration management (Git/DB/FS)
│   ├── variable_resolver.py     # Runtime variable resolution
│   └── exceptions.py            # Custom exceptions
│
├── tasks/
│   ├── __init__.py
│   ├── base_task.py             # Base implementation
│   ├── system/                  # System tasks
│   │   ├── log_task.py
│   │   ├── replace_text_task.py
│   │   ├── wait_task.py
│   │   ├── notify_task.py
│   │   └── ...
│   ├── data/
│   │   ├── script_task.py
│   │   ├── shell_task.py
│   │   ├── convert_task.py
│   │   └── ...
│   ├── io/
│   │   ├── http_task.py
│   │   ├── sftp_task.py
│   │   ├── s3_task.py
│   │   ├── db_task.py
│   │   └── ...
│   └── control/
│       ├── flow_task.py         # Call another flow
│       ├── route_task.py
│       └── split_task.py
│
├── services/
│   ├── __init__.py
│   ├── base_service.py          # Base implementation
│   ├── auth/
│   │   ├── oauth2_authenticator.py
│   │   ├── oauth2_bearer_validator.py
│   │   └── ...
│   ├── connectivity/
│   │   ├── pulsar_connection.py
│   │   ├── db_connection.py
│   │   ├── sftp_connection.py
│   │   └── ...
│   └── utils/
│       ├── https_manager.py
│       └── ...
│
├── engine/
│   ├── __init__.py
│   ├── flow_parser.py           # JSON flow parser
│   ├── flow_validator.py        # Flow validation
│   ├── executor.py              # Execution engine
│   ├── scheduler.py             # Task scheduler
│   └── error_handler.py         # Error handling and retries
│
├── gui/
│   ├── __init__.py
│   ├── editor/                  # Creation GUI
│   │   ├── app.py
│   │   ├── components/
│   │   │   ├── flow_canvas.py
│   │   │   ├── task_panel.py
│   │   │   └── property_editor.py
│   │   └── handlers/
│   │       ├── save_handler.py
│   │       └── import_export.py
│   └── runtime/                 # Runtime GUI
│       ├── app.py
│       ├── dashboard.py
│       ├── logs_viewer.py
│       └── metrics.py
│
├── config/
│   ├── __init__.py
│   ├── config.py                # Global configuration
│   └── storage/
│       ├── storage_factory.py   # Factory for Git/DB/FS
│       ├── git_storage.py
│       ├── sqlite_storage.py
│       └── filesystem_storage.py
│
├── tests/
├── examples/
├── docs/
└── main.py                      # Entry point
```

### 2.2. Main Class Diagram

```
┌─────────────────────┐
│   Task (Interface)  │
├─────────────────────┤
│ - name: str         │
│ - version: str      │
│ - parameters: dict  │
│ + execute(flowfile) │
│ + get_schema()      │
└─────────────────────┘
         │
         │ inherits
         ▼
┌─────────────────────┐
│   BaseTask          │
├─────────────────────┤
│ - config: dict      │
│ + validate()        │
│ + cleanup()         │
└─────────────────────┘
         │
    ┌────┴────┬────────┬─────────┐
    │         │        │         │
    ▼         ▼        ▼         ▼
┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐
│ Log   │ │ HTTP  │ │ Script│ │ Shell │
│Task   │ │Task   │ │Task   │ │Task   │
└───────┘ └───────┘ └───────┘ └───────┘

┌─────────────────────┐
│  Service (Interface)│
├─────────────────────┤
│ - name: str         │
│ - version: str      │
│ - parameters: dict  │
│ + connect()         │
│ + disconnect()      │
│ + get_schema()      │
└─────────────────────┘
         │
         │ inherits
         ▼
┌─────────────────────┐
│   BaseService       │
├─────────────────────┤
│ - instance: object  │
│ - pool_size: int    │
│ + init_connection() │
│ + health_check()    │
└─────────────────────┘
         │
    ┌────┴────┬────────┬─────────┐
    │         │        │         │
    ▼         ▼        ▼         ▼
┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐
│  OAuth│ │  DB   │ │ SFTP  │ │ Pulsar│
└───────┘ └───────┘ └───────┘ └───────┘

┌─────────────────────┐
│     FlowFile        │
├─────────────────────┤
│ - content: bytes    │
│ - attributes: dict  │
│ - process_id: str   │
│ + get_attr(key)     │
│ + set_attr(key, val)│
└─────────────────────┘

┌─────────────────────┐
│      Flow           │
├─────────────────────┤
│ - name: str         │
│ - entries: list     │  # Entries (sources)
│ - exits: list       │  # Exits (destinations)
│ - tasks: dict       │  # Mapping task_id -> TaskConfig
│ - relations: list   │  # Relations between tasks
│ - parameters: dict  │  # Global parameters
└─────────────────────┘
```

---

## 3. Fundamental Concepts

### 3.1. The Four Object Types

#### 3.1.1. Services
Services are reusable components that provide specific capabilities:
- **Authentication**: OAuth2, JWT, Basic Auth
- **Connectivity**: DB, SFTP, HTTP, Pulsar, S3
- **Utilities**: HTTPS Manager, Rate Limiter

**Characteristics**:
- Independent lifecycle (connect/disconnect)
- Can be shared between multiple tasks
- Persistent configuration in the repository

#### 3.1.2. Tasks
Tasks are atomic processing units:
- **Transformation**: ReplaceText, Convert, Filter
- **IO**: HTTP, SFTP, DB, S3
- **Control**: Wait, Notify, Split, Route
- **Custom**: Python Script, Shell command

**Characteristics**:
- Accept a FlowFile as input
- Produce one or more FlowFiles as output
- Expose their parameters via a standardized interface

#### 3.1.3. Flows
A flow is a task orchestration:
- **Entries**: Data sources (0 to N)
- **Exits**: Final destinations (0 to N)
- **Tasks**: Intermediate components
- **Relations**: Connections between tasks (with routing)

**Characteristics**:
- Declarative (JSON file)
- Configurable and overridable at runtime
- Can call other flows (composition)

#### 3.1.4. Groups
Groups allow visual and logical organization:
- Grouping of tasks/flows
- Hierarchical tree structure
- Configuration scope

### 3.2. FlowFiles

A FlowFile represents a unit of data flowing through the pipeline:

```python
class FlowFile:
    content: bytes              # Binary content
    attributes: Dict[str, str]  # Metadata
    process_id: str             # Instance UUID
    
    # Utility methods
    def get_attribute(key: str) -> Optional[str]
    def set_attribute(key: str, value: str)
    def delete_attribute(key: str)
    def get_content() -> bytes
    def write_content(data: bytes)
```

**Standard attributes**:
- `filename`: Original file name
- `fileSize`: Size in bytes
- `timestamp`: Entry timestamp
- `uuid`: Unique UUID
- `batch.id`: Batch ID
- `error.count`: Error count

### 3.3. Relations

A relation defines how FlowFiles flow between components:

```json
{
  "from": "task_1",
  "to": "task_2",
  "relation_type": "success|failure|timeout|any",
  "routing_strategy": "direct|round_robin|load_balance",
  "queue_size": 1000
}
```

**Relation types**:
- `success`: Task completed successfully
- `failure`: Task failed
- `timeout`: Task timed out
- `any`: Any state

**Routing strategies**:
- `direct`: Direct send to recipient
- `round_robin`: Cyclic distribution
- `load_balance`: Load balancing

---

## 4. JSON Flow Format

### 4.1. General Structure

```json
{
  "$schema": "http://pawflow.org/schemas/flow-v1.json",
  "metadata": {
    "name": "my-flow",
    "version": "1.0.0",
    "description": "Flow description",
    "author": "first.last",
    "created": "2024-01-01T00:00:00Z",
    "modified": "2024-01-15T00:00:00Z"
  },
  "parameters": {
    "param1": "value1",
    "param2": "${runtime_variable}"
  },
  "entries": [
    {
      "id": "entry_1",
      "type": "http_source|file_source|db_source",
      "config": {}
    }
  ],
  "exits": [
    {
      "id": "exit_1",
      "type": "http_dest|file_dest|db_dest",
      "config": {}
    }
  ],
  "tasks": {
    "task_1": {
      "type": "replace_text",
      "name": "Replace text",
      "parameters": {
        "search": "old",
        "replace": "new"
      }
    }
  },
  "groups": {
    "group_1": {
      "name": "Processing group",
      "tasks": ["task_1", "task_2"],
      "x": 100,
      "y": 100,
      "width": 400,
      "height": 200
    }
  },
  "relations": [
    {
      "id": "rel_1",
      "from": "entry_1",
      "to": "task_1",
      "type": "success",
      "routing": "direct"
    }
  ],
  "variables": {
    "runtime_variable": {
      "type": "string|secret|reference",
      "default": "default_value",
      "description": "Description",
      "required": true
    }
  }
}
```

### 4.2. Complete Schema

See file: `docs/schemas/flow-v1.json` (to be created)

### 4.3. Complex Example

See: `examples/complex_flow.json`

---

## 5. Task Interface

### 5.1. Interface Definition

```python
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from core.flowfile import FlowFile

class Task(ABC):
    """Abstract interface for all tasks."""
    
    # Task metadata (class attributes)
    TYPE: str                    # Unique type (e.g.: "log", "http")
    VERSION: str                 # Implementation version
    NAME: str                    # Display name
    DESCRIPTION: str             # Detailed description
    ICON: str                    # Icon for the UI
    
    @abstractmethod
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the task with its configuration.
        
        Args:
            config: Dictionary of task parameters
        """
        pass
    
    @abstractmethod
    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """
        Execute the task on a FlowFile.
        
        Args:
            flowfile: Input FlowFile
            
        Returns:
            List of output FlowFiles (1 or more)
        """
        pass
    
    @abstractmethod
    def get_parameter_schema(self) -> Dict[str, Any]:
        """
        Return the parameter schema for the UI.
        
        Returns:
            Schema describing each parameter (type, validation, etc.)
        """
        pass
    
    def validate(self) -> List[str]:
        """
        Validate the task configuration.
        
        Returns:
            List of error messages (empty if valid)
        """
        pass
    
    def initialize(self):
        """
        Initialize the task (called before execution).
        """
        pass
    
    def cleanup(self):
        """
        Clean up the task (called after execution).
        """
        pass
```

### 5.2. Base Implementation

```python
class BaseTask(Task):
    """Base implementation with common functionality."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.parameters = self._parse_parameters(config)
        self._validate_config()
    
    def _parse_parameters(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve variables in parameters."""
        resolved = {}
        for key, value in config.items():
            if isinstance(value, str) and value.startswith('${'):
                # Variable resolution
                resolved[key] = VariableResolver.resolve(value)
            else:
                resolved[key] = value
        return resolved
    
    def _validate_config(self):
        """Validate the configuration."""
        errors = []
        schema = self.get_parameter_schema()
        for param_name, param_schema in schema.items():
            if param_schema.get('required', False):
                if param_name not in self.parameters:
                    errors.append(f"Missing required parameter: {param_name}")
        if errors:
            raise ValueError("; ".join(errors))
```

### 5.3. Implementation Example

```python
class LogTask(BaseTask):
    TYPE = "log"
    NAME = "Log"
    DESCRIPTION = "Log a message"
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.message = self.parameters.get('message', '')
        self.level = self.parameters.get('level', 'INFO')
    
    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        import logging
        logger = logging.getLogger(self.__class__.__name__)
        
        # Log the message
        msg = self.message.format(
            **{k: flowfile.get_attribute(k) for k in flowfile.attributes}
        )
        
        if self.level == 'DEBUG':
            logger.debug(msg)
        elif self.level == 'INFO':
            logger.info(msg)
        # ... other levels
        
        # Return the FlowFile unchanged
        return [flowfile]
    
    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'message': {
                'type': 'string',
                'required': True,
                'description': 'Message to log',
                'placeholder': 'Message: ${filename}'
            },
            'level': {
                'type': 'select',
                'options': ['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                'default': 'INFO'
            }
        }
```

### 5.4. Complete Task Catalog

See section 11 for the complete list with parameter schemas.

---

## 6. Service Interface

### 6.1. Interface Definition

```python
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class Service(ABC):
    """Abstract interface for all services."""
    
    # Metadata (class attributes)
    TYPE: str                    # Unique type
    VERSION: str                 # Version
    NAME: str                    # Display name
    DESCRIPTION: str             # Description
    
    @abstractmethod
    def __init__(self, config: Dict[str, Any]):
        """Initialize the service."""
        pass
    
    @abstractmethod
    def connect(self):
        """Establish the connection to the service."""
        pass
    
    @abstractmethod
    def disconnect(self):
        """Close the connection."""
        pass
    
    @abstractmethod
    def get_parameter_schema(self) -> Dict[str, Any]:
        """Parameter schema."""
        pass
    
    def validate(self) -> List[str]:
        """Validate the configuration."""
        pass
    
    def health_check(self) -> bool:
        """Check the service health status."""
        pass
    
    def get_instance(self):
        """Return the connected instance (for use by tasks)."""
        pass
```

### 6.2. Lifecycle Management

```python
class BaseService(Service):
    """Base implementation with lifecycle management."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.parameters = self._resolve_variables(config)
        self._connection = None
        self._validated = False
    
    def connect(self):
        """Establish the connection with error handling."""
        if self._connection is not None:
            return
        
        try:
            self._connection = self._create_connection()
            self._validated = True
        except Exception as e:
            raise ServiceConnectionError(f"Connection failed: {e}")
    
    def disconnect(self):
        """Close the connection cleanly."""
        if self._connection is not None:
            try:
                self._close_connection()
            finally:
                self._connection = None
    
    def __enter__(self):
        """Context manager support."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close the connection after use."""
        self.disconnect()
    
    @abstractmethod
    def _create_connection(self):
        """Create the actual connection (implemented by subclass)."""
        pass
    
    @abstractmethod
    def _close_connection(self):
        """Close the actual connection."""
        pass
```

### 6.3. Service Example

```python
class SFTPService(BaseService):
    TYPE = "sftp_connection"
    NAME = "SFTP Connection"
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.host = self.parameters['host']
        self.port = self.parameters.get('port', 22)
        self.username = self.parameters['username']
        self.password = self.parameters.get('password')
        self.key_file = self.parameters.get('key_file')
    
    def _create_connection(self):
        import paramiko
        
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        if self.key_file:
            key = paramiko.RSAKey.from_private_key_file(self.key_file)
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                key_filename=self.key_file
            )
        else:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password
            )
        
        return client
    
    def _close_connection(self):
        if self._connection:
            self._connection.close()
    
    def get_connection(self):
        """Return the SFTP client."""
        return self._connection
    
    def get_parameter_schema(self):
        return {
            'host': {
                'type': 'string',
                'required': True,
                'description': 'SFTP host'
            },
            'port': {
                'type': 'integer',
                'default': 22,
                'min': 1,
                'max': 65535
            },
            'username': {
                'type': 'string',
                'required': True
            },
            'password': {
                'type': 'password',
                'required': False
            },
            'key_file': {
                'type': 'file',
                'required': False
            }
        }
```

---

## 7. Flow and Group Interface

### 7.1. Flow Interface

```python
from typing import Dict, List, Optional
from core.flowfile import FlowFile
from core.task import Task
from core.service import Service

class Flow:
    """Task orchestration."""
    
    def __init__(self, config: Dict[str, Any]):
        self.name = config['name']
        self.version = config['version']
        self.description = config.get('description', '')
        self.parameters = config.get('parameters', {})
        self.variables = config.get('variables', {})
        
        # Entries and exits
        self.entries = self._parse_entries(config.get('entries', []))
        self.exits = self._parse_exits(config.get('exits', []))
        
        # Tasks
        self.tasks = self._parse_tasks(config.get('tasks', {}))
        
        # Groups
        self.groups = self._parse_groups(config.get('groups', {}))
        
        # Relations
        self.relations = self._parse_relations(config.get('relations', []))
    
    def _parse_entries(self, entries_config: List[Dict]) -> List[Dict]:
        """Parse entries."""
        return entries_config
    
    def _parse_exits(self, exits_config: List[Dict]) -> List[Dict]:
        """Parse exits."""
        return exits_config
    
    def _parse_tasks(self, tasks_config: Dict) -> Dict[str, Task]:
        """Parse and instantiate tasks."""
        tasks = {}
        for task_id, task_config in tasks_config.items():
            task_class = TaskFactory.get(task_config['type'])
            task = task_class(task_config.get('parameters', {}))
            tasks[task_id] = task
        return tasks
    
    def _parse_groups(self, groups_config: Dict) -> Dict[str, Dict]:
        """Parse groups."""
        return groups_config
    
    def _parse_relations(self, relations_config: List[Dict]) -> List[Dict]:
        """Parse relations."""
        return relations_config
    
    def execute(self, input_flowfile: Optional[FlowFile] = None) -> List[FlowFile]:
        """
        Execute the flow.
        
        Args:
            input_flowfile: Optional FlowFile for entries
            
        Returns:
            List of output FlowFiles
        """
        # Create input FlowFiles
        flowfiles = self._create_input_flowfiles(input_flowfile)
        
        # Execute the DAG
        output_flowfiles = self._execute_dag(flowfiles)
        
        return output_flowfiles
    
    def _create_input_flowfiles(self, input_flowfile: Optional[FlowFile]) -> List[FlowFile]:
        """Create initial FlowFiles."""
        flowfiles = []
        for entry in self.entries:
            ff = FlowFile(
                content=self._read_entry(entry),
                attributes=self._get_entry_attributes(entry)
            )
            flowfiles.append(ff)
        return flowfiles
    
    def _execute_dag(self, flowfiles: List[FlowFile]) -> List[FlowFile]:
        """Execute the task DAG."""
        # Topological sort of tasks
        sorted_tasks = self._topological_sort()
        
        # Execution
        current_flowfiles = flowfiles
        for task_id in sorted_tasks:
            task = self.tasks[task_id]
            new_flowfiles = []
            for ff in current_flowfiles:
                outputs = task.execute(ff)
                new_flowfiles.extend(outputs)
            current_flowfiles = new_flowfiles
        
        return current_flowfiles
    
    def _topological_sort(self) -> List[str]:
        """Topological sort of tasks."""
        # Implementation of topological sort algorithm
        pass
    
    def get_statistics(self) -> Dict[str, Any]:
        """Return flow statistics."""
        return {
            'name': self.name,
            'total_tasks': len(self.tasks),
            'total_relations': len(self.relations),
            'entry_count': len(self.entries),
            'exit_count': len(self.exits)
        }
```

### 7.2. Group Interface

```python
class Group:
    """Logical grouping of tasks."""
    
    def __init__(self, config: Dict[str, Any]):
        self.id = config['id']
        self.name = config.get('name', '')
        self.description = config.get('description', '')
        self.tasks = config.get('tasks', [])
        self.flows = config.get('flows', [])
        
        # Position and dimensions for the UI
        self.x = config.get('x', 0)
        self.y = config.get('y', 0)
        self.width = config.get('width', 400)
        self.height = config.get('height', 200)
    
    def add_task(self, task_id: str):
        """Add a task to the group."""
        if task_id not in self.tasks:
            self.tasks.append(task_id)
    
    def remove_task(self, task_id: str):
        """Remove a task from the group."""
        if task_id in self.tasks:
            self.tasks.remove(task_id)
    
    def get_children(self) -> List[str]:
        """Return all children (tasks + subgroups)."""
        return self.tasks.copy()
```

---

## 8. FlowFile and Attributes

### 8.1. FlowFile Class

```python
import uuid
from typing import Dict, Optional, BinaryIO
from datetime import datetime

class FlowFile:
    """Represents a unit of data in the pipeline."""
    
    def __init__(
        self,
        content: bytes = b'',
        attributes: Optional[Dict[str, str]] = None,
        process_id: Optional[str] = None
    ):
        self.content = content
        self.attributes = attributes or {}
        self.process_id = process_id or str(uuid.uuid4())
        self._original_content = content.copy()
    
    # --- Attribute access ---
    
    def get_attribute(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieve an attribute."""
        return self.attributes.get(key, default)
    
    def set_attribute(self, key: str, value: str):
        """Set an attribute."""
        self.attributes[key] = str(value)
    
    def delete_attribute(self, key: str):
        """Delete an attribute."""
        if key in self.attributes:
            del self.attributes[key]
    
    def get_attributes(self) -> Dict[str, str]:
        """Retrieve all attributes."""
        return self.attributes.copy()
    
    def set_attributes(self, attributes: Dict[str, str]):
        """Set all attributes."""
        self.attributes = attributes.copy()
    
    # --- Content management ---
    
    def get_content(self) -> bytes:
        """Retrieve the content."""
        return self.content
    
    def set_content(self, content: bytes):
        """Set the content."""
        self.content = content
    
    def write_content(self, file_obj: BinaryIO):
        """Write content from a file."""
        self.content = file_obj.read()
    
    def read_content(self) -> BinaryIO:
        """Read content as a file."""
        from io import BytesIO
        return BytesIO(self.content)
    
    def clone(self) -> 'FlowFile':
        """Create a copy."""
        return FlowFile(
            content=self.content.copy(),
            attributes=self.attributes.copy(),
            process_id=str(uuid.uuid4())
        )
    
    # --- Utility methods ---
    
    def size(self) -> int:
        """Content size."""
        return len(self.content)
    
    def is_empty(self) -> bool:
        """Check if empty."""
        return len(self.content) == 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (for serialization)."""
        return {
            'process_id': self.process_id,
            'size': len(self.content),
            'attributes': self.attributes
        }
    
    def __repr__(self):
        return f"FlowFile(process_id={self.process_id}, size={len(self.content)})"
```

### 8.2. Standard Attributes

```python
STANDARD_ATTRIBUTES = {
    # Basic metadata
    'filename': 'Original file name',
    'fileSize': 'Size in bytes',
    'timestamp': 'Entry timestamp (ISO8601)',
    'uuid': 'Unique FlowFile UUID',
    
    # Flow control
    'batch.id': 'Batch ID',
    'process.id': 'Process ID',
    'route.key': 'Routing key',
    
    # Errors
    'error.message': 'Error message',
    'error.count': 'Error count',
    'retry.count': 'Retry count',
    
    # Data
    'mime.type': 'MIME type',
    'encoding': 'Encoding',
    'line.count': 'Line count',
    
    # System
    'pawflow.task.id': 'Current task ID',
    'pawflow.flow.id': 'Flow ID',
    'pawflow.execution.id': 'Execution ID'
}
```

---

## 9. Configuration System

### 9.1. Config Manager

```python
from enum import Enum
from typing import Optional, Dict, Any
from abc import ABC, abstractmethod

class StorageType(Enum):
    FILESYSTEM = "filesystem"
    GIT = "git"
    SQLITE = "sqlite"
    POSTGRES = "postgres"

class ConfigStorage(ABC):
    """Abstract interface for storage."""
    
    @abstractmethod
    def save_flow(self, flow_id: str, config: Dict[str, Any]) -> bool:
        """Save a flow."""
        pass
    
    @abstractmethod
    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        """Load a flow."""
        pass
    
    @abstractmethod
    def delete_flow(self, flow_id: str) -> bool:
        """Delete a flow."""
        pass
    
    @abstractmethod
    def list_flows(self) -> List[str]:
        """List all flows."""
        pass
    
    @abstractmethod
    def save_task(self, task_type: str, config: Dict[str, Any]) -> bool:
        """Save a custom task."""
        pass
    
    @abstractmethod
    def load_service(self, service_type: str, config: Dict[str, Any]) -> bool:
        """Save a service."""
        pass

class ConfigManager:
    """Main configuration manager."""
    
    def __init__(self, storage_type: StorageType, config: Dict[str, Any]):
        self.storage_type = storage_type
        self.storage = self._create_storage(storage_type, config)
    
    def _create_storage(self, storage_type: StorageType, config: Dict[str, Any]):
        """Factory to create the appropriate storage."""
        if storage_type == StorageType.FILESYSTEM:
            from config.storage.filesystem_storage import FilesystemStorage
            return FilesystemStorage(config)
        elif storage_type == StorageType.GIT:
            from config.storage.git_storage import GitStorage
            return GitStorage(config)
        elif storage_type == StorageType.SQLITE:
            from config.storage.sqlite_storage import SqliteStorage
            return SqliteStorage(config)
        # ...
    
    def save_flow(self, flow_id: str, config: Dict[str, Any]) -> bool:
        return self.storage.save_flow(flow_id, config)
    
    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        return self.storage.load_flow(flow_id)
    
    def delete_flow(self, flow_id: str) -> bool:
        return self.storage.delete_flow(flow_id)
    
    def list_flows(self) -> List[str]:
        return self.storage.list_flows()
```

### 9.2. Global Configuration File

```python
# config/config.py

from dataclasses import dataclass
from typing import Dict, Any
from enum import Enum

class StorageType(Enum):
    FILESYSTEM = "filesystem"
    GIT = "git"
    SQLITE = "sqlite"
    POSTGRES = "postgres"

@dataclass
class Config:
    """Global application configuration."""
    
    # Storage
    storage_type: StorageType = StorageType.FILESYSTEM
    storage_config: Dict[str, Any] = None
    
    # Paths
    flows_path: str = "./flows"
    tasks_path: str = "./tasks"
    services_path: str = "./services"
    logs_path: str = "./logs"
    
    # Runtime
    max_workers: int = 10
    max_retries: int = 3
    retry_delay: int = 5
    timeout: int = 300
    
    # GUI
    gui_host: str = "0.0.0.0"
    gui_port: int = 8501
    
    # Global variables
    global_variables: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.storage_config is None:
            self.storage_config = {}
        if self.global_variables is None:
            self.global_variables = {}
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Config':
        """Create Config from a dictionary."""
        return cls(**data)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'storage_type': self.storage_type.value,
            'storage_config': self.storage_config,
            'flows_path': self.flows_path,
            'tasks_path': self.tasks_path,
            'services_path': self.services_path,
            'logs_path': self.logs_path,
            'max_workers': self.max_workers,
            'max_retries': self.max_retries,
            'retry_delay': self.retry_delay,
            'timeout': self.timeout,
            'gui_host': self.gui_host,
            'gui_port': self.gui_port,
            'global_variables': self.global_variables
        }
```

---

## 10. Runtime Variable Management

### 10.1. Variable System

```python
from typing import Dict, Any, Optional
from jinja2 import Template

class VariableType(Enum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    SECRET = "secret"
    REFERENCE = "reference"
    JSON = "json"

class Variable:
    """Representation of a variable."""
    
    def __init__(
        self,
        name: str,
        var_type: VariableType,
        default: Any = None,
        description: str = "",
        required: bool = False,
        scope: str = "flow"  # flow, task, global
    ):
        self.name = name
        self.var_type = var_type
        self.default = default
        self.description = description
        self.required = required
        self.scope = scope
        self.value: Optional[Any] = None
    
    def resolve(self, context: Dict[str, Any]) -> Any:
        """Resolve the value in a context."""
        # 1. Look in the context
        if self.name in context:
            self.value = context[self.name]
        # 2. Use the default value
        elif self.default is not None:
            self.value = self.default
        # 3. Raise error if required
        elif self.required:
            raise ValueError(f"Required variable not defined: {self.name}")
        # 4. None if not required
        else:
            self.value = None
        
        return self.value
    
    def validate(self, value: Any) -> List[str]:
        """Validate a value for the variable."""
        errors = []
        
        if value is None:
            if self.required:
                errors.append(f"Required variable: {self.name}")
            return errors
        
        # Type validation
        if self.var_type == VariableType.INTEGER:
            if not isinstance(value, int):
                errors.append(f"Expected type: integer, received: {type(value)}")
        elif self.var_type == VariableType.FLOAT:
            if not isinstance(value, (int, float)):
                errors.append(f"Expected type: float, received: {type(value)}")
        # ... other types
        
        return errors

class VariableResolver:
    """Variable resolver for parameters."""
    
    _variables: Dict[str, Variable] = {}
    _context: Dict[str, Any] = {}
    
    @classmethod
    def register_variables(cls, variables: Dict[str, Variable]):
        """Register a flow's variables."""
        cls._variables.update(variables)
    
    @classmethod
    def set_context(cls, context: Dict[str, Any]):
        """Set the resolution context."""
        cls._context = context
    
    @classmethod
    def resolve(cls, value: str) -> Any:
        """
        Resolve a string containing variables.
        
        Example: "Hello ${name}!" -> "Hello John!"
        """
        if not isinstance(value, str) or '${' not in value:
            return value
        
        template = Template(value)
        return template.render(cls._context)
    
    @classmethod
    def resolve_all(cls, config: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve all variables in a configuration."""
        resolved = {}
        for key, value in config.items():
            if isinstance(value, str):
                resolved[key] = cls.resolve(value)
            elif isinstance(value, dict):
                resolved[key] = cls.resolve_all(value)
            elif isinstance(value, list):
                resolved[key] = [
                    cls.resolve(v) if isinstance(v, str) else v
                    for v in value
                ]
            else:
                resolved[key] = value
        return resolved
```

### 10.2. Parameter Overrides

```python
class ParameterOverride:
    """Allows overriding parameters at runtime."""
    
    def __init__(self, flow_id: str):
        self.flow_id = flow_id
        self.overrides: Dict[str, Dict[str, Any]] = {}
    
    def set_task_parameter(self, task_id: str, param_name: str, value: Any):
        """Override a task parameter."""
        if task_id not in self.overrides:
            self.overrides[task_id] = {}
        self.overrides[task_id][param_name] = value
    
    def set_flow_parameter(self, param_name: str, value: Any):
        """Override a flow parameter."""
        if 'flow' not in self.overrides:
            self.overrides['flow'] = {}
        self.overrides['flow'][param_name] = value
    
    def apply(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply overrides to a configuration."""
        import copy
        resolved = copy.deepcopy(config)
        
        # Apply flow overrides
        if 'flow' in self.overrides:
            for key, value in self.overrides['flow'].items():
                resolved[key] = value
        
        # Apply task overrides
        if 'tasks' in resolved and 'tasks' in self.overrides:
            for task_id, task_config in resolved['tasks'].items():
                if task_id in self.overrides['tasks']:
                    for key, value in self.overrides['tasks'][task_id].items():
                        if isinstance(task_config, dict) and key in task_config:
                            task_config[key] = value
        
        return resolved
```

---

*(The document continues in the following files...)*
