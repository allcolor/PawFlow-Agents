# Count Text Task

"""
Tâche CountText - Compter les lignes, mots et caractères du contenu.
"""

from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class CountTextTask(BaseTask):
    """Compter les lignes, mots et caractères du contenu d'un FlowFile."""

    TYPE = "countText"
    VERSION = "1.0.0"
    NAME = "Count Text"
    DESCRIPTION = "Compter les lignes, mots et caractères du contenu d'un FlowFile"
    ICON = "count"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Compter les lignes, mots et caractères du contenu du FlowFile."""
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


# Enregistrement dans la factory
TaskFactory.register(CountTextTask)
