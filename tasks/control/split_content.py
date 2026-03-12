# SplitContent Task

"""
Tâche SplitContent - Découper un FlowFile en plusieurs.
"""

from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class SplitContentTask(BaseTask):
    """Découper le contenu d'un FlowFile en plusieurs FlowFiles."""

    TYPE = "splitContent"
    VERSION = "1.0.0"
    NAME = "SplitContent"
    DESCRIPTION = "Découper un FlowFile selon un séparateur"
    ICON = "scissors"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.separator = self.config.get('separator', '\n')
        self.keep_separator = self.config.get('keep_separator', False)
        self.max_splits = self.config.get('max_splits', 0)  # 0 = illimité

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Découper le contenu en plusieurs fragments."""
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
                'description': 'Séparateur (\\n, ;, \\t, etc.)',
            },
            'keep_separator': {
                'type': 'boolean', 'required': False, 'default': False,
                'description': 'Conserver le séparateur dans les fragments',
            },
            'max_splits': {
                'type': 'integer', 'required': False, 'default': 0,
                'description': 'Nombre max de découpes (0 = illimité)',
            },
        }


# Enregistrement dans la factory
TaskFactory.register(SplitContentTask)