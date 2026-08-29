"""File-backed batch and deterministic finalization tasks for Website Creator."""

from __future__ import annotations

import hashlib
import json
import posixpath
from typing import Any, ClassVar, Mapping, Sequence

from core import FlowFile, TaskFactory
from core.agent_contracts import CapabilityEffect, IdempotencyClass
from core.website_creator_batches import (
    BATCH_SCHEMA_VERSION,
    BATCH_SIZE,
    StaticSiteFinalizer,
    WebsiteBatchCoordinator,
)
from core.website_creator_contracts import (
    ReferenceKind,
    assign_local_page_paths,
    canonicalize_url,
    stable_record_id,
)
from tasks.ai.workflow.website_creator_crawl import _WebsiteCrawlTask
from tasks.ai.workflow.website_creator_tasks import (
    WebsiteCreatorToolTask,
    _load_state,
    _store_state,
)


_MAPPING_BATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entries"],
    "properties": {
        "entries": {
            "type": "array",
            "minItems": 1,
            "maxItems": BATCH_SIZE,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "page_url", "local_path", "template_component",
                    "implementation", "notes",
                ],
                "properties": {
                    "page_url": {"type": "string", "format": "uri", "maxLength": 8192},
                    "local_path": {"type": "string", "minLength": 1, "maxLength": 4096},
                    "template_component": {
                        "type": "string", "minLength": 1, "maxLength": 2000,
                    },
                    "implementation": {
                        "type": "string", "minLength": 1, "maxLength": 8000,
                    },
                    "notes": {"type": "string", "maxLength": 4000},
                },
            },
        },
    },
}


_BUILD_BATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "pages_built", "skipped_pages", "assets_materialized",
        "files_changed", "validation", "remaining_issues",
    ],
    "properties": {
        "pages_built": {
            "type": "array", "maxItems": BATCH_SIZE,
            "items": {"type": "string", "format": "uri", "maxLength": 8192},
        },
        "skipped_pages": {
            "type": "array", "maxItems": BATCH_SIZE,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["page_url", "reason", "decision_id"],
                "properties": {
                    "page_url": {"type": "string", "format": "uri", "maxLength": 8192},
                    "reason": {
                        "enum": [
                            "accepted_omission", "explicit_user_request", "not_applicable",
                        ],
                    },
                    "decision_id": {"type": "string", "minLength": 1, "maxLength": 512},
                },
            },
        },
        "assets_materialized": {
            "type": "array", "maxItems": 1000,
            "items": {"type": "string", "minLength": 1, "maxLength": 4096},
        },
        "files_changed": {
            "type": "array", "maxItems": 1000,
            "items": {"type": "string", "minLength": 1, "maxLength": 4096},
        },
        "validation": {
            "type": "array", "maxItems": 200,
            "items": {"type": "string", "minLength": 1, "maxLength": 4000},
        },
        "remaining_issues": {
            "type": "array", "maxItems": 200,
            "items": {
                "oneOf": [
                    {"type": "string", "minLength": 1, "maxLength": 4000},
                    {"type": "object"},
                ],
            },
        },
    },
}


def _read_json(service, path: str) -> dict[str, Any]:
    try:
        value = json.loads(service.read_file(path, local=False))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Website Creator JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Website Creator JSON must contain an object: {path}")
    return value


def _read_ndjson(service, path: str, *, maximum: int = 10000) -> list[dict[str, Any]]:
    content = service.read_file(path, local=False)
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            continue
        if len(records) >= maximum:
            raise ValueError("Website Creator record file exceeds its bounded count")
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"invalid Website Creator NDJSON at {path}:{line_number}"
            ) from exc
        if not isinstance(value, dict):
            raise ValueError(f"Website Creator NDJSON records must be objects: {path}")
        records.append(value)
    return records


def _website_path(workspace: str, relative: str) -> str:
    return posixpath.join(workspace.rstrip("/"), relative)


def _batch_state(website: Mapping[str, Any], phase: str) -> dict[str, Any]:
    value = dict((website.get("batches") or {}).get(phase) or {})
    contract = value.get("contract")
    if not value or not isinstance(contract, dict):
        raise ValueError(f"Website Creator {phase} batches are not prepared")
    return value


def _coordinator(service, website: Mapping[str, Any], phase: str) -> WebsiteBatchCoordinator:
    state = _batch_state(website, phase)
    contract = state["contract"]
    return WebsiteBatchCoordinator(
        service,
        str(website["workspace"]),
        phase=phase,
        manifest_digest=str(contract["inventory_manifest_digest"]),
        template_digest=str(contract.get("template_digest") or ""),
        mapping_revision=str(contract.get("mapping_revision") or ""),
    )


class _WebsiteBatchIoTask(_WebsiteCrawlTask):
    """Exact-relay file authorization shared by batch/finalizer tasks."""

    AGENT_WORKFLOW_SAFE = True
    AUTHORIZATION_TARGET_KIND = "website.batch"

    def workflow_authorization_target(self, flowfile: FlowFile) -> dict[str, Any]:
        state = _load_state(flowfile)
        website = dict(state.get("website") or {})
        context = self._context()
        workspace = str(website.get("workspace") or "")
        return {
            "user_id": context.user_id,
            "conversation_id": context.conversation_id,
            "relay_id": str(website.get("relay_id") or ""),
            "workspace": workspace,
            "paths": [
                _website_path(workspace, relative)
                for relative in (
                    "inventory", "mapping", "build", "correction",
                    "assets/manifest", "site", "reports/finalize.json",
                )
            ] if workspace else [],
        }


class _PrepareWebsiteBatchesTask(_WebsiteBatchIoTask):
    EFFECTS = (
        CapabilityEffect.RESOURCE_READ,
        CapabilityEffect.FILESYSTEM_READ,
        CapabilityEffect.FILESYSTEM_WRITE,
    )
    IDEMPOTENCY = IdempotencyClass.KEYED_EFFECT
    RELATIONSHIPS: ClassVar = ["batch", "complete", "failure"]
    PHASE = ""

    def _mapping_entries(
        self,
        service,
        website: dict[str, Any],
    ) -> list[dict[str, Any]]:
        workspace = str(website["workspace"])
        inventory = dict(website.get("inventory") or {})
        manifest_digest = str(inventory.get("manifest_digest") or "")
        complete_path = str(
            inventory.get("complete_path")
            or _website_path(workspace, "inventory/complete.json")
        )
        if not service.exists(complete_path, local=False):
            raise ValueError("Website Creator inventory completion manifest is missing")
        actual = str(service.hash_file(complete_path, local=False).get("sha256") or "")
        if actual != manifest_digest:
            raise ValueError("Website Creator inventory manifest digest mismatch")
        records = _read_ndjson(
            service,
            _website_path(workspace, "inventory/pages.ndjson"),
            maximum=2000,
        )
        accepted = [
            record for record in records
            if 200 <= int(record.get("status") or 0) < 400
            and not str(record.get("error") or "")
            and not str(record.get("omission_reason") or "")
        ]
        urls = [canonicalize_url(str(record.get("canonical_url") or "")) for record in accepted]
        paths = assign_local_page_paths(urls)
        return [
            {
                "page_url": url,
                "local_path": paths[url],
                "source_record_id": str(record.get("record_id") or ""),
                "raw_html_path": str(record.get("raw_html_path") or ""),
            }
            for record, url in zip(accepted, urls)
        ]

    @staticmethod
    def _merged_mapping(service, website: Mapping[str, Any]) -> dict[str, Any]:
        mapping = dict(website.get("mapping") or {})
        path = str(mapping.get("result_path") or "")
        if not path:
            raise ValueError("Website Creator approved mapping result is missing")
        merged = _read_json(service, path)
        if merged.get("result_digest") != mapping.get("result_digest"):
            raise ValueError("Website Creator mapping revision mismatch")
        return merged

    def _build_entries(
        self,
        service,
        website: dict[str, Any],
    ) -> list[dict[str, Any]]:
        merged = self._merged_mapping(service, website)
        entries = list(merged.get("entries") or [])
        return [
            {
                **dict(entry),
                "source_record_id": stable_record_id(
                    ReferenceKind.INTERNAL_PAGE,
                    canonicalize_url(str(entry.get("page_url") or "")),
                ),
                "raw_html_path": "",
            }
            for entry in entries
        ]

    @staticmethod
    def _issue_values(website: Mapping[str, Any]) -> list[Any]:
        values: list[Any] = []
        finalize = dict(website.get("finalize") or {})
        values.extend(list(finalize.get("issue_summary") or []))
        review = dict(website.get("review") or {})
        values.extend(list(review.get("issues") or []))
        feedback = str(website.get("user_feedback") or "").strip()
        if feedback:
            values.append(feedback)
        return values

    def _correction_entries(
        self,
        service,
        website: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str]:
        entries = self._build_entries(service, website)
        issues = self._issue_values(website)
        normalized_evidence = json.dumps(issues, ensure_ascii=False, sort_keys=True)
        global_requested = bool(website.get("global_correction")) or any(
            phrase in normalized_evidence.casefold()
            for phrase in ("all pages", "site-wide", "site wide", "global correction")
        )
        selected: list[dict[str, Any]] = []
        for entry in entries:
            page_url = entry["page_url"]
            local_path = entry["local_path"]
            matched = []
            for issue in issues:
                if isinstance(issue, Mapping):
                    issue_url = str(issue.get("page_url") or issue.get("url") or "")
                    issue_path = str(issue.get("local_path") or issue.get("path") or "")
                    if issue_url == page_url or local_path in issue_path:
                        matched.append(dict(issue))
                elif page_url in str(issue) or local_path in str(issue):
                    matched.append(str(issue)[:1000])
            if global_requested or matched or (len(entries) == 1 and issues):
                selected.append({**entry, "issues": matched or issues[:50]})
        if not selected:
            raise ValueError(
                "Website Creator correction issues do not identify affected pages; "
                "an explicit global correction is required"
            )
        issue_revision = hashlib.sha256(json.dumps(
            issues, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        return selected, issue_revision

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load_state(flowfile)
        website = state["website"]
        service = self._resolve_relay(state)
        inventory = dict(website.get("inventory") or {})
        manifest_digest = str(inventory.get("manifest_digest") or "")
        template_digest = ""
        mapping_revision = ""
        if self.PHASE == "mapping":
            entries = self._mapping_entries(service, website)
        else:
            template = dict(website.get("template") or {})
            template_digest = str(template.get("sha256") or "")
            mapping = dict(website.get("mapping") or {})
            mapping_revision = str(mapping.get("result_digest") or "")
            if self.PHASE == "build":
                entries = self._build_entries(service, website)
            else:
                entries, issue_revision = self._correction_entries(service, website)
                mapping_revision = hashlib.sha256(
                    f"{mapping_revision}\n{issue_revision}".encode("utf-8")
                ).hexdigest()
        coordinator = WebsiteBatchCoordinator(
            service,
            str(website["workspace"]),
            phase=self.PHASE,
            manifest_digest=manifest_digest,
            template_digest=template_digest,
            mapping_revision=mapping_revision,
        )
        summary = coordinator.prepare(entries)
        summary["contract"] = {
            "inventory_manifest_digest": manifest_digest,
            "template_digest": template_digest,
            "mapping_revision": mapping_revision,
        }
        website.setdefault("batches", {})[self.PHASE] = summary
        website["status"] = f"{self.PHASE}_batches_prepared"
        relationship = (
            "complete"
            if summary["cursor"] >= summary["batch_count"]
            else "batch"
        )
        flowfile.set_attribute("route.relationship", relationship)
        return _store_state(flowfile, state)


class PrepareWebsiteMappingBatchesTask(_PrepareWebsiteBatchesTask):
    TYPE = "prepareWebsiteMappingBatches"
    NAME = "Prepare Website Mapping Batches"
    DESCRIPTION = "Partition accepted inventory pages into stable mapping batches."
    PHASE = "mapping"


class PrepareWebsiteBuildBatchesTask(_PrepareWebsiteBatchesTask):
    TYPE = "prepareWebsiteBuildBatches"
    NAME = "Prepare Website Build Batches"
    DESCRIPTION = "Partition the approved mapping into stable build batches."
    PHASE = "build"


class PrepareWebsiteCorrectionBatchesTask(_PrepareWebsiteBatchesTask):
    TYPE = "prepareWebsiteCorrectionBatches"
    NAME = "Prepare Website Correction Batches"
    DESCRIPTION = "Select affected pages and prepare stable correction batches."
    PHASE = "correction"


class _WebsitePageBatchTask(WebsiteCreatorToolTask):
    """Run the existing constrained LLM/tool loop for one current page batch."""

    AGENT_WORKFLOW_SAFE = True
    BATCH_PHASE = ""
    LLM_PHASE = ""
    RELATIONSHIPS: ClassVar = ["more", "complete", "failure"]

    def __init__(self, config: dict[str, Any]):
        merged = dict(config or {})
        merged["phase"] = self.LLM_PHASE
        super().__init__(merged)

    def _submission_schema(self, phase: str) -> dict[str, Any]:
        if phase != self.LLM_PHASE:
            raise ValueError("Website Creator batch LLM phase mismatch")
        return (
            _MAPPING_BATCH_SCHEMA
            if self.BATCH_PHASE == "mapping"
            else _BUILD_BATCH_SCHEMA
        )

    def _prompt(self, phase: str, state: dict[str, Any]) -> tuple[str, str]:
        system, user = WebsiteCreatorToolTask._prompt(phase, state)
        old_schema = json.dumps(
            WebsiteCreatorToolTask._schema(phase),
            ensure_ascii=False,
            sort_keys=True,
        )
        new_schema = json.dumps(
            self._submission_schema(phase),
            ensure_ascii=False,
            sort_keys=True,
        )
        system = system.replace(old_schema, new_schema, 1)
        batch = dict(state["website"].get("current_batch") or {})
        system += (
            "\n\nThis is one file-backed page batch. Read current_batch_path, work "
            "only on those accepted pages, and return exactly one result for each page. "
            "Do not infer or load other inventory batches."
        )
        payload = json.loads(user)
        payload["batch_phase"] = self.BATCH_PHASE
        payload["current_batch"] = batch
        return system, json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load_state(flowfile)
        website = state["website"]
        service = self._resolve_website_relay(state)
        coordinator = _coordinator(service, website, self.BATCH_PHASE)
        batch = coordinator.current_batch()
        if batch is None:
            flowfile.set_attribute("route.relationship", "complete")
            website["status"] = f"{self.BATCH_PHASE}_batches_complete"
            return _store_state(flowfile, state)
        website["current_batch"] = {
            "phase": self.BATCH_PHASE,
            "index": int(batch["index"]),
            "batch_id": str(batch["batch_id"]),
            "entry_count": len(batch.get("entries") or []),
            "current_batch_path": str(
                _batch_state(website, self.BATCH_PHASE)["current_batch_path"]
            ),
            "current_result_path": str(batch["result_path"]),
        }
        _store_state(flowfile, state)
        result = super().execute(flowfile)
        updated = _load_state(flowfile)
        summary = _batch_state(updated["website"], self.BATCH_PHASE)
        relationship = (
            "complete"
            if int(summary["cursor"]) >= int(summary["batch_count"])
            else "more"
        )
        flowfile.set_attribute("route.relationship", relationship)
        return result

    def _commit_phase_submission(
        self,
        state: dict[str, Any],
        phase: str,
        submission: dict[str, Any],
        calls: dict[str, int],
    ) -> None:
        website = state["website"]
        service = self._website_fs_service
        coordinator = _coordinator(service, website, self.BATCH_PHASE)
        current = dict(website.get("current_batch") or {})
        summary = coordinator.store_result(int(current["index"]), submission)
        contract = _batch_state(website, self.BATCH_PHASE)["contract"]
        summary["contract"] = contract
        website.setdefault("batches", {})[self.BATCH_PHASE] = summary
        website.pop("current_batch", None)
        totals = dict(website.get("tool_calls") or {})
        for name, count in calls.items():
            totals[name] = int(totals.get(name, 0)) + count
        website["tool_calls"] = totals
        complete = summary["cursor"] >= summary["batch_count"]
        website["status"] = (
            f"{self.BATCH_PHASE}_batches_complete"
            if complete else f"{self.BATCH_PHASE}_batch_completed"
        )


class MapWebsitePageBatchTask(_WebsitePageBatchTask):
    TYPE = "mapWebsitePageBatch"
    NAME = "Map Website Page Batch"
    DESCRIPTION = "Map one accepted page batch through the constrained visual phase."
    BATCH_PHASE = "mapping"
    LLM_PHASE = "explore"


class BuildWebsitePageBatchTask(_WebsitePageBatchTask):
    TYPE = "buildWebsitePageBatch"
    NAME = "Build Website Page Batch"
    DESCRIPTION = "Build one approved page batch in the run workspace."
    BATCH_PHASE = "build"
    LLM_PHASE = "build"


class CorrectWebsitePageBatchTask(_WebsitePageBatchTask):
    TYPE = "correctWebsitePageBatch"
    NAME = "Correct Website Page Batch"
    DESCRIPTION = "Correct one affected page batch in the run workspace."
    BATCH_PHASE = "correction"
    LLM_PHASE = "correct"


class RouteWebsiteBatchesTask(_WebsiteBatchIoTask):
    TYPE = "routeWebsiteBatches"
    NAME = "Route Website Batches"
    DESCRIPTION = "Route a file-backed phase to its next batch or merge."
    EFFECTS = (CapabilityEffect.RESOURCE_READ,)
    IDEMPOTENCY = IdempotencyClass.PURE
    RELATIONSHIPS: ClassVar = ["batch", "merge", "failure"]

    def get_parameter_schema(self) -> dict[str, Any]:
        return {
            "phase": {
                "type": "select",
                "required": True,
                "options": ["mapping", "build", "correction"],
            },
        }

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load_state(flowfile)
        phase = str(self.config.get("phase") or "").strip().casefold()
        batch = _batch_state(state["website"], phase)
        relationship = (
            "merge"
            if int(batch["cursor"]) >= int(batch["batch_count"])
            else "batch"
        )
        flowfile.set_attribute("route.relationship", relationship)
        return [flowfile]


class _MergeWebsiteBatchesTask(_WebsiteBatchIoTask):
    EFFECTS = (
        CapabilityEffect.RESOURCE_READ,
        CapabilityEffect.FILESYSTEM_READ,
        CapabilityEffect.FILESYSTEM_WRITE,
    )
    IDEMPOTENCY = IdempotencyClass.KEYED_EFFECT
    RELATIONSHIPS: ClassVar = ["success", "failure"]
    PHASE = ""

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load_state(flowfile)
        website = state["website"]
        service = self._resolve_relay(state)
        merged = _coordinator(service, website, self.PHASE).merge()
        if self.PHASE == "mapping":
            website["mapping"] = {
                "result_path": merged["result_path"],
                "result_digest": merged["result_digest"],
                "entry_count": merged["entry_count"],
                "batch_count": _batch_state(website, self.PHASE)["batch_count"],
            }
            website["explore"] = {
                "summary": "File-backed mapping completed for every accepted page.",
                "mapping_path": merged["result_path"],
                "mapping_digest": merged["result_digest"],
                "page_count": merged["entry_count"],
                "batch_count": _batch_state(website, self.PHASE)["batch_count"],
            }
            website["status"] = "mapped"
        elif self.PHASE == "build":
            website["build"] = {
                key: value for key, value in merged.items() if key != "entries"
            }
            website["status"] = "built"
        else:
            website["correction"] = merged
            website["correction_passes"] = int(
                website.get("correction_passes", 0)
            ) + 1
            website["status"] = "corrected"
        flowfile.set_attribute("route.relationship", "success")
        return _store_state(flowfile, state)


class MergeWebsiteMappingTask(_MergeWebsiteBatchesTask):
    TYPE = "mergeWebsiteMapping"
    NAME = "Merge Website Mapping"
    DESCRIPTION = "Verify exactly-once mapping coverage and emit a bounded summary."
    PHASE = "mapping"


class MergeWebsiteBuildTask(_MergeWebsiteBatchesTask):
    TYPE = "mergeWebsiteBuild"
    NAME = "Merge Website Build"
    DESCRIPTION = "Verify exactly-once build coverage and aggregate bounded results."
    PHASE = "build"


class MergeWebsiteCorrectionTask(_MergeWebsiteBatchesTask):
    TYPE = "mergeWebsiteCorrection"
    NAME = "Merge Website Correction"
    DESCRIPTION = "Verify affected-page correction coverage and aggregate results."
    PHASE = "correction"


class FinalizeStaticSiteTask(_WebsiteBatchIoTask):
    TYPE = "finalizeStaticSite"
    NAME = "Finalize Static Website"
    DESCRIPTION = "Rewrite static references and enforce deterministic completeness."
    EFFECTS = (
        CapabilityEffect.RESOURCE_READ,
        CapabilityEffect.FILESYSTEM_READ,
        CapabilityEffect.FILESYSTEM_WRITE,
    )
    IDEMPOTENCY = IdempotencyClass.KEYED_EFFECT
    RELATIONSHIPS: ClassVar = ["review", "correction", "failure"]

    @staticmethod
    def _asset_entries(service, workspace: str) -> list[dict[str, Any]]:
        checkpoint_path = _website_path(workspace, "assets/manifest/checkpoint.json")
        if not service.exists(checkpoint_path, local=False):
            return []
        checkpoint = _read_json(service, checkpoint_path)
        count = int(checkpoint.get("count") or 0)
        entries: list[dict[str, Any]] = []
        for index in range((count + BATCH_SIZE - 1) // BATCH_SIZE):
            batch_path = _website_path(
                workspace, f"assets/manifest/batch-{index + 1:04d}.json",
            )
            batch = _read_json(service, batch_path)
            batch_entries = list(batch.get("entries") or [])
            if len(batch_entries) > BATCH_SIZE:
                raise ValueError("Website Creator asset manifest batch exceeds 25 entries")
            entries.extend({**dict(value), "required": True} for value in batch_entries)
        if len(entries) != count:
            raise ValueError("Website Creator asset manifest count mismatch")
        return entries

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load_state(flowfile)
        website = state["website"]
        workspace = str(website["workspace"])
        service = self._resolve_relay(state)
        mapping_state = dict(website.get("mapping") or {})
        mapping = _read_json(service, str(mapping_state.get("result_path") or ""))
        if mapping.get("result_digest") != mapping_state.get("result_digest"):
            raise ValueError("Website Creator mapping digest mismatch during finalization")
        template = dict(website.get("template") or {})
        inventory = dict(website.get("inventory") or {})
        omissions = list(inventory.get("accepted_omissions") or [])
        if not omissions:
            complete_path = str(inventory.get("complete_path") or "")
            if complete_path and service.exists(complete_path, local=False):
                omissions = list(_read_json(service, complete_path).get("accepted_omissions") or [])
        notice_path = str(template.get("notice_path") or "")
        attribution = [
            posixpath.relpath(notice_path, workspace)
        ] if notice_path else []
        finalizer = StaticSiteFinalizer(
            service,
            workspace,
            inventory_manifest_digest=str(inventory.get("manifest_digest") or ""),
            mapping_digest=str(mapping_state.get("result_digest") or ""),
            template_digest=str(template.get("sha256") or ""),
            accepted_omissions=omissions,
            attribution_paths=attribution,
        )
        report = finalizer.run(
            pages=list(mapping.get("entries") or []),
            assets=self._asset_entries(service, workspace),
        )
        report_hash = str(service.hash_file(
            finalizer.report_path, local=False,
        ).get("sha256") or "")
        website["finalize"] = {
            "passed": bool(report["passed"]),
            "report_path": finalizer.report_path,
            "report_hash": report_hash,
            "replay_key": report["replay_key"],
            "counts": dict(report["counts"]),
            "issue_summary": [
                {
                    "code": str(issue.get("code") or ""),
                    "detail": str(issue.get("detail") or "")[:500],
                    "url": str(issue.get("url") or "")[:1000],
                    "path": str(issue.get("path") or "")[:1000],
                }
                for issue in list(report.get("blocking_issues") or [])[:20]
            ],
        }
        if report["passed"]:
            relationship = "review"
            website["status"] = "finalized"
        else:
            relationship = "correction"
            website["status"] = "deterministic_correction_required"
            website["user_feedback"] = (
                "Deterministic finalization failed. Resolve every issue in "
                f"{finalizer.report_path}."
            )
        flowfile.set_attribute("route.relationship", relationship)
        return _store_state(flowfile, state)


for _task in (
    PrepareWebsiteMappingBatchesTask,
    MapWebsitePageBatchTask,
    RouteWebsiteBatchesTask,
    MergeWebsiteMappingTask,
    PrepareWebsiteBuildBatchesTask,
    BuildWebsitePageBatchTask,
    MergeWebsiteBuildTask,
    PrepareWebsiteCorrectionBatchesTask,
    CorrectWebsitePageBatchTask,
    MergeWebsiteCorrectionTask,
    FinalizeStaticSiteTask,
):
    TaskFactory.register(_task)


__all__ = [
    "BuildWebsitePageBatchTask",
    "CorrectWebsitePageBatchTask",
    "FinalizeStaticSiteTask",
    "MapWebsitePageBatchTask",
    "MergeWebsiteBuildTask",
    "MergeWebsiteCorrectionTask",
    "MergeWebsiteMappingTask",
    "PrepareWebsiteBuildBatchesTask",
    "PrepareWebsiteCorrectionBatchesTask",
    "PrepareWebsiteMappingBatchesTask",
    "RouteWebsiteBatchesTask",
]
