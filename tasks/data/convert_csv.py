# CSV Conversion Tasks

"""
Conversion CSV <-> JSON.
"""

import csv
import json
from io import StringIO
from typing import Dict, Any, List
from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask


class ConvertCSVToJSONTask(BaseTask):
    """Convertir du CSV en JSON."""

    TYPE = "convertCSVToJSON"
    VERSION = "1.0.0"
    NAME = "Convert CSV to JSON"
    DESCRIPTION = "Convertir du contenu CSV en JSON"
    ICON = "file-text"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.delimiter = self.config.get("delimiter", ",")
        self.has_header = self.config.get("has_header", True)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        content = flowfile.get_content().decode('utf-8')

        if self.has_header:
            reader = csv.DictReader(StringIO(content), delimiter=self.delimiter)
            result = list(reader)
        else:
            reader = csv.reader(StringIO(content), delimiter=self.delimiter)
            result = [row for row in reader]

        json_output = json.dumps(result, ensure_ascii=False, indent=2)
        flowfile.set_content(json_output.encode('utf-8'))
        flowfile.set_attribute('mime.type', 'application/json')
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'delimiter': {
                'type': 'string', 'required': False, 'default': ',',
                'description': 'Caractère de séparation CSV',
            },
            'has_header': {
                'type': 'boolean', 'required': False, 'default': True,
                'description': 'Le CSV contient une ligne d\'en-tête',
            },
        }


class ConvertJSONToCSVTask(BaseTask):
    """Convertir du JSON en CSV."""

    TYPE = "convertJSONToCSV"
    VERSION = "1.0.0"
    NAME = "Convert JSON to CSV"
    DESCRIPTION = "Convertir du contenu JSON en CSV"
    ICON = "file-text"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.delimiter = self.config.get("delimiter", ",")
        self.include_header = self.config.get("include_header", True)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        content = flowfile.get_content().decode('utf-8')

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise TaskError(f"JSON invalide: {e}")

        if not isinstance(data, list):
            raise TaskError("Le JSON doit être un tableau pour la conversion CSV")

        output = StringIO()
        writer = csv.writer(output, delimiter=self.delimiter)

        if data:
            if isinstance(data[0], dict):
                keys = list(data[0].keys())
                if self.include_header:
                    writer.writerow(keys)
                for row in data:
                    writer.writerow([row.get(k, '') for k in keys])
            elif isinstance(data[0], list):
                for row in data:
                    writer.writerow(row)
            else:
                raise TaskError("Structure JSON non supportée pour CSV")

        flowfile.set_content(output.getvalue().encode('utf-8'))
        flowfile.set_attribute('mime.type', 'text/csv')
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'delimiter': {
                'type': 'string', 'required': False, 'default': ',',
                'description': 'Caractère de séparation CSV',
            },
            'include_header': {
                'type': 'boolean', 'required': False, 'default': True,
                'description': 'Inclure la ligne d\'en-tête',
            },
        }


TaskFactory.register(ConvertCSVToJSONTask)
TaskFactory.register(ConvertJSONToCSVTask)
