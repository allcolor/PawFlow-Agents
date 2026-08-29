"""Crawl-limit and durable-confirmation contracts for Website Creator."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core import FlowFile
from core.website_creator_contracts import (
    CrawlLimits,
    SourceRightsDeclaration,
    parse_labelled_crawl_options,
    prepare_crawl_contract,
)
from core.workflow_agent_contracts import (
    AgentWorkflowRequest,
    WorkflowConversationRef,
    WorkflowRequestBody,
    WorkflowTurnRef,
)
from tasks.ai.workflow.website_creator_crawl import (
    ApplyCrawlDecisionTask,
    ApplyInventoryDecisionTask,
    FetchSiteCrawlEntryTask,
    InitializeSiteCrawlTask,
    PrepareCrawlDecisionTask,
    PrepareInventoryDecisionTask,
    RouteSiteCrawlTask,
)
from tasks.ai.workflow.website_creator_tasks import PrepareWebsiteRequestTask


def _request(parameters, message="Create https://source.example from https://template.example"):
    return AgentWorkflowRequest(
        request=WorkflowRequestBody(message=message),
        conversation=WorkflowConversationRef(id="conv-1", agent="website-creator"),
        turn=WorkflowTurnRef(root_turn_id="turn-1", request_message_ids=("turn-1",)),
        parameters=parameters,
    )


def _context():
    return SimpleNamespace(
        run_id="run-1",
        conversation_id="conv-1",
        agent_name="website-creator",
        user_id="alice",
        root_turn_id="turn-1",
    )


def _flowfile(website):
    return FlowFile(content=json.dumps({"website": website}).encode("utf-8"))


def test_crawl_limits_apply_defaults_and_hard_bounds():
    assert CrawlLimits.from_mapping({}).to_dict() == {
        "max_pages": 100,
        "max_depth": 3,
        "politeness_delay_ms": 750,
        "request_timeout_seconds": 30,
        "max_total_bytes": 256 * 1024 * 1024,
        "max_duration_seconds": 1800,
    }
    assert CrawlLimits.from_mapping({"max_pages": 2000, "max_depth": 0}).max_pages == 2000
    for values in ({"max_pages": 2001}, {"max_depth": 9}, {"max_pages": True}):
        with pytest.raises(ValueError):
            CrawlLimits.from_mapping(values)


def test_rights_declaration_requires_provenance_and_rejects_other():
    rights = SourceRightsDeclaration.from_mapping({
        "basis": "permission",
        "allowed_asset_kinds": ["image", "stylesheet", "image"],
        "provenance": "Licensed by the source owner on 2026-08-29.",
    })
    assert rights.to_dict()["allowed_asset_kinds"] == ["image", "stylesheet"]
    with pytest.raises(ValueError, match="provenance"):
        SourceRightsDeclaration.from_mapping({
            "basis": "permission", "allowed_asset_kinds": ["image"],
        })
    with pytest.raises(ValueError, match="other"):
        SourceRightsDeclaration.from_mapping({
            "basis": "owner", "allowed_asset_kinds": ["other"],
        })


def test_only_exact_labelled_lines_are_parsed_from_free_text():
    parsed = parse_labelled_crawl_options(
        "Use maybe 900 pages\nmax_pages: 900\nmax_depth about 4\n"
        "max_depth: 4\nrights_basis: owner\nrights_asset_kinds: image, font"
    )
    assert parsed == {
        "max_pages": 900,
        "max_depth": 4,
        "rights": {
            "basis": "owner",
            "allowed_asset_kinds": ["image", "font"],
        },
    }


def test_confirmation_is_skipped_only_when_all_bounds_and_rights_are_explicit():
    complete = {
        "max_pages": 100,
        "max_depth": 3,
        "politeness_delay_ms": 750,
        "request_timeout_seconds": 30,
        "max_total_bytes": 1024,
        "max_duration_seconds": 60,
        "rights": {"basis": "none", "allowed_asset_kinds": []},
    }
    assert prepare_crawl_contract(complete, "")["confirmed"] is True
    del complete["max_depth"]
    proposed = prepare_crawl_contract(complete, "")
    assert proposed["confirmed"] is False
    assert proposed["effective_limits"]["max_depth"] == 3


def test_prepare_request_stores_frozen_explicit_crawl_contract(monkeypatch):
    crawl = {
        "max_pages": 10,
        "max_depth": 2,
        "politeness_delay_ms": 500,
        "request_timeout_seconds": 20,
        "max_total_bytes": 100000,
        "max_duration_seconds": 300,
        "rights": {"basis": "owner", "allowed_asset_kinds": ["image"]},
    }
    request = _request({
        "source_url": "https://source.example",
        "template_url": "https://template.example",
        "crawl": crawl,
    })
    task = PrepareWebsiteRequestTask({})
    monkeypatch.setattr(task, "_context", _context)
    monkeypatch.setattr(
        "tasks.ai.workflow.website_creator_tasks.validate_public_website_url",
        lambda value: value,
    )
    flowfile = FlowFile(content=json.dumps(request.to_dict()).encode("utf-8"))
    task.execute(flowfile)
    website = json.loads(flowfile.get_content())["website"]
    assert website["crawl"]["confirmed"] is True
    assert website["crawl"]["rights"]["allowed_asset_kinds"] == ["image"]


def test_prepare_crawl_decision_routes_explicit_contract_without_a_question():
    crawl = prepare_crawl_contract({
        "max_pages": 1,
        "max_depth": 0,
        "politeness_delay_ms": 250,
        "request_timeout_seconds": 1,
        "max_total_bytes": 1,
        "max_duration_seconds": 1,
        "rights": {"basis": "none", "allowed_asset_kinds": []},
    }, "")
    flowfile = _flowfile({"source_url": "https://example.com/", "crawl": crawl})
    PrepareCrawlDecisionTask({
        "output_attribute": "website.crawl_decision",
    }).execute(flowfile)
    assert flowfile.get_attribute("route.relationship") == "confirmed"
    assert not flowfile.get_attribute("website.crawl_decision")


def test_prepare_and_apply_durable_crawl_form():
    crawl = prepare_crawl_contract(None, "")
    flowfile = _flowfile({"source_url": "https://example.com/", "crawl": crawl})
    PrepareCrawlDecisionTask({
        "output_attribute": "website.crawl_decision",
    }).execute(flowfile)
    payload = json.loads(flowfile.get_attribute("website.crawl_decision"))
    assert flowfile.get_attribute("route.relationship") == "ask"
    assert payload["kind"] == "form"
    assert len(payload["response_schema"]["fields"]) == 12

    answer = {
        "decision": "confirm",
        "max_pages": 20,
        "max_depth": 2,
        "politeness_delay_ms": 500,
        "request_timeout_seconds": 10,
        "max_total_bytes": 1000000,
        "max_duration_seconds": 600,
        "rights_basis": "permission",
        "allowed_asset_kinds": ["image", "font"],
        "rights_provenance": "Written permission reference 42.",
        "include_url_patterns": "^https://example\\.com/",
        "exclude_url_patterns": "/logout$",
    }
    flowfile.set_attribute("durable.wait.value", json.dumps({
        "status": "answered", "answer": answer,
    }))
    flowfile.set_attribute("durable.wait.status", "signaled")
    ApplyCrawlDecisionTask({}).execute(flowfile)
    website = json.loads(flowfile.get_content())["website"]
    assert flowfile.get_attribute("route.relationship") == "confirmed"
    assert website["crawl"]["effective_limits"]["max_pages"] == 20
    assert website["crawl"]["rights"]["basis"] == "permission"
    assert website["crawl"]["include_url_patterns"] == ["^https://example\\.com/"]
    assert not flowfile.get_attribute("durable.wait.value")


def test_apply_crawl_stop_is_terminal_before_network_access():
    flowfile = _flowfile({
        "source_url": "https://example.com/",
        "crawl": prepare_crawl_contract(None, ""),
    })
    flowfile.set_attribute("durable.wait.value", json.dumps({
        "status": "answered", "answer": {"decision": "stop"},
    }))
    ApplyCrawlDecisionTask({}).execute(flowfile)
    website = json.loads(flowfile.get_content())["website"]
    assert flowfile.get_attribute("route.relationship") == "stopped"
    assert website["status"] == "stopped"


class _FakeRelay:
    _service_id = "relay-test"

    def __init__(self, responses=None):
        self.files = {}
        self.responses = {
            url: list(values) for url, values in (responses or {}).items()
        }
        self.http_calls = []

    def mkdir(self, _path, local=False):
        assert local is False

    def exists(self, path, local=False):
        assert local is False
        return path in self.files

    def read_file(self, path, local=False):
        assert local is False
        return self.files[path]

    def atomic_write_file(self, path, content, local=False):
        assert local is False
        self.files[path] = bytes(content)
        return {"written": len(content)}

    def append_file(self, path, content, expected_size, local=False):
        assert local is False
        current = self.files.get(path, b"")
        if len(current) != expected_size:
            raise ValueError("append offset mismatch")
        self.files[path] = current + bytes(content)
        return {"size": len(self.files[path])}

    def truncate_file(self, path, size, expected_size=None, local=False):
        assert local is False
        current = self.files[path]
        if expected_size is not None and len(current) != expected_size:
            raise ValueError("truncate size mismatch")
        if size > len(current):
            raise ValueError("cannot extend")
        self.files[path] = current[:size]
        return {"size": size}

    def stat(self, path, local=False):
        assert local is False
        return SimpleNamespace(size=len(self.files[path]))

    def delete_file(self, path, local=False):
        assert local is False
        del self.files[path]

    def http_fetch(self, url, **kwargs):
        self.http_calls.append((url, kwargs))
        values = self.responses.get(url)
        if not values:
            raise AssertionError(f"unexpected HTTP fetch: {url}")
        response = values.pop(0)
        if isinstance(response, Exception):
            raise response
        return {
            "status": response.get("status", 200),
            "headers": response.get("headers", {}),
            "body_bytes": response.get("body", b""),
            "url": response.get("url", url),
        }


def _confirmed_crawl(**overrides):
    values = {
        "max_pages": 10,
        "max_depth": 3,
        "politeness_delay_ms": 250,
        "request_timeout_seconds": 10,
        "max_total_bytes": 10 * 1024 * 1024,
        "max_duration_seconds": 600,
        "rights": {"basis": "owner", "allowed_asset_kinds": ["image", "stylesheet"]},
    }
    values.update(overrides)
    return prepare_crawl_contract(values, "")


def _crawler_flowfile(crawl=None):
    return _flowfile({
        "source_url": "https://example.com/",
        "template_url": "https://template.example/",
        "workspace": "/workspace/pawflow-sites/run-1",
        "crawl": crawl or _confirmed_crawl(),
        "status": "prepared",
    })


def _response(status=200, body=b"", content_type="text/html", **extra):
    return {
        "status": status,
        "body": body,
        "headers": {"Content-Type": content_type, **extra.pop("headers", {})},
        **extra,
    }


def _seed_responses(root_responses):
    return {
        "https://example.com/robots.txt": [
            _response(404, content_type="text/plain"),
        ],
        "https://example.com/sitemap.xml": [
            _response(404, content_type="application/xml"),
        ],
        "https://example.com/sitemap_index.xml": [
            _response(404, content_type="application/xml"),
        ],
        "https://example.com/": list(root_responses),
    }


def _bind(task, relay, now):
    task._website_fs_service = relay
    task._now = lambda: now[0]
    return task


def _run_until_terminal(flowfile, relay, now, maximum=20):
    for _index in range(maximum):
        task = _bind(FetchSiteCrawlEntryTask({}), relay, now)
        task.execute(flowfile)
        state = json.loads(flowfile.get_content())["website"]
        if state["inventory"]["status"] != "running":
            return state
        now[0] += 1.0
    raise AssertionError("crawler did not reach a terminal state")


def test_resumable_crawler_fetches_one_entry_per_call_and_hashes_complete_inventory():
    root = b"""<html><head><title>Home</title>
      <link rel='stylesheet' href='/assets/site.css'></head>
      <body><a href='/about'>About</a><img src='/logo.png'></body></html>"""
    about = b"<html><head><title>About</title></head><body>Done</body></html>"
    responses = _seed_responses([_response(body=root)])
    responses["https://example.com/about"] = [_response(body=about)]
    relay = _FakeRelay(responses)
    flowfile = _crawler_flowfile()
    now = [1000.0]
    initializer = _bind(InitializeSiteCrawlTask({}), relay, now)
    initializer.execute(flowfile)
    assert flowfile.get_attribute("route.relationship") == "queued"

    previous_calls = 0
    for _index in range(10):
        _bind(FetchSiteCrawlEntryTask({}), relay, now).execute(flowfile)
        assert len(relay.http_calls) - previous_calls <= 1
        previous_calls = len(relay.http_calls)
        website = json.loads(flowfile.get_content())["website"]
        if website["inventory"]["status"] == "complete":
            break
        now[0] += 1.0
    else:
        raise AssertionError("crawler did not complete")

    assert len(relay.http_calls) == 5
    for _url, kwargs in relay.http_calls:
        assert kwargs["public_only"] is True
        assert kwargs["timeout"] == 10
        assert kwargs["local"] is False
    website = json.loads(flowfile.get_content())["website"]
    assert website["inventory"]["counts"]["pages"] == 2
    assert website["inventory"]["counts"]["assets"] == 2
    assert website["inventory"]["manifest_digest"]
    pages = relay.files["/workspace/pawflow-sites/run-1/inventory/pages.ndjson"]
    assert pages.count(b"\n") == 5

    calls_before_reuse = len(relay.http_calls)
    _bind(InitializeSiteCrawlTask({}), relay, now).execute(flowfile)
    assert flowfile.get_attribute("route.relationship") == "finished"
    assert len(relay.http_calls) == calls_before_reuse


def test_crawl_depth_bound_requires_explicit_inventory_acceptance():
    responses = _seed_responses([
        _response(body=b"<html><body><a href='/next'>Next</a></body></html>"),
    ])
    relay = _FakeRelay(responses)
    flowfile = _crawler_flowfile(_confirmed_crawl(max_depth=0))
    now = [2000.0]
    _bind(InitializeSiteCrawlTask({}), relay, now).execute(flowfile)
    website = _run_until_terminal(flowfile, relay, now)
    assert website["inventory"]["status"] == "bounded"
    assert website["inventory"]["bounded_reasons"][0]["code"] == "max_depth"

    PrepareInventoryDecisionTask({
        "output_attribute": "website.inventory_decision",
    }).execute(flowfile)
    assert flowfile.get_attribute("route.relationship") == "ask"
    flowfile.set_attribute("durable.wait.value", json.dumps({
        "status": "answered",
        "answer": {"decision": "accept", "feedback": "The root page is sufficient."},
    }))
    accept = _bind(ApplyInventoryDecisionTask({}), relay, now)
    accept.execute(flowfile)
    website = json.loads(flowfile.get_content())["website"]
    assert flowfile.get_attribute("route.relationship") == "accepted"
    assert website["inventory"]["status"] == "complete"
    complete = json.loads(relay.files[
        "/workspace/pawflow-sites/run-1/inventory/complete.json"
    ])
    assert complete["crawl_status"] == "bounded"
    assert complete["accepted_omissions"][0]["decision"] == "accept"


def test_retry_after_parks_without_fetching_early_and_retries_same_entry():
    responses = _seed_responses([
        _response(
            429,
            body=b"later",
            content_type="text/plain",
            headers={"Retry-After": "5"},
        ),
        _response(body=b"<html><body>ok</body></html>"),
    ])
    relay = _FakeRelay(responses)
    flowfile = _crawler_flowfile()
    now = [3000.0]
    _bind(InitializeSiteCrawlTask({}), relay, now).execute(flowfile)
    for _index in range(3):
        _bind(FetchSiteCrawlEntryTask({}), relay, now).execute(flowfile)
        now[0] += 1.0
    _bind(FetchSiteCrawlEntryTask({}), relay, now).execute(flowfile)
    calls_after_429 = len(relay.http_calls)
    assert relay.http_calls[-1][0] == "https://example.com/"

    now[0] += 1.0
    _bind(FetchSiteCrawlEntryTask({}), relay, now).execute(flowfile)
    assert len(relay.http_calls) == calls_after_429
    now[0] += 4.0
    _bind(FetchSiteCrawlEntryTask({}), relay, now).execute(flowfile)
    assert len(relay.http_calls) == calls_after_429 + 1
    assert relay.http_calls[-1][0] == "https://example.com/"


def test_initialize_repairs_uncheckpointed_ndjson_tail_after_interruption():
    relay = _FakeRelay(_seed_responses([_response(body=b"<html></html>")]))
    flowfile = _crawler_flowfile()
    now = [4000.0]
    initializer = _bind(InitializeSiteCrawlTask({}), relay, now)
    initializer.execute(flowfile)
    pages_path = "/workspace/pawflow-sites/run-1/inventory/pages.ndjson"
    relay.files[pages_path] += b'{"orphan":true}\n'
    assert relay.files[pages_path]

    _bind(InitializeSiteCrawlTask({}), relay, now).execute(flowfile)
    assert relay.files[pages_path] == b""
    assert flowfile.get_attribute("route.relationship") == "queued"


def test_route_site_crawl_exposes_absolute_durable_deadline():
    flowfile = _crawler_flowfile()
    website = json.loads(flowfile.get_content())["website"]
    website["inventory"] = {"status": "running", "next_allowed_at": 5000.0}
    flowfile.set_content(json.dumps({"website": website}).encode("utf-8"))
    RouteSiteCrawlTask({}).execute(flowfile)
    assert flowfile.get_attribute("route.relationship") == "queued"
    assert flowfile.get_attribute("website.crawl.next_allowed_at").endswith("+00:00")


def test_crawl_authorization_target_binds_relay_and_inventory_paths(monkeypatch):
    task = InitializeSiteCrawlTask({})
    monkeypatch.setattr(task, "_context", _context)
    flowfile = _crawler_flowfile()
    website = json.loads(flowfile.get_content())["website"]
    website["relay_id"] = "relay-exact"
    flowfile.set_content(json.dumps({"website": website}).encode("utf-8"))
    target = task.workflow_authorization_target(flowfile)
    assert target["relay_id"] == "relay-exact"
    assert target["workspace"] == "/workspace/pawflow-sites/run-1"
    assert all(path.startswith(target["workspace"] + "/inventory") for path in target["paths"])
