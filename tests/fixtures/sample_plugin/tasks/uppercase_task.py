"""Sample plugin task: converts FlowFile content to uppercase."""

from core import Task, FlowFile
from typing import Dict, Any, List


class UppercaseTask(Task):
    TYPE = "uppercase"
    VERSION = "1.0.0"
    NAME = "Uppercase"
    DESCRIPTION = "Converts FlowFile content to uppercase"
    ICON = "text"

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        content = flowfile.get_content().decode("utf-8", errors="replace")
        flowfile.set_content(content.upper().encode("utf-8"))
        flowfile.set_attribute("plugin.task", "uppercase")
        return [flowfile]
