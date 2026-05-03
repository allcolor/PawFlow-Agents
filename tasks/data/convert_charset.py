# Convert Charset Task

"""
Task ConvertCharset - Convert FlowFile content encoding.
"""

from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class ConvertCharsetTask(BaseTask):
    """Convert FlowFile content encoding."""

    TYPE = "convertCharset"
    VERSION = "1.0.0"
    NAME = "Convert Charset"
    DESCRIPTION = "Convert FlowFile content encoding"
    ICON = "exchange"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.source_encoding = self.config.get('source_encoding', 'utf-8')
        self.target_encoding = self.config.get('target_encoding', 'utf-8')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Convert the FlowFile content encoding."""
        content = flowfile.get_content()
        decoded_content = content.decode(self.source_encoding)
        converted_content = decoded_content.encode(self.target_encoding)
        flowfile.set_content(converted_content)
        flowfile.set_attribute('charset', self.target_encoding)
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'source_encoding': {
                'type': 'select',
                'required': False,
                'description': 'Encodage source du contenu',
                'options': ['utf-8', 'utf-16', 'latin-1', 'iso-8859-1', 'ascii', 'cp1252'],
                'default': 'utf-8'
            },
            'target_encoding': {
                'type': 'select',
                'required': False,
                'description': 'Encodage cible du contenu',
                'options': ['utf-8', 'utf-16', 'latin-1', 'iso-8859-1', 'ascii', 'cp1252'],
                'default': 'utf-8'
            }
        }


# Register in the factory
TaskFactory.register(ConvertCharsetTask)
