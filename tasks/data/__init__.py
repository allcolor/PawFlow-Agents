# Data Tasks

"""
Modules Data pour PawFlow.
Tâches pour le traitement, la transformation et la validation de données.
"""

from tasks.data.transform_json import TransformJSONTask
from tasks.data.evaluate_jsonpath import EvaluateJSONPathTask
from tasks.data.extract_text import ExtractTextTask
from tasks.data.compress_content import CompressContentTask
from tasks.data.validate_json import ValidateJSONTask
from tasks.data.convert_charset import ConvertCharsetTask
from tasks.data.filter_content import FilterContentTask
from tasks.data.base64_encode import Base64EncodeTask
from tasks.data.count_text import CountTextTask
from tasks.data.convert_csv import ConvertCSVToJSONTask, ConvertJSONToCSVTask
from tasks.data.execute_sql import ExecuteSQLTask, PutSQLTask
from tasks.data.cache_tasks import PutCacheTask, GetCacheTask
from tasks.data.dist_cache_tasks import FetchDistributedMapCacheTask, PutDistributedMapCacheTask
from tasks.data.detect_duplicate import DetectDuplicateTask
from tasks.data.attributes_to_json import AttributesToJSONTask
from tasks.data.split_json import SplitJSONTask
from tasks.data.infer_llm import InferLLMTask
from tasks.data.parse_xml import ParseXMLTask, TransformXMLTask
from tasks.data.convert_avro_parquet import (
    ConvertAvroToJSONTask, ConvertJSONToAvroTask,
    ConvertParquetToJSONTask, ConvertJSONToParquetTask,
)

__all__ = [
    'TransformJSONTask', 'EvaluateJSONPathTask', 'ExtractTextTask',
    'CompressContentTask', 'ValidateJSONTask', 'ConvertCharsetTask',
    'FilterContentTask', 'Base64EncodeTask', 'CountTextTask',
    'ConvertCSVToJSONTask', 'ConvertJSONToCSVTask',
    'ExecuteSQLTask', 'PutSQLTask',
    'PutCacheTask', 'GetCacheTask',
    'FetchDistributedMapCacheTask', 'PutDistributedMapCacheTask',
    'DetectDuplicateTask', 'AttributesToJSONTask', 'SplitJSONTask',
    'InferLLMTask', 'ParseXMLTask', 'TransformXMLTask',
    'ConvertAvroToJSONTask', 'ConvertJSONToAvroTask',
    'ConvertParquetToJSONTask', 'ConvertJSONToParquetTask',
]