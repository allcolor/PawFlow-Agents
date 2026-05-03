# Avro / Parquet Conversion Tasks

"""Tasks ConvertAvro / ConvertParquet - columnar format conversion."""

import json
import logging
from io import BytesIO
from typing import Dict, Any, List

import fastavro
import pyarrow
import pyarrow.parquet

from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class ConvertAvroToJSONTask(BaseTask):
    """Convert Avro content to JSON."""

    TYPE = "convertAvroToJSON"
    VERSION = "1.0.0"
    NAME = "Convert Avro to JSON"
    DESCRIPTION = "Convertir du contenu Avro binaire en JSON"
    ICON = "file-text"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.pretty = self.config.get('pretty', True)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        try:
            reader = fastavro.reader(BytesIO(flowfile.get_content()))
            records = list(reader)
        except Exception as e:
            raise TaskError(f"convertAvroToJSON: invalid Avro data: {e}")

        indent = 2 if self.pretty else None
        json_output = json.dumps(records, ensure_ascii=False, indent=indent, default=str)
        flowfile.set_content(json_output.encode('utf-8'))
        flowfile.set_attribute('mime.type', 'application/json')
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'pretty': {'type': 'boolean', 'required': False, 'default': True,
                       'description': 'Indenter le JSON de sortie'},
        }


class ConvertJSONToAvroTask(BaseTask):
    """Convert JSON to binary Avro."""

    TYPE = "convertJSONToAvro"
    VERSION = "1.0.0"
    NAME = "Convert JSON to Avro"
    DESCRIPTION = "Convertir du contenu JSON en Avro binaire"
    ICON = "file-text"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.schema_text = self.config.get('avro_schema', '')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        try:
            records = json.loads(flowfile.get_content().decode('utf-8'))
        except json.JSONDecodeError as e:
            raise TaskError(f"convertJSONToAvro: invalid JSON: {e}")

        if not isinstance(records, list):
            records = [records]

        if self.schema_text:
            try:
                schema = json.loads(self.schema_text)
                parsed_schema = fastavro.parse_schema(schema)
            except Exception as e:
                raise TaskError(f"convertJSONToAvro: invalid Avro schema: {e}")
        else:
            # Auto-infer schema from first record
            parsed_schema = self._infer_schema(records[0] if records else {})

        try:
            output = BytesIO()
            fastavro.writer(output, parsed_schema, records)
            flowfile.set_content(output.getvalue())
        except Exception as e:
            raise TaskError(f"convertJSONToAvro: write error: {e}")

        flowfile.set_attribute('mime.type', 'application/avro')
        return [flowfile]

    def _infer_schema(self, record: dict) -> dict:
        """Infer a simple Avro schema from a dict record."""
        type_map = {str: 'string', int: 'long', float: 'double', bool: 'boolean'}
        fields = []
        for k, v in record.items():
            avro_type = type_map.get(type(v), 'string')
            fields.append({'name': k, 'type': ['null', avro_type], 'default': None})
        schema = {'type': 'record', 'name': 'AutoInferred', 'fields': fields}
        return fastavro.parse_schema(schema)

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'avro_schema': {'type': 'string', 'required': False,
                           'description': 'Avro schema as JSON (auto-inferred if empty)'},
        }


class ConvertParquetToJSONTask(BaseTask):
    """Convert Parquet content to JSON."""

    TYPE = "convertParquetToJSON"
    VERSION = "1.0.0"
    NAME = "Convert Parquet to JSON"
    DESCRIPTION = "Convertir du contenu Parquet en JSON"
    ICON = "file-text"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.pretty = self.config.get('pretty', True)
        self.columns = self.config.get('columns', '')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        try:
            columns = [c.strip() for c in self.columns.split(',') if c.strip()] or None
            table = pyarrow.parquet.read_table(BytesIO(flowfile.get_content()), columns=columns)
            records = table.to_pydict()
            # Convert column-oriented to row-oriented
            if records:
                keys = list(records.keys())
                n_rows = len(records[keys[0]]) if keys else 0
                rows = [{k: records[k][i] for k in keys} for i in range(n_rows)]
            else:
                rows = []
        except Exception as e:
            raise TaskError(f"convertParquetToJSON: {e}")

        indent = 2 if self.pretty else None
        json_output = json.dumps(rows, ensure_ascii=False, indent=indent, default=str)
        flowfile.set_content(json_output.encode('utf-8'))
        flowfile.set_attribute('mime.type', 'application/json')
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'pretty': {'type': 'boolean', 'required': False, 'default': True},
            'columns': {'type': 'string', 'required': False,
                       'description': 'Columns to read (comma-separated, empty = all)'},
        }


class ConvertJSONToParquetTask(BaseTask):
    """Convert JSON to Parquet."""

    TYPE = "convertJSONToParquet"
    VERSION = "1.0.0"
    NAME = "Convert JSON to Parquet"
    DESCRIPTION = "Convertir du contenu JSON en Parquet"
    ICON = "file-text"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.compression = self.config.get('compression', 'snappy')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        try:
            records = json.loads(flowfile.get_content().decode('utf-8'))
        except json.JSONDecodeError as e:
            raise TaskError(f"convertJSONToParquet: invalid JSON: {e}")

        if not isinstance(records, list):
            records = [records]

        try:
            # Convert row-oriented to column-oriented
            if records:
                keys = list(records[0].keys())
                columns = {k: [r.get(k) for r in records] for k in keys}
                table = pyarrow.table(columns)
            else:
                table = pyarrow.table({})

            output = BytesIO()
            pyarrow.parquet.write_table(table, output, compression=self.compression)
            flowfile.set_content(output.getvalue())
        except Exception as e:
            raise TaskError(f"convertJSONToParquet: {e}")

        flowfile.set_attribute('mime.type', 'application/parquet')
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'compression': {'type': 'string', 'required': False, 'default': 'snappy',
                           'enum': ['none', 'snappy', 'gzip', 'lz4', 'zstd'],
                           'description': 'Algorithme de compression Parquet'},
        }


TaskFactory.register(ConvertAvroToJSONTask)
TaskFactory.register(ConvertJSONToAvroTask)
TaskFactory.register(ConvertParquetToJSONTask)
TaskFactory.register(ConvertJSONToParquetTask)
