from pathlib import Path

ROOT = Path(__file__).parents[1]


def _text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_vscode_uses_generic_surface_event_and_server_dispatch():
    runtime = _text("pawflow-vscode/media/webview/ui_surfaces.js")
    handlers = _text("pawflow-vscode/media/webview/chat_handlers.js")
    host = _text("pawflow-vscode/src/webview/chatPanel.ts")
    assert "pawflow.ui-surface.v1" in runtime
    assert "ui_surface_upserted" in handlers
    assert "uiSurfaceAction" in runtime and "uiSurfaceAction" in host
    assert "ui_surface_list" in host


def test_vscode_renders_and_restores_typed_interactions():
    runtime = _text("pawflow-vscode/media/webview/interactions.js")
    handlers = _text("pawflow-vscode/media/webview/chat_handlers.js")
    host = _text("pawflow-vscode/src/webview/chatPanel.ts")
    assert "interaction_request" in handlers
    assert "interaction_answered" in handlers
    assert "interactionResponse" in runtime and "interactionResponse" in host
    assert "respond_interaction" in host
    assert "list_interactions" in host
    for kind in ("choice", "multi", "multiline", "integer", "decimal",
                 "date", "datetime", "file", "form"):
        assert kind in runtime
    assert "workflow_proposal" not in runtime
    assert "workflow_proposal" not in handlers


def test_web_loader_and_terminal_dispatch_are_producer_agnostic():
    loader = _text("tasks/io/chat_ui/ui_surface_loader.js")
    terminal = _text("pawflow_cli/event_handler.py")
    assert "ui_surface_list" in loader
    assert "ui_surface_upserted" in terminal
    assert "ui_surface_list" in _text("pawflow_cli/_app_events.py")
    assert "workflow_proposal" not in loader
