"""Detect duplicate FlowFiles using cache."""

import hashlib
from core.base_task import BaseTask
from core import TaskFactory, FlowFile
from services.distributed_cache import get_default_cache


class DetectDuplicateTask(BaseTask):
    TYPE = "detectDuplicate"
    VERSION = "1.0.0"
    NAME = "Detect Duplicate"
    DESCRIPTION = "Detects duplicate FlowFiles based on content hash or attribute"
    ICON = "🔍"
    RELATIONSHIPS = ["non-duplicate", "duplicate"]

    @classmethod
    def get_parameter_schema(cls):
        return {
            "cache_entry_identifier": {
                "type": "string", "required": False, "default": "",
                "description": "Attribute name to use as cache key. Empty = use content hash.",
            },
        }

    def execute(self, flowfile):
        attr_name = self.config.get("cache_entry_identifier", "")

        if attr_name:
            cache_key = flowfile.get_attribute(attr_name) or ""
        else:
            cache_key = hashlib.sha256(flowfile.get_content()).hexdigest()

        cache = get_default_cache()
        is_duplicate = cache.contains(f"dedup:{cache_key}")

        output = FlowFile(content=flowfile.get_content(), attributes=flowfile.get_attributes())

        if is_duplicate:
            output.set_attribute("duplicate", "true")
            output.set_attribute("duplicate.key", cache_key)
        else:
            cache.put(f"dedup:{cache_key}", b"1")
            output.set_attribute("duplicate", "false")
            output.set_attribute("duplicate.key", cache_key)

        return [output]


TaskFactory.register(DetectDuplicateTask)
