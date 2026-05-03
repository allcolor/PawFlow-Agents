# Flow Service

"""
Service for flow management.
Abstraction layer between GUI and FlowParser/FlowValidator.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
import logging

from engine import FlowParser, FlowValidator
from core import Flow, FlowFile, TaskFactory, ServiceFactory, StorageManager

logger = logging.getLogger(__name__)


class FlowService:
    """Flow management service."""

    def __init__(self, storage_manager: Optional[StorageManager] = None):
        """
        Initialize the service.

        Args:
            storage_manager: Storage manager (default: FileSystem)
        """
        self.storage_manager = storage_manager or StorageManager()
        self._initialized = False

    def initialize(self):
        """Initialize the service and register all tasks/services."""
        if not self._initialized:
            # Register all tasks and services
            from tasks import register_all_tasks

            register_all_tasks()

            self._initialized = True
            logger.info("FlowService initialized")

    def parse(self, config: Dict[str, Any]) -> Flow:
        """
        Parse a flow from configuration.

        Args:
            config: Flow configuration

        Returns:
            Parsed Flow object
        """
        self.initialize()
        return FlowParser.parse(config)

    def parse_from_file(self, filepath: str) -> Flow:
        """
        Parse a flow from a JSON file.

        Args:
            filepath: Path to the JSON file

        Returns:
            Parsed Flow object
        """
        self.initialize()
        return FlowParser.parse_from_file(filepath)

    def parse_from_json(self, json_string: str) -> Flow:
        """
        Parse a flow from a JSON string.

        Args:
            json_string: JSON string

        Returns:
            Parsed Flow object
        """
        self.initialize()
        return FlowParser.parse_from_json(json_string)

    def validate(self, flow: Flow, strict: bool = True) -> List[str]:
        """
        Validate a flow.

        Args:
            flow: Flow to validate
            strict: Strict mode (raises errors) or not

        Returns:
            List of error messages (empty if valid)
        """
        self.initialize()
        return FlowValidator.validate(flow, strict)

    def save(self, flow: Flow, filepath: Optional[str] = None) -> str:
        """
        Save a flow.

        Args:
            flow: Flow to save
            filepath: Optional path (generated if not specified)

        Returns:
            Path of the saved file
        """
        self.initialize()

        if filepath is None:
            from core.paths import REPOSITORY_DIR
            pkg_dir = REPOSITORY_DIR / "flows" / "global" / "default" / flow.id
            pkg_dir.mkdir(parents=True, exist_ok=True)
            filepath = str(pkg_dir / "latest.json")

        # Create directory if it doesn't exist
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)

        # Convert flow to dict
        config = self.flow_to_dict(flow)

        # Save
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        logger.info(f"Flow saved: {filepath}")
        return filepath

    def load(self, filepath: str) -> Flow:
        """
        Load a flow from a file.

        Args:
            filepath: File path

        Returns:
            Loaded Flow object
        """
        self.initialize()
        return self.parse_from_file(filepath)

    def list_flows(self, directory: str = "flows") -> List[str]:
        """
        List all flows in a directory.

        Args:
            directory: Directory to scan

        Returns:
            List of JSON file paths
        """
        self.initialize()
        flows_dir = Path(directory)
        if not flows_dir.exists():
            return []
        return [str(f) for f in flows_dir.glob("*.json")]

    def delete(self, filepath: str) -> bool:
        """
        Delete a flow.

        Args:
            filepath: File path to delete

        Returns:
            True if successful
        """
        try:
            Path(filepath).unlink()
            logger.info(f"Flow deleted: {filepath}")
            return True
        except Exception as e:
            logger.error(f"Error deleting: {e}")
            return False

    def flow_to_dict(self, flow: Flow) -> Dict[str, Any]:
        """
        Convert a Flow object to JSON dictionary.

        Args:
            flow: Flow object

        Returns:
            JSON dictionary
        """
        return {
            "id": flow.id,
            "name": flow.name,
            "version": flow.version,
            "description": flow.description,
            "author": flow.author,
            "created_at": flow.created_at.isoformat() if hasattr(flow, 'created_at') and flow.created_at else None,
            "parameters": flow.parameters,
            "entries": flow.entries,
            "exits": flow.exits,
            "tasks": {
                task_id: {
                    "type": task.get_type(),
                    "parameters": task.config,
                }
                for task_id, task in flow.tasks.items()
            },
            "services": {
                service_id: {
                    "type": service.get_type(),
                    "parameters": service.config,
                }
                for service_id, service in flow.services.items()
            },
            "groups": flow.groups,
            "relations": flow.relations,
            "variables": flow.variables,
        }

    def dict_to_flow(self, config: Dict[str, Any]) -> Flow:
        """
        Convert a JSON dictionary to Flow object.

        Args:
            config: Configuration dictionary

        Returns:
            Flow object
        """
        return self.parse(config)

    def get_task_schema(self, task_type: str) -> Dict[str, Any]:
        """
        Get the parameter schema for a task.

        Args:
            task_type: Task type

        Returns:
            Parameter schema
        """
        self.initialize()
        try:
            task_class = TaskFactory.get(task_type)
            task_instance = task_class({})
            return task_instance.get_parameter_schema()
        except Exception as e:
            logger.error(f"Error retrieving task schema {task_type}: {e}")
            return {}

    def get_available_tasks(self) -> List[str]:
        """
        List all available tasks.

        Returns:
            List of task types
        """
        self.initialize()
        return TaskFactory.list_types()

    def get_available_services(self) -> List[str]:
        """
        List all available services.

        Returns:
            List of service types
        """
        self.initialize()
        return ServiceFactory.list_types()
