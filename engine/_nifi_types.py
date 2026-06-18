"""Static NiFi→PawFlow mapping tables and conversion result dataclasses.

Extracted from nifi_converter.py to keep each module <=800 lines. The
NiFiConverter class imports these names; nifi_converter re-exports them so the
public import path (engine.nifi_converter) stays unchanged.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List



# ============================================================================
# NiFi processor → PawFlow task mapping
# ============================================================================

PROCESSOR_MAP: Dict[str, str] = {
    # IO
    "org.apache.nifi.processors.standard.GetFile": "getFile",
    "org.apache.nifi.processors.standard.PutFile": "putFile",
    "org.apache.nifi.processors.standard.FetchFile": "getFile",
    "org.apache.nifi.processors.standard.InvokeHTTP": "fetchHTTP",
    "org.apache.nifi.processors.standard.GetHTTP": "fetchHTTP",
    "org.apache.nifi.processors.standard.PostHTTP": "fetchHTTP",
    "org.apache.nifi.processors.standard.HandleHttpRequest": "listenHTTP",
    "org.apache.nifi.processors.standard.HandleHttpResponse": "listenHTTP",
    "org.apache.nifi.processors.standard.ListFile": "listFiles",
    "org.apache.nifi.processors.standard.ListenHTTP": "listenHTTP",
    # Data transformation
    "org.apache.nifi.processors.standard.TransformJSON": "transformJSON",
    "org.apache.nifi.processors.standard.EvaluateJsonPath": "evaluateJSONPath",
    "org.apache.nifi.processors.standard.JoltTransformJSON": "transformJSON",
    "org.apache.nifi.processors.standard.SplitJson": "splitJSON",
    "org.apache.nifi.processors.standard.MergeContent": "mergeContent",
    "org.apache.nifi.processors.standard.SplitContent": "splitContent",
    "org.apache.nifi.processors.standard.CompressContent": "compressContent",
    "org.apache.nifi.processors.standard.ConvertCharacterSet": "convertCharset",
    "org.apache.nifi.processors.standard.ReplaceText": "replace_text",
    "org.apache.nifi.processors.standard.ExtractText": "extractText",
    "org.apache.nifi.processors.standard.ValidateRecord": "validateJSON",
    "org.apache.nifi.processors.standard.Base64EncodeContent": "base64Encode",
    "org.apache.nifi.processors.standard.ConvertRecord": "transformJSON",
    # CSV/JSON
    "org.apache.nifi.processors.standard.ConvertCSVToJSON": "convertCSVToJSON",
    "org.apache.nifi.processors.standard.ConvertJSONToCSV": "convertJSONToCSV",
    # Control
    "org.apache.nifi.processors.standard.RouteOnAttribute": "routeOnAttribute",
    "org.apache.nifi.processors.standard.RouteOnContent": "filterContent",
    "org.apache.nifi.processors.standard.UpdateAttribute": "updateAttribute",
    "org.apache.nifi.processors.standard.DuplicateFlowFile": "duplicateContent",
    "org.apache.nifi.processors.standard.ControlRate": "controlRate",
    "org.apache.nifi.processors.standard.GenerateFlowFile": "generateFlowFile",
    # Hash
    "org.apache.nifi.processors.standard.HashContent": "hashContent",
    "org.apache.nifi.processors.standard.HashAttribute": "hashContent",
    # SQL
    "org.apache.nifi.processors.standard.ExecuteSQL": "executeSQL",
    "org.apache.nifi.processors.standard.PutSQL": "putSQL",
    "org.apache.nifi.processors.standard.PutDatabaseRecord": "putSQL",
    "org.apache.nifi.processors.standard.ExecuteSQLRecord": "executeSQL",
    # Cache
    "org.apache.nifi.processors.standard.PutDistributedMapCache": "putDistributedMapCache",
    "org.apache.nifi.processors.standard.FetchDistributedMapCache": "fetchDistributedMapCache",
    # Dedup
    "org.apache.nifi.processors.standard.DetectDuplicate": "detectDuplicate",
    # Log/System
    "org.apache.nifi.processors.standard.LogAttribute": "log",
    "org.apache.nifi.processors.standard.LogMessage": "log",
    "org.apache.nifi.processors.standard.Wait": "wait",
    "org.apache.nifi.processors.standard.Notify": "notify",
    # Script
    "org.apache.nifi.processors.standard.ExecuteScript": "executeScript",
    "org.apache.nifi.processors.groovyx.ExecuteGroovyScript": "executeScript",
    "org.apache.nifi.processors.script.ExecuteScript": "executeScript",
    # Attributes
    "org.apache.nifi.processors.standard.AttributesToJSON": "attributesToJSON",
    # Funnel
    "org.apache.nifi.processors.standard.Funnel": "funnel",
    # Communication
    "org.apache.nifi.processors.standard.PutEmail": "sendEmail",
    # SFTP
    "org.apache.nifi.processors.standard.GetSFTP": "getSFTP",
    "org.apache.nifi.processors.standard.PutSFTP": "putSFTP",
    "org.apache.nifi.processors.standard.FetchSFTP": "getSFTP",
    # Kafka
    "org.apache.nifi.processors.kafka.pubsub.PublishKafka": "publishKafka",
    "org.apache.nifi.processors.kafka.pubsub.ConsumeKafka": "consumeKafka",
    "org.apache.nifi.processors.kafka.pubsub.PublishKafka_2_6": "publishKafka",
    "org.apache.nifi.processors.kafka.pubsub.ConsumeKafka_2_6": "consumeKafka",
    # S3
    "org.apache.nifi.processors.aws.s3.PutS3Object": "putS3",
    "org.apache.nifi.processors.aws.s3.FetchS3Object": "getS3",
    "org.apache.nifi.processors.aws.s3.ListS3": "getS3",
    # MQTT
    "org.apache.nifi.processors.mqtt.PublishMQTT": "publishMQTT",
    "org.apache.nifi.processors.mqtt.ConsumeMQTT": "consumeMQTT",
    # XML
    "org.apache.nifi.processors.standard.TransformXml": "transformXML",
    # Avro/Parquet
    "org.apache.nifi.processors.avro.ConvertAvroToJSON": "convertAvroToJSON",
    "org.apache.nifi.processors.parquet.ConvertParquetToJSON": "convertParquetToJSON",
    # GCS
    "org.apache.nifi.processors.gcp.storage.PutGCSObject": "putGCS",
    "org.apache.nifi.processors.gcp.storage.FetchGCSObject": "getGCS",
    "org.apache.nifi.processors.gcp.storage.ListGCSBucket": "getGCS",
    # Azure
    "org.apache.nifi.processors.azure.storage.PutAzureBlobStorage": "putAzureBlob",
    "org.apache.nifi.processors.azure.storage.FetchAzureBlobStorage": "getAzureBlob",
    "org.apache.nifi.processors.azure.storage.ListAzureBlobStorage": "getAzureBlob",
    "org.apache.nifi.processors.azure.storage.PutAzureBlobStorage_v12": "putAzureBlob",
    "org.apache.nifi.processors.azure.storage.FetchAzureBlobStorage_v12": "getAzureBlob",
    # Ports (process group boundaries)
    "org.apache.nifi.processors.standard.InputPort": "inputPort",
    "org.apache.nifi.processors.standard.OutputPort": "outputPort",
}

# Short name fallback: if full class path not found, try just the class name
PROCESSOR_SHORT_MAP: Dict[str, str] = {}
for full_name, pyfi_type in PROCESSOR_MAP.items():
    short = full_name.rsplit(".", 1)[-1]
    PROCESSOR_SHORT_MAP[short] = pyfi_type

# NiFi controller service → PawFlow service mapping
SERVICE_MAP: Dict[str, str] = {
    "org.apache.nifi.dbcp.DBCPConnectionPool": "dbConnectionPool",
    "org.apache.nifi.distributed.cache.client.DistributedMapCacheClientService": "distributedMapCache",
    "org.apache.nifi.ssl.StandardSSLContextService": None,  # No direct equivalent
}

# NiFi relationship → PawFlow relation type
RELATIONSHIP_MAP: Dict[str, str] = {
    "success": "success",
    "failure": "failure",
    "retry": "retry",
    "original": "original",
    "matched": "matched",
    "unmatched": "unmatched",
    "response": "success",
    "no retry": "failure",
    "comms.failure": "failure",
}


@dataclass
class ConversionWarning:
    """Warning generated during conversion."""
    level: str  # "info", "warning", "error"
    processor_id: str
    processor_type: str
    message: str


@dataclass
class ConversionResult:
    """Result of NiFi → PawFlow conversion."""
    flow: Dict[str, Any]
    warnings: List[ConversionWarning] = field(default_factory=list)
    unmapped_processors: List[str] = field(default_factory=list)
    script_processors: List[Dict[str, Any]] = field(default_factory=list)
    subflows: List[Dict[str, Any]] = field(default_factory=list)
    success: bool = True

