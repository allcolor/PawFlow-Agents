"""Session commands: /quit, /login, /clear, /new."""

import os


def handle_session_commands(app, cmd, arg, text):
    """Handle session commands. Returns True if handled, False otherwise."""

    if cmd in ("/quit", "/exit"):
        app.renderer.print_system("Shutting down...")
        app._running = False
        app._cleanup()
        return True

    if cmd == "/clear":
        if app.renderer.console:
            app.renderer.console.clear()
        else:
            os.system("cls" if os.name == "nt" else "clear")
        return True

    if cmd == "/new":
        app.conversation_id = None
        app.selected_agent = ""
        if app.sse:
            app.sse.disconnect()
            app.sse = None
        app.renderer.print_system("New conversation started.")
        return True

    if cmd == "/login":
        from pawflow_cli.auth import authenticate
        auth = authenticate(app.server_url, force=True)
        app.session_token = auth["token"]
        app.username = auth["username"]
        app.api.session_token = app.session_token
        app.renderer.print_system(f"Re-authenticated as {app.username}")
        return True

    return False
