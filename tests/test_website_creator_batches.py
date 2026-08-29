"""Batched Website Creator work and deterministic static-site finalization."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from core import FlowFile, TaskFactory
from core.agent_contracts import CapabilityEffect
from core.website_creator_batches import (
    BATCH_SIZE,
    StaticSiteFinalizer,
    WebsiteBatchCoordinator,
)
from tasks.ai.workflow.website_creator_batches import (
    BuildWebsitePageBatchTask,
    CorrectWebsitePageBatchTask,
    FinalizeStaticSiteTask,
    MapWebsitePageBatchTask,
    MergeWebsiteBuildTask,
    MergeWebsiteCorrectionTask,
    MergeWebsiteMappingTask,
    PrepareWebsiteBuildBatchesTask,
    PrepareWebsiteCorrectionBatchesTask,
    PrepareWebsiteMappingBatchesTask,
    RouteWebsiteBatchesTask,
)


def _stable(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


class _Relay:
    _service_id = "website-relay"

    def __init__(self):
        self.files = {}
        self.writes = []

    def exists(self, path, local=False):
        assert local is False
        return path in self.files

    def read_file(self, path, local=False):
        assert local is False
        return self.files[path]

    def atomic_write_file(self, path, content, local=False):
        assert local is False
        self.files[path] = bytes(content)
        self.writes.append(path)

    def mkdir(self, path, local=False):
        assert local is False

    def stat(self, path, local=False):
        assert local is False
        return SimpleNamespace(size=len(self.files[path]))

    def hash_file(self, path, local=False):
        assert local is False
        content = self.files[path]
        return {
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }


def _pages(count):
    return [
        {
            "page_url": f"https://example.com/page-{index}/",
            "local_path": f"page-{index}/index.html",
            "source_record_id": hashlib.sha256(str(index).encode()).hexdigest(),
            "raw_html_path": f"inventory/raw/{index}.html",
        }
        for index in range(count)
    ]


def _mapping_result(batch):
    return {
        "entries": [
            {
                "page_url": row["page_url"],
                "local_path": row["local_path"],
                "template_component": "content-page",
                "implementation": "Render the accepted page content.",
                "notes": "",
            }
            for row in batch["entries"]
        ],
    }


def test_two_thousand_pages_are_file_backed_in_batches_of_at_most_25():
    relay = _Relay()
    coordinator = WebsiteBatchCoordinator(
        relay,
        "/workspace/pawflow-sites/run-1",
        phase="mapping",
        manifest_digest="a" * 64,
        template_digest="",
        mapping_revision="",
    )

    summary = coordinator.prepare(_pages(2000))
    manifest = json.loads(relay.files[summary["manifest_path"]])

    assert BATCH_SIZE == 25
    assert summary == {
        "phase": "mapping",
        "manifest_path": "/workspace/pawflow-sites/run-1/mapping/batches.json",
        "manifest_digest": manifest["manifest_digest"],
        "batch_count": 80,
        "entry_count": 2000,
        "completed_batches": 0,
        "cursor": 0,
        "current_batch_path": (
            "/workspace/pawflow-sites/run-1/mapping/batch-0001.json"
        ),
        "current_result_path": manifest["batches"][0]["result_path"],
    }
    assert max(item["entry_count"] for item in manifest["batches"]) == 25
    assert "entries" not in summary


def test_completed_matching_batches_replay_and_only_changed_batch_invalidates():
    relay = _Relay()
    kwargs = {
        "phase": "mapping",
        "manifest_digest": "a" * 64,
        "template_digest": "",
        "mapping_revision": "",
    }
    coordinator = WebsiteBatchCoordinator(
        relay, "/workspace/pawflow-sites/run-1", **kwargs,
    )
    coordinator.prepare(_pages(30))
    first = coordinator.current_batch()
    coordinator.store_result(first["index"], _mapping_result(first))
    second = coordinator.current_batch()
    coordinator.store_result(second["index"], _mapping_result(second))

    replayed = WebsiteBatchCoordinator(
        relay, "/workspace/pawflow-sites/run-1", **kwargs,
    ).prepare(_pages(30))
    assert replayed["completed_batches"] == 2
    assert replayed["cursor"] == 2

    changed = _pages(30)
    changed[-1] = {
        **changed[-1],
        "raw_html_path": "inventory/raw/changed.html",
    }
    invalidated = WebsiteBatchCoordinator(
        relay, "/workspace/pawflow-sites/run-1", **kwargs,
    ).prepare(changed)
    assert invalidated["completed_batches"] == 1
    assert invalidated["cursor"] == 1


def test_mapping_merge_requires_exactly_once_batch_coverage():
    relay = _Relay()
    coordinator = WebsiteBatchCoordinator(
        relay,
        "/workspace/pawflow-sites/run-1",
        phase="mapping",
        manifest_digest="a" * 64,
        template_digest="",
        mapping_revision="",
    )
    coordinator.prepare(_pages(2))
    batch = coordinator.current_batch()
    missing = _mapping_result(batch)
    missing["entries"].pop()
    with pytest.raises(ValueError, match="exactly once"):
        coordinator.store_result(0, missing)

    duplicate = _mapping_result(batch)
    duplicate["entries"].append(dict(duplicate["entries"][0]))
    with pytest.raises(ValueError, match="exactly once"):
        coordinator.store_result(0, duplicate)

    coordinator.store_result(0, _mapping_result(batch))
    merged = coordinator.merge()
    assert merged["entry_count"] == 2
    assert merged["result_path"].endswith("mapping/merged.json")


def test_build_skips_require_approved_typed_policy():
    relay = _Relay()
    page = _pages(1)[0]
    coordinator = WebsiteBatchCoordinator(
        relay,
        "/workspace/pawflow-sites/run-1",
        phase="build",
        manifest_digest="a" * 64,
        template_digest="b" * 64,
        mapping_revision="c" * 64,
    )
    coordinator.prepare([page])
    result = {
        "pages_built": [],
        "skipped_pages": [{
            "page_url": page["page_url"],
            "reason": "model_choice",
            "decision_id": "",
        }],
        "assets_materialized": [],
        "files_changed": [],
        "validation": [],
        "remaining_issues": [],
    }
    with pytest.raises(ValueError, match="approved skip"):
        coordinator.store_result(0, result)

    approved = {**page, "skip_allowed": True, "skip_decision_id": "decision-1"}
    coordinator.prepare([approved])
    result["skipped_pages"][0].update({
        "reason": "accepted_omission",
        "decision_id": "decision-1",
    })
    coordinator.store_result(0, result)
    assert coordinator.merge()["skipped_count"] == 1


def _finalizer(relay, *, accepted_omissions=()):
    return StaticSiteFinalizer(
        relay,
        "/workspace/pawflow-sites/run-1",
        inventory_manifest_digest="a" * 64,
        mapping_digest="b" * 64,
        template_digest="c" * 64,
        accepted_omissions=list(accepted_omissions),
        attribution_paths=["site/LICENSE.txt"],
    )


def test_finalizer_rewrites_html_and_keeps_external_navigation():
    relay = _Relay()
    workspace = "/workspace/pawflow-sites/run-1"
    relay.files[workspace + "/site/index.html"] = (
        b'<html><head><base href="https://example.com/root/">'
        b'<link rel="stylesheet" href="/assets/site.css"></head><body>'
        b'<a id="internal" href="/about/">About</a>'
        b'<a id="external" href="https://other.example/news">News</a>'
        b'<form action="/submit"><button>Send</button></form>'
        b'<img src="/assets/logo.png" srcset="/assets/logo.png 1x, '
        b'/assets/logo@2x.png 2x"></body></html>'
    )
    relay.files[workspace + "/site/about/index.html"] = b"<h1>About</h1>"
    relay.files[workspace + "/site/LICENSE.txt"] = b"Template license"
    relay.files[workspace + "/assets/site.css"] = b"body { color: black; }"
    relay.files[workspace + "/assets/logo.png"] = b"logo"
    relay.files[workspace + "/assets/logo@2x.png"] = b"logo2"
    assets = []
    for url, path, kind in (
        ("https://example.com/assets/site.css", "assets/site.css", "stylesheet"),
        ("https://example.com/assets/logo.png", "assets/logo.png", "image"),
        ("https://example.com/assets/logo@2x.png", "assets/logo@2x.png", "image"),
    ):
        content = relay.files[workspace + "/" + path]
        assets.append({
            "url": url,
            "path": path,
            "kind": kind,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "required": True,
        })

    report = _finalizer(relay).run(
        pages=[
            {"page_url": "https://example.com/", "local_path": "index.html"},
            {"page_url": "https://example.com/about/", "local_path": "about/index.html"},
        ],
        assets=assets,
    )
    html = relay.files[workspace + "/site/index.html"].decode()

    assert report["passed"] is True
    assert "<base" not in html
    assert 'href="about/index.html"' in html
    assert 'href="https://other.example/news"' in html
    assert 'action="/submit"' not in html
    assert 'data-pawflow-disabled="active_endpoint"' in html
    assert 'src="assets/logo.png"' in html
    assert "assets/logo@2x.png 2x" in html


def test_finalizer_parses_css_transitively_and_never_mutates_javascript():
    relay = _Relay()
    workspace = "/workspace/pawflow-sites/run-1"
    relay.files[workspace + "/site/index.html"] = (
        b'<link rel="stylesheet" href="https://example.com/css/main.css">'
        b'<script src="assets/app.js"></script>'
    )
    relay.files[workspace + "/site/LICENSE.txt"] = b"license"
    relay.files[workspace + "/site/assets/app.js"] = (
        b'const untouched = "https://example.com/img/bg.png";'
    )
    relay.files[workspace + "/assets/main.css"] = (
        b'@import "theme.css"; .hero { background: url("../img/bg.png") }'
    )
    relay.files[workspace + "/assets/theme.css"] = b"body { color: navy }"
    relay.files[workspace + "/assets/bg.png"] = b"png"
    assets = []
    for url, path, kind in (
        ("https://example.com/css/main.css", "assets/main.css", "stylesheet"),
        ("https://example.com/css/theme.css", "assets/theme.css", "stylesheet"),
        ("https://example.com/img/bg.png", "assets/bg.png", "image"),
    ):
        content = relay.files[workspace + "/" + path]
        assets.append({
            "url": url,
            "path": path,
            "kind": kind,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "required": True,
        })
    javascript_before = relay.files[workspace + "/site/assets/app.js"]

    report = _finalizer(relay).run(
        pages=[{"page_url": "https://example.com/", "local_path": "index.html"}],
        assets=assets,
    )

    assert report["passed"] is True
    rewritten = relay.files[workspace + "/site/assets/main.css"].decode()
    assert "theme.css" in rewritten
    assert "bg.png" in rewritten
    assert relay.files[workspace + "/assets/main.css"].startswith(b'@import')
    assert relay.files[workspace + "/site/assets/app.js"] == javascript_before


def test_missing_required_asset_and_unaccepted_omission_are_blocking():
    relay = _Relay()
    workspace = "/workspace/pawflow-sites/run-1"
    relay.files[workspace + "/site/index.html"] = b'<img src="/missing.png">'
    relay.files[workspace + "/site/LICENSE.txt"] = b"license"

    report = _finalizer(relay, accepted_omissions=[{
        "page_url": "https://example.com/omitted/",
        "decision_id": "",
    }]).run(
        pages=[{"page_url": "https://example.com/", "local_path": "index.html"}],
        assets=[{
            "url": "https://example.com/missing.png",
            "path": "assets/missing.png",
            "kind": "image",
            "bytes": 7,
            "sha256": "d" * 64,
            "required": True,
        }],
    )

    assert report["passed"] is False
    codes = {item["code"] for item in report["blocking_issues"]}
    assert "missing_required_asset" in codes
    assert "omission_without_decision" in codes


def test_finalizer_replay_key_includes_generated_file_hashes():
    relay = _Relay()
    workspace = "/workspace/pawflow-sites/run-1"
    relay.files[workspace + "/site/index.html"] = b"<h1>One</h1>"
    relay.files[workspace + "/site/LICENSE.txt"] = b"license"
    finalizer = _finalizer(relay)
    args = {
        "pages": [{"page_url": "https://example.com/", "local_path": "index.html"}],
        "assets": [],
    }

    first = finalizer.run(**args)
    writes_after_first = len(relay.writes)
    second = finalizer.run(**args)
    assert second["replayed"] is True
    assert len(relay.writes) == writes_after_first

    relay.files[workspace + "/site/index.html"] = b"<h1>Changed</h1>"
    third = finalizer.run(**args)
    assert third["replayed"] is False
    assert third["replay_key"] != first["replay_key"]


def _flowfile(website):
    return FlowFile(content=_stable({"website": website}))


def test_prepare_mapping_task_reads_manifest_and_keeps_flow_state_bounded():
    relay = _Relay()
    workspace = "/workspace/pawflow-sites/run-1"
    page_records = [
        {
            "record_id": row["source_record_id"],
            "record_kind": "page",
            "canonical_url": row["page_url"],
            "status": 200,
            "raw_html_path": row["raw_html_path"],
            "error": "",
            "omission_reason": "",
        }
        for row in _pages(26)
    ]
    relay.files[workspace + "/inventory/pages.ndjson"] = b"".join(
        _stable(row) + b"\n" for row in page_records
    )
    complete = _stable({"status": "complete", "accepted_omissions": []})
    complete_path = workspace + "/inventory/complete.json"
    relay.files[complete_path] = complete
    task = PrepareWebsiteMappingBatchesTask({})
    task._website_fs_service = relay
    flowfile = _flowfile({
        "workspace": workspace,
        "inventory": {
            "complete_path": complete_path,
            "manifest_digest": hashlib.sha256(complete).hexdigest(),
        },
    })

    task.execute(flowfile)
    website = json.loads(flowfile.get_content())["website"]
    state = website["batches"]["mapping"]

    assert flowfile.get_attribute("route.relationship") == "batch"
    assert state["batch_count"] == 2
    assert state["entry_count"] == 26
    assert state["current_batch_path"].endswith("mapping/batch-0001.json")
    assert "entries" not in json.dumps(state)


def test_batch_model_tasks_have_closed_phase_specific_schemas():
    mapping = MapWebsitePageBatchTask({"service": "creator"})
    mapping_schema = mapping._submission_schema("explore")
    assert mapping_schema["additionalProperties"] is False
    assert mapping_schema["properties"]["entries"]["maxItems"] == 25
    assert set(mapping_schema["properties"]["entries"]["items"]["required"]) == {
        "page_url", "local_path", "template_component", "implementation", "notes",
    }

    for task, phase in (
        (BuildWebsitePageBatchTask({"service": "creator"}), "build"),
        (CorrectWebsitePageBatchTask({"service": "creator"}), "correct"),
    ):
        schema = task._submission_schema(phase)
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {
            "pages_built", "skipped_pages", "assets_materialized",
            "files_changed", "validation", "remaining_issues",
        }


def test_wp6_tasks_are_registered_safe_and_exactly_effectful():
    task_types = {
        "prepareWebsiteMappingBatches": PrepareWebsiteMappingBatchesTask,
        "mapWebsitePageBatch": MapWebsitePageBatchTask,
        "mergeWebsiteMapping": MergeWebsiteMappingTask,
        "prepareWebsiteBuildBatches": PrepareWebsiteBuildBatchesTask,
        "buildWebsitePageBatch": BuildWebsitePageBatchTask,
        "mergeWebsiteBuild": MergeWebsiteBuildTask,
        "prepareWebsiteCorrectionBatches": PrepareWebsiteCorrectionBatchesTask,
        "correctWebsitePageBatch": CorrectWebsitePageBatchTask,
        "mergeWebsiteCorrection": MergeWebsiteCorrectionTask,
        "routeWebsiteBatches": RouteWebsiteBatchesTask,
        "finalizeStaticSite": FinalizeStaticSiteTask,
    }
    for task_type, task_class in task_types.items():
        assert TaskFactory.get(task_type) is task_class
        assert task_class.AGENT_WORKFLOW_SAFE is True
    assert FinalizeStaticSiteTask.EFFECTS == (
        CapabilityEffect.RESOURCE_READ,
        CapabilityEffect.FILESYSTEM_READ,
        CapabilityEffect.FILESYSTEM_WRITE,
    )


def test_finalize_task_routes_deterministic_failure_to_correction():
    relay = _Relay()
    workspace = "/workspace/pawflow-sites/run-1"
    relay.files[workspace + "/site/index.html"] = b'<a href="/missing/">Missing</a>'
    relay.files[workspace + "/site/THIRD_PARTY_NOTICES.txt"] = b"notice"
    mapping = {
        "schema_version": 1,
        "phase": "mapping",
        "batch_manifest_digest": "d" * 64,
        "entries": [{
            "page_url": "https://example.com/",
            "local_path": "index.html",
            "template_component": "home",
            "implementation": "home",
            "notes": "",
        }],
        "entry_count": 1,
        "result_digest": "b" * 64,
    }
    mapping_path = workspace + "/mapping/merged.json"
    relay.files[mapping_path] = _stable(mapping)
    task = FinalizeStaticSiteTask({})
    task._website_fs_service = relay
    flowfile = _flowfile({
        "source_url": "https://example.com/",
        "workspace": workspace,
        "inventory": {
            "manifest_digest": "a" * 64,
            "accepted_omissions": [],
        },
        "mapping": {
            "result_path": mapping_path,
            "result_digest": "b" * 64,
        },
        "template": {
            "sha256": "c" * 64,
            "notice_path": workspace + "/site/THIRD_PARTY_NOTICES.txt",
        },
    })

    task.execute(flowfile)
    website = json.loads(flowfile.get_content())["website"]

    assert flowfile.get_attribute("route.relationship") == "correction"
    assert website["finalize"]["passed"] is False
    assert website["finalize"]["report_path"].endswith("reports/finalize.json")
    assert "blocking_issues" not in website["finalize"]
