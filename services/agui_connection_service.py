"""Reusable scoped connection settings for external AG-UI agents."""

from core import ServiceError, ServiceFactory
from core.base_service import BaseService


class AguiConnectionService(BaseService):
    TYPE = "aguiConnection"
    CATEGORY = "ai"
    VERSION = "1.0.0"
    NAME = "AG-UI Connection"
    DESCRIPTION = "Connection to an external AG-UI agent endpoint"

    def _create_connection(self):
        if not str(self.config.get("endpoint") or "").strip():
            raise ServiceError("endpoint is required")
        return {"ready": True}

    def _close_connection(self):
        pass

    def runtime_config(self) -> dict:
        return {
            "agui_url": str(self.config.get("endpoint") or "").strip(),
            "agui_auth_secret": str(self.config.get("auth_secret") or "").strip(),
            "agui_allow_private": bool(self.config.get("allow_private", False)),
            "agui_timeout": max(1, int(self.config.get("timeout") or 300)),
            "agui_max_tool_rounds": max(
                0, min(32, int(
                    self.config.get("max_tool_rounds")
                    if self.config.get("max_tool_rounds") not in (None, "")
                    else 8))),
        }

    def health_check(self):
        return {"ready": bool(self.config.get("endpoint")),
                "endpoint": str(self.config.get("endpoint") or "")}

    def get_parameter_schema(self):
        return {
            "endpoint": {"type": "string", "required": True,
                         "description": "Full AG-UI POST/SSE endpoint URL"},
            "auth_secret": {"type": "string", "required": False, "default": "",
                            "description": "SecretStore key containing the Bearer token"},
            "allow_private": {"type": "boolean", "required": False, "default": False,
                              "description": "Allow private/loopback or relay targets"},
            "timeout": {"type": "integer", "required": False, "default": 300,
                        "description": "SSE read timeout in seconds"},
            "max_tool_rounds": {"type": "integer", "required": False, "default": 8,
                                "description": "Maximum remote tool-result follow-up runs (0-32)"},
        }


ServiceFactory.register(AguiConnectionService)
