"""Split JSON array into individual FlowFiles."""

import json
from core.base_task import BaseTask
from core import TaskFactory, FlowFile


class SplitJSONTask(BaseTask):
    TYPE = "splitJSON"
    VERSION = "1.0.0"
    NAME = "Split JSON"
    DESCRIPTION = "Splits a JSON array into individual FlowFiles, one per element"
    ICON = "✂️"

    @classmethod
    def get_parameter_schema(cls):
        return {
            "json_path_expression": {
                "type": "string", "required": False, "default": "$",
                "description": "JSONPath to the array to split. '$' = root array.",
            },
        }

    def execute(self, flowfile):
        path_expr = self.config.get("json_path_expression", "$")
        content = flowfile.get_content()
        data = json.loads(content)

        # Navigate to the target array
        if path_expr == "$":
            target = data
        else:
            # Simple dot-notation path (e.g. "$.items" or "items")
            keys = path_expr.replace("$.", "").replace("$", "").split(".")
            target = data
            for key in keys:
                if key and isinstance(target, dict):
                    target = target.get(key, [])

        if not isinstance(target, list):
            target = [target]

        results = []
        for i, item in enumerate(target):
            item_json = json.dumps(item, ensure_ascii=False).encode("utf-8")
            ff = FlowFile(content=item_json, attributes=flowfile.get_attributes())
            ff.set_attribute("split.index", str(i))
            ff.set_attribute("split.count", str(len(target)))
            ff.set_attribute("segment.original.filename",
                           flowfile.get_attribute("filename") or "")
            results.append(ff)

        return results


TaskFactory.register(SplitJSONTask)
