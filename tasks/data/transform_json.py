# TransformJSON Task

"""
Task TransformJSON - Transform JSON content.
"""

import json
from typing import Dict, Any, List, Union
from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask


class TransformJSONTask(BaseTask):
    """Transform the JSON content of a FlowFile."""

    TYPE = "transformJSON"
    VERSION = "1.0.0"
    NAME = "TransformJSON"
    DESCRIPTION = "Transform JSON content (extraire, modifier, filtrer)"
    ICON = "braces"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.operation = self.config.get('operation', 'extract')
        self.json_path = self.config.get('json_path', '')
        self.set_values = self.config.get('set_values', {})
        self.delete_keys = self.config.get('delete_keys', [])
        self.output_format = self.config.get('output_format', 'json')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Transform the FlowFile JSON content."""
        content = flowfile.get_content()
        try:
            data = json.loads(content.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise TaskError(f"Contenu JSON invalide: {e}")

        if self.operation == 'extract':
            data = self._extract(data, self.json_path)
        elif self.operation == 'set':
            data = self._set_values(data, self.set_values)
        elif self.operation == 'delete':
            data = self._delete_keys(data, self.delete_keys)
        elif self.operation == 'flatten':
            data = self._flatten(data)

        if self.output_format == 'json':
            output = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        else:
            output = str(data).encode('utf-8')

        flowfile.set_content(output)
        flowfile.set_attribute('mime.type', 'application/json')
        flowfile.set_attribute('fileSize', str(len(output)))

        return [flowfile]

    def _extract(self, data: Any, path: str) -> Any:
        """Extract a value by path ($.key1.key2)."""
        if not path or path == '$':
            return data
        keys = path.lstrip('$.').split('.')
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            elif isinstance(current, list):
                try:
                    current = current[int(key)]
                except (ValueError, IndexError):
                    raise TaskError(f"Chemin invalide: {path}")
            else:
                raise TaskError(f"Clé non trouvée: {key} dans {path}")
        return current

    def _set_values(self, data: Any, values: Dict[str, Any]) -> Any:
        """Add/modify values."""
        if not isinstance(data, dict):
            raise TaskError("set_values requires a root JSON object")
        for key, value in values.items():
            data[key] = value
        return data

    def _delete_keys(self, data: Any, keys: List[str]) -> Any:
        """Delete keys."""
        if not isinstance(data, dict):
            raise TaskError("delete_keys requires a root JSON object")
        for key in keys:
            data.pop(key, None)
        return data

    def _flatten(self, data: Any, prefix: str = '') -> Union[Dict[str, Any], Any]:
        """Flatten a nested JSON object."""
        result = {}
        if isinstance(data, dict):
            for k, v in data.items():
                new_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, (dict, list)):
                    result.update(self._flatten(v, new_key))
                else:
                    result[new_key] = v
        elif isinstance(data, list):
            for i, v in enumerate(data):
                new_key = f"{prefix}[{i}]"
                if isinstance(v, (dict, list)):
                    result.update(self._flatten(v, new_key))
                else:
                    result[new_key] = v
        else:
            result[prefix] = data
        return result

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'operation': {
                'type': 'select', 'required': True,
                'options': ['extract', 'set', 'delete', 'flatten'],
                'description': 'Operation to perform',
            },
            'json_path': {
                'type': 'string', 'required': False, 'default': '$',
                'description': 'Chemin JSON (pour extract, ex: $.data.items)',
            },
            'set_values': {
                'type': 'map', 'required': False,
                'description': 'Values to add/modify (pour set)',
            },
            'delete_keys': {
                'type': 'list', 'required': False,
                'description': 'Keys to delete (pour delete)',
            },
            'output_format': {
                'type': 'select', 'required': False, 'default': 'json',
                'options': ['json', 'string'],
                'description': 'Format de sortie',
            },
        }


# Register in the factory
TaskFactory.register(TransformJSONTask)