"""Split JSON array into individual FlowFiles."""

import json
import time

from core import TaskError, TaskFactory
from core.base_task import BaseTask


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
            "max_fragments": {
                "type": "integer", "required": False, "default": 0,
                "description": "Maximum emitted FlowFiles per input; 0 means unlimited",
            },
            "empty_relationship": {
                "type": "string", "required": False, "default": "",
                "description": (
                    "Optional relationship that emits one marker for an empty "
                    "collection; omitted preserves the legacy zero-output behavior"
                ),
            },
            "started_at_attribute": {
                "type": "string", "required": False,
                "default": "fragment.started_at",
                "description": "Attribute stamped once for the correlated wave",
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

        max_fragments = self.config.get("max_fragments", 0)
        if (isinstance(max_fragments, bool) or not isinstance(max_fragments, int)
                or max_fragments < 0):
            raise TaskError("max_fragments must be an integer >= 0")
        if max_fragments > 0 and len(target) > max_fragments:
            raise TaskError(
                f"JSON split contains {len(target)} items; limit is {max_fragments}")

        fragment_id = flowfile.process_id
        started_at = str(time.time())
        started_at_attribute = str(
            self.config.get("started_at_attribute", "fragment.started_at") or "")
        if not target:
            empty_relationship = str(
                self.config.get("empty_relationship") or "").strip()
            if not empty_relationship:
                return []
            empty = flowfile.clone()
            empty.set_content(b"[]")
            empty.set_attribute("fragment.identifier", fragment_id)
            empty.set_attribute("fragment.index", "0")
            empty.set_attribute("fragment.count", "1")
            empty.set_attribute("fragment.empty", "true")
            if started_at_attribute:
                empty.set_attribute(started_at_attribute, started_at)
            empty.set_attribute(
                "route.relationship",
                empty_relationship,
            )
            return [empty]

        results = []
        for i, item in enumerate(target):
            item_json = json.dumps(item, ensure_ascii=False).encode("utf-8")
            ff = flowfile.clone()
            ff.set_content(item_json)
            ff.set_attribute("fragment.identifier", fragment_id)
            ff.set_attribute("fragment.index", str(i))
            ff.set_attribute("fragment.count", str(len(target)))
            if started_at_attribute:
                ff.set_attribute(started_at_attribute, started_at)
            ff.set_attribute("route.relationship", "success")
            # Legacy aliases retained until the one-shot migration removes them.
            ff.set_attribute("split.index", str(i))
            ff.set_attribute("split.count", str(len(target)))
            ff.set_attribute("segment.original.filename",
                           flowfile.get_attribute("filename") or "")
            results.append(ff)

        return results

    def get_output_relationships(self):
        empty_relationship = str(
            self.config.get("empty_relationship") or "").strip()
        return ["success", empty_relationship] if empty_relationship else ["success"]


TaskFactory.register(SplitJSONTask)
