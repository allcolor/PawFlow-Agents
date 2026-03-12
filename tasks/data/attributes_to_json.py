"""Convert FlowFile attributes to JSON content."""

import json
from core.base_task import BaseTask
from core import TaskFactory, FlowFile


class AttributesToJSONTask(BaseTask):
    TYPE = "attributesToJSON"
    VERSION = "1.0.0"
    NAME = "Attributes To JSON"
    DESCRIPTION = "Converts FlowFile attributes to JSON content"
    ICON = "📋"

    @classmethod
    def get_parameter_schema(cls):
        return {
            "attributes_list": {
                "type": "string", "required": False, "default": "",
                "description": "Comma-separated list of attribute names. Empty = all attributes.",
            },
            "destination": {
                "type": "string", "required": False, "default": "flowfile-content",
                "description": "Where to put JSON: flowfile-content or flowfile-attribute",
            },
            "destination_attribute": {
                "type": "string", "required": False, "default": "JSONAttributes",
                "description": "Attribute name when destination=flowfile-attribute",
            },
            "include_core_attributes": {
                "type": "boolean", "required": False, "default": True,
                "description": "Include core attributes (uuid, timestamp, etc.)",
            },
        }

    def execute(self, flowfile):
        attrs_filter = self.config.get("attributes_list", "")
        destination = self.config.get("destination", "flowfile-content")
        dest_attr = self.config.get("destination_attribute", "JSONAttributes")
        include_core = self.config.get("include_core_attributes", True)

        all_attrs = flowfile.get_attributes()

        core_attrs = {"uuid", "timestamp", "fileSize", "filename"}

        if attrs_filter:
            keys = [k.strip() for k in attrs_filter.split(",") if k.strip()]
            filtered = {k: v for k, v in all_attrs.items() if k in keys}
        elif not include_core:
            filtered = {k: v for k, v in all_attrs.items() if k not in core_attrs}
        else:
            filtered = dict(all_attrs)

        json_str = json.dumps(filtered, indent=2, ensure_ascii=False)

        if destination == "flowfile-content":
            output = FlowFile(content=json_str.encode("utf-8"), attributes=flowfile.get_attributes())
        else:
            output = FlowFile(content=flowfile.get_content(), attributes=flowfile.get_attributes())
            output.set_attribute(dest_attr, json_str)

        return [output]


TaskFactory.register(AttributesToJSONTask)
