"""Immutable template download and extraction task for Website Creator."""

from __future__ import annotations

import json
import posixpath
from typing import Any, ClassVar

from core import FlowFile, TaskFactory
from core.agent_contracts import CapabilityEffect, IdempotencyClass
from core.website_creator_templates import (
    MAX_TEMPLATE_ARCHIVE_BYTES,
    TEMPLATE_CATALOG_VERSION,
    resolve_template,
    template_cache_identity,
)
from core.workflow_agent_contracts import AgentWorkflowRequest
from tasks.ai.workflow.turn_tasks import _WorkflowContextTask
from tasks.ai.workflow.website_creator_tasks import _load_state, _store_state


def _workspace_path(workspace: str, relative: str) -> str:
    return posixpath.join(workspace.rstrip("/"), relative)


def _atomic_bytes(service, path: str, content: bytes) -> None:
    writer = getattr(service, "atomic_write_file", None)
    if not callable(writer):
        raise ValueError("Website Creator relay lacks atomic_write_file capability")
    writer(path, content, local=False)


class DownloadTemplateTask(_WorkflowContextTask):
    """Download, verify and materialize one exact reviewed template archive."""

    TYPE = "downloadTemplate"
    NAME = "Download Website Template"
    DESCRIPTION = "Download and verify one immutable catalog template."
    EFFECTS = (
        CapabilityEffect.RESOURCE_READ,
        CapabilityEffect.FILESYSTEM_READ,
        CapabilityEffect.FILESYSTEM_WRITE,
    )
    IDEMPOTENCY = IdempotencyClass.KEYED_EFFECT
    RELATIONSHIPS: ClassVar = ["success", "failure"]
    AGENT_WORKFLOW_SAFE = True
    AUTHORIZATION_TARGET_KIND = "website.template"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._website_fs_service = None

    def _resolve_relay(self, state: dict[str, Any]):
        if self._website_fs_service is not None:
            service = self._website_fs_service
            state["website"]["relay_id"] = str(
                getattr(service, "_service_id", None)
                or state["website"].get("relay_id")
                or "test-relay"
            )
            return service
        context = self._context()
        website = state["website"]
        request = AgentWorkflowRequest.from_dict(state["request"])
        requested = str(
            request.parameters.get("relay") or website.get("relay_id") or ""
        ).strip()
        from core.relay_bindings import get_default, get_linked
        if not requested:
            requested = str(
                get_default(context.conversation_id, agent=context.agent_name) or ""
            )
        if not requested:
            linked = tuple(
                get_linked(context.conversation_id, agent=context.agent_name)
            )
            if len(linked) == 1:
                requested = str(linked[0])
        if not requested:
            raise ValueError(
                "Website Creator requires a configured, default or sole linked relay"
            )
        from core.service_registry import ServiceRegistry
        service = ServiceRegistry.get_instance().resolve(
            requested, user_id=context.user_id, conv_id=context.conversation_id,
        )
        if service is None:
            raise ValueError("Website Creator relay is not available")
        website["relay_id"] = requested
        return service

    def workflow_authorization_target(self, flowfile: FlowFile) -> dict[str, Any]:
        state = _load_state(flowfile)
        website = dict(state.get("website") or {})
        workspace = str(website.get("workspace") or "")
        context = self._context()
        return {
            "user_id": context.user_id,
            "conversation_id": context.conversation_id,
            "relay_id": str(website.get("relay_id") or ""),
            "workspace": workspace,
            "paths": [
                _workspace_path(workspace, relative)
                for relative in (
                    "template/.archive.zip", "template/content",
                    "template/manifest.json", "site/THIRD_PARTY_NOTICES.txt",
                )
            ] if workspace else [],
        }

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load_state(flowfile)
        website = state.get("website")
        if not isinstance(website, dict):
            raise ValueError("Website Creator template download requires website state")
        workspace = str(website.get("workspace") or "").strip()
        if not workspace:
            raise ValueError("Website Creator template download requires a workspace")
        entry = resolve_template(str(website.get("template_url") or ""))
        identity = template_cache_identity(entry)
        previous = website.get("template")
        if isinstance(previous, dict) and previous.get("cache_identity") == identity:
            return _store_state(flowfile, state)

        service = self._resolve_relay(state)
        archive_path = _workspace_path(workspace, "template/.archive.zip")
        content_path = _workspace_path(workspace, "template/content")
        service.mkdir(_workspace_path(workspace, "template"), local=False)
        service.mkdir(_workspace_path(workspace, "site"), local=False)
        response = service.http_fetch_to_file(
            entry["package_url"],
            archive_path,
            headers={"User-Agent": "PawFlow Website Creator"},
            timeout=300,
            max_bytes=MAX_TEMPLATE_ARCHIVE_BYTES,
            public_only=True,
            local=False,
        )
        actual_hash = str(response.get("sha256") or "").casefold()
        if actual_hash != entry["sha256"]:
            service.delete_file(archive_path, local=False)
            raise ValueError("Website Creator template SHA-256 mismatch")
        final_url = str(response.get("url") or "")
        if final_url != entry["package_url"]:
            service.delete_file(archive_path, local=False)
            raise ValueError("Website Creator template redirected away from immutable source")
        extractor = getattr(service, "extract_zip_subtree", None)
        if not callable(extractor):
            raise ValueError("Website Creator relay lacks extract_zip_subtree capability")
        extraction = extractor(
            archive_path,
            content_path,
            artifact_root=entry["artifact_root"],
            local=False,
        )
        notice = (
            f"{entry['attribution']}\n"
            f"License: {entry['license']}\n"
            f"Immutable source: {entry['package_url']}\n"
            f"SHA-256: {entry['sha256']}\n"
        ).encode("utf-8")
        notice_path = _workspace_path(workspace, "site/THIRD_PARTY_NOTICES.txt")
        _atomic_bytes(service, notice_path, notice)
        record = {
            **entry,
            "catalog_version": TEMPLATE_CATALOG_VERSION,
            "cache_identity": identity,
            "archive_bytes": int(response.get("bytes") or 0),
            "content_path": content_path,
            "content_sha256": str(extraction.get("sha256") or ""),
            "file_count": int(extraction.get("files") or 0),
            "notice_path": notice_path,
        }
        manifest_path = _workspace_path(workspace, "template/manifest.json")
        _atomic_bytes(service, manifest_path, json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8"))
        website["template"] = record
        return _store_state(flowfile, state)


TaskFactory.register(DownloadTemplateTask)
