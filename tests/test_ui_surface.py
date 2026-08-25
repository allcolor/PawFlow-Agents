import pytest

from core.ui_surface import (
    FORMAT,
    UiSurfaceValidationError,
    available_ui_surface_actions,
    make_ui_surface,
    select_ui_surface_mode,
    validate_ui_surface,
)


def _surface(**overrides):
    values = {
        "user_id": "alice",
        "conversation_id": "conv",
        "producer_kind": "workflow_task",
        "producer_id": "review",
        "semantic": {
            "role": "review",
            "title": "Review workflow",
            "summary": "Planner revision 3",
            "fields": [{
                "id": "comment", "type": "string", "label": "Comment",
                "required": False,
            }],
            "actions": [{
                "id": "send",
                "label": "Send to planner",
                "kind": "primary",
                "input_schema": {
                    "type": "object",
                    "properties": {"comment": {"type": "string"}},
                },
                "dispatch": {
                    "action": "workflow_proposal_submit_to_planner",
                    "arguments": {"proposal_id": "wp_1", "state_revision": 3},
                },
            }, {
                "id": "edit",
                "label": "Edit workflow",
                "dispatch": {
                    "action": "open_client_uri",
                    "arguments": {"uri": "pawflow://workflow/draft_1"},
                },
                "requires": ["workflow.editor"],
                "handoff": {
                    "message": "Open this surface in a workflow editor.",
                    "uri": "https://pawflow.example/chat?surface=uis_1",
                },
            }],
        },
        "presentation": {
            "component": "example.review:workflow-card",
            "props": {"dense": True},
            "requires": ["ui.component"],
        },
        "fallback": {"mode": "semantic"},
    }
    values.update(overrides)
    return make_ui_surface(**values)


def test_surface_is_timestamped_uuid_addressed_and_detached():
    semantic = {
        "role": "notification", "title": "Done", "fields": [], "actions": [],
    }
    surface = make_ui_surface(
        user_id="alice", conversation_id="conv",
        producer_kind="task", producer_id="notify", semantic=semantic,
    )
    semantic["title"] = "mutated"
    assert surface["format"] == FORMAT
    assert surface["surface_id"].startswith("uis_")
    assert surface["semantic"]["title"] == "Done"
    assert surface["created_at"] and surface["updated_at"]


def test_client_selects_rich_semantic_or_handoff_from_capabilities():
    surface = _surface()
    assert select_ui_surface_mode(surface, {"ui.component"}) == "rich"
    assert select_ui_surface_mode(surface, set()) == "semantic"
    strict = _surface(
        fallback={"mode": "handoff", "message": "Use webchat."})
    assert select_ui_surface_mode(strict, set()) == "handoff"
    required = _surface(required_capabilities=["workflow.editor"])
    assert select_ui_surface_mode(required, {"ui.component"}) == "handoff"


def test_unavailable_action_remains_visible_with_handoff_metadata():
    actions = available_ui_surface_actions(_surface(), {"semantic.form"})
    assert actions[0]["available"] is True
    assert actions[1]["available"] is False
    assert actions[1]["missing_capabilities"] == ["workflow.editor"]
    assert actions[1]["handoff"]["uri"].startswith("https://")


@pytest.mark.parametrize("mutate, message", [
    (lambda s: s.update(format="wrong"), "format"),
    (lambda s: s.update(revision=0), "revision"),
    (lambda s: s["semantic"]["actions"].append(
        dict(s["semantic"]["actions"][0])), "duplicate semantic action"),
    (lambda s: s["presentation"].update(component="unqualified"), "component"),
    (lambda s: s["semantic"]["actions"][0]["input_schema"].update(
        oneOf=[]), "unsupported keywords"),
])
def test_invalid_contracts_are_rejected(mutate, message):
    surface = _surface()
    mutate(surface)
    with pytest.raises(UiSurfaceValidationError, match=message):
        validate_ui_surface(surface)


def test_dispatch_is_namespaced_for_pfp_server_handler():
    surface = _surface()
    action = surface["semantic"]["actions"][0]
    action["dispatch"]["extension"] = "example.review"
    validated = validate_ui_surface(surface)
    assert validated["semantic"]["actions"][0]["dispatch"]["extension"] == (
        "example.review")
