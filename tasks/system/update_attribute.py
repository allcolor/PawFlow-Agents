# UpdateAttribute Task

"""
Task UpdateAttribute - Modify FlowFile attributes.
"""

from typing import Dict, Any, List
import re
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class UpdateAttributeTask(BaseTask):
    """Add, modify, or delete attributes on a FlowFile."""

    TYPE = "updateAttribute"
    VERSION = "1.0.0"
    NAME = "UpdateAttribute"
    DESCRIPTION = "Modify FlowFile attributes"
    ICON = "edit"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        # Save original values before resolution
        self._raw_attributes_to_set = config.get('set', {})
        self._raw_attributes_to_delete = config.get('delete', [])

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Modify FlowFile attributes."""
        # Remove attributes
        for key in self._raw_attributes_to_delete:
            flowfile.delete_attribute(key)

        # Add/modify attributes with resolution on the FlowFile
        for key, value in self._raw_attributes_to_set.items():
            resolved = self._resolve_attribute_value(flowfile, str(value))
            flowfile.set_attribute(key, resolved)

        return [flowfile]

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        """Resolve references to other attributes in the value."""
        if '${' not in value:
            return value

        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)

        # Loop to resolve cascading references
        result = value
        max_iterations = 10
        for _ in range(max_iterations):
            new_result = re.sub(r'\$\{([^}]+)\}', replace_ref, result)
            if new_result == result:
                break
            result = new_result

        return result

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'set': {
                'type': 'map', 'required': False,
                'description': 'Attributes to add/modify (key -> value)',
            },
            'delete': {
                'type': 'list', 'required': False,
                'description': 'Attributes to remove',
            },
        }


# Register in the factory
TaskFactory.register(UpdateAttributeTask)