# Duplicate Content Task

"""
Task DuplicateContent - Create FlowFile copies.
"""

from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class DuplicateContentTask(BaseTask):
    """Create copies of FlowFile content."""

    TYPE = "duplicateContent"
    VERSION = "1.0.0"
    NAME = "Duplicate Content"
    DESCRIPTION = "Create copies of FlowFile content"
    ICON = "copy"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.copies = self.config.get('copies', 2)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Create copies of the FlowFile."""
        content = flowfile.get_content()
        attributes = dict(flowfile.get_attributes())
        result = []

        for i in range(self.copies):
            attrs = attributes.copy()
            attrs['copy.index'] = str(i)
            new_flowfile = FlowFile(content=content, attributes=attrs)
            result.append(new_flowfile)

        return result

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'copies': {
                'type': 'integer',
                'required': False,
                'description': 'Number of copies to create',
                'default': 2,
                'min': 1
            }
        }


# Register in the factory
TaskFactory.register(DuplicateContentTask)
