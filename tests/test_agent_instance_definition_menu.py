"""The agent menu's View/Edit definition opens the instance's definition.

Incident 2026-09-28 (Ultima conversation): the instance GameDev2 runs the
definition 'claude'. Edit definition asked get_resource_detail for the
agent 'GameDev2', which is no definition, and failed with "agent 'GameDev2'
not found". It only worked in conversations whose instances are named
after their definitions.
"""

import json
from pathlib import Path

from core import FlowFile
from tasks.ai.actions.agent_resource import _handle_agent_resource


def _list_agents(monkeypatch, conv_agents, definitions):
    class Task:
        def _ensure_active_agent(self, conv_id, active, user_id):
            return active

    class Store:
        def resolve_owner(self, cid):
            return "alice"

        def get_extra(self, conv_id, key):
            return None

    class ResourceStore:
        def list_all(self, rtype, user_id, conversation_id=""):
            return list(definitions) if rtype == "agent" else []

    from core.resource_store import ResourceStore as RealResourceStore
    import core.conv_agent_config as conv_agent_config
    monkeypatch.setattr(RealResourceStore, "instance",
                        staticmethod(lambda: ResourceStore()))
    monkeypatch.setattr(conv_agent_config, "get_all_agent_configs",
                        lambda conv_id: conv_agents)
    ff = FlowFile(content=b"")
    _handle_agent_resource(Task(), "list_resources",
                           {"conversation_id": "conv1"}, Store(), "alice", ff)
    body = json.loads(ff.get_content().decode("utf-8"))
    return {a["name"]: a for a in body["agents"]}


def test_instance_lists_the_definition_it_runs(monkeypatch):
    agents = _list_agents(
        monkeypatch,
        {"GameDev2": {"definition": "claude"},
         "claude": {"definition": "claude"}},
        [{"name": "claude", "_scope": "user"}])
    assert agents["GameDev2"]["definition"] == "claude"
    assert agents["GameDev2"]["scope"] == "user"
    assert agents["claude"]["definition"] == "claude"


def test_unmapped_instance_falls_back_to_its_own_name(monkeypatch):
    agents = _list_agents(monkeypatch, {"ghost": {"definition": "gone"}}, [])
    assert agents["ghost"]["definition"] == "ghost"


def _menu_js():
    js = Path("tasks/io/chat_ui/resources_menus.js").read_text(
        encoding="utf-8")
    return js[js.index("function showAgentMenu"):
              js.index("function _showSkillAssignDialog")]


def test_definition_entries_open_the_definition_not_the_instance():
    menu = _menu_js()
    assert "const defName = definition || name;" in menu
    assert "showResourceEditor('agent', defName, true)" in menu
    assert "showResourceEditor('agent', defName)" in menu
    assert "showResourceEditor('agent', name" not in menu
    # Instance-level entries keep the instance name.
    assert "_showAgentConvConfigDialog(name)" in menu


def test_agent_row_passes_the_definition_to_the_menu():
    js = Path("tasks/io/chat_ui/resources_render.js").read_text(
        encoding="utf-8")
    assert "_pfpJsArg(aRuntime) + ',' + _pfpJsArg(a.definition || aName)" in js
