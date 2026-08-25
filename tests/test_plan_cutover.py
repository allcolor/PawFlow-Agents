import json
from pathlib import Path

from chat_ui_testing import rendered_chat_html
from core import FlowFile
from tasks.ai.actions.plans import LEGACY_PLAN_ACTIONS, _handle_plans
from pawflow_cli.commands.files import handle_files_commands


ROOT = Path(__file__).resolve().parents[1]
CHAT_UI = ROOT / "tasks" / "io" / "chat_ui"


def _legacy_action(action: str):
    flowfile = FlowFile()
    result = _handle_plans(
        None, action, {"conversation_id": "conv"}, None, "alice", flowfile)
    assert result == [flowfile]
    return (
        json.loads(flowfile.get_content().decode()),
        flowfile.get_attribute("http.response.status"),
    )


def test_legacy_http_actions_fail_closed_without_opening_plan_store(monkeypatch):
    monkeypatch.setenv("PAWFLOW_WORKFLOW_PROPOSALS_ENABLED", "1")
    monkeypatch.setattr(
        "core.plan_store.PlanStore.instance",
        lambda: (_ for _ in ()).throw(AssertionError("PlanStore was opened")),
    )

    assert LEGACY_PLAN_ACTIONS == {
        "get_plans", "get_plan", "create_plan_user", "approve_plan",
        "reject_plan", "cancel_plan", "delete_plan", "update_plan_step",
        "assign_plan", "cancel_step", "resume_step", "set_plan_verifier",
        "reset_plan", "verify_plan_step", "pause_plan_step",
    }
    for action in LEGACY_PLAN_ACTIONS:
        data, status = _legacy_action(action)
        assert status == "404"
        assert data == {"error": "Legacy plans are disabled"}


def test_unrelated_actions_still_fall_through_during_cutover(monkeypatch):
    monkeypatch.setenv("PAWFLOW_WORKFLOW_PROPOSALS_ENABLED", "1")
    flowfile = FlowFile()
    assert _handle_plans(
        None, "unrelated", {}, None, "alice", flowfile) is None


def test_web_cutover_is_exclusive_and_flag_off_keeps_legacy(monkeypatch):
    monkeypatch.setenv("PAWFLOW_WORKFLOW_PROPOSALS_ENABLED", "0")
    legacy = rendered_chat_html(inline_css=False)
    assert 'id="plansPanel"' in legacy
    assert 'id="plansMenuItem"' in legacy
    assert "/chat/js/plans_panel.js?" in legacy
    assert "window.PAWFLOW_WORKFLOW_PROPOSALS_ENABLED=false" in legacy

    monkeypatch.setenv("PAWFLOW_WORKFLOW_PROPOSALS_ENABLED", "1")
    canonical = rendered_chat_html(inline_css=False)
    assert 'id="plansPanel"' not in canonical
    assert 'id="plansMenuItem"' not in canonical
    assert "/chat/js/plans_panel.js?" not in canonical
    assert "window.PAWFLOW_WORKFLOW_PROPOSALS_ENABLED=true" in canonical


def test_web_runtime_does_not_wire_legacy_plan_routes_after_cutover():
    command = (CHAT_UI / "cmd_conversation.js").read_text(encoding="utf-8")
    sse = (CHAT_UI / "sse_handlers_b.js").read_text(encoding="utf-8")
    openspace = (CHAT_UI / "openspace_scene.js").read_text(encoding="utf-8")

    assert "PAWFLOW_WORKFLOW_PROPOSALS_ENABLED" in command
    assert "loadUiSurfaces(conversationId)" in command
    assert "propose_workflow" in command
    assert "PAWFLOW_WORKFLOW_PROPOSALS_ENABLED" in sse
    assert "if (!window.PAWFLOW_WORKFLOW_PROPOSALS_ENABLED)" in sse
    assert "PAWFLOW_WORKFLOW_PROPOSALS_ENABLED" in openspace


class _CliRenderer:
    def __init__(self):
        self.surfaces = []
        self.system = []
        self.errors = []

    def print_ui_surface(self, surface):
        self.surfaces.append(surface)

    def print_system(self, message):
        self.system.append(message)

    def print_error(self, message):
        self.errors.append(message)

    def print(self, _message):
        pass


class _CliApi:
    def __init__(self, proposals=True):
        self.proposals = proposals
        self.calls = []

    def send_action(self, action, **kwargs):
        self.calls.append((action, kwargs))
        if action == "workflow_proposal_list":
            if not self.proposals:
                raise Exception(
                    'API error 404: {"error":"Workflow proposals are disabled"}')
            return {"surfaces": [{
                "format": "pawflow.ui-surface.v1", "surface_id": "uis_1",
                "semantic": {"title": "Release"},
            }]}
        if action == "get_plans":
            return {"plans": []}
        raise AssertionError(action)


class _CliApp:
    conversation_id = "conv"

    def __init__(self, proposals=True):
        self.api = _CliApi(proposals)
        self.renderer = _CliRenderer()
        self.sent = []

    def _send_message(self, message):
        self.sent.append(message)


def test_pawcode_plan_command_negotiates_canonical_then_legacy():
    canonical = _CliApp(proposals=True)
    assert handle_files_commands(canonical, "/plan", "list", "/plan list")
    assert canonical.renderer.surfaces[0]["surface_id"] == "uis_1"
    assert [call[0] for call in canonical.api.calls] == [
        "workflow_proposal_list"]

    legacy = _CliApp(proposals=False)
    assert handle_files_commands(legacy, "/plan", "list", "/plan list")
    assert [call[0] for call in legacy.api.calls] == [
        "workflow_proposal_list", "get_plans"]


def test_pawcode_free_form_plan_uses_canonical_planner_protocol():
    app = _CliApp(proposals=True)
    assert handle_files_commands(
        app, "/plan", "ship release", "/plan ship release")
    assert len(app.sent) == 1
    assert "propose_workflow" in app.sent[0]
    assert "create_plan" not in app.sent[0]


def test_vscode_plan_entry_negotiates_workflow_proposals():
    panels = (ROOT / "pawflow-vscode/media/webview/panels.js").read_text(
        encoding="utf-8")
    commands = (ROOT / "pawflow-vscode/media/webview/commands.js").read_text(
        encoding="utf-8")
    host = (ROOT / "pawflow-vscode/src/webview/chatPanel.ts").read_text(
        encoding="utf-8")

    assert "command: 'workflow_proposal_list'" in panels
    assert "action === 'workflow_proposal_list'" in panels
    assert "vscodeUiSurfaceEvent({surface: surface})" in panels
    assert "Workflow proposals are disabled" in panels
    assert "command: 'get_plans'" in panels
    assert "propose_workflow" in host
    assert "Workflow proposals are disabled" in host
    assert "create_plan tool" in host
    assert "_sendPlan" in commands
