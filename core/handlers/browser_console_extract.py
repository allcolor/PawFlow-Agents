"""Fixed-script rendered DOM extraction for the Website Creator workflow."""

from __future__ import annotations

import json
import posixpath
from typing import Any, Dict

import jsonschema

from core.agent_contracts import CapabilityEffect
from core.handlers._fs_base import BaseFsHandler


SCRIPT_IDS = (
    "rendered_inventory_v1",
    "dom_outline_v1",
    "computed_assets_v1",
)
MAX_EXTRACTION_BYTES = 32 * 1024 * 1024
_OPTIONS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "max_items": {"type": "integer", "minimum": 1, "maximum": 5000},
        "include_hidden": {"type": "boolean"},
    },
}


class BrowserConsoleExtractHandler(BaseFsHandler):
    """Invoke one shipped browser script against one workflow-bound target."""

    EFFECTS = (
        CapabilityEffect.BROWSER_CONTROL,
        CapabilityEffect.FILESYSTEM_WRITE,
    )

    def __init__(
        self,
        workspace: str,
        *,
        session_id: str,
        target_id: str,
        approved_origin: str,
        inventory_budget_bytes: int,
    ):
        super().__init__()
        self.workspace = str(workspace).rstrip("/")
        self.session_id = str(session_id)
        self.target_id = str(target_id)
        self.approved_origin = str(approved_origin)
        self.inventory_budget_bytes = int(inventory_budget_bytes or MAX_EXTRACTION_BYTES)

    @property
    def name(self) -> str:
        return "browser_console_extract"

    @property
    def description(self) -> str:
        return (
            "Extract bounded rendered DOM evidence with one shipped fixed script. "
            "No model-authored JavaScript is accepted. The target, public origin, "
            "run inventory budget and output path are enforced."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "script_id": {"type": "string", "enum": list(SCRIPT_IDS)},
                "target_id": {"type": "string", "const": self.target_id},
                "options": _OPTIONS_SCHEMA,
                "write_to": {"type": "string", "minLength": 1, "maxLength": 4096},
                "timeout": {
                    "type": "integer", "minimum": 1, "maximum": 30, "default": 10,
                },
            },
            "required": ["script_id", "target_id"],
        }

    def set_service(self, service) -> None:
        self.set_fs_service(service)

    def _confined_path(self, value: str, script_id: str) -> str:
        raw = str(value or f"inventory/{script_id}.json")
        if not raw.startswith("/"):
            raw = posixpath.join(self.workspace, raw)
        normalized = posixpath.normpath(raw)
        if not normalized.startswith(self.workspace + "/"):
            raise ValueError("browser extraction output must stay in the run workspace")
        return normalized

    def _ledger(self, service) -> tuple[str, dict[str, Any]]:
        path = posixpath.join(self.workspace, "inventory/browser-extractions.json")
        if not service.exists(path, local=False):
            return path, {"schema_version": 1, "records": {}}
        try:
            value = json.loads(service.read_file(path, local=False).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("browser extraction ledger is invalid") from exc
        if not isinstance(value, dict) or not isinstance(value.get("records"), dict):
            raise ValueError("browser extraction ledger is invalid")
        return path, value

    def execute(self, arguments: Dict[str, Any]) -> str:
        if str(arguments.get("target_id") or "") != self.target_id:
            raise ValueError("browser extraction target_id does not match the workflow target")
        jsonschema.Draft202012Validator(self.parameters_schema).validate(arguments)
        script_id = str(arguments["script_id"])
        output_path = self._confined_path(str(arguments.get("write_to") or ""), script_id)
        service = self._fs_service
        if service is None:
            service, workdir = self._resolve("")
            if service is None or workdir is not None:
                raise ValueError("browser extraction requires the selected relay")
        ledger_path, ledger = self._ledger(service)
        records = ledger["records"]
        record_key = f"{self.target_id}:{script_id}:{output_path}"
        previous_bytes = int((records.get(record_key) or {}).get("bytes") or 0)
        used = sum(int((record or {}).get("bytes") or 0) for record in records.values())
        remaining = self.inventory_budget_bytes - used + previous_bytes
        if remaining < 1:
            raise ValueError("Website Creator browser inventory budget is exhausted")
        timeout = int(arguments.get("timeout") or 10)
        result = service._request(
            "browser_console_extract",
            ".",
            session_id=self.session_id,
            target_id=self.target_id,
            approved_origin=self.approved_origin,
            script_id=script_id,
            options=dict(arguments.get("options") or {}),
            write_to=output_path,
            timeout=timeout,
            max_bytes=min(MAX_EXTRACTION_BYTES, remaining),
            local=False,
        )
        records[record_key] = {
            "bytes": int(result.get("bytes") or 0),
            "path": str(result.get("path") or output_path),
            "sha256": str(result.get("sha256") or ""),
        }
        service.atomic_write_file(
            ledger_path,
            json.dumps(ledger, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            local=False,
        )
        return json.dumps(result, ensure_ascii=False, sort_keys=True)
