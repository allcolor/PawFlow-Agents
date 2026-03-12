# Evaluate JSONPath Task

"""
Tâche EvaluateJSONPath - Évaluer des expressions JSONPath simples sur du contenu JSON.
"""

from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class EvaluateJSONPathTask(BaseTask):
    """Évaluer des expressions JSONPath simples (dot notation) sur du contenu JSON."""

    TYPE = "evaluateJSONPath"
    VERSION = "1.0.0"
    NAME = "Evaluate JSONPath"
    DESCRIPTION = "Évaluer des expressions JSONPath simples sur du contenu JSON"
    ICON = "code"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.expressions = self.config.get('expressions', {})
        self.destination = self.config.get('destination', 'attribute')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Évaluer les expressions JSONPath sur le contenu du FlowFile."""
        content = flowfile.get_content().decode('utf-8')
        data = self.parse_json(content)

        results = {}
        for attr_name, path in self.expressions.items():
            result = self._resolve_dot_path(data, path)
            results[attr_name] = result

        if self.destination == 'attribute':
            for attr_name, value in results.items():
                if value is not None:
                    if isinstance(value, (dict, list)):
                        value = self.serialize_json(value)
                    flowfile.set_attribute(attr_name, str(value))
        elif self.destination == 'content':
            content_json = self.serialize_json(results)
            flowfile.set_content(content_json.encode('utf-8'))

        return [flowfile]

    def _resolve_dot_path(self, data: Any, path: str) -> Any:
        """Résoudre un chemin en notation point (ex: "user.name", "items.0.id")."""
        if not path:
            return data

        segments = path.split('.')
        current = data

        for segment in segments:
            if current is None:
                return None

            if isinstance(current, list):
                try:
                    index = int(segment)
                    if 0 <= index < len(current):
                        current = current[index]
                    else:
                        return None
                except ValueError:
                    return None
            elif isinstance(current, dict):
                if segment in current:
                    current = current[segment]
                else:
                    return None
            else:
                return None

        return current

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'expressions': {
                'type': 'map',
                'required': True,
                'description': 'Expressions JSONPath (clé: nom attribut, valeur: chemin)',
                'help': 'Notation point: "user.name", "items.0.id", "address.city"'
            },
            'destination': {
                'type': 'select',
                'required': False,
                'description': 'Destination des résultats',
                'options': ['attribute', 'content'],
                'default': 'attribute'
            }
        }


# Enregistrement dans la factory
TaskFactory.register(EvaluateJSONPathTask)
