"""Durable crawl-policy and inventory tasks for Website Creator."""

from __future__ import annotations

import json
import posixpath
import re
import time
from datetime import datetime, timezone
from typing import Any, ClassVar
from urllib.parse import urlsplit

from core import FlowFile, TaskFactory
from core.agent_contracts import CapabilityEffect, IdempotencyClass
from core.website_creator_contracts import (
    CRAWL_LIMIT_BOUNDS,
    CRAWL_LIMIT_FIELDS,
    AssetKind,
    CrawlLimits,
    ReferenceKind,
    SourceRightsDeclaration,
    canonical_origin,
    canonicalize_url,
    inventory_relative_paths,
    normalize_url_patterns,
    stable_record_id,
)
from core.website_creator_crawler import (
    CRAWLER_SCHEMA_VERSION,
    CRAWLER_USER_AGENT,
    MAX_HTML_RESPONSE_BYTES,
    MAX_ROBOTS_BYTES,
    MAX_SITEMAP_COMPRESSED_BYTES,
    MAX_SITEMAP_NESTING,
    RobotsPolicy,
    crawl_cache_identity,
    extract_html_inventory,
    parse_retry_after,
    parse_robots,
    parse_sitemap,
    sha256_bytes,
    stable_json_bytes,
)
from core.workflow_agent_contracts import AgentWorkflowRequest
from tasks.ai.workflow.turn_tasks import _WorkflowContextTask
from tasks.ai.workflow.website_creator_tasks import (
    _durable_answer,
    _load_state,
    _store_state,
)


def _pattern_lines(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, str):
        raise ValueError(f"crawl {field} must be multiline text")
    return [line.strip() for line in value.splitlines() if line.strip()]


_RECORD_FILES = {
    "pages": "inventory/pages.ndjson",
    "assets": "inventory/assets.ndjson",
    "external_links": "inventory/external-links.ndjson",
}
_CHECKPOINT_PATH = "inventory/checkpoint.json"
_INDEX_PATH = "inventory/index.json"
_COMPLETE_PATH = "inventory/complete.json"
_RAW_DIRECTORY = "inventory/raw"
_MAX_ISSUE_EXAMPLES = 20


def _workspace_path(workspace: str, relative: str) -> str:
    return posixpath.join(workspace.rstrip("/"), relative)


def _read_json(service, path: str) -> dict[str, Any]:
    try:
        value = json.loads(service.read_file(path, local=False).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Website Creator file is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Website Creator file must contain an object: {path}")
    return value


def _atomic_bytes(service, path: str, content: bytes) -> None:
    writer = getattr(service, "atomic_write_file", None)
    if not callable(writer):
        raise ValueError("Website Creator relay lacks atomic_write_file capability")
    writer(path, content, local=False)


def _atomic_json(service, path: str, value: dict[str, Any]) -> None:
    _atomic_bytes(service, path, stable_json_bytes(value))


def _stat_size(service, path: str) -> int:
    entry = service.stat(path, local=False)
    size = entry.get("size") if isinstance(entry, dict) else getattr(entry, "size", None)
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError(f"relay returned an invalid file size for {path}")
    return size


def _checkpoint_path(workspace: str) -> str:
    return _workspace_path(workspace, _CHECKPOINT_PATH)


def _save_checkpoint(service, workspace: str, checkpoint: dict[str, Any]) -> None:
    _atomic_json(service, _checkpoint_path(workspace), checkpoint)


def _append_record(
    service,
    workspace: str,
    checkpoint: dict[str, Any],
    family: str,
    record: dict[str, Any],
    *,
    count_key: str | None = None,
) -> bool:
    record_id = str(record.get("record_id") or "")
    known = checkpoint["record_ids"][family]
    if record_id in known:
        return False
    line = stable_json_bytes(record) + b"\n"
    expected = int(checkpoint["record_offsets"][family])
    append = getattr(service, "append_file", None)
    if not callable(append):
        raise ValueError("Website Creator relay lacks append_file capability")
    append(
        _workspace_path(workspace, _RECORD_FILES[family]),
        line,
        expected_size=expected,
        local=False,
    )
    checkpoint["record_offsets"][family] = expected + len(line)
    known.append(record_id)
    if count_key is not None:
        checkpoint["counts"][count_key] += 1
    _save_checkpoint(service, workspace, checkpoint)
    return True


def _reconcile_record_files(service, workspace: str, checkpoint: dict[str, Any]) -> None:
    truncate = getattr(service, "truncate_file", None)
    if not callable(truncate):
        raise ValueError("Website Creator relay lacks truncate_file capability")
    for family, relative in _RECORD_FILES.items():
        path = _workspace_path(workspace, relative)
        expected = int(checkpoint["record_offsets"][family])
        current = _stat_size(service, path)
        if current < expected:
            raise ValueError(
                f"Website Creator {family} records are shorter than their checkpoint"
            )
        if current > expected:
            truncate(path, expected, expected_size=current, local=False)


def _record_issue(
    checkpoint: dict[str, Any],
    code: str,
    url: str,
    detail: str,
    *,
    bounded: bool = False,
) -> None:
    issue = {"code": code, "url": url, "detail": str(detail)[:1000]}
    target = checkpoint["bounded_reasons"] if bounded else checkpoint["issues"]
    if issue not in target and len(target) < _MAX_ISSUE_EXAMPLES:
        target.append(issue)


def _matches_url_policy(url: str, crawl: dict[str, Any]) -> bool:
    includes = tuple(crawl.get("include_url_patterns") or ())
    excludes = tuple(crawl.get("exclude_url_patterns") or ())
    if includes and not any(re.search(pattern, url) for pattern in includes):
        return False
    return not any(re.search(pattern, url) for pattern in excludes)


def _entry_key(entry: dict[str, Any]) -> str:
    return f"{entry['kind']}:{entry['url']}"


def _enqueue(
    checkpoint: dict[str, Any],
    entry: dict[str, Any],
    *,
    front: bool = False,
) -> bool:
    key = _entry_key(entry)
    in_flight = checkpoint.get("in_flight")
    if (
        key in checkpoint["queued_keys"]
        or key in checkpoint["processed_keys"]
        or (isinstance(in_flight, dict) and _entry_key(in_flight) == key)
    ):
        return False
    if front:
        checkpoint["queue"].insert(0, entry)
    else:
        checkpoint["queue"].append(entry)
    checkpoint["queued_keys"].append(key)
    return True


def _new_checkpoint(source_url: str, crawl: dict[str, Any], now: float) -> dict[str, Any]:
    source = canonicalize_url(source_url)
    origin = canonical_origin(source)
    queue: list[dict[str, Any]] = []
    for entry in (
        {"kind": "robots", "url": origin + "/robots.txt", "depth": 0, "referrer": ""},
        {"kind": "sitemap", "url": origin + "/sitemap.xml", "depth": 0,
         "referrer": "", "sitemap_depth": 0, "conventional": True},
        {"kind": "sitemap", "url": origin + "/sitemap_index.xml", "depth": 0,
         "referrer": "", "sitemap_depth": 0, "conventional": True},
        {"kind": "page", "url": source, "depth": 0, "referrer": ""},
    ):
        queue.append(entry)
    return {
        "schema_version": CRAWLER_SCHEMA_VERSION,
        "cache_identity": crawl_cache_identity(source, crawl),
        "source_url": source,
        "source_origin": origin,
        "status": "running",
        "started_at": float(now),
        "updated_at": float(now),
        "next_allowed_at": 0.0,
        "queue": queue,
        "queued_keys": [_entry_key(entry) for entry in queue],
        "processed_keys": [],
        "in_flight": None,
        "record_offsets": {family: 0 for family in _RECORD_FILES},
        "record_ids": {family: [] for family in _RECORD_FILES},
        "counts": {
            "fetches": 0,
            "pages": 0,
            "robots": 0,
            "sitemaps": 0,
            "assets": 0,
            "external_links": 0,
            "bytes": 0,
            "errors": 0,
            "omissions": 0,
        },
        "robots": RobotsPolicy().to_dict(),
        "bounded_reasons": [],
        "issues": [],
        "accepted_omissions": [],
    }


def _index_document(
    checkpoint: dict[str, Any],
    crawl: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": CRAWLER_SCHEMA_VERSION,
        "source_url": checkpoint["source_url"],
        "canonical_origin": checkpoint["source_origin"],
        "effective_limits": dict(crawl["effective_limits"]),
        "policy": {
            "include_url_patterns": list(crawl.get("include_url_patterns") or []),
            "exclude_url_patterns": list(crawl.get("exclude_url_patterns") or []),
            "rights": crawl.get("rights"),
            "cache_identity": checkpoint["cache_identity"],
        },
        "counts": dict(checkpoint["counts"]),
        "records": inventory_relative_paths(),
        "status": checkpoint["status"],
    }


def _write_index(
    service,
    workspace: str,
    checkpoint: dict[str, Any],
    crawl: dict[str, Any],
) -> bytes:
    content = stable_json_bytes(_index_document(checkpoint, crawl))
    _atomic_bytes(service, _workspace_path(workspace, _INDEX_PATH), content)
    return content


def _complete_manifest(
    service,
    workspace: str,
    checkpoint: dict[str, Any],
    crawl: dict[str, Any],
    *,
    crawl_status: str,
) -> dict[str, Any]:
    checkpoint["status"] = "complete"
    checkpoint["updated_at"] = float(time.time())
    _save_checkpoint(service, workspace, checkpoint)
    index_content = _write_index(service, workspace, checkpoint, crawl)
    files: dict[str, str] = {_INDEX_PATH: sha256_bytes(index_content)}
    for relative in (*_RECORD_FILES.values(), _CHECKPOINT_PATH):
        files[relative] = sha256_bytes(
            service.read_file(_workspace_path(workspace, relative), local=False)
        )
    manifest = {
        "schema_version": CRAWLER_SCHEMA_VERSION,
        "status": "complete",
        "crawl_status": crawl_status,
        "cache_identity": checkpoint["cache_identity"],
        "accepted_omissions": list(checkpoint.get("accepted_omissions") or []),
        "index_sha256": files[_INDEX_PATH],
        "files": files,
    }
    content = stable_json_bytes(manifest)
    _atomic_bytes(service, _workspace_path(workspace, _COMPLETE_PATH), content)
    manifest["manifest_digest"] = sha256_bytes(content)
    return manifest


def _verify_complete_manifest(
    service,
    workspace: str,
    cache_identity: str,
) -> dict[str, Any] | None:
    path = _workspace_path(workspace, _COMPLETE_PATH)
    if not service.exists(path, local=False):
        return None
    manifest = _read_json(service, path)
    if (
        manifest.get("status") != "complete"
        or manifest.get("cache_identity") != cache_identity
    ):
        return None
    for relative, expected in dict(manifest.get("files") or {}).items():
        content = service.read_file(_workspace_path(workspace, relative), local=False)
        if sha256_bytes(content) != expected:
            raise ValueError(f"Website Creator cache hash mismatch: {relative}")
    manifest["manifest_digest"] = sha256_bytes(
        service.read_file(path, local=False)
    )
    return manifest


def _update_inventory_state(
    website: dict[str, Any],
    checkpoint: dict[str, Any],
    manifest: dict[str, Any] | None = None,
) -> None:
    workspace = str(website["workspace"])
    website["inventory"] = {
        "index_path": _workspace_path(workspace, _INDEX_PATH),
        "checkpoint_path": _workspace_path(workspace, _CHECKPOINT_PATH),
        "complete_path": _workspace_path(workspace, _COMPLETE_PATH),
        "cache_identity": checkpoint["cache_identity"],
        "manifest_digest": str((manifest or {}).get("manifest_digest") or ""),
        "counts": dict(checkpoint["counts"]),
        "status": checkpoint["status"],
        "next_allowed_at": float(checkpoint.get("next_allowed_at") or 0.0),
        "bounded_reasons": list(checkpoint.get("bounded_reasons") or [])[:10],
        "issues": list(checkpoint.get("issues") or [])[:10],
    }


class _WebsiteCrawlTask(_WorkflowContextTask):
    """Shared exact-relay resolution and authorization target for crawl I/O."""

    AGENT_WORKFLOW_SAFE = True
    AUTHORIZATION_TARGET_KIND = "website.inventory"

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
            requested,
            user_id=context.user_id,
            conv_id=context.conversation_id,
        )
        if service is None:
            raise ValueError("Website Creator relay is not available")
        website["relay_id"] = requested
        return service

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
                _workspace_path(workspace, relative)
                for relative in (
                    _INDEX_PATH, _CHECKPOINT_PATH, _COMPLETE_PATH,
                    *_RECORD_FILES.values(), _RAW_DIRECTORY,
                )
            ] if workspace else [],
        }


class InitializeSiteCrawlTask(_WebsiteCrawlTask):
    """Create, resume, or hash-verify one run-scoped crawl inventory."""

    TYPE = "initializeSiteCrawl"
    NAME = "Initialize Site Crawl"
    DESCRIPTION = "Create or resume the exact run-scoped website inventory."
    EFFECTS = (
        CapabilityEffect.RESOURCE_READ,
        CapabilityEffect.FILESYSTEM_READ,
        CapabilityEffect.FILESYSTEM_WRITE,
    )
    IDEMPOTENCY = IdempotencyClass.KEYED_EFFECT
    RELATIONSHIPS: ClassVar = ["queued", "finished", "bounded", "errors", "failure"]

    def _now(self) -> float:
        return time.time()

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load_state(flowfile)
        website = state["website"]
        crawl = dict(website.get("crawl") or {})
        if crawl.get("confirmed") is not True:
            raise ValueError("Website Creator crawl limits and rights are not confirmed")
        workspace = str(website["workspace"])
        service = self._resolve_relay(state)
        source_url = canonicalize_url(str(website["source_url"]))
        identity = crawl_cache_identity(source_url, crawl)
        service.mkdir(_workspace_path(workspace, "inventory"), local=False)
        service.mkdir(_workspace_path(workspace, _RAW_DIRECTORY), local=False)

        fresh = bool(crawl.pop("fresh_crawl", False))
        manifest = None if fresh else _verify_complete_manifest(
            service, workspace, identity,
        )
        if manifest is not None:
            checkpoint = _read_json(service, _checkpoint_path(workspace))
            _update_inventory_state(website, checkpoint, manifest)
            website["status"] = "inventory_complete"
            relationship = "finished"
        else:
            checkpoint_file = _checkpoint_path(workspace)
            resume = service.exists(checkpoint_file, local=False) and not fresh
            checkpoint = _read_json(service, checkpoint_file) if resume else None
            if checkpoint is not None and checkpoint.get("cache_identity") == identity:
                _reconcile_record_files(service, workspace, checkpoint)
                relationship = {
                    "running": "queued",
                    "complete": "finished",
                    "bounded": "bounded",
                    "errors": "errors",
                }.get(str(checkpoint.get("status")), "failure")
            else:
                complete_path = _workspace_path(workspace, _COMPLETE_PATH)
                if service.exists(complete_path, local=False):
                    service.delete_file(complete_path, local=False)
                for relative in _RECORD_FILES.values():
                    _atomic_bytes(service, _workspace_path(workspace, relative), b"")
                checkpoint = _new_checkpoint(source_url, crawl, self._now())
                _save_checkpoint(service, workspace, checkpoint)
                _write_index(service, workspace, checkpoint, crawl)
                relationship = "queued"
            _update_inventory_state(website, checkpoint)
            website["status"] = "inventory_" + str(checkpoint["status"])
        flowfile.set_attribute("route.relationship", relationship)
        return _store_state(flowfile, state)


class FetchSiteCrawlEntryTask(_WebsiteCrawlTask):
    """Perform at most one bounded public relay fetch and checkpoint its result."""

    TYPE = "fetchSiteCrawlEntry"
    NAME = "Fetch Site Crawl Entry"
    DESCRIPTION = "Fetch and record one queued page, robots file, or sitemap."
    EFFECTS = (
        CapabilityEffect.RESOURCE_READ,
        CapabilityEffect.NETWORK_READ,
        CapabilityEffect.FILESYSTEM_READ,
        CapabilityEffect.FILESYSTEM_WRITE,
    )
    IDEMPOTENCY = IdempotencyClass.KEYED_EFFECT
    RELATIONSHIPS: ClassVar = ["success", "failure"]

    def _now(self) -> float:
        return time.time()

    @staticmethod
    def _headers(response: dict[str, Any]) -> dict[str, str]:
        return {
            str(name).casefold(): str(value)
            for name, value in dict(response.get("headers") or {}).items()
        }

    @staticmethod
    def _record(
        entry: dict[str, Any],
        *,
        final_url: str,
        status: int,
        content_type: str,
        content: bytes,
        title: str = "",
        canonical_url: str = "",
        raw_html_path: str = "",
        error: str = "",
        omission_reason: str = "",
    ) -> dict[str, Any]:
        canonical = canonical_url or canonicalize_url(final_url or entry["url"])
        return {
            "record_id": stable_record_id(entry["kind"], canonical),
            "record_kind": entry["kind"],
            "requested_url": entry["url"],
            "final_url": final_url,
            "canonical_url": canonical,
            "status": int(status),
            "title": title,
            "depth": int(entry.get("depth") or 0),
            "content_type": content_type,
            "bytes": len(content),
            "content_sha256": sha256_bytes(content),
            "raw_html_path": raw_html_path,
            "referrer": str(entry.get("referrer") or ""),
            "error": error,
            "omission_reason": omission_reason,
        }

    def _finish_entry(
        self,
        service,
        workspace: str,
        checkpoint: dict[str, Any],
        entry: dict[str, Any],
        now: float,
        crawl: dict[str, Any],
    ) -> None:
        key = _entry_key(entry)
        if key not in checkpoint["processed_keys"]:
            checkpoint["processed_keys"].append(key)
        checkpoint["in_flight"] = None
        delay_ms = max(
            int(crawl["effective_limits"]["politeness_delay_ms"]),
            RobotsPolicy.from_dict(checkpoint.get("robots")).crawl_delay_ms,
        )
        checkpoint["next_allowed_at"] = now + (delay_ms / 1000.0)
        checkpoint["updated_at"] = now
        _save_checkpoint(service, workspace, checkpoint)

    def _terminalize_if_needed(
        self,
        service,
        workspace: str,
        checkpoint: dict[str, Any],
        crawl: dict[str, Any],
        now: float,
    ) -> dict[str, Any] | None:
        limits = crawl["effective_limits"]
        pending = list(checkpoint["queue"])
        if isinstance(checkpoint.get("in_flight"), dict):
            pending.append(checkpoint["in_flight"])
        page_waiting = any(item.get("kind") == "page" for item in pending)
        if page_waiting and checkpoint["counts"]["pages"] >= limits["max_pages"]:
            _record_issue(
                checkpoint, "max_pages", checkpoint["source_url"],
                "approved page limit reached", bounded=True,
            )
        if pending and checkpoint["counts"]["bytes"] >= limits["max_total_bytes"]:
            _record_issue(
                checkpoint, "max_total_bytes", checkpoint["source_url"],
                "approved crawl byte budget reached", bounded=True,
            )
        if pending and now - checkpoint["started_at"] >= limits["max_duration_seconds"]:
            _record_issue(
                checkpoint, "max_duration_seconds", checkpoint["source_url"],
                "approved crawl duration reached", bounded=True,
            )
        if checkpoint["bounded_reasons"]:
            checkpoint["status"] = "bounded"
        elif not pending and checkpoint["counts"]["errors"]:
            checkpoint["status"] = "errors"
        elif not pending:
            checkpoint["status"] = "complete"
            return _complete_manifest(
                service, workspace, checkpoint, crawl, crawl_status="complete",
            )
        else:
            checkpoint["status"] = "running"
        checkpoint["updated_at"] = now
        _save_checkpoint(service, workspace, checkpoint)
        _write_index(service, workspace, checkpoint, crawl)
        return None

    def _handle_fetch_failure(
        self,
        service,
        workspace: str,
        checkpoint: dict[str, Any],
        crawl: dict[str, Any],
        entry: dict[str, Any],
        now: float,
        exc: Exception,
        remaining_bytes: int,
    ) -> None:
        detail = str(exc)[:1000]
        if remaining_bytes <= self._response_cap(entry) and (
            "byte limit" in detail.casefold() or "exceed" in detail.casefold()
        ):
            _record_issue(
                checkpoint, "max_total_bytes", entry["url"], detail, bounded=True,
            )
        else:
            checkpoint["counts"]["errors"] += 1
            _record_issue(checkpoint, "fetch_error", entry["url"], detail)
        record = self._record(
            entry,
            final_url=entry["url"],
            status=0,
            content_type="",
            content=b"",
            error=detail,
        )
        _append_record(
            service, workspace, checkpoint, "pages", record,
            count_key={"page": "pages", "robots": "robots", "sitemap": "sitemaps"}[
                entry["kind"]
            ],
        )
        self._finish_entry(service, workspace, checkpoint, entry, now, crawl)

    @staticmethod
    def _response_cap(entry: dict[str, Any]) -> int:
        return {
            "page": MAX_HTML_RESPONSE_BYTES,
            "robots": MAX_ROBOTS_BYTES,
            "sitemap": MAX_SITEMAP_COMPRESSED_BYTES,
        }[entry["kind"]]

    def _process_robots(
        self,
        service,
        workspace: str,
        checkpoint: dict[str, Any],
        entry: dict[str, Any],
        response: dict[str, Any],
        content: bytes,
        content_type: str,
    ) -> None:
        status = int(response.get("status") or 0)
        final_url = canonicalize_url(str(response.get("url") or entry["url"]))
        error = "" if status == 404 or 200 <= status < 300 else f"HTTP {status}"
        if 200 <= status < 300:
            try:
                policy = parse_robots(content, checkpoint["source_url"])
                checkpoint["robots"] = policy.to_dict()
                for sitemap in policy.sitemaps:
                    _enqueue(checkpoint, {
                        "kind": "sitemap", "url": sitemap, "depth": 0,
                        "referrer": final_url, "sitemap_depth": 0,
                        "conventional": False,
                    })
            except ValueError as exc:
                error = str(exc)
        if error:
            checkpoint["counts"]["errors"] += 1
            _record_issue(checkpoint, "robots_error", final_url, error)
        if _append_record(
            service, workspace, checkpoint, "pages",
            self._record(
                entry, final_url=final_url, status=status,
                content_type=content_type, content=content, error=error,
            ), count_key="robots",
        ):
            pass

    def _process_sitemap(
        self,
        service,
        workspace: str,
        checkpoint: dict[str, Any],
        entry: dict[str, Any],
        response: dict[str, Any],
        content: bytes,
        content_type: str,
        crawl: dict[str, Any],
    ) -> None:
        status = int(response.get("status") or 0)
        final_url = canonicalize_url(str(response.get("url") or entry["url"]))
        optional_missing = bool(entry.get("conventional")) and status in {404, 410}
        error = "" if optional_missing or 200 <= status < 300 else f"HTTP {status}"
        if 200 <= status < 300:
            try:
                kind, urls = parse_sitemap(
                    content,
                    final_url,
                    compressed=(
                        final_url.casefold().endswith(".gz")
                        or "gzip" in content_type.casefold()
                    ),
                )
                if kind == "sitemapindex":
                    next_depth = int(entry.get("sitemap_depth") or 0) + 1
                    if next_depth > MAX_SITEMAP_NESTING:
                        _record_issue(
                            checkpoint, "sitemap_nesting", final_url,
                            "sitemap nesting limit reached", bounded=True,
                        )
                    else:
                        for url in urls:
                            _enqueue(checkpoint, {
                                "kind": "sitemap", "url": url, "depth": 0,
                                "referrer": final_url,
                                "sitemap_depth": next_depth,
                                "conventional": False,
                            })
                else:
                    for url in urls:
                        if _matches_url_policy(url, crawl):
                            _enqueue(checkpoint, {
                                "kind": "page", "url": url, "depth": 0,
                                "referrer": final_url,
                            })
            except ValueError as exc:
                error = str(exc)
        if error:
            checkpoint["counts"]["errors"] += 1
            _record_issue(checkpoint, "sitemap_error", final_url, error)
        if _append_record(
            service, workspace, checkpoint, "pages",
            self._record(
                entry, final_url=final_url, status=status,
                content_type=content_type, content=content, error=error,
            ), count_key="sitemaps",
        ):
            pass

    def _process_page_references(
        self,
        service,
        workspace: str,
        checkpoint: dict[str, Any],
        entry: dict[str, Any],
        inventory,
        crawl: dict[str, Any],
    ) -> None:
        next_depth = int(entry.get("depth") or 0) + 1
        for reference in inventory.references:
            canonical = reference.canonical_url
            if reference.kind is ReferenceKind.INTERNAL_PAGE and canonical:
                if next_depth > int(crawl["effective_limits"]["max_depth"]):
                    _record_issue(
                        checkpoint, "max_depth", canonical,
                        "approved link depth reached", bounded=True,
                    )
                    continue
                if not _matches_url_policy(canonical, crawl):
                    continue
                if not RobotsPolicy.from_dict(checkpoint.get("robots")).allows(canonical):
                    checkpoint["counts"]["omissions"] += 1
                    _record_issue(
                        checkpoint, "robots_disallowed", canonical,
                        "robots policy excludes this discovered page",
                    )
                    continue
                _enqueue(checkpoint, {
                    "kind": "page", "url": canonical, "depth": next_depth,
                    "referrer": inventory.canonical_url,
                })
                continue
            record = {
                "record_id": stable_record_id(reference.kind, canonical or reference.original),
                "source_page_url": inventory.canonical_url,
                **reference.to_record(),
            }
            family = (
                "assets"
                if reference.kind in {
                    ReferenceKind.FIRST_PARTY_ASSET,
                    ReferenceKind.APPROVED_THIRD_PARTY_ASSET,
                }
                else "external_links"
            )
            _append_record(
                service, workspace, checkpoint, family, record,
                count_key=family,
            )

    def _process_page(
        self,
        service,
        workspace: str,
        checkpoint: dict[str, Any],
        entry: dict[str, Any],
        response: dict[str, Any],
        content: bytes,
        content_type: str,
        crawl: dict[str, Any],
    ) -> None:
        status = int(response.get("status") or 0)
        final_url = canonicalize_url(str(response.get("url") or entry["url"]))
        error = ""
        title = ""
        canonical = final_url
        raw_path = ""
        html_type = content_type.split(";", 1)[0].strip().casefold()
        if canonical_origin(final_url) != checkpoint["source_origin"]:
            error = "page redirect left the approved source origin"
        elif not 200 <= status < 300:
            error = f"HTTP {status}"
        elif html_type not in {"text/html", "application/xhtml+xml"}:
            error = ""  # recorded as a non-HTML page response, never parsed
        else:
            raw_relative = (
                "inventory/raw/"
                + stable_record_id("raw_html", final_url)
                + ".html"
            )
            _atomic_bytes(service, _workspace_path(workspace, raw_relative), content)
            raw_path = raw_relative
            try:
                inventory = extract_html_inventory(
                    content,
                    final_url,
                    source_origin=checkpoint["source_origin"],
                )
                title = inventory.title
                canonical = inventory.canonical_url
                self._process_page_references(
                    service, workspace, checkpoint, entry, inventory, crawl,
                )
            except Exception as exc:
                error = f"HTML parse failed: {exc}"
        if error:
            checkpoint["counts"]["errors"] += 1
            _record_issue(checkpoint, "page_error", final_url, error)
        if _append_record(
            service, workspace, checkpoint, "pages",
            self._record(
                entry,
                final_url=final_url,
                status=status,
                content_type=content_type,
                content=content,
                title=title,
                canonical_url=canonical,
                raw_html_path=raw_path,
                error=error,
            ), count_key="pages",
        ):
            pass

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load_state(flowfile)
        website = state["website"]
        crawl = dict(website.get("crawl") or {})
        if crawl.get("confirmed") is not True:
            raise ValueError("Website Creator crawl contract is not confirmed")
        workspace = str(website["workspace"])
        service = self._resolve_relay(state)
        checkpoint = _read_json(service, _checkpoint_path(workspace))
        expected_identity = crawl_cache_identity(str(website["source_url"]), crawl)
        if checkpoint.get("cache_identity") != expected_identity:
            raise ValueError("Website Creator crawl checkpoint cache identity changed")
        _reconcile_record_files(service, workspace, checkpoint)
        now = self._now()
        if checkpoint.get("status") != "running":
            _update_inventory_state(website, checkpoint)
            return _store_state(flowfile, state)
        if self._terminalize_if_needed(
            service, workspace, checkpoint, crawl, now,
        ) is not None or checkpoint.get("status") != "running":
            _update_inventory_state(website, checkpoint)
            return _store_state(flowfile, state)
        if now < float(checkpoint.get("next_allowed_at") or 0.0):
            _update_inventory_state(website, checkpoint)
            return _store_state(flowfile, state)

        entry = checkpoint.get("in_flight")
        if not isinstance(entry, dict):
            if not checkpoint["queue"]:
                self._terminalize_if_needed(
                    service, workspace, checkpoint, crawl, now,
                )
                _update_inventory_state(website, checkpoint)
                return _store_state(flowfile, state)
            entry = checkpoint["queue"].pop(0)
            key = _entry_key(entry)
            checkpoint["queued_keys"] = [
                item for item in checkpoint["queued_keys"] if item != key
            ]
            checkpoint["in_flight"] = entry
            checkpoint["updated_at"] = now
            _save_checkpoint(service, workspace, checkpoint)

        if entry["kind"] == "page" and not RobotsPolicy.from_dict(
            checkpoint.get("robots")
        ).allows(entry["url"]):
            record = self._record(
                entry,
                final_url=entry["url"],
                status=0,
                content_type="",
                content=b"",
                omission_reason="robots_disallowed",
            )
            checkpoint["counts"]["omissions"] += 1
            _append_record(
                service, workspace, checkpoint, "pages", record,
                count_key="pages",
            )
            self._finish_entry(service, workspace, checkpoint, entry, now, crawl)
            self._terminalize_if_needed(service, workspace, checkpoint, crawl, now)
            _update_inventory_state(website, checkpoint)
            return _store_state(flowfile, state)

        limits = crawl["effective_limits"]
        remaining_bytes = int(limits["max_total_bytes"]) - int(
            checkpoint["counts"]["bytes"]
        )
        if remaining_bytes <= 0:
            _record_issue(
                checkpoint, "max_total_bytes", entry["url"],
                "approved crawl byte budget reached", bounded=True,
            )
            checkpoint["status"] = "bounded"
            _save_checkpoint(service, workspace, checkpoint)
            _write_index(service, workspace, checkpoint, crawl)
            _update_inventory_state(website, checkpoint)
            return _store_state(flowfile, state)
        cap = min(self._response_cap(entry), remaining_bytes)
        try:
            response = service.http_fetch(
                entry["url"],
                method="GET",
                headers={
                    "User-Agent": CRAWLER_USER_AGENT,
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml,text/xml,*/*;q=0.1"
                    ),
                },
                timeout=int(limits["request_timeout_seconds"]),
                local=False,
                max_bytes=cap,
                public_only=True,
            )
        except Exception as exc:
            self._handle_fetch_failure(
                service, workspace, checkpoint, crawl, entry, now, exc,
                remaining_bytes,
            )
            self._terminalize_if_needed(service, workspace, checkpoint, crawl, now)
            _update_inventory_state(website, checkpoint)
            return _store_state(flowfile, state)

        content = bytes(response.get("body_bytes") or b"")
        checkpoint["counts"]["fetches"] += 1
        checkpoint["counts"]["bytes"] += len(content)
        headers = self._headers(response)
        status = int(response.get("status") or 0)
        if status in {429, 503} and int(entry.get("attempt") or 0) < 2:
            retry = parse_retry_after(headers.get("retry-after", ""), now)
            retry = retry if retry is not None else (
                int(limits["politeness_delay_ms"]) / 1000.0
            )
            if now + retry - checkpoint["started_at"] <= limits["max_duration_seconds"]:
                entry["attempt"] = int(entry.get("attempt") or 0) + 1
                checkpoint["in_flight"] = entry
                checkpoint["next_allowed_at"] = now + retry
                checkpoint["updated_at"] = now
                _save_checkpoint(service, workspace, checkpoint)
                _update_inventory_state(website, checkpoint)
                return _store_state(flowfile, state)
            _record_issue(
                checkpoint, "retry_after_duration", entry["url"],
                "Retry-After exceeds remaining approved duration", bounded=True,
            )

        final_url = canonicalize_url(str(response.get("url") or entry["url"]))
        if canonical_origin(final_url) != checkpoint["source_origin"]:
            checkpoint["counts"]["errors"] += 1
            detail = "redirect left the approved source origin"
            _record_issue(checkpoint, "redirect_origin", final_url, detail)
            _append_record(
                service, workspace, checkpoint, "pages",
                self._record(
                    entry, final_url=final_url, status=status,
                    content_type=headers.get("content-type", ""), content=content,
                    error=detail,
                ),
                count_key={
                    "page": "pages", "robots": "robots", "sitemap": "sitemaps",
                }[entry["kind"]],
            )
        elif entry["kind"] == "robots":
            self._process_robots(
                service, workspace, checkpoint, entry, response, content,
                headers.get("content-type", ""),
            )
        elif entry["kind"] == "sitemap":
            self._process_sitemap(
                service, workspace, checkpoint, entry, response, content,
                headers.get("content-type", ""), crawl,
            )
        else:
            self._process_page(
                service, workspace, checkpoint, entry, response, content,
                headers.get("content-type", ""), crawl,
            )
        self._finish_entry(service, workspace, checkpoint, entry, now, crawl)
        manifest = self._terminalize_if_needed(
            service, workspace, checkpoint, crawl, now,
        )
        _update_inventory_state(website, checkpoint, manifest)
        website["status"] = "inventory_" + str(checkpoint["status"])
        return _store_state(flowfile, state)


def _crawl_form(crawl: dict[str, Any], source_url: str) -> dict[str, Any]:
    limits = CrawlLimits.from_mapping(crawl.get("effective_limits")).to_dict()
    fields: list[dict[str, Any]] = [
        {
            "name": "decision",
            "label": "Decision",
            "type": "choice",
            "required": True,
            "options": [
                {"value": "confirm", "label": "Confirm and crawl"},
                {"value": "stop", "label": "Stop"},
            ],
        },
    ]
    labels = {
        "max_pages": "Maximum pages",
        "max_depth": "Maximum link depth",
        "politeness_delay_ms": "Politeness delay (milliseconds)",
        "request_timeout_seconds": "Request timeout (seconds)",
        "max_total_bytes": "Maximum crawl bytes",
        "max_duration_seconds": "Maximum crawl duration (seconds)",
    }
    for name in CRAWL_LIMIT_FIELDS:
        minimum, maximum = CRAWL_LIMIT_BOUNDS[name]
        fields.append({
            "name": name,
            "label": labels[name],
            "type": "integer",
            "required": True,
            "minimum": minimum,
            "maximum": maximum,
            "default": limits[name],
        })
    asset_options = [
        {"value": item.value, "label": item.value.replace("_", " ").title()}
        for item in AssetKind
        if item is not AssetKind.OTHER
    ]
    fields.extend([
        {
            "name": "rights_basis",
            "label": "Rights to source material",
            "type": "choice",
            "required": True,
            "options": [
                {"value": "owner", "label": "I own the source material"},
                {"value": "permission", "label": "I have permission"},
                {"value": "none", "label": "Do not reuse source assets"},
            ],
        },
        {
            "name": "allowed_asset_kinds",
            "label": "Source asset kinds allowed for reuse",
            "type": "multi",
            "required": False,
            "options": asset_options,
        },
        {
            "name": "rights_provenance",
            "label": "Permission or provenance details",
            "type": "multiline",
            "required": False,
            "max_length": 4000,
        },
        {
            "name": "include_url_patterns",
            "label": "Include URL regexes, one per line",
            "type": "multiline",
            "required": False,
            "max_length": 12000,
        },
        {
            "name": "exclude_url_patterns",
            "label": "Exclude URL regexes, one per line",
            "type": "multiline",
            "required": False,
            "max_length": 12000,
        },
    ])
    return {
        "message": (
            "Confirm the bounded crawl contract for the exact source origin. "
            "Source assets are reusable only under the rights declaration below.\n\n"
            f"Source: {source_url}\n"
            + "\n".join(f"{name}: {limits[name]}" for name in CRAWL_LIMIT_FIELDS)
        )[:24000],
        "title": "Confirm Website Creator crawl",
        "kind": "form",
        "response_schema": {"fields": fields},
    }


class PrepareCrawlDecisionTask(_WorkflowContextTask):
    """Route an explicit crawl contract or create its bounded approval form."""

    TYPE = "prepareCrawlDecision"
    NAME = "Prepare Crawl Decision"
    DESCRIPTION = "Require explicit effective crawl limits and source rights."
    AGENT_WORKFLOW_SAFE = True
    EFFECTS = (CapabilityEffect.RESOURCE_READ,)
    IDEMPOTENCY = IdempotencyClass.PURE
    AUTHORIZATION_TARGET_KIND = "workflow.runtime"
    RELATIONSHIPS: ClassVar = ["ask", "confirmed", "failure"]

    def get_parameter_schema(self) -> dict[str, Any]:
        return {
            "output_attribute": {
                "type": "string", "required": True,
                "default": "website.crawl_decision",
            },
        }

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load_state(flowfile)
        website = state["website"]
        crawl = dict(website.get("crawl") or {})
        output_attribute = str(
            self.config.get("output_attribute") or "website.crawl_decision"
        )
        if crawl.get("confirmed") is True:
            flowfile.delete_attribute(output_attribute)
            relationship = "confirmed"
        else:
            payload = _crawl_form(crawl, str(website["source_url"]))
            flowfile.set_attribute(
                output_attribute,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
            relationship = "ask"
        flowfile.set_attribute("route.relationship", relationship)
        return [flowfile]


class ApplyCrawlDecisionTask(_WorkflowContextTask):
    """Validate and freeze the durable crawl answer before network access."""

    TYPE = "applyCrawlDecision"
    NAME = "Apply Crawl Decision"
    DESCRIPTION = "Freeze confirmed crawl limits, patterns and source rights."
    AGENT_WORKFLOW_SAFE = True
    EFFECTS = (CapabilityEffect.RESOURCE_READ,)
    IDEMPOTENCY = IdempotencyClass.NATURAL
    AUTHORIZATION_TARGET_KIND = "workflow.runtime"
    RELATIONSHIPS: ClassVar = ["confirmed", "stopped", "failure"]

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load_state(flowfile)
        website = state["website"]
        answer = _durable_answer(flowfile)
        if not isinstance(answer, dict):
            raise ValueError("durable crawl decision must be a form object")
        decision = str(answer.get("decision") or "").strip().casefold()
        if decision == "stop":
            website["crawl"] = {
                **dict(website.get("crawl") or {}),
                "confirmed": False,
                "status": "stopped",
            }
            website["status"] = "stopped"
            relationship = "stopped"
        elif decision == "confirm":
            limits = CrawlLimits.from_mapping({
                name: answer.get(name) for name in CRAWL_LIMIT_FIELDS
            })
            rights = SourceRightsDeclaration.from_mapping({
                "basis": answer.get("rights_basis"),
                "allowed_asset_kinds": answer.get("allowed_asset_kinds") or [],
                "provenance": answer.get("rights_provenance") or "",
            })
            confirmed = {
                "effective_limits": limits.to_dict(),
                "include_url_patterns": list(normalize_url_patterns(
                    _pattern_lines(answer.get("include_url_patterns"), "include_url_patterns"),
                    "include_url_patterns",
                )),
                "exclude_url_patterns": list(normalize_url_patterns(
                    _pattern_lines(answer.get("exclude_url_patterns"), "exclude_url_patterns"),
                    "exclude_url_patterns",
                )),
                "rights": rights.to_dict(),
                "confirmation_required": True,
                "confirmed": True,
                "status": "confirmed",
            }
            previous = dict(website.get("crawl") or {})
            if previous.get("confirmed") is True and previous != confirmed:
                raise ValueError("confirmed crawl contract is immutable")
            website["crawl"] = confirmed
            website["status"] = "crawl_confirmed"
            relationship = "confirmed"
        else:
            raise ValueError("crawl decision must be confirm or stop")
        flowfile.set_attribute("route.relationship", relationship)
        return _store_state(flowfile, state)


class RouteSiteCrawlTask(_WorkflowContextTask):
    """Route the checkpoint status and expose the durable politeness deadline."""

    TYPE = "routeSiteCrawl"
    NAME = "Route Site Crawl"
    DESCRIPTION = "Route queued, complete, bounded, or error crawl state."
    AGENT_WORKFLOW_SAFE = True
    EFFECTS = (CapabilityEffect.RESOURCE_READ,)
    IDEMPOTENCY = IdempotencyClass.PURE
    AUTHORIZATION_TARGET_KIND = "workflow.runtime"
    RELATIONSHIPS: ClassVar = [
        "queued", "finished", "bounded", "errors", "failure",
    ]

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load_state(flowfile)
        inventory = dict(state["website"].get("inventory") or {})
        status = str(inventory.get("status") or "")
        relationship = {
            "running": "queued",
            "complete": "finished",
            "bounded": "bounded",
            "errors": "errors",
        }.get(status)
        if relationship is None:
            raise ValueError(f"unsupported Website Creator crawl status: {status}")
        if relationship == "queued":
            deadline = datetime.fromtimestamp(
                float(inventory.get("next_allowed_at") or 0.0), timezone.utc,
            ).isoformat()
            flowfile.set_attribute("website.crawl.next_allowed_at", deadline)
        flowfile.set_attribute("route.relationship", relationship)
        return [flowfile]


class PrepareInventoryDecisionTask(_WorkflowContextTask):
    """Require explicit acceptance before bounded/error inventories advance."""

    TYPE = "prepareInventoryDecision"
    NAME = "Prepare Inventory Decision"
    DESCRIPTION = "Present bounded crawl omissions and errors for durable approval."
    AGENT_WORKFLOW_SAFE = True
    EFFECTS = (CapabilityEffect.RESOURCE_READ,)
    IDEMPOTENCY = IdempotencyClass.PURE
    AUTHORIZATION_TARGET_KIND = "workflow.runtime"
    RELATIONSHIPS: ClassVar = ["ask", "accepted", "failure"]

    def get_parameter_schema(self) -> dict[str, Any]:
        return {
            "output_attribute": {
                "type": "string", "required": True,
                "default": "website.inventory_decision",
            },
        }

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load_state(flowfile)
        website = state["website"]
        inventory = dict(website.get("inventory") or {})
        status = str(inventory.get("status") or "")
        output_attribute = str(
            self.config.get("output_attribute") or "website.inventory_decision"
        )
        if status == "complete":
            flowfile.delete_attribute(output_attribute)
            relationship = "accepted"
        elif status in {"bounded", "errors"}:
            summary = {
                "status": status,
                "counts": inventory.get("counts") or {},
                "bounded_reasons": inventory.get("bounded_reasons") or [],
                "issues": inventory.get("issues") or [],
                "index_path": inventory.get("index_path"),
            }
            payload = {
                "message": (
                    "The deterministic crawl did not establish unqualified completeness. "
                    "Accept the recorded omissions, adjust and restart the crawl limits, "
                    "or stop.\n\n"
                    + json.dumps(summary, ensure_ascii=False, indent=2)
                )[:24000],
                "title": "Review Website Creator inventory",
                "kind": "form",
                "response_schema": {
                    "fields": [
                        {
                            "name": "decision", "label": "Decision",
                            "type": "choice", "required": True,
                            "options": [
                                {
                                    "value": "accept",
                                    "label": "Accept recorded omissions",
                                },
                                {
                                    "value": "adjust",
                                    "label": "Adjust limits and restart",
                                },
                                {"value": "stop", "label": "Stop"},
                            ],
                        },
                        {
                            "name": "feedback", "label": "Decision notes",
                            "type": "multiline", "required": False,
                            "max_length": 6000,
                        },
                    ],
                },
            }
            flowfile.set_attribute(
                output_attribute,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
            relationship = "ask"
        else:
            raise ValueError("inventory decision requires complete, bounded, or errors")
        flowfile.set_attribute("route.relationship", relationship)
        return [flowfile]


class ApplyInventoryDecisionTask(_WebsiteCrawlTask):
    """Persist acceptance, request a fresh bounded crawl, or stop."""

    TYPE = "applyInventoryDecision"
    NAME = "Apply Inventory Decision"
    DESCRIPTION = "Apply the durable decision for crawl omissions and errors."
    EFFECTS = (
        CapabilityEffect.RESOURCE_READ,
        CapabilityEffect.FILESYSTEM_READ,
        CapabilityEffect.FILESYSTEM_WRITE,
    )
    IDEMPOTENCY = IdempotencyClass.KEYED_EFFECT
    RELATIONSHIPS: ClassVar = ["accepted", "adjust", "stopped", "failure"]

    def _now(self) -> float:
        return time.time()

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _load_state(flowfile)
        website = state["website"]
        answer = _durable_answer(flowfile)
        if not isinstance(answer, dict):
            raise ValueError("durable inventory decision must be a form object")
        decision = str(answer.get("decision") or "").strip().casefold()
        feedback = str(answer.get("feedback") or "").strip()[:6000]
        if decision == "accept":
            service = self._resolve_relay(state)
            workspace = str(website["workspace"])
            checkpoint = _read_json(service, _checkpoint_path(workspace))
            original_status = str(checkpoint.get("status") or "")
            if original_status not in {"bounded", "errors"}:
                raise ValueError("only bounded or error inventories need acceptance")
            acceptance = {
                "decision": "accept",
                "crawl_status": original_status,
                "feedback": feedback,
                "accepted_at": datetime.fromtimestamp(
                    self._now(), timezone.utc,
                ).isoformat(),
                "bounded_reasons": list(checkpoint.get("bounded_reasons") or []),
                "issues": list(checkpoint.get("issues") or []),
            }
            checkpoint["accepted_omissions"] = [acceptance]
            manifest = _complete_manifest(
                service,
                workspace,
                checkpoint,
                dict(website["crawl"]),
                crawl_status=original_status,
            )
            _update_inventory_state(website, checkpoint, manifest)
            website["status"] = "inventory_complete"
            relationship = "accepted"
        elif decision == "adjust":
            crawl = dict(website.get("crawl") or {})
            crawl.update({
                "confirmed": False,
                "confirmation_required": True,
                "status": "pending_confirmation",
                "fresh_crawl": True,
            })
            website["crawl"] = crawl
            website["status"] = "crawl_adjustment_requested"
            if feedback:
                website["user_feedback"] = feedback
            relationship = "adjust"
        elif decision == "stop":
            website["status"] = "stopped"
            if feedback:
                website["user_feedback"] = feedback
            relationship = "stopped"
        else:
            raise ValueError("inventory decision must be accept, adjust, or stop")
        flowfile.set_attribute("route.relationship", relationship)
        return _store_state(flowfile, state)


for _task in (
    PrepareCrawlDecisionTask,
    ApplyCrawlDecisionTask,
    InitializeSiteCrawlTask,
    FetchSiteCrawlEntryTask,
    RouteSiteCrawlTask,
    PrepareInventoryDecisionTask,
    ApplyInventoryDecisionTask,
):
    TaskFactory.register(_task)


__all__ = [
    "ApplyCrawlDecisionTask",
    "ApplyInventoryDecisionTask",
    "FetchSiteCrawlEntryTask",
    "InitializeSiteCrawlTask",
    "PrepareCrawlDecisionTask",
    "PrepareInventoryDecisionTask",
    "RouteSiteCrawlTask",
]
