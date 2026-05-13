"""PawFlow package management tool handler."""

from __future__ import annotations

import json
from typing import Any, Dict

from core.tool_handler import ToolHandler


class ManagePackageHandler(ToolHandler):
    """Manage signed PawFlow Package (.pfp) artifacts."""

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "manage_package"

    @property
    def description(self) -> str:
        return (
            "Manage PawFlow packages (.pfp). Actions: key_create, build, "
            "inspect, install, update, uninstall, list_installed, export, dev_load, dev_unload, registry_add, "
            "registry_remove, registry_list, search, reload_tasks. Packages are "
            "signed zip artifacts; install is selective and records provenance."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "path": {"type": "string"},
                "ref": {"type": "string"},
                "url": {"type": "string"},
                "name": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "sha256": {"type": "string"},
                "source_dir": {"type": "string"},
                "output_path": {"type": "string"},
                "private_key": {"type": "string"},
                "private_key_env": {"type": "string"},
                "package": {"type": "string"},
                "version": {"type": "string"},
                "scope": {"type": "string", "enum": ["user", "conversation", "conv"]},
                "include": {"type": "array", "items": {"type": "string"}},
                "exclude": {"type": "array", "items": {"type": "string"}},
                "force": {"type": "boolean"},
                "replace": {"type": "boolean"},
                "dry_run": {"type": "boolean"},
                "output_dir": {"type": "string"},
                "secret_bindings": {"type": "object"},
            },
            "required": ["action"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        from core import pfp_package

        action = str(arguments.get("action") or "")
        try:
            if action == "key_create":
                result = pfp_package.create_signing_key()
            elif action == "build":
                result = pfp_package.build_pfp(
                    arguments.get("source_dir") or arguments.get("path") or "",
                    arguments.get("output_path") or "",
                    private_key=arguments.get("private_key") or "",
                    private_key_env=arguments.get("private_key_env") or "",
                )
            elif action == "inspect":
                resolved = self._resolve_package_path(arguments)
                result = pfp_package.inspect_pfp(
                    resolved["path"],
                    user_id=self._user_id,
                    conversation_id=self._conversation_id,
                    scope=arguments.get("scope") or "user",
                )
                if resolved.get("downloaded"):
                    result["download"] = resolved
            elif action == "install":
                resolved = self._resolve_package_path(arguments)
                result = pfp_package.install_pfp(
                    resolved["path"],
                    user_id=self._user_id,
                    conversation_id=self._conversation_id,
                    scope=arguments.get("scope") or "user",
                    include=arguments.get("include") or None,
                    exclude=arguments.get("exclude") or None,
                    force=bool(arguments.get("force", False)),
                    replace=bool(arguments.get("replace", False)),
                    dry_run=bool(arguments.get("dry_run", False)),
                    secret_bindings=arguments.get("secret_bindings") or {},
                )
                if resolved.get("downloaded"):
                    result["download"] = resolved
            elif action == "update":
                resolved = self._resolve_package_path(arguments)
                result = pfp_package.update_pfp(
                    resolved["path"],
                    user_id=self._user_id,
                    conversation_id=self._conversation_id,
                    scope=arguments.get("scope") or "user",
                    include=arguments.get("include") or None,
                    exclude=arguments.get("exclude") or None,
                    force=bool(arguments.get("force", False)),
                    dry_run=bool(arguments.get("dry_run", False)),
                    secret_bindings=arguments.get("secret_bindings") or {},
                )
                if resolved.get("downloaded"):
                    result["download"] = resolved
            elif action == "uninstall":
                result = pfp_package.uninstall_pfp(
                    arguments.get("package") or "",
                    user_id=self._user_id,
                    conversation_id=self._conversation_id,
                    scope=arguments.get("scope") or "user",
                    force=bool(arguments.get("force", False)),
                )
            elif action == "list_installed":
                result = pfp_package.list_installed_packages(
                    user_id=self._user_id,
                    conversation_id=self._conversation_id,
                    scope=arguments.get("scope") or "user",
                )
            elif action in {"dev_load", "dev-load"}:
                result = pfp_package.dev_load_pfp(
                    arguments.get("source_dir") or arguments.get("path") or "",
                    user_id=self._user_id,
                    conversation_id=self._conversation_id,
                    scope=arguments.get("scope") or "conversation",
                    include=arguments.get("include") or None,
                    exclude=arguments.get("exclude") or None,
                    force=bool(arguments.get("force", True)),
                    replace=bool(arguments.get("replace", True)),
                    dry_run=bool(arguments.get("dry_run", False)),
                    secret_bindings=arguments.get("secret_bindings") or {},
                )
            elif action in {"dev_unload", "dev-unload"}:
                result = pfp_package.dev_unload_pfp(
                    arguments.get("package") or arguments.get("name") or "",
                    user_id=self._user_id,
                    conversation_id=self._conversation_id,
                    scope=arguments.get("scope") or "conversation",
                    force=bool(arguments.get("force", True)),
                )
            elif action == "reload_tasks":
                result = pfp_package.load_installed_package_tasks(
                    user_id=self._user_id,
                    conversation_id=self._conversation_id,
                    scope=arguments.get("scope") or "user",
                )
            elif action == "export":
                result = pfp_package.export_pfpdir(
                    arguments.get("package") or "",
                    arguments.get("version") or "",
                    arguments.get("include") or [],
                    output_dir=arguments.get("output_dir") or arguments.get("path") or "",
                    user_id=self._user_id,
                    conversation_id=self._conversation_id,
                )
            elif action == "registry_add":
                from core import pfp_registry
                result = pfp_registry.add_registry(
                    arguments.get("url") or arguments.get("path") or "",
                    user_id=self._user_id,
                    name=arguments.get("name") or "",
                )
            elif action == "registry_remove":
                from core import pfp_registry
                result = pfp_registry.remove_registry(
                    arguments.get("name") or arguments.get("url") or arguments.get("path") or "",
                    user_id=self._user_id,
                )
            elif action == "registry_list":
                from core import pfp_registry
                result = pfp_registry.list_registries(user_id=self._user_id)
            elif action == "search":
                from core import pfp_registry
                result = pfp_registry.search_registries(
                    arguments.get("query") or "",
                    user_id=self._user_id,
                    limit=int(arguments.get("limit") or 20),
                )
            else:
                result = {"error": f"Unknown package action: {action}"}
        except Exception as exc:
            result = {"error": str(exc)}
        return json.dumps(result, ensure_ascii=False, indent=2)

    def _resolve_package_path(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        from core import pfp_registry

        return pfp_registry.resolve_package_path(
            arguments.get("path") or arguments.get("ref") or "",
            user_id=self._user_id,
            expected_sha256=arguments.get("sha256") or "",
        )

