"""ListenHTTP - receive FlowFiles via HTTP POST."""

from datetime import datetime
from core.base_task import BaseTask
from core import TaskFactory, FlowFile


class ListenHTTPTask(BaseTask):
    TYPE = "listenHTTP"
    VERSION = "1.0.0"
    NAME = "Listen HTTP"
    DESCRIPTION = "Generates a FlowFile from HTTP request data (simulated for pipeline use)"
    ICON = "🌐"

    @classmethod
    def get_parameter_schema(cls):
        return {
            "port": {
                "type": "integer", "required": False, "default": 8080,
                "description": "HTTP listening port (informational)",
            },
            "base_path": {
                "type": "string", "required": False, "default": "/contentListener",
                "description": "Base path for the HTTP endpoint",
            },
        }

    def execute(self, flowfile):
        port = self.config.get("port", 8080)
        base_path = self.config.get("base_path", "/contentListener")

        output = FlowFile(content=flowfile.get_content(), attributes=flowfile.get_attributes())
        output.set_attribute("http.listener.port", str(port))
        output.set_attribute("http.listener.path", base_path)
        output.set_attribute("http.received.timestamp", datetime.now().isoformat())
        return [output]


TaskFactory.register(ListenHTTPTask)
