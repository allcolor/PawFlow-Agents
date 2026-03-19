"""Tests for P9 — NiFi → PawFlow flow conversion."""

import json
import pytest

from engine.nifi_converter import (
    NiFiConverter, ConversionResult, PROCESSOR_MAP, PROCESSOR_SHORT_MAP,
    RELATIONSHIP_MAP,
)
from engine.nifi_script_converter import NiFiScriptConverter, ScriptConversionResult


# ============================================================================
# NiFi XML conversion
# ============================================================================

SAMPLE_NIFI_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<template>
  <name>Sample ETL Flow</name>
  <snippet>
    <processGroup>
      <name>Sample ETL</name>
      <id>pg-001</id>
      <processor>
        <id>proc-001</id>
        <name>Get Input Files</name>
        <class>org.apache.nifi.processors.standard.GetFile</class>
        <config>
          <property><name>Input Directory</name><value>/data/input</value></property>
          <property><name>File Filter</name><value>*.csv</value></property>
        </config>
      </processor>
      <processor>
        <id>proc-002</id>
        <name>Transform JSON</name>
        <class>org.apache.nifi.processors.standard.JoltTransformJSON</class>
        <config>
          <property><name>Jolt Specification</name><value>{}</value></property>
        </config>
      </processor>
      <processor>
        <id>proc-003</id>
        <name>Write Output</name>
        <class>org.apache.nifi.processors.standard.PutFile</class>
        <config>
          <property><name>Directory</name><value>/data/output</value></property>
        </config>
      </processor>
      <processor>
        <id>proc-004</id>
        <name>Log Result</name>
        <class>org.apache.nifi.processors.standard.LogAttribute</class>
      </processor>
      <connection>
        <sourceId>proc-001</sourceId>
        <destinationId>proc-002</destinationId>
        <relationship>success</relationship>
      </connection>
      <connection>
        <sourceId>proc-002</sourceId>
        <destinationId>proc-003</destinationId>
        <relationship>success</relationship>
      </connection>
      <connection>
        <sourceId>proc-002</sourceId>
        <destinationId>proc-004</destinationId>
        <relationship>failure</relationship>
      </connection>
    </processGroup>
  </snippet>
</template>
"""

SAMPLE_NIFI_XML_WITH_SCRIPT = """<?xml version="1.0" encoding="UTF-8"?>
<processGroup>
  <name>Script Flow</name>
  <id>pg-script</id>
  <processor>
    <id>proc-script-1</id>
    <name>Groovy Transform</name>
    <class>org.apache.nifi.processors.groovyx.ExecuteGroovyScript</class>
    <config>
      <property><name>Script Body</name><value>
def ff = session.get()
if (!ff) return
def content = session.read(ff)
ff.putAttribute('processed', 'true')
session.transfer(ff, REL_SUCCESS)
      </value></property>
    </config>
  </processor>
  <processor>
    <id>proc-script-2</id>
    <name>Log Output</name>
    <class>org.apache.nifi.processors.standard.LogAttribute</class>
  </processor>
  <connection>
    <sourceId>proc-script-1</sourceId>
    <destinationId>proc-script-2</destinationId>
    <relationship>success</relationship>
  </connection>
</processGroup>
"""

SAMPLE_NIFI_XML_WITH_PORTS = """<?xml version="1.0" encoding="UTF-8"?>
<processGroup>
  <name>Sub Process Group</name>
  <id>pg-ports</id>
  <inputPort>
    <id>port-in-1</id>
    <name>data_in</name>
  </inputPort>
  <outputPort>
    <id>port-out-1</id>
    <name>data_out</name>
  </outputPort>
  <processor>
    <id>proc-mid</id>
    <name>Process Data</name>
    <class>org.apache.nifi.processors.standard.ReplaceText</class>
    <config>
      <property><name>Search Value</name><value>old</value></property>
      <property><name>Replacement Value</name><value>new</value></property>
    </config>
  </processor>
  <connection>
    <sourceId>port-in-1</sourceId>
    <destinationId>proc-mid</destinationId>
    <relationship>success</relationship>
  </connection>
  <connection>
    <sourceId>proc-mid</sourceId>
    <destinationId>port-out-1</destinationId>
    <relationship>success</relationship>
  </connection>
  <parameterContext>
    <parameter><name>env</name><value>production</value></parameter>
    <parameter><name>batch_size</name><value>100</value></parameter>
  </parameterContext>
</processGroup>
"""


class TestNiFiXMLConversion:

    def test_basic_xml_template(self):
        converter = NiFiConverter()
        result = converter.convert_xml(SAMPLE_NIFI_XML_TEMPLATE)
        assert result.success
        flow = result.flow
        assert len(flow["tasks"]) == 4
        assert len(flow["relations"]) == 3

    def test_xml_processor_mapping(self):
        converter = NiFiConverter()
        result = converter.convert_xml(SAMPLE_NIFI_XML_TEMPLATE)
        tasks = result.flow["tasks"]
        # GetFile → getFile
        get_task = tasks.get("Get_Input_Files")
        assert get_task is not None
        assert get_task["type"] == "getFile"
        assert get_task["parameters"]["directory"] == "/data/input"
        assert get_task["parameters"]["file_filter"] == "*.csv"

    def test_xml_putfile_mapping(self):
        converter = NiFiConverter()
        result = converter.convert_xml(SAMPLE_NIFI_XML_TEMPLATE)
        tasks = result.flow["tasks"]
        put_task = tasks.get("Write_Output")
        assert put_task is not None
        assert put_task["type"] == "putFile"
        assert put_task["parameters"]["directory"] == "/data/output"

    def test_xml_relations(self):
        converter = NiFiConverter()
        result = converter.convert_xml(SAMPLE_NIFI_XML_TEMPLATE)
        rels = result.flow["relations"]
        # success: Get→Transform, Transform→Put
        success_rels = [r for r in rels if r["type"] == "success"]
        failure_rels = [r for r in rels if r["type"] == "failure"]
        assert len(success_rels) == 2
        assert len(failure_rels) == 1

    def test_xml_script_extraction(self):
        converter = NiFiConverter()
        result = converter.convert_xml(SAMPLE_NIFI_XML_WITH_SCRIPT)
        assert len(result.script_processors) == 1
        sp = result.script_processors[0]
        assert sp["task_id"] == "Groovy_Transform"
        assert "session.get()" in sp["script"]
        assert sp["language"] == "groovy"

    def test_xml_ports(self):
        converter = NiFiConverter()
        result = converter.convert_xml(SAMPLE_NIFI_XML_WITH_PORTS)
        flow = result.flow
        assert "inputPort_data_in" in flow["tasks"]
        assert "outputPort_data_out" in flow["tasks"]
        assert "inputPort_data_in" in flow["entries"]
        assert "outputPort_data_out" in flow["exits"]

    def test_xml_parameter_context(self):
        converter = NiFiConverter()
        result = converter.convert_xml(SAMPLE_NIFI_XML_WITH_PORTS)
        params = result.flow["parameters"]
        assert params["env"] == "production"
        assert params["batch_size"] == "100"

    def test_xml_invalid(self):
        converter = NiFiConverter()
        result = converter.convert_xml("not valid xml <<<<")
        assert not result.success

    def test_xml_unmapped_processor(self):
        xml = """<processGroup>
          <name>Test</name>
          <processor>
            <id>p1</id>
            <name>Unknown Proc</name>
            <class>com.custom.UnknownProcessor</class>
          </processor>
        </processGroup>"""
        converter = NiFiConverter()
        result = converter.convert_xml(xml)
        assert len(result.unmapped_processors) == 1
        assert "com.custom.UnknownProcessor" in result.unmapped_processors
        assert len(result.warnings) >= 1
        # Replaced with log task
        task = result.flow["tasks"]["Unknown_Proc"]
        assert task["type"] == "log"


# ============================================================================
# NiFi JSON conversion
# ============================================================================

SAMPLE_NIFI_JSON = {
    "processGroupFlow": {
        "breadcrumb": {"breadcrumb": {"id": "pg-json-001", "name": "JSON Flow"}},
        "flow": {
            "processors": [
                {
                    "component": {
                        "id": "proc-j1",
                        "name": "Fetch Data",
                        "type": "org.apache.nifi.processors.standard.InvokeHTTP",
                        "config": {
                            "properties": {
                                "Remote URL": "https://api.example.com/data",
                                "HTTP Method": "GET",
                            }
                        },
                    }
                },
                {
                    "component": {
                        "id": "proc-j2",
                        "name": "Route Results",
                        "type": "org.apache.nifi.processors.standard.RouteOnAttribute",
                        "config": {
                            "properties": {
                                "Routing Strategy": "Route to Property name",
                                "valid": "${http.status.code:equals('200')}",
                                "error": "${http.status.code:gt('399')}",
                            }
                        },
                    }
                },
                {
                    "component": {
                        "id": "proc-j3",
                        "name": "Save File",
                        "type": "org.apache.nifi.processors.standard.PutFile",
                        "config": {
                            "properties": {
                                "Directory": "/output",
                            }
                        },
                    }
                },
            ],
            "connections": [
                {
                    "component": {
                        "source": {"id": "proc-j1"},
                        "destination": {"id": "proc-j2"},
                        "selectedRelationships": ["success"],
                    }
                },
                {
                    "component": {
                        "source": {"id": "proc-j2"},
                        "destination": {"id": "proc-j3"},
                        "selectedRelationships": ["matched"],
                    }
                },
            ],
            "inputPorts": [
                {"component": {"id": "ip-1", "name": "input"}}
            ],
            "outputPorts": [],
        },
    }
}


class TestNiFiJSONConversion:

    def test_basic_json(self):
        converter = NiFiConverter()
        result = converter.convert_json(json.dumps(SAMPLE_NIFI_JSON))
        assert result.success
        flow = result.flow
        assert flow["name"] == "JSON Flow"
        assert len(flow["tasks"]) == 4  # 3 procs + 1 input port
        assert len(flow["relations"]) == 2

    def test_json_fetchhttp_mapping(self):
        converter = NiFiConverter()
        result = converter.convert_json(json.dumps(SAMPLE_NIFI_JSON))
        task = result.flow["tasks"]["Fetch_Data"]
        assert task["type"] == "fetchHTTP"
        assert task["parameters"]["url"] == "https://api.example.com/data"
        assert task["parameters"]["method"] == "GET"

    def test_json_route_on_attribute(self):
        converter = NiFiConverter()
        result = converter.convert_json(json.dumps(SAMPLE_NIFI_JSON))
        task = result.flow["tasks"]["Route_Results"]
        assert task["type"] == "routeOnAttribute"
        assert "conditions" in task["parameters"]
        assert "valid" in task["parameters"]["conditions"]

    def test_json_relationship_mapping(self):
        converter = NiFiConverter()
        result = converter.convert_json(json.dumps(SAMPLE_NIFI_JSON))
        rels = result.flow["relations"]
        assert rels[0]["type"] == "success"
        assert rels[1]["type"] == "matched"

    def test_json_input_port(self):
        converter = NiFiConverter()
        result = converter.convert_json(json.dumps(SAMPLE_NIFI_JSON))
        assert "inputPort_input" in result.flow["tasks"]
        assert "inputPort_input" in result.flow["entries"]

    def test_json_invalid(self):
        converter = NiFiConverter()
        result = converter.convert_json("not json {{{")
        assert not result.success


# ============================================================================
# Auto-detect format
# ============================================================================

class TestAutoDetect:

    def test_detect_xml(self):
        converter = NiFiConverter()
        result = converter.convert(SAMPLE_NIFI_XML_TEMPLATE)
        assert result.success
        assert len(result.flow["tasks"]) == 4

    def test_detect_json(self):
        converter = NiFiConverter()
        result = converter.convert(json.dumps(SAMPLE_NIFI_JSON))
        assert result.success

    def test_detect_unknown(self):
        converter = NiFiConverter()
        result = converter.convert("random text")
        assert not result.success


# ============================================================================
# Processor mapping table
# ============================================================================

class TestProcessorMapping:

    def test_common_processors_mapped(self):
        """Verify the most common NiFi processors have mappings."""
        essential = [
            "org.apache.nifi.processors.standard.GetFile",
            "org.apache.nifi.processors.standard.PutFile",
            "org.apache.nifi.processors.standard.InvokeHTTP",
            "org.apache.nifi.processors.standard.RouteOnAttribute",
            "org.apache.nifi.processors.standard.UpdateAttribute",
            "org.apache.nifi.processors.standard.ExecuteScript",
            "org.apache.nifi.processors.standard.LogAttribute",
            "org.apache.nifi.processors.standard.SplitJson",
            "org.apache.nifi.processors.standard.MergeContent",
            "org.apache.nifi.processors.standard.EvaluateJsonPath",
        ]
        for proc in essential:
            assert proc in PROCESSOR_MAP, f"Missing mapping for {proc}"

    def test_short_name_fallback(self):
        assert "GetFile" in PROCESSOR_SHORT_MAP
        assert PROCESSOR_SHORT_MAP["GetFile"] == "getFile"

    def test_relationship_mapping(self):
        assert RELATIONSHIP_MAP["success"] == "success"
        assert RELATIONSHIP_MAP["failure"] == "failure"
        assert RELATIONSHIP_MAP["matched"] == "matched"


# ============================================================================
# Script converter (static, no LLM)
# ============================================================================

class TestScriptConverterStatic:

    def test_empty_script(self):
        converter = NiFiScriptConverter()
        result = converter.convert("")
        assert result.success
        assert "result_flowfiles" in result.converted_python

    def test_basic_groovy_conversion(self):
        groovy = """
import groovy.json.JsonSlurper
def ff = session.get()
if (!ff) return
def content = new String(session.read(ff))
def json = new JsonSlurper().parseText(content)
ff.putAttribute('count', json.size().toString())
session.transfer(ff, REL_SUCCESS)
"""
        converter = NiFiScriptConverter()
        result = converter.convert(groovy)
        assert result.success
        assert not result.used_llm
        py = result.converted_python
        # Should have Python imports
        assert "import json" in py
        # Should convert API calls
        assert "get_attribute" in py or "set_attribute" in py
        assert "result_flowfiles" in py

    def test_java_type_conversion(self):
        groovy = "def list = new ArrayList()\ndef map = new HashMap()"
        converter = NiFiScriptConverter()
        result = converter.convert(groovy)
        assert "[]" in result.converted_python
        assert "{}" in result.converted_python

    def test_null_true_false_conversion(self):
        groovy = "if (value == null) { flag = true } else { flag = false }"
        converter = NiFiScriptConverter()
        result = converter.convert(groovy)
        py = result.converted_python
        assert "None" in py
        assert "True" in py
        assert "False" in py

    def test_json_conversion(self):
        groovy = 'def obj = new JsonSlurper().parseText(text)\ndef out = JsonOutput.toJson(obj)'
        converter = NiFiScriptConverter()
        result = converter.convert(groovy)
        assert "json.loads" in result.converted_python
        assert "json.dumps" in result.converted_python

    def test_log_conversion(self):
        groovy = 'log.info("processing")\nlog.warn("slow")\nlog.error("failed")'
        converter = NiFiScriptConverter()
        result = converter.convert(groovy)
        py = result.converted_python
        assert "logger.info" in py
        assert "logger.warning" in py
        assert "logger.error" in py

    def test_has_llm_property(self):
        converter = NiFiScriptConverter()
        assert not converter.has_llm

        converter_with = NiFiScriptConverter({"provider": "openai", "api_key": "test"})
        assert converter_with.has_llm

    def test_convert_with_feedback_no_llm(self):
        converter = NiFiScriptConverter()
        result = converter.convert_with_feedback("groovy", "python", "fix it")
        assert not result.success
        assert "LLM not configured" in result.error

    def test_semicolons_removed(self):
        groovy = "def x = 1;\ndef y = 2;"
        converter = NiFiScriptConverter()
        result = converter.convert(groovy)
        # Semicolons at end of line should be removed
        for line in result.converted_python.split("\n"):
            if line.strip() and not line.strip().startswith("#"):
                assert not line.rstrip().endswith(";") or ";" not in line


# ============================================================================
# LLM client shared module
# ============================================================================

class TestSharedLLMClient:

    def test_import_from_core(self):
        from core.llm_client import LLMClient, LLMMessage, LLMResponse, LLMClientError
        assert LLMClient is not None

    def test_from_config(self):
        from core.llm_client import LLMClient
        config = {"provider": "openai", "api_key": "test-key", "base_url": "http://localhost:8080"}
        client = LLMClient.from_config(config)
        assert client.provider == "openai"
        assert client.api_key == "test-key"
        assert client.base_url == "http://localhost:8080"

    def test_default_urls(self):
        from core.llm_client import LLMClient
        client = LLMClient(provider="openai", api_key="x")
        assert "openai" in client.base_url
        client2 = LLMClient(provider="anthropic", api_key="x")
        assert "anthropic" in client2.base_url

    def test_llm_service_still_works(self):
        """Verify LLMConnectionService still works after refactoring."""
        from services.llm_connection import LLMConnectionService, LLMMessage, LLMResponse
        svc = LLMConnectionService({"provider": "openai", "api_key": "test"})
        assert svc.provider == "openai"
        assert svc.api_key == "test"
        assert svc.TYPE == "llmConnection"


# ============================================================================
# End-to-end: XML → flow dict → can be parsed by FlowParser
# ============================================================================

# ============================================================================
# Process groups → subflows
# ============================================================================

SAMPLE_XML_WITH_PROCESS_GROUP = """<?xml version="1.0" encoding="UTF-8"?>
<processGroup>
  <name>Parent Flow</name>
  <id>pg-parent</id>
  <processor>
    <id>proc-p1</id>
    <name>Get Input</name>
    <class>org.apache.nifi.processors.standard.GetFile</class>
    <config>
      <property><name>Input Directory</name><value>/input</value></property>
    </config>
  </processor>
  <processGroup>
    <name>ETL Subflow</name>
    <id>pg-child-etl</id>
    <processor>
      <id>proc-c1</id>
      <name>Transform</name>
      <class>org.apache.nifi.processors.standard.JoltTransformJSON</class>
    </processor>
    <processor>
      <id>proc-c2</id>
      <name>Validate</name>
      <class>org.apache.nifi.processors.standard.ValidateRecord</class>
    </processor>
    <connection>
      <sourceId>proc-c1</sourceId>
      <destinationId>proc-c2</destinationId>
      <relationship>success</relationship>
    </connection>
    <inputPort>
      <id>child-in</id>
      <name>child_input</name>
    </inputPort>
  </processGroup>
  <connection>
    <sourceId>proc-p1</sourceId>
    <destinationId>pg-child-etl</destinationId>
    <relationship>success</relationship>
  </connection>
</processGroup>
"""

SAMPLE_JSON_WITH_PROCESS_GROUP = {
    "processGroupFlow": {
        "breadcrumb": {"breadcrumb": {"id": "pg-json-nested", "name": "Nested JSON Flow"}},
        "flow": {
            "processors": [
                {
                    "component": {
                        "id": "proc-n1",
                        "name": "Generate",
                        "type": "org.apache.nifi.processors.standard.GenerateFlowFile",
                        "config": {"properties": {}},
                    }
                },
            ],
            "processGroups": [
                {
                    "component": {
                        "id": "pg-sub-1",
                        "name": "Processing Group",
                        "contents": {
                            "processors": [
                                {
                                    "component": {
                                        "id": "proc-sub1",
                                        "name": "Sub Transform",
                                        "type": "org.apache.nifi.processors.standard.ReplaceText",
                                        "config": {"properties": {"Search Value": "a", "Replacement Value": "b"}},
                                    }
                                },
                            ],
                            "connections": [],
                            "inputPorts": [],
                            "outputPorts": [],
                            "processGroups": [],
                        },
                    }
                },
            ],
            "connections": [
                {
                    "component": {
                        "source": {"id": "proc-n1"},
                        "destination": {"id": "pg-sub-1"},
                        "selectedRelationships": ["success"],
                    }
                },
            ],
            "inputPorts": [],
            "outputPorts": [],
        },
    }
}


class TestProcessGroupToSubflow:

    def test_xml_process_group_creates_subflow(self):
        converter = NiFiConverter()
        result = converter.convert_xml(SAMPLE_XML_WITH_PROCESS_GROUP)
        assert result.success
        # Parent should have the processor + an executeFlow task
        tasks = result.flow["tasks"]
        assert "Get_Input" in tasks
        subflow_tasks = [t for t in tasks.values() if t["type"] == "executeFlow"]
        assert len(subflow_tasks) == 1
        assert subflow_tasks[0]["parameters"]["flow_path"] == "flows/pg-child-etl.json"

    def test_xml_subflow_is_separate_flow(self):
        converter = NiFiConverter()
        result = converter.convert_xml(SAMPLE_XML_WITH_PROCESS_GROUP)
        assert len(result.subflows) == 1
        child = result.subflows[0]
        assert child["name"] == "ETL Subflow"
        assert child["id"] == "pg-child-etl"
        assert "Transform" in child["tasks"]
        assert "Validate" in child["tasks"]
        assert len(child["relations"]) == 1

    def test_xml_subflow_has_ports(self):
        converter = NiFiConverter()
        result = converter.convert_xml(SAMPLE_XML_WITH_PROCESS_GROUP)
        child = result.subflows[0]
        assert "inputPort_child_input" in child["tasks"]
        assert "inputPort_child_input" in child["entries"]

    def test_xml_parent_child_connection(self):
        converter = NiFiConverter()
        result = converter.convert_xml(SAMPLE_XML_WITH_PROCESS_GROUP)
        rels = result.flow["relations"]
        # proc-p1 → pg-child-etl (mapped to executeFlow task)
        assert len(rels) == 1
        assert rels[0]["from"] == "Get_Input"
        assert rels[0]["type"] == "success"

    def test_xml_child_processors_not_in_parent(self):
        converter = NiFiConverter()
        result = converter.convert_xml(SAMPLE_XML_WITH_PROCESS_GROUP)
        parent_tasks = result.flow["tasks"]
        # Child processors should NOT appear in parent
        assert "Transform" not in parent_tasks
        assert "Validate" not in parent_tasks

    def test_json_process_group_creates_subflow(self):
        converter = NiFiConverter()
        result = converter.convert_json(json.dumps(SAMPLE_JSON_WITH_PROCESS_GROUP))
        assert result.success
        tasks = result.flow["tasks"]
        subflow_tasks = [t for t in tasks.values() if t["type"] == "executeFlow"]
        assert len(subflow_tasks) == 1

    def test_json_subflow_content(self):
        converter = NiFiConverter()
        result = converter.convert_json(json.dumps(SAMPLE_JSON_WITH_PROCESS_GROUP))
        assert len(result.subflows) == 1
        child = result.subflows[0]
        assert child["name"] == "Processing Group"
        assert "Sub_Transform" in child["tasks"]

    def test_json_parent_child_connection(self):
        converter = NiFiConverter()
        result = converter.convert_json(json.dumps(SAMPLE_JSON_WITH_PROCESS_GROUP))
        rels = result.flow["relations"]
        assert len(rels) == 1
        assert rels[0]["from"] == "Generate"
        assert rels[0]["type"] == "success"

    def test_subflow_info_warnings(self):
        converter = NiFiConverter()
        result = converter.convert_xml(SAMPLE_XML_WITH_PROCESS_GROUP)
        info_warnings = [w for w in result.warnings if w.level == "info" and "subflow" in w.message]
        assert len(info_warnings) == 1


class TestEndToEnd:

    def test_converted_flow_structure(self):
        """Verify converted flow has all required PawFlow fields."""
        converter = NiFiConverter()
        result = converter.convert_xml(SAMPLE_NIFI_XML_TEMPLATE)
        flow = result.flow
        assert "id" in flow
        assert "name" in flow
        assert "tasks" in flow
        assert "relations" in flow
        assert "parameters" in flow
        assert "variables" in flow
        assert isinstance(flow["tasks"], dict)
        assert isinstance(flow["relations"], list)

    def test_converted_flow_valid_json(self):
        """Verify converted flow can be serialized to JSON."""
        converter = NiFiConverter()
        result = converter.convert_xml(SAMPLE_NIFI_XML_WITH_PORTS)
        json_str = json.dumps(result.flow, indent=2)
        reloaded = json.loads(json_str)
        assert reloaded["name"] == "Sub Process Group"
        assert len(reloaded["parameters"]) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
