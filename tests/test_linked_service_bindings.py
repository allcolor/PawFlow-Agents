"""Optional conversation bindings for automatic PawFlow service roles."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core import FlowFile
from core._service_defs import ServiceDef
from core import linked_service_bindings as bindings


class _Store:
    def __init__(self):
        self.extras = {}

    def get_extra_cached(self, conversation_id, key, default=None):
        return self.extras.get((conversation_id, key), default)

    def set_extra(self, conversation_id, key, value):
        self.extras[(conversation_id, key)] = value


class _Registry:
    def __init__(self, definitions, live=None):
        self.definitions = list(definitions)
        self.live = dict(live or {})

    def resolve_all(self, **_kwargs):
        return {item.service_id: item for item in self.definitions}

    def get_definition(self, scope, _scope_id, service_id):
        return next((
            item for item in self.definitions
            if item.scope == scope and item.service_id == service_id
        ), None)

    def get_live_instance(self, scope, _scope_id, service_id):
        return self.live.get((scope, service_id))


@pytest.fixture
def linked_env(monkeypatch):
    store = _Store()
    llm = ServiceDef(
        service_id="Writer", service_type="llmConnection",
        scope="user", scope_id="alice")
    summarizer = ServiceDef(
        service_id="Summary", service_type="summarizer",
        scope="global", config={"llm_service": "Writer"})
    relay = ServiceDef(service_id="Relay", service_type="relay", scope="user")
    live_llm = SimpleNamespace(complete=lambda *_args, **_kwargs: None)
    registry = _Registry(
        [llm, summarizer, relay], {("user", "Writer"): live_llm})
    monkeypatch.setattr(bindings, "_store", lambda: store)
    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        staticmethod(lambda: registry))
    return store, registry, live_llm


def test_absent_binding_is_only_the_pawflow_default(linked_env):
    store, _registry, _live = linked_env

    assert bindings.get_binding("auto_memory", "conv-1") == {}
    assert bindings.resolve_service_override(
        "auto_memory", "alice", "conv-1") == (None, None, False)
    assert store.extras == {}


def test_service_binding_round_trip_and_resolution(linked_env):
    store, _registry, live = linked_env
    binding = {"kind": "service", "scope": "user", "service_id": "Writer"}

    bindings.set_binding(
        "content_review", "conv-1", binding, user_id="alice")

    assert store.extras[("conv-1", "linked_service_bindings")] == {
        "content_review": binding,
    }
    service, definition, explicit = bindings.resolve_service_override(
        "content_review", "alice", "conv-1")
    assert service is live
    assert definition.service_id == "Writer"
    assert explicit is True
    assert bindings.clear_binding("content_review", "conv-1") is True
    assert bindings.get_binding("content_review", "conv-1") == {}


def test_broken_binding_is_reported_and_resolves_to_fallback(linked_env):
    store, _registry, _live = linked_env
    store.extras[("conv-1", "linked_service_bindings")] = {
        "content_review": {
            "kind": "service", "scope": "user", "service_id": "Missing",
        },
    }

    state = next(
        item for item in bindings.summary("alice", "conv-1")["roles"]
        if item["role"] == "content_review")
    assert state["explicit"] is True
    assert state["broken"] is True
    assert "not compatible" in state["error"]
    assert bindings.resolve_llm_override(
        "content_review", "alice", "conv-1") == (
            None, None, "", True)


def test_available_targets_are_capability_filtered(linked_env, monkeypatch):
    _store, _registry, _live = linked_env
    monkeypatch.setattr(
        "core.conv_agent_config.get_all_agent_configs", lambda _cid: ["Wiki"])
    monkeypatch.setattr(
        bindings, "_agent_supports", lambda *_args: True)
    monkeypatch.setattr(
        bindings, "_agent_definition",
        lambda *_args: (
            "Wiki", {"definition": "wiki"}, {"description": "Wiki Agent"}))

    assert [item["service_id"] for item in bindings.list_available(
        "summary_compaction", "alice", "conv-1")] == ["Summary"]
    assert [item["service_id"] for item in bindings.list_available(
        "conversation_title", "alice", "conv-1")] == ["Writer"]
    wiki = bindings.list_available("project_wiki", "alice", "conv-1")
    assert {item["kind"] for item in wiki} == {"service", "agent"}
    assert not any(
        item["kind"] == "agent" for item in bindings.list_available(
            "auto_memory", "alice", "conv-1"))


def test_unreadable_agent_does_not_hide_service_targets(linked_env, monkeypatch):
    monkeypatch.setattr(
        "core.conv_agent_config.get_all_agent_configs", lambda _cid: ["Broken"])
    monkeypatch.setattr(
        bindings, "_agent_supports",
        MagicMock(side_effect=RuntimeError("corrupt agent resource")))

    available = bindings.list_available("project_wiki", "alice", "conv-1")

    assert {item["service_id"] for item in available} == {"Writer", "Summary"}


def test_link_list_and_unlink_actions_use_the_same_binding(linked_env):
    from tasks.ai.actions.misc import _handle_misc

    flowfile = FlowFile()
    _handle_misc(None, "linked_service_link", {
        "conversation_id": "conv-1", "role": "content_review",
        "kind": "service", "scope": "user", "service_id": "Writer",
    }, None, "alice", flowfile)
    linked = json.loads(flowfile.content)
    assert linked["ok"] is True
    role = next(
        item for item in linked["linked_services"]["roles"]
        if item["role"] == "content_review")
    assert role["binding"]["service_id"] == "Writer"

    flowfile = FlowFile()
    _handle_misc(None, "linked_service_unlink", {
        "conversation_id": "conv-1", "role": "content_review",
    }, None, "alice", flowfile)
    unlinked = json.loads(flowfile.content)
    assert unlinked["ok"] is True
    assert unlinked["removed"] is True


def test_historical_embedding_parameter_precedes_binding(monkeypatch):
    from core.embeddings import _resolve_embedding_llm_service

    configured = SimpleNamespace(embed=lambda _texts: [[1.0]])
    registry = SimpleNamespace(resolve=lambda *_args, **_kwargs: configured)
    linked = MagicMock()
    monkeypatch.setattr("core.expression.resolve_value", lambda *_args, **_kwargs: "Configured")
    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        staticmethod(lambda: registry))
    monkeypatch.setattr(bindings, "resolve_service_override", linked)

    assert _resolve_embedding_llm_service("alice", "conv-1") == (
        configured, "Configured")
    linked.assert_not_called()


def test_historical_title_and_ocr_parameters_precede_bindings(monkeypatch):
    from tasks.ai.agent_context import AgentContextMixin
    from tasks.ai.agent_utils import AgentUtilsMixin

    linked_service = MagicMock()
    monkeypatch.setattr(bindings, "resolve_service_override", linked_service)
    context = object.__new__(AgentContextMixin)
    context.config = {"attachment_ocr_llm_service": "ConfiguredOcr"}
    context._resolve_service_param = lambda *_args: "ConfiguredTitle"

    assert context._title_service_id("alice", "conv-1") == "ConfiguredTitle"
    assert context._attachment_ocr_service_id(
        "alice", "conv-1") == "ConfiguredOcr"
    linked_service.assert_not_called()

    title_service = SimpleNamespace(complete=lambda *_args, **_kwargs: None)
    utils = object.__new__(AgentUtilsMixin)
    utils._resolve_service_param = lambda *_args: "ConfiguredTitle"
    utils._resolve_llm_service = lambda *_args: (None, title_service)
    linked_llm = MagicMock()
    monkeypatch.setattr(bindings, "resolve_llm_override", linked_llm)
    assert utils._get_title_client("alice", "conv-1") == (
        title_service, "ConfiguredTitle")
    linked_llm.assert_not_called()


def test_agent_automation_roles_are_validated():
    from core.workflow_agent_resources import validate_agent_definition_data

    assert validate_agent_definition_data({
        "automation_roles": ["project_wiki"],
    })["automation_roles"] == ["project_wiki"]
    with pytest.raises(ValueError, match="unique list"):
        validate_agent_definition_data({
            "automation_roles": ["project_wiki", "project_wiki"],
        })


def test_agent_resource_actions_preserve_automation_roles(monkeypatch):
    from tasks.ai.actions._agentres_k4 import _handle_agentres_k4

    repository = MagicMock()
    monkeypatch.setattr(
        "core.resource_store.ResourceStore.instance",
        staticmethod(lambda: repository))
    monkeypatch.setattr(
        "core.admin_scope.effective_owner",
        lambda *_args, **_kwargs: ("alice", ""))
    data = {
        "prompt": "Maintain the wiki.",
        "automation_roles": ["project_wiki"],
    }

    flowfile = FlowFile()
    _handle_agentres_k4(None, "create_resource", {
        "resource_type": "agent", "name": "wiki-helper",
        "scope": "user", "data": data,
    }, None, "alice", flowfile)
    assert json.loads(flowfile.content)["ok"] is True
    assert repository.create.call_args.args[3]["automation_roles"] == [
        "project_wiki"]

    flowfile = FlowFile()
    _handle_agentres_k4(None, "update_resource", {
        "resource_type": "agent", "name": "wiki-helper",
        "scope": "user", "data": data,
    }, None, "alice", flowfile)
    assert json.loads(flowfile.content)["ok"] is True
    assert repository.update.call_args.args[3]["automation_roles"] == [
        "project_wiki"]


def test_linked_services_web_ui_is_loaded_and_localized():
    root = Path("tasks/io/chat_ui")
    serve = Path("tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")
    resources = (root / "resources.js").read_text(encoding="utf-8")
    renderer = (root / "resources_render.js").read_text(encoding="utf-8")
    module = (root / "resources_linked_services.js").read_text(encoding="utf-8")

    assert serve.index('"resources_linked_services.js"') < serve.index(
        '"resources_render.js"')
    assert "'_linked_services'" in resources
    assert "_renderLinkedServicesSection(data)" in renderer
    for action in (
        "linked_services_list", "linked_service_link",
        "linked_service_unlink",
    ):
        assert action in module
    for language in ("en", "fr", "es"):
        catalog = json.loads(
            (root / "i18n" / f"{language}.json").read_text(encoding="utf-8"))
        assert catalog["linkedServices"]
        assert catalog["linkedServicesPawFlowDefault"]
