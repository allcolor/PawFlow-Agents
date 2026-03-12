# Filter Content Task

"""
Tâche FilterContent - Filtrer les lignes du contenu selon un motif regex.
"""

import re
from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class FilterContentTask(BaseTask):
    """Filtrer les lignes du contenu selon un motif regex."""

    TYPE = "filterContent"
    VERSION = "1.0.0"
    NAME = "Filter Content"
    DESCRIPTION = "Filtrer les lignes du contenu selon un motif regex"
    ICON = "filter"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.pattern = self.config.get('pattern', '')
        self.mode = self.config.get('mode', 'include')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Filtrer les lignes du contenu du FlowFile."""
        content = flowfile.get_content().decode('utf-8')
        lines = content.split('\n')
        compiled_pattern = re.compile(self.pattern)

        if self.mode == 'include':
            filtered_lines = [line for line in lines if compiled_pattern.search(line)]
        else:
            filtered_lines = [line for line in lines if not compiled_pattern.search(line)]

        flowfile.set_content('\n'.join(filtered_lines).encode('utf-8'))
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'pattern': {
                'type': 'string',
                'required': True,
                'description': "Expression regex pour filtrer les lignes"
            },
            'mode': {
                'type': 'select',
                'required': False,
                'description': "Mode de filtrage",
                'options': ['include', 'exclude'],
                'default': 'include'
            }
        }


# Enregistrement dans la factory
TaskFactory.register(FilterContentTask)
