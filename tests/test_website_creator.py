"""Unit contracts for the Website Creator Workflow Agent."""

from __future__ import annotations

import hashlib

import socket
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import FlowFile
from core.llm_client import LLMResponse, LLMToolCall
from core.tool_registry import ToolRegistry
from tasks.ai.workflow.website_creator_tasks import (
    ApplyWebsiteDecisionTask,
    PrepareWebsiteDecisionTask,
    SaveWebsiteAssetHandler,
    WebsiteCreatorToolTask,
    _SubmitWebsitePhaseHandler,
    _parse_website_phase_response,
    project_workspace_for_run,
    validate_public_website_url,
)


def _resolve_public(host: str, _port: int, **_kwargs):
    assert host == "example.com"
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://localhost/admin",
        "http://127.0.0.1/",
        "http://10.0.0.3/",
        "http://[::1]/",
        "https://user:secret@example.com/",
    ],
)
def test_public_website_url_rejects_non_public_targets(url):
    with pytest.raises(ValueError):
        validate_public_website_url(url)


def test_public_website_url_normalizes_http_url_and_checks_dns():
    assert validate_public_website_url(
        " https://EXAMPLE.com/path?q=1 ", resolver=_resolve_public
    ) == "https://example.com/path?q=1"


def test_public_website_url_rejects_hostname_resolving_to_private_address():
    def private(_host: str, _port: int, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.8", 443))]

    with pytest.raises(ValueError, match="public"):
        validate_public_website_url("https://example.com", resolver=private)


def test_project_workspace_is_stable_and_scoped_to_run():
    assert project_workspace_for_run("wr_123-abc") == (
        "/workspace/pawflow-sites/wr_123-abc"
    )
    with pytest.raises(ValueError):
        project_workspace_for_run("../../escape")


def test_tool_phases_have_no_implicit_limits_and_require_visual_tools():
    assert WebsiteCreatorToolTask.allowed_tool_names("explore") == (
        "screen", "see", "browser_console_extract", "fetch", "read", "glob", "search"
    )
    assert set(WebsiteCreatorToolTask.allowed_tool_names("build")) == {
        "screen", "see", "save_source_asset", "read", "write", "edit",
        "glob", "search", "browser_console_extract",
    }
    for unsafe in ("bash", "run_tests", "apply_patch"):
        assert unsafe not in WebsiteCreatorToolTask.allowed_tool_names("build")
    schema = WebsiteCreatorToolTask({
        "service": "creator", "phase": "build",
    }).get_parameter_schema()
    assert schema["max_iterations"]["default"] == 0
    assert schema["max_tokens"]["default"] == 0
    assert schema["browser_extractor_required"]["default"] is False
    with pytest.raises(ValueError, match="screen"):
        WebsiteCreatorToolTask.validate_required_observations(
            "explore", {"see": 1}
        )
    with pytest.raises(ValueError, match="see"):
        WebsiteCreatorToolTask.validate_required_observations(
            "explore", {"screen": 1}
        )
    WebsiteCreatorToolTask.validate_required_observations(
        "explore", {"screen": 2, "see": 1}
    )


def test_build_prompt_uses_visible_chromium_console_and_preserves_source_images():
    system, _user = WebsiteCreatorToolTask._prompt("build", {
        "website": {
            "source_url": "https://example.com/source",
            "template_url": "https://example.com/template",
            "workspace": "/workspace/pawflow-sites/wr-1",
            "request": "Build it",
            "user_feedback": "Keep every gallery image.",
        },
    })

    assert "DevTools" in system
    assert "clipboard_read" in system
    assert "save_source_asset" in system
    assert "source images" in system


def test_source_asset_handler_writes_downloaded_image_inside_workspace(monkeypatch):
    workspace = "/workspace/pawflow-sites/wr-1"
    handler = SaveWebsiteAssetHandler(
        workspace,
        source_url="https://example.com/",
        rights={"basis": "owner", "allowed_asset_kinds": ["image"]},
        total_budget_bytes=256 * 1024 * 1024,
    )
    fetched = {}

    class Relay:
        _service_id = "arbitrary-configured-relay"
        files = {}

        def exists(self, path, local=False):
            return path in self.files

        def read_file(self, path, local=False):
            return self.files[path]

        def atomic_write_file(self, path, content, local=False):
            self.files[path] = bytes(content)

        def stat(self, path, local=False):
            return SimpleNamespace(size=len(self.files[path]))

        def hash_file(self, path, local=False):
            content = self.files[path]
            return {
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }

        def http_fetch_to_file(self, url, path, headers=None,
                               timeout=300, local=False, **kwargs):
            fetched.update(
                url=url, path=path, headers=headers,
                timeout=timeout, local=local, kwargs=kwargs,
            )
            content = b"\x89PNG\r\n\x1a\nasset"
            self.files[path] = content
            return {
                "status": 200,
                "headers": {"Content-Type": "image/png"},
                "url": url,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "content_type": "image/png",
                "saved": True,
            }

    relay = Relay()
    handler.set_fs_service(relay)
    monkeypatch.setattr(
        "tasks.ai.workflow.website_creator_tasks.validate_public_website_url",
        lambda value: value,
    )
    result = json.loads(handler.execute({
        "url": "https://example.com/photo.png",
        "path": "assets/photo.png",
        "kind": "image",
    }))

    assert fetched["url"] == "https://example.com/photo.png"
    assert fetched["path"] == workspace + "/assets/photo.png"
    assert fetched["local"] is False
    assert fetched["kwargs"] == {
        "max_bytes": 12 * 1024 * 1024,
        "public_only": True,
        "expected_kind": "image",
    }
    assert relay.files[workspace + "/assets/photo.png"].startswith(b"\x89PNG")
    assert result["bytes"] == len(relay.files[workspace + "/assets/photo.png"])
    assert result["path"] == workspace + "/assets/photo.png"


def test_website_review_repeats_until_explicit_acceptance():
    flow = json.loads(Path(
        "packages/pawflow.website-creator.pfpdir/content/flows/website-creator.json"
    ).read_text(encoding="utf-8"))
    edges = {
        (row["from"], row["type"]): row["to"]
        for row in flow["relations"]
    }

    assert edges[("apply_review_decision", "accepted")] == "format_result"
    assert edges[("apply_review_decision", "revise")] == (
        "prepare_correction_batches"
    )
    assert edges[("merge_correction", "success")] == "finalize_static_site"
    correction_edge = next(
        row for row in flow["relations"]
        if row["from"] == "merge_correction"
        and row["to"] == "finalize_static_site"
    )
    assert correction_edge["explicit_loop"] is True
    assert "max_visits" not in correction_edge
    assert "one final correction" not in flow["description"].casefold()


def _build_phase_task(monkeypatch, responses):
    task = WebsiteCreatorToolTask({
        "service": "creator", "phase": "build", "max_iterations": 12,
    })
    context = SimpleNamespace(
        conversation_id="conv-1",
        run_id="wr-1",
        agent_name="website-creator",
        user_id="alice",
        root_turn_id="turn-1",
        flow_ref=SimpleNamespace(name="pawflow.agents.website-creator:1.1.0"),
    )
    submit = _SubmitWebsitePhaseHandler(task._schema("build"))
    registry = ToolRegistry()
    registry.register(submit)
    seen_messages = []

    class Client:
        pass

    class Service:
        def get_client(self):
            return Client()

    class RunStore:
        def begin_llm_step(self, *_args):
            return None

        def commit_llm_step(self, _run_id, _step_key, _input_hash,
                            result, usage):
            return {"result": result, "usage": usage, "step_usage": usage}

        def abort_llm_step(self, *_args):
            raise AssertionError("completed fake calls must be committed")

    iterator = iter(responses)

    def complete(_client, messages, _tools, _cancel_event):
        seen_messages.append([(message.role, message.content) for message in messages])
        return next(iterator)

    monkeypatch.setattr(task, "_context", lambda: context)
    monkeypatch.setattr(
        task, "_resolve_service",
        lambda _context: ("creator", {"id": "creator"}, Service()))
    monkeypatch.setattr(task, "_bind_client", lambda *_args: None)
    monkeypatch.setattr(task, "_registry", lambda *_args: (registry, submit))
    monkeypatch.setattr(task, "_complete_tool_turn", complete)
    monkeypatch.setattr(task, "_usage", lambda *_args: {})
    monkeypatch.setattr(task, "_record_usage_once", lambda *_args: None)
    task._workflow_run_store = RunStore()
    flowfile = FlowFile(content=json.dumps({
        "website": {
            "source_url": "https://example.com/source",
            "template_url": "https://example.com/template",
            "workspace": "/workspace/pawflow-sites/wr-1",
            "request": "Build it",
        },
    }).encode("utf-8"))
    return task, flowfile, seen_messages


def test_text_only_tool_turn_gets_correction_until_submission(monkeypatch):
    submission = {
        "summary": "Built",
        "workspace": "/workspace/pawflow-sites/wr-1",
        "preview_url": "",
        "files_changed": ["index.html"],
        "validation": ["reviewed"],
        "remaining_issues": [],
    }
    task, flowfile, seen = _build_phase_task(monkeypatch, [
        LLMResponse(content="I will do that.", model="fake", tool_calls=[]),
        LLMResponse(content="Still preparing.", model="fake", tool_calls=[]),
        LLMResponse(
            content="", model="fake",
            tool_calls=[LLMToolCall(
                id="submit-1", name="submit_website_phase",
                arguments=submission)],
        ),
    ])

    result = task.execute(flowfile)

    assert len(result) == 1
    assert len(seen) == 3
    assert "No textual answer is accepted" in seen[1][-1][1]
    assert "No textual answer is accepted" in seen[2][-1][1]


def test_tool_phase_emits_redacted_execution_timeline(monkeypatch):
    submission = {
        "summary": "Built",
        "workspace": "/workspace/pawflow-sites/wr-1",
        "preview_url": "",
        "files_changed": ["index.html"],
        "validation": ["reviewed"],
        "remaining_issues": [],
    }
    task, flowfile, _seen = _build_phase_task(monkeypatch, [
        LLMResponse(content="I am checking the generated page.", model="fake"),
        LLMResponse(
            content="", model="fake",
            tool_calls=[LLMToolCall(
                id="submit-1", name="submit_website_phase",
                arguments=submission)],
        ),
    ])
    events = []
    task._workflow_event_callback = lambda kind, data: events.append((kind, data))

    task.execute(flowfile)

    kinds = [kind for kind, _data in events]
    assert "agent_message" in kinds
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    message = next(data for kind, data in events if kind == "agent_message")
    assert message["role"] == "assistant"
    assert message["content"] == "I am checking the generated page."
    call = next(data for kind, data in events if kind == "tool_call")
    assert call["tool_call_id"] == "submit-1"
    assert call["tool_name"] == "submit_website_phase"
    assert call["arguments"]["workspace"].endswith("/wr-1")
    result = next(data for kind, data in events if kind == "tool_result")
    assert result["tool_call_id"] == "submit-1"
    assert result["outcome"] == "completed"


def test_structured_text_result_completes_cli_provider_phase(monkeypatch):
    submission = {
        "summary": "Built",
        "workspace": "/workspace/pawflow-sites/wr-1",
        "preview_url": "",
        "files_changed": ["index.html"],
        "validation": ["reviewed"],
        "remaining_issues": [],
    }
    task, flowfile, seen = _build_phase_task(monkeypatch, [
        LLMResponse(content=json.dumps(submission), model="fake", tool_calls=[]),
    ])
    events = []
    task._workflow_event_callback = lambda kind, data: events.append((kind, data))

    result = task.execute(flowfile)

    assert len(result) == 1
    assert len(seen) == 1
    website = json.loads(result[0].get_content().decode("utf-8"))["website"]
    assert website["build"] == submission
    assert website["status"] == "built"
    message = next(data for kind, data in events if kind == "agent_message")
    assert "content" not in message
    assert message["structured_content"] == submission


def test_structured_text_result_recovers_only_missing_json_closers(monkeypatch):
    submission = {
        "summary": "Built",
        "workspace": "/workspace/pawflow-sites/wr-1",
        "preview_url": "",
        "files_changed": ["index.html"],
        "validation": ["reviewed"],
        "remaining_issues": [],
    }
    task, flowfile, seen = _build_phase_task(monkeypatch, [
        LLMResponse(content=json.dumps(submission)[:-1], model="fake", tool_calls=[]),
    ])
    events = []
    task._workflow_event_callback = lambda kind, data: events.append((kind, data))

    result = task.execute(flowfile)

    assert len(result) == 1
    assert len(seen) == 1
    website = json.loads(result[0].get_content().decode("utf-8"))["website"]
    assert website["build"] == submission
    message = next(data for kind, data in events if kind == "agent_message")
    assert "content" not in message
    assert message["structured_content"] == submission


def test_structured_text_result_does_not_invent_truncated_string_content():
    assert _parse_website_phase_response('{"summary":"cut') is None


def test_structured_text_result_recovers_nested_closer_before_final_object():
    submission = {
        "summary": "Explored",
        "risks": ["Keep claims aligned with the source"],
    }
    complete = json.dumps(submission)
    assert complete.endswith("]}")

    assert _parse_website_phase_response(complete[:-2] + "}") == submission


def test_structured_blocked_result_reports_provider_reason(monkeypatch):
    task, flowfile, seen = _build_phase_task(monkeypatch, [
        LLMResponse(
            content=json.dumps({
                "status": "blocked_visual_inspection",
                "reason": "screen was denied because no live subscriber exists",
            }),
            model="fake",
            tool_calls=[],
        ),
    ])

    with pytest.raises(
        RuntimeError,
        match=(
            "Website Creator build blocked_visual_inspection: screen was denied "
            "because no live subscriber exists"
        ),
    ):
        task.execute(flowfile)

    assert len(seen) == 1


def test_workflow_tool_scope_is_ephemeral_allowlisted_and_confines_wrappers():
    from core.workflow_tool_scope import (
        authorize_workflow_tool,
        workflow_tool_scope,
        workflow_tool_visible_names,
    )

    conversation_id = "conv-1::workflow::wr-1::build"
    workspace = "/workspace/pawflow-sites/wr-1"

    def guard(name, arguments):
        return WebsiteCreatorToolTask.confine_tool_arguments(
            name, arguments, workspace,
        )

    assert authorize_workflow_tool(conversation_id, "bash", {}) is None
    with workflow_tool_scope(conversation_id, {"read", "write"}, guard):
        provider_conversation_id = (
            conversation_id + "__ephemeral_" + "a" * 32
        )
        assert workflow_tool_visible_names(provider_conversation_id) == frozenset({
            "get_tool_schema", "read", "use_tool", "write",
        })
        direct = authorize_workflow_tool(
            provider_conversation_id, "read", {"path": "index.html"},
        )
        assert direct == {
            "tool_name": "read",
            "arguments": {"path": workspace + "/index.html"},
        }
        assert authorize_workflow_tool(
            conversation_id + "__ephemeral_not-provider-owned",
            "read",
            {"path": "index.html"},
        ) is None
        wrapped = authorize_workflow_tool(conversation_id, "use_tool", {
            "tool_name": "write",
            "arguments_json": json.dumps({"path": "site.css", "content": "x"}),
        })
        assert wrapped["tool_name"] == "use_tool"
        assert json.loads(wrapped["arguments"]["arguments_json"])["path"] == (
            workspace + "/site.css"
        )
        with pytest.raises(PermissionError, match="not allowed"):
            authorize_workflow_tool(conversation_id, "bash", {"command": "pwd"})
        with pytest.raises(ValueError, match="requires one allowed tool_name"):
            authorize_workflow_tool(conversation_id, "get_tool_schema", {})
        with pytest.raises(ValueError, match="run workspace"):
            authorize_workflow_tool(
                conversation_id, "read", {"path": "/etc/passwd"},
            )
    assert authorize_workflow_tool(conversation_id, "bash", {}) is None


def test_cli_prompt_allows_validated_json_when_submit_tool_is_not_exposed():
    system, _user = WebsiteCreatorToolTask._prompt("build", {
        "website": {
            "source_url": "https://example.com/source",
            "template_url": "https://example.com/template",
            "workspace": "/workspace/pawflow-sites/wr-1",
            "request": "Build it",
        },
    })

    assert "does not expose submit_website_phase" in system
    assert "exact JSON object matching its schema" in system
    assert '"additionalProperties": false' in system
    assert '"files_changed"' in system
    assert '"remaining_issues"' in system


def test_website_visual_tools_are_pinned_to_the_selected_relay():
    workspace = "/workspace/pawflow-sites/wr-1"

    screen = WebsiteCreatorToolTask.confine_tool_arguments(
        "screen", {"action": "screenshot"}, workspace,
        relay_id="WebSiteRelay",
    )
    see = WebsiteCreatorToolTask.confine_tool_arguments(
        "see", {"path": "fs://filestore/capture/image.png"}, workspace,
        relay_id="WebSiteRelay",
    )

    assert screen["relay"] == "WebSiteRelay"
    assert see["source"] == "WebSiteRelay"


def test_website_tool_turn_passes_ephemeral_identity_per_call():
    task = WebsiteCreatorToolTask({
        "service": "creator", "phase": "build", "max_iterations": 2,
    })
    task._website_tool_guard = lambda name, arguments: arguments
    captured = {}

    class Client:
        _user_id = "alice"
        _conversation_id = "conv-1::workflow::wr-1::build"
        _agent_name = "website-creator"
        _event_cid = "conv-1"

        def complete(self, **kwargs):
            captured.update(kwargs)
            return LLMResponse(content="{}", model="fake")

    response = task._complete_tool_turn(Client(), [], [], None)

    assert response.model == "fake"
    assert captured["max_tokens"] is None
    assert {key: captured[key] for key in (
        "call_user_id", "call_conversation_id", "call_agent_name",
        "call_event_cid", "call_ephemeral_stream",
    )} == {
        "call_user_id": "alice",
        "call_conversation_id": "conv-1::workflow::wr-1::build",
        "call_agent_name": "website-creator",
        "call_event_cid": "conv-1",
        "call_ephemeral_stream": True,
    }


def test_website_tool_phase_has_no_implicit_timeout():
    task = WebsiteCreatorToolTask({
        "service": "creator", "phase": "build", "max_iterations": 2,
    })

    assert "timeout" not in task.get_parameter_schema()


def test_website_tool_turn_aborts_only_on_explicit_cancellation():
    task = WebsiteCreatorToolTask({"service": "creator", "phase": "build"})
    task._website_tool_guard = lambda name, arguments: arguments
    cancel = threading.Event()
    entered = threading.Event()

    class Client:
        _user_id = "alice"
        _conversation_id = "conv-1::workflow::wr-1::build"
        _agent_name = "website-creator"
        _event_cid = "conv-1"

        def __init__(self):
            self.aborted = threading.Event()

        def complete(self, **_kwargs):
            entered.set()
            assert self.aborted.wait(2)
            return LLMResponse(content="{}", model="fake")

        def abort(self):
            self.aborted.set()

    client = Client()
    stopper = threading.Thread(
        target=lambda: (entered.wait(2), cancel.set()), daemon=True,
    )
    stopper.start()

    response = task._complete_tool_turn(client, [], [], cancel)

    stopper.join(timeout=2)
    assert response.model == "fake"
    assert client.aborted.is_set()


def test_website_tools_reject_workspace_escape_and_non_workspace_file_urls():
    workspace = "/workspace/pawflow-sites/wr_123"
    assert WebsiteCreatorToolTask.confine_tool_arguments(
        "write", {"path": "assets/site.css"}, workspace,
    )["path"] == workspace + "/assets/site.css"
    with pytest.raises(ValueError, match="run workspace"):
        WebsiteCreatorToolTask.confine_tool_arguments(
            "read", {"path": "../other/secret.txt"}, workspace,
        )
    assert WebsiteCreatorToolTask.confine_tool_arguments(
        "screen", {
            "action": "type",
            "text": "file:///workspace/pawflow-sites/wr_123/index.html",
        }, workspace,
    )["text"] == "file:///workspace/pawflow-sites/wr_123/index.html"
    with pytest.raises(ValueError, match="run workspace"):
        WebsiteCreatorToolTask.confine_tool_arguments(
            "screen", {"action": "type", "text": "file:///etc/passwd"},
            workspace,
        )
    with pytest.raises(ValueError, match="public HTTP"):
        WebsiteCreatorToolTask.confine_tool_arguments(
            "screen", {"action": "type", "text": "ftp://example.com/site"},
            workspace,
        )


@pytest.mark.parametrize(
    "decision,details_key,output_attribute,expected_values",
    [
        (
            "mapping",
            "explore",
            "website.mapping_decision",
            ["approved", "rejected"],
        ),
        (
            "review",
            "review",
            "website.review_decision",
            ["accepted", "revise"],
        ),
    ],
)
def test_website_decision_forms_include_dynamic_report_and_feedback(
    decision, details_key, output_attribute, expected_values,
):
    details = {"summary": f"{decision} report", "checks": ["desktop"]}
    if decision == "review":
        details["passed"] = True
    flowfile = FlowFile(content=json.dumps({
        "website": {details_key: details},
    }).encode("utf-8"))

    PrepareWebsiteDecisionTask({
        "decision": decision,
        "output_attribute": output_attribute,
    }).execute(flowfile)

    payload = json.loads(flowfile.get_attribute(output_attribute))
    assert json.dumps(details, ensure_ascii=False, indent=2) in payload["message"]
    assert payload["kind"] == "form"
    fields = payload["response_schema"]["fields"]
    assert [option["value"] for option in fields[0]["options"]] == expected_values
    assert fields[1] == {
        "name": "feedback",
        "label": "Feedback",
        "type": "multiline",
        "required": False,
        "max_length": 6000,
    }
    if decision == "review":
        assert "one" not in payload["message"].casefold()
        assert [option["label"] for option in fields[0]["options"]] == [
            "Accept website", "Request corrections",
        ]


def test_negative_website_review_routes_back_without_user_interaction():
    output_attribute = "website.review_decision"
    flowfile = FlowFile(
        content=json.dumps({
            "website": {
                "status": "reviewed",
                "review": {
                    "summary": "The cookie banner blocks the site.",
                    "passed": False,
                    "checks": ["desktop", "mobile"],
                    "issues": [
                        "Cookie banner cannot be dismissed.",
                        "Three pages are missing.",
                    ],
                    "preview_url": "file:///workspace/site/index.html",
                },
            },
        }).encode("utf-8"),
        attributes={output_attribute: "stale-form"},
    )

    PrepareWebsiteDecisionTask({
        "decision": "review",
        "output_attribute": output_attribute,
    }).execute(flowfile)

    website = json.loads(flowfile.get_content().decode("utf-8"))["website"]
    assert flowfile.get_attribute("route.relationship") == "revise"
    assert flowfile.get_attribute(output_attribute) is None
    assert website["status"] == "revision_required"
    assert "Cookie banner cannot be dismissed." in website["user_feedback"]
    assert "Three pages are missing." in website["user_feedback"]


def test_website_review_requires_an_explicit_boolean_verdict():
    flowfile = FlowFile(content=json.dumps({
        "website": {"review": {"summary": "Ambiguous review"}},
    }).encode("utf-8"))

    with pytest.raises(ValueError, match="passed"):
        PrepareWebsiteDecisionTask({
            "decision": "review",
            "output_attribute": "website.review_decision",
        }).execute(flowfile)


@pytest.mark.parametrize(
    "decision,answer,relationship",
    [
        ("mapping", "approved", "approved"),
        ("mapping", "rejected", "rejected"),
        ("review", "accepted", "accepted"),
        ("review", "revise", "revise"),
    ],
)
def test_website_durable_decisions_route_once_and_preserve_feedback(
    decision, answer, relationship,
):
    flowfile = FlowFile(
        content=json.dumps({"website": {"status": "reviewed"}}).encode("utf-8"),
        attributes={
            "durable.wait.status": "signaled",
            "durable.wait.value": json.dumps({
                "status": "answered",
                "answer": {"decision": answer, "feedback": "Please keep the logo."},
            }),
        },
    )

    ApplyWebsiteDecisionTask({"decision": decision}).execute(flowfile)

    website = json.loads(flowfile.get_content().decode("utf-8"))["website"]
    assert flowfile.get_attribute("route.relationship") == relationship
    assert website[f"{decision}_decision"] == answer
    assert website["user_feedback"] == "Please keep the logo."
    assert flowfile.get_attribute("durable.wait.status") is None
    assert flowfile.get_attribute("durable.wait.value") is None
    if answer == "rejected":
        assert website["status"] == "rejected"


def test_workflow_subconversation_inherits_parent_service_scope():
    from core.service_registry import _parent_conversation_id

    assert _parent_conversation_id(
        "conv-1::workflow::wr-1::WebsiteCreatorToolTask"
        "__ephemeral_0123456789abcdef0123456789abcdef"
    ) == "conv-1"
