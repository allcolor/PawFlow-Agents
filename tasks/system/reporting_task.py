"""Reporting Task - collects and reports execution metrics."""

import json
from datetime import datetime

from core.base_task import BaseTask
from core import TaskFactory, FlowFile
from engine.provenance import get_provenance_repository
from core.bulletin import BulletinBoard


class ReportingTask(BaseTask):
    TYPE = "reporting"
    VERSION = "1.0.0"
    NAME = "Reporting Task"
    DESCRIPTION = "Collects and reports execution metrics as FlowFile content"
    ICON = "📊"

    @classmethod
    def get_parameter_schema(cls):
        return {
            "report_type": {
                "type": "string",
                "required": False,
                "default": "summary",
                "description": "Report type: summary, provenance, bulletin",
            },
            "format": {
                "type": "string",
                "required": False,
                "default": "json",
                "description": "Output format: json, text",
            },
        }

    def execute(self, flowfile):
        report_type = self.config.get("report_type", "summary")
        fmt = self.config.get("format", "json")
        now = datetime.now().isoformat()

        if report_type == "summary":
            repo = get_provenance_repository()
            data = repo.to_dict()
            board = BulletinBoard.get_instance()
            data["bulletin_counts"] = board.count_by_level()

        elif report_type == "provenance":
            repo = get_provenance_repository()
            events = repo.get_events(limit=100)
            data = [e.to_dict() for e in events]

        elif report_type == "bulletin":
            board = BulletinBoard.get_instance()
            data = board.get_messages(limit=100)

        else:
            data = {"error": f"Unknown report_type: {report_type}"}

        if fmt == "json":
            content = json.dumps(data, indent=2, ensure_ascii=False, default=str).encode("utf-8")
        else:
            if isinstance(data, list):
                content = "\n".join(str(item) for item in data).encode("utf-8")
            elif isinstance(data, dict):
                lines = [f"{k}: {v}" for k, v in data.items()]
                content = "\n".join(lines).encode("utf-8")
            else:
                content = str(data).encode("utf-8")

        output = FlowFile(content=content, attributes=flowfile.get_attributes())
        output.set_attribute("report.type", report_type)
        output.set_attribute("report.format", fmt)
        output.set_attribute("report.timestamp", now)

        return [output]


TaskFactory.register(ReportingTask)
