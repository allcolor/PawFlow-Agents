# Funnel Task

"""
Task Funnel - Merge multiple connexions en une seule sortie.
"""

from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class FunnelTask(BaseTask):
    """Merge multiple connexions en une seule sortie (pass-through)."""

    TYPE = "funnel"
    VERSION = "1.0.0"
    NAME = "Funnel"
    DESCRIPTION = "Merge multiple connexions en une seule sortie"
    ICON = "git-merge"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {}


TaskFactory.register(FunnelTask)
