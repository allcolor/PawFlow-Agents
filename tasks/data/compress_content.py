# Compress Content Task

"""
Task CompressContent - Compress or decompress FlowFile content.
"""

import gzip
import zlib
from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class CompressContentTask(BaseTask):
    """Compress or decompress FlowFile content."""

    TYPE = "compressContent"
    VERSION = "1.0.0"
    NAME = "Compress Content"
    DESCRIPTION = "Compress or decompress FlowFile content"
    ICON = "compress"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.mode = self.config.get('mode', 'compress')
        self.algorithm = self.config.get('algorithm', 'gzip')
        self.level = self.config.get('level', 6)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Compress or decompress the FlowFile content."""
        content = flowfile.get_content()

        if self.mode == 'compress':
            if self.algorithm == 'gzip':
                result = gzip.compress(content, compresslevel=self.level)
                flowfile.set_attribute('mime.type', 'application/gzip')
            else:
                result = zlib.compress(content, level=self.level)
                flowfile.set_attribute('mime.type', 'application/x-zlib')
        else:
            if self.algorithm == 'gzip':
                result = gzip.decompress(content)
            else:
                result = zlib.decompress(content)
            flowfile.set_attribute('mime.type', 'application/octet-stream')

        flowfile.set_content(result)
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'mode': {
                'type': 'select',
                'required': False,
                'description': "Operation mode",
                'options': ['compress', 'decompress'],
                'default': 'compress'
            },
            'algorithm': {
                'type': 'select',
                'required': False,
                'description': 'Algorithme de compression',
                'options': ['gzip', 'zlib'],
                'default': 'gzip'
            },
            'level': {
                'type': 'integer',
                'required': False,
                'description': 'Niveau de compression (1-9)',
                'default': 6,
                'min': 1,
                'max': 9
            }
        }


# Register in the factory
TaskFactory.register(CompressContentTask)
