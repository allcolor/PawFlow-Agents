"""NiFi → PawFlow flow converter.

Parses NiFi flow exports (XML templates or JSON REST API format)
and converts them to PawFlow flow dicts.

Handles:
- Processor mapping (NiFi processor type → PawFlow task type)
- Controller service mapping
- Connection/relationship extraction
- Parameter context extraction
- Process group → subflow conversion
"""

import json
import logging
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


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
    # SFTP/FTP
    "org.apache.nifi.processors.standard.GetSFTP": "getSFTP",
    "org.apache.nifi.processors.standard.PutSFTP": "putSFTP",
    "org.apache.nifi.processors.standard.GetFTP": "getFTP",
    "org.apache.nifi.processors.standard.PutFTP": "putFTP",
    "org.apache.nifi.processors.standard.FetchSFTP": "getSFTP",
    "org.apache.nifi.processors.standard.FetchFTP": "getFTP",
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


class NiFiConverter:
    """Converts NiFi flow exports to PawFlow flow dicts."""

    def convert_xml(self, xml_content: str) -> ConversionResult:
        """Convert NiFi XML template/flow to PawFlow flow dict."""
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            return ConversionResult(flow={}, success=False,
                                    warnings=[ConversionWarning("error", "", "", f"XML parse error: {e}")])

        # NiFi templates wrap in <template>, flow.xml uses <flowController> or <processGroup>
        if root.tag == "template":
            # Navigate: template → snippet → processGroup (or directly to processGroup)
            snippet = root.find(".//snippet")
            if snippet is not None:
                pg = snippet.find("processGroup")
                if pg is not None:
                    snippet = pg
            else:
                pg = root.find(".//processGroup")
                snippet = pg if pg is not None else root
        elif root.tag in ("flowController", "rootGroup", "processGroup"):
            snippet = root
        else:
            snippet = root

        return self._convert_process_group(snippet, source_format="xml")

    def convert_json(self, json_content: str) -> ConversionResult:
        """Convert NiFi JSON (REST API format) to PawFlow flow dict."""
        try:
            data = json.loads(json_content)
        except json.JSONDecodeError as e:
            return ConversionResult(flow={}, success=False,
                                    warnings=[ConversionWarning("error", "", "", f"JSON parse error: {e}")])

        # REST API wraps in processGroupFlow
        pg = data.get("processGroupFlow", data)
        flow_data = pg.get("flow", pg)

        # Pass the full pg for breadcrumb extraction
        return self._convert_json_flow(flow_data, pg_data=pg)

    def convert(self, content: str) -> ConversionResult:
        """Auto-detect format (XML or JSON) and convert."""
        content = content.strip()
        if content.startswith("<"):
            return self.convert_xml(content)
        elif content.startswith("{") or content.startswith("["):
            return self.convert_json(content)
        else:
            return ConversionResult(flow={}, success=False,
                                    warnings=[ConversionWarning("error", "", "", "Unknown format (not XML or JSON)")])

    # ========================================================================
    # XML conversion
    # ========================================================================

    def _convert_process_group(self, pg_element, source_format="xml") -> ConversionResult:
        """Convert a NiFi processGroup XML element to PawFlow flow dict.

        Handles nested process groups by converting each to a separate subflow
        and inserting an executeFlow task in the parent.
        """
        warnings = []
        unmapped = []
        script_procs = []
        subflows = []

        flow_name = self._xml_text(pg_element, "name") or "Imported NiFi Flow"
        flow_id = self._xml_text(pg_element, "id") or f"nifi_import_{uuid.uuid4().hex[:8]}"

        flow = {
            "id": flow_id,
            "name": flow_name,
            "version": "1.0.0",
            "description": f"Imported from NiFi ({source_format})",
            "author": "NiFi Import",
            "parameters": {},
            "entries": [],
            "exits": [],
            "tasks": {},
            "groups": {},
            "relations": [],
            "variables": {},
        }

        # Collect IDs of nested processGroup elements to exclude their children
        nested_pg_ids = set()
        for child_pg in pg_element.findall("processGroup"):
            pg_id = self._xml_text(child_pg, "id")
            if pg_id:
                nested_pg_ids.add(pg_id)

        # Extract processors (only direct children, not from nested process groups)
        id_map = {}  # NiFi UUID → PawFlow task_id
        for proc in self._direct_children(pg_element, "processor"):
            task_id, task_config, proc_warnings = self._convert_xml_processor(proc)
            if task_id:
                flow["tasks"][task_id] = task_config
                nifi_id = self._xml_text(proc, "id")
                if nifi_id:
                    id_map[nifi_id] = task_id
                warnings.extend(proc_warnings)

                # Track script processors
                nifi_type = self._xml_text(proc, "class") or self._xml_text(proc, "type") or ""
                if "Script" in nifi_type or "Groovy" in nifi_type:
                    script_procs.append({
                        "task_id": task_id,
                        "nifi_type": nifi_type,
                        "script": self._extract_script_from_xml(proc),
                        "language": self._extract_script_language_xml(proc),
                    })

                # Track unmapped
                if task_config.get("_unmapped"):
                    unmapped.append(nifi_type)
                    del task_config["_unmapped"]

        # Convert nested process groups → subflows + executeFlow tasks
        for child_pg in pg_element.findall("processGroup"):
            child_result = self._convert_process_group(child_pg, source_format=source_format)
            child_flow = child_result.flow
            child_name = child_flow.get("name", "subflow")
            child_id = child_flow.get("id", f"sub_{uuid.uuid4().hex[:6]}")

            # Add subflow to results
            subflows.append(child_flow)
            subflows.extend(child_result.subflows)
            warnings.extend(child_result.warnings)
            unmapped.extend(child_result.unmapped_processors)
            script_procs.extend(child_result.script_processors)

            # Create an executeFlow task in the parent for this process group
            task_id = f"subflow_{self._safe_id(child_name)}"
            base_id = task_id
            counter = 1
            while task_id in flow["tasks"]:
                counter += 1
                task_id = f"{base_id}_{counter}"

            flow["tasks"][task_id] = {
                "type": "executeFlow",
                "parameters": {
                    "flow_path": f"flows/{child_id}.json",
                    "pass_attributes": True,
                    "parameter_mapping": {},
                },
            }

            # Map the child process group's NiFi ID to this task
            nifi_pg_id = self._xml_text(child_pg, "id")
            if nifi_pg_id:
                id_map[nifi_pg_id] = task_id

            warnings.append(ConversionWarning(
                "info", task_id, "processGroup",
                f"Process group '{child_name}' converted to subflow '{child_id}.json'",
            ))

        # Extract input/output ports (direct children only)
        for port in self._direct_children(pg_element, "inputPort"):
            port_id = self._xml_text(port, "id")
            port_name = self._xml_text(port, "name") or "input"
            task_id = f"inputPort_{self._safe_id(port_name)}"
            flow["tasks"][task_id] = {"type": "inputPort", "parameters": {"port_name": port_name}}
            if port_id:
                id_map[port_id] = task_id
            flow["entries"].append(task_id)

        for port in self._direct_children(pg_element, "outputPort"):
            port_id = self._xml_text(port, "id")
            port_name = self._xml_text(port, "name") or "output"
            task_id = f"outputPort_{self._safe_id(port_name)}"
            flow["tasks"][task_id] = {"type": "outputPort", "parameters": {"port_name": port_name}}
            if port_id:
                id_map[port_id] = task_id
            flow["exits"].append(task_id)

        # Extract connections (direct children only)
        for conn in self._direct_children(pg_element, "connection"):
            src_id = self._xml_text(conn, "sourceId")
            dst_id = self._xml_text(conn, "destinationId")
            relationships = [r.text for r in conn.findall(".//relationship") if r.text]

            if src_id in id_map and dst_id in id_map:
                rel_type = self._map_relationship(relationships)
                flow["relations"].append({
                    "from": id_map[src_id],
                    "to": id_map[dst_id],
                    "type": rel_type,
                })

        # Extract parameter contexts (direct children only)
        for ctx in self._direct_children(pg_element, "parameterContext"):
            for param in ctx.iter("parameter"):
                p_name = self._xml_text(param, "name")
                p_value = self._xml_text(param, "value") or ""
                if p_name:
                    flow["parameters"][p_name] = p_value

        # Extract variables
        for var in self._direct_children(pg_element, "variable"):
            v_name = var.get("name", "")
            v_value = var.get("value", "")
            if v_name:
                flow["variables"][v_name] = v_value

        return ConversionResult(
            flow=flow,
            warnings=warnings,
            unmapped_processors=unmapped,
            script_processors=script_procs,
            subflows=subflows,
        )

    def _convert_xml_processor(self, proc_element) -> Tuple[Optional[str], Dict[str, Any], List[ConversionWarning]]:
        """Convert a single NiFi processor XML element to PawFlow task config."""
        warnings = []
        nifi_type = self._xml_text(proc_element, "class") or self._xml_text(proc_element, "type") or ""
        nifi_name = self._xml_text(proc_element, "name") or ""

        # Map processor type
        pyfi_type = self._map_processor_type(nifi_type)
        task_id = self._safe_id(nifi_name) if nifi_name else f"task_{uuid.uuid4().hex[:6]}"

        # Avoid duplicate task IDs
        task_config = {"type": pyfi_type, "parameters": {}}

        if not pyfi_type or pyfi_type == "_unmapped":
            task_config["type"] = "log"  # Fallback to log
            task_config["parameters"]["log_level"] = "WARN"
            task_config["parameters"]["message"] = f"UNMAPPED NiFi processor: {nifi_type}"
            task_config["_unmapped"] = True
            warnings.append(ConversionWarning(
                "warning", task_id, nifi_type,
                f"No PawFlow equivalent for '{nifi_type}'. Replaced with log task.",
            ))

        # Extract properties
        props = self._extract_xml_properties(proc_element)
        task_config["parameters"].update(self._map_properties(pyfi_type, props))

        return task_id, task_config, warnings

    def _extract_xml_properties(self, proc_element) -> Dict[str, str]:
        """Extract NiFi processor properties from XML."""
        props = {}
        # Try <property><name>...</name><value>...</value></property>
        for prop in proc_element.iter("property"):
            name = self._xml_text(prop, "name")
            value = self._xml_text(prop, "value")
            if name:
                props[name] = value or ""
        # Try <entry><key>...</key><value>...</value></entry> (config section)
        config = proc_element.find("config")
        if config is not None:
            for prop in config.iter("property"):
                name = self._xml_text(prop, "name")
                value = self._xml_text(prop, "value")
                if name:
                    props[name] = value or ""
            for entry in config.iter("entry"):
                key = self._xml_text(entry, "key")
                value = self._xml_text(entry, "value")
                if key:
                    props[key] = value or ""
        return props

    def _extract_script_from_xml(self, proc_element) -> str:
        """Extract script body from ExecuteScript/ExecuteGroovyScript."""
        props = self._extract_xml_properties(proc_element)
        return props.get("Script Body", props.get("script-body", ""))

    def _extract_script_language_xml(self, proc_element) -> str:
        """Extract script language from ExecuteScript."""
        props = self._extract_xml_properties(proc_element)
        lang = props.get("Script Engine", props.get("script-engine", ""))
        if "groovy" in lang.lower() or "Groovy" in lang:
            return "groovy"
        if "python" in lang.lower():
            return "python"
        # Default to groovy for ExecuteGroovyScript
        nifi_type = self._xml_text(proc_element, "class") or self._xml_text(proc_element, "type") or ""
        if "Groovy" in nifi_type:
            return "groovy"
        return lang or "unknown"

    # ========================================================================
    # JSON conversion (NiFi REST API format)
    # ========================================================================

    def _convert_json_flow(self, flow_data: Dict[str, Any], pg_data: Optional[Dict[str, Any]] = None) -> ConversionResult:
        """Convert NiFi JSON REST API flow to PawFlow flow dict."""
        warnings = []
        unmapped = []
        script_procs = []

        # Extract name from breadcrumb (lives in pg_data, not flow_data)
        source = pg_data or flow_data
        breadcrumb = source.get("breadcrumb", {})
        if isinstance(breadcrumb, dict) and "breadcrumb" in breadcrumb:
            breadcrumb = breadcrumb["breadcrumb"]
        flow_name = breadcrumb.get("name", source.get("name", "Imported NiFi Flow"))
        flow_id = breadcrumb.get("id", source.get("id", f"nifi_import_{uuid.uuid4().hex[:8]}"))

        flow = {
            "id": flow_id,
            "name": flow_name,
            "version": "1.0.0",
            "description": "Imported from NiFi (JSON REST API)",
            "author": "NiFi Import",
            "parameters": {},
            "entries": [],
            "exits": [],
            "tasks": {},
            "groups": {},
            "relations": [],
            "variables": {},
        }

        id_map = {}  # NiFi UUID → PawFlow task_id

        # Convert processors
        processors = flow_data.get("processors", [])
        for proc in processors:
            component = proc.get("component", proc)
            nifi_id = component.get("id", "")
            nifi_type = component.get("type", "")
            nifi_name = component.get("name", "")

            pyfi_type = self._map_processor_type(nifi_type)
            task_id = self._safe_id(nifi_name) if nifi_name else f"task_{uuid.uuid4().hex[:6]}"

            # Avoid collisions
            base_id = task_id
            counter = 1
            while task_id in flow["tasks"]:
                counter += 1
                task_id = f"{base_id}_{counter}"

            task_config = {"type": pyfi_type, "parameters": {}}

            if not pyfi_type or pyfi_type == "_unmapped":
                task_config["type"] = "log"
                task_config["parameters"]["log_level"] = "WARN"
                task_config["parameters"]["message"] = f"UNMAPPED NiFi processor: {nifi_type}"
                unmapped.append(nifi_type)
                warnings.append(ConversionWarning(
                    "warning", task_id, nifi_type,
                    f"No PawFlow equivalent for '{nifi_type}'. Replaced with log task.",
                ))
            else:
                # Extract properties
                props = component.get("config", {}).get("properties", {})
                task_config["parameters"].update(self._map_properties(pyfi_type, props))

            # Track script processors
            if "Script" in nifi_type or "Groovy" in nifi_type:
                props = component.get("config", {}).get("properties", {})
                script_body = props.get("Script Body", props.get("script-body", ""))
                lang = props.get("Script Engine", props.get("script-engine", ""))
                if "groovy" in (lang or "").lower() or "Groovy" in nifi_type:
                    lang = "groovy"
                script_procs.append({
                    "task_id": task_id,
                    "nifi_type": nifi_type,
                    "script": script_body,
                    "language": lang or "unknown",
                })

            flow["tasks"][task_id] = task_config
            id_map[nifi_id] = task_id

        # Convert input/output ports
        for port in flow_data.get("inputPorts", []):
            comp = port.get("component", port)
            port_name = comp.get("name", "input")
            port_id = comp.get("id", "")
            task_id = f"inputPort_{self._safe_id(port_name)}"
            flow["tasks"][task_id] = {"type": "inputPort", "parameters": {"port_name": port_name}}
            id_map[port_id] = task_id
            flow["entries"].append(task_id)

        for port in flow_data.get("outputPorts", []):
            comp = port.get("component", port)
            port_name = comp.get("name", "output")
            port_id = comp.get("id", "")
            task_id = f"outputPort_{self._safe_id(port_name)}"
            flow["tasks"][task_id] = {"type": "outputPort", "parameters": {"port_name": port_name}}
            id_map[port_id] = task_id
            flow["exits"].append(task_id)

        # Convert nested process groups → subflows
        subflows = []
        for child_pg in flow_data.get("processGroups", []):
            child_comp = child_pg.get("component", child_pg)
            child_name = child_comp.get("name", "subflow")
            child_id = child_comp.get("id", f"sub_{uuid.uuid4().hex[:6]}")
            child_nifi_id = child_comp.get("id", "")

            # Recursively convert the child process group
            child_flow_data = child_comp.get("contents", child_comp)
            child_result = self._convert_json_flow(child_flow_data)
            child_flow = child_result.flow
            child_flow["id"] = child_id
            child_flow["name"] = child_name

            subflows.append(child_flow)
            subflows.extend(child_result.subflows)
            warnings.extend(child_result.warnings)
            unmapped.extend(child_result.unmapped_processors)
            script_procs.extend(child_result.script_processors)

            # Create executeFlow task in parent
            task_id = f"subflow_{self._safe_id(child_name)}"
            base_tid = task_id
            counter = 1
            while task_id in flow["tasks"]:
                counter += 1
                task_id = f"{base_tid}_{counter}"

            flow["tasks"][task_id] = {
                "type": "executeFlow",
                "parameters": {
                    "flow_path": f"flows/{child_id}.json",
                    "pass_attributes": True,
                    "parameter_mapping": {},
                },
            }
            if child_nifi_id:
                id_map[child_nifi_id] = task_id

            warnings.append(ConversionWarning(
                "info", task_id, "processGroup",
                f"Process group '{child_name}' converted to subflow '{child_id}.json'",
            ))

        # Convert connections
        for conn in flow_data.get("connections", []):
            comp = conn.get("component", conn)
            src = comp.get("source", {})
            dst = comp.get("destination", {})
            src_id = src.get("id", "")
            dst_id = dst.get("id", "")
            relationships = comp.get("selectedRelationships", [])

            if src_id in id_map and dst_id in id_map:
                rel_type = self._map_relationship(relationships)
                flow["relations"].append({
                    "from": id_map[src_id],
                    "to": id_map[dst_id],
                    "type": rel_type,
                })

        return ConversionResult(
            flow=flow,
            warnings=warnings,
            unmapped_processors=unmapped,
            script_processors=script_procs,
            subflows=subflows,
        )

    # ========================================================================
    # Mapping helpers
    # ========================================================================

    def _map_processor_type(self, nifi_type: str) -> str:
        """Map NiFi processor class to PawFlow task type."""
        if nifi_type in PROCESSOR_MAP:
            return PROCESSOR_MAP[nifi_type]
        # Try short name
        short = nifi_type.rsplit(".", 1)[-1] if "." in nifi_type else nifi_type
        if short in PROCESSOR_SHORT_MAP:
            return PROCESSOR_SHORT_MAP[short]
        return "_unmapped"

    def _map_relationship(self, relationships: List[str]) -> str:
        """Map NiFi relationship names to PawFlow relation type."""
        if not relationships:
            return "success"
        # Use first relationship
        rel = relationships[0].lower().strip()
        return RELATIONSHIP_MAP.get(rel, rel)

    def _map_properties(self, pyfi_type: str, nifi_props: Dict[str, str]) -> Dict[str, Any]:
        """Map NiFi processor properties to PawFlow task parameters."""
        params = {}

        # Common property mappings per task type
        prop_maps = {
            "getFile": {
                "Input Directory": "directory",
                "File Filter": "file_filter",
                "Recurse Subdirectories": "recurse",
                "Keep Source File": "keep_source",
            },
            "putFile": {
                "Directory": "directory",
                "Conflict Resolution Strategy": "conflict_resolution",
                "Create Missing Directories": "create_dirs",
            },
            "fetchHTTP": {
                "Remote URL": "url",
                "HTTP Method": "method",
                "Request Body": "body",
            },
            "routeOnAttribute": {
                # Dynamic properties become routing conditions
            },
            "updateAttribute": {
                # Dynamic properties become set attributes
            },
            "log": {
                "Log Level": "log_level",
                "Log Message": "message",
                "Log prefix": "message",
            },
            "replace_text": {
                "Search Value": "search_value",
                "Replacement Value": "replacement_value",
                "Replacement Strategy": "strategy",
            },
            "executeSQL": {
                "SQL select query": "query",
                "SQL Pre-Query": "pre_query",
            },
            "compressContent": {
                "Compression Format": "format",
                "Mode": "mode",
                "Compression Level": "level",
            },
            "generateFlowFile": {
                "File Size": "file_size",
                "Batch Size": "batch_size",
                "Data Format": "data_format",
                "Custom Text": "custom_text",
            },
            "executeScript": {
                "Script Body": "script",
                "Script Engine": "language",
                "script-body": "script",
                "script-engine": "language",
            },
            "publishKafka": {
                "bootstrap.servers": "bootstrap_servers",
                "topic": "topic",
            },
            "consumeKafka": {
                "bootstrap.servers": "bootstrap_servers",
                "topic": "topic",
                "group.id": "group_id",
            },
            "publishMQTT": {
                "Broker URI": "broker_url",
                "Topic": "topic",
            },
            "consumeMQTT": {
                "Broker URI": "broker_url",
                "Topic Filter": "topic",
            },
            "getSFTP": {
                "Hostname": "hostname",
                "Port": "port",
                "Username": "username",
                "Password": "password",
                "Remote Path": "remote_path",
            },
            "putSFTP": {
                "Hostname": "hostname",
                "Port": "port",
                "Username": "username",
                "Password": "password",
                "Remote Path": "remote_path",
            },
            "putS3": {
                "Bucket": "bucket",
                "Object Key": "key",
                "Region": "region",
            },
            "getS3": {
                "Bucket": "bucket",
                "Object Key": "key",
                "Region": "region",
            },
            "putGCS": {
                "Bucket": "bucket",
                "Key": "blob_name",
                "GCP Project ID": "project_id",
            },
            "getGCS": {
                "Bucket": "bucket",
                "Key": "blob_name",
                "GCP Project ID": "project_id",
            },
            "putAzureBlob": {
                "Container Name": "container_name",
                "Blob Name": "blob_name",
                "Storage Account Connection String": "connection_string",
            },
            "getAzureBlob": {
                "Container Name": "container_name",
                "Blob Name": "blob_name",
                "Storage Account Connection String": "connection_string",
            },
        }

        mapping = prop_maps.get(pyfi_type, {})

        for nifi_key, nifi_value in nifi_props.items():
            if nifi_key in mapping:
                params[mapping[nifi_key]] = nifi_value
            elif pyfi_type == "routeOnAttribute":
                # All dynamic properties are routing conditions
                if nifi_key not in ("Routing Strategy",):
                    params.setdefault("conditions", {})[nifi_key] = nifi_value
            elif pyfi_type == "updateAttribute":
                # Dynamic properties become set operations
                params.setdefault("set", {})[nifi_key] = nifi_value

        return params

    # ========================================================================
    # Utilities
    # ========================================================================

    def _direct_children(self, element, tag: str) -> List:
        """Get direct child elements with the given tag (not recursive)."""
        return element.findall(tag)

    def _xml_text(self, element, tag: str) -> Optional[str]:
        """Get text content of a child XML element."""
        child = element.find(tag)
        if child is not None and child.text:
            return child.text.strip()
        return None

    def _safe_id(self, name: str) -> str:
        """Convert a name to a safe task ID."""
        safe = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        safe = re.sub(r'_+', '_', safe).strip('_')
        return safe[:50] or f"task_{uuid.uuid4().hex[:6]}"
