# Hash Content Task

"""
Task HashContent - Hash FlowFile content.
"""

import hashlib
from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class HashContentTask(BaseTask):
    """Hash FlowFile content with different algorithms."""

    TYPE = "hashContent"
    VERSION = "1.0.0"
    NAME = "Hash Content"
    DESCRIPTION = "Hash FlowFile content"
    ICON = "lock"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.algorithm = self.config.get('algorithm', 'sha256').lower()
        self.attribute_name = self.config.get('attribute_name', 'content.hash')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Hash the FlowFile content."""
        content = flowfile.get_content()

        hasher = hashlib.new(self.algorithm) if self.algorithm in ('md5', 'sha1', 'sha256', 'sha512') else hashlib.sha256()
        hasher.update(content)
        flowfile.set_attribute(self.attribute_name, hasher.hexdigest())

        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'algorithm': {
                'type': 'select',
                'required': False,
                'description': "Algorithme de hachage",
                'options': ['md5', 'sha1', 'sha256', 'sha512'],
                'default': 'sha256'
            },
            'attribute_name': {
                'type': 'string',
                'required': False,
                'description': "Attribute name used to store the hash",
                'default': 'content.hash'
            }
        }


# Register in the factory
TaskFactory.register(HashContentTask)
