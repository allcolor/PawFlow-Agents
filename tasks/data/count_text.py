# Count Text Task

"""
CountText task - Count lines, words, and characters in content.
"""

from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class CountTextTask(BaseTask):
    """Count lines, words, and characters in FlowFile content."""

    TYPE = "countText"
    VERSION = "1.0.0"
    NAME = "Count Text"
    DESCRIPTION = "Count lines, words, and characters in FlowFile content"
    ICON = "count"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Count lines, words, and characters in the FlowFile content."""
        content = flowfile.get_content().decode('utf-8')

        lines = content.split('\n')
        line_count = len(lines)
        word_count = len(content.split())
        char_count = len(content)

        flowfile.set_attribute('text.line.count', str(line_count))
        flowfile.set_attribute('text.word.count', str(word_count))
        flowfile.set_attribute('text.character.count', str(char_count))

        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {}


# Register in the factory
TaskFactory.register(CountTextTask)
