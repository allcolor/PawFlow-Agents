# SplitContent Task

"""
Task SplitContent - Split a FlowFile into multiple FlowFiles.
"""

from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class SplitContentTask(BaseTask):
    """Split FlowFile content into multiple FlowFiles."""

    TYPE = "splitContent"
    VERSION = "1.0.0"
    NAME = "SplitContent"
    DESCRIPTION = "Split a FlowFile by separator"
    ICON = "scissors"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.separator = self.config.get('separator', '\n')
        self.keep_separator = self.config.get('keep_separator', False)
        self.max_splits = self.config.get('max_splits', 0)  # 0 = unlimited

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Split content into multiple fragments."""
        content = flowfile.get_content().decode('utf-8')
        sep = self.separator

        if self.max_splits > 0:
            parts = content.split(sep, self.max_splits)
        else:
            parts = content.split(sep)

        if self.keep_separator and len(parts) > 1:
            parts = [p + sep for p in parts[:-1]] + [parts[-1]]

        results = []
        for i, part in enumerate(parts):
            if not part and not self.keep_separator:
                continue
            ff = flowfile.clone()
            ff.set_content(part.encode('utf-8'))
            ff.set_attribute('fragment.index', str(i))
            ff.set_attribute('fragment.count', str(len(parts)))
            ff.set_attribute('fileSize', str(len(part.encode('utf-8'))))
            results.append(ff)

        return results if results else [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'separator': {
                'type': 'string', 'required': False, 'default': '\\n',
                'description': 'Separator (\\n, ;, \\t, etc.)',
            },
            'keep_separator': {
                'type': 'boolean', 'required': False, 'default': False,
                'description': 'Keep the separator in fragments',
            },
            'max_splits': {
                'type': 'integer', 'required': False, 'default': 0,
                'description': 'Maximum number of splits (0 = unlimited)',
            },
        }


# Register in the factory
TaskFactory.register(SplitContentTask)