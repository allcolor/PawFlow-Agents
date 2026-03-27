"""Session commands: /quit, /login, /clear, /new, /link, /connect, /disconnect."""

import os


def handle_session_commands(app, cmd, arg, text):
    """Handle session commands. Returns True if handled, False otherwise."""

    if cmd == "/connect":
        _connect_relay(app, arg.strip() if arg else "")
        return True

    if cmd == "/disconnect":
        _disconnect_relay(app, arg.strip() if arg else "")
        return True

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

    if cmd == "/link":
        parts = arg.split() if arg else []
        try:
            if not parts or parts[0] == "status":
                # /link or /link status — list linked accounts
                data = app.api.send_action("list_linked_accounts")
                links = data.get("links", {})
                if not links:
                    app.renderer.print_system("No linked accounts.")
                else:
                    app.renderer.print("  [bold]Linked accounts:[/bold]")
                    for provider, channel_id in links.items():
                        app.renderer.print(f"    {provider}: {channel_id}")
            elif parts[0] == "unlink":
                # /link unlink <provider>
                provider = parts[1] if len(parts) > 1 else ""
                if not provider:
                    app.renderer.print_error("Usage: /link unlink <provider>")
                    return True
                data = app.api.send_action("unlink_account", provider=provider)
                if data.get("unlinked"):
                    app.renderer.print_system(f"Unlinked {provider}")
                else:
                    app.renderer.print_error(data.get("error", f"Failed to unlink {provider}"))
            else:
                # /link <provider> <id> [bot_token]
                provider = parts[0]
                provider_id = parts[1] if len(parts) > 1 else ""
                bot_token = parts[2] if len(parts) > 2 else ""
                if not provider_id:
                    app.renderer.print_error("Usage: /link <provider> <id> [bot_token]")
                    return True
                kwargs = {"provider": provider, "provider_id": provider_id}
                if bot_token:
                    kwargs["bot_token"] = bot_token
                data = app.api.send_action("link_account", **kwargs)
                if data.get("linked"):
                    msg = f"Linked {provider} ({provider_id})"
                    if data.get("bot_username"):
                        msg += f" — bot: @{data['bot_username']}"
                    if data.get("bot_warning"):
                        msg += f" ⚠ {data['bot_warning']}"
                    app.renderer.print_system(msg)
                else:
                    app.renderer.print_error(data.get("error", "Link failed"))
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    return False


def _connect_relay(app, path: str):
    """Connect filesystem relay to a directory. Default: current workspace."""
    from pawflow_cli.relay import RelayThread

    directory = path or app.directory
    if not directory:
        app.renderer.print_error("No path specified and no workspace directory set.")
        return

    directory = os.path.abspath(os.path.expanduser(directory))
    if not os.path.isdir(directory):
        app.renderer.print_error(f"Directory not found: {directory}")
        return

    # Stop existing relay if running
    if app.relay and app.relay._registered:
        app.renderer.print_system(f"Stopping current relay ({app.relay.directory})...")
        app.relay.stop()

    app.renderer.print_system(f"Connecting relay to {directory}...")
    try:
        app.relay = RelayThread(
            app.server_url, app.session_token, app.username,
            directory, app.allow_exec,
            docker_image=getattr(app, 'docker_image', ''),
        )
        app.relay.start()
        app.renderer.print_system(f"Relay '{app.relay.relay_id}' connected on port {app.relay.port}")
    except Exception as e:
        app.renderer.print_error(f"Relay failed: {e}")


def _disconnect_relay(app, path: str):
    """Disconnect filesystem relay."""
    if not app.relay or not app.relay._registered:
        app.renderer.print_system("No relay connected.")
        return

    app.renderer.print_system(f"Disconnecting relay ({app.relay.directory})...")
    try:
        app.relay.stop()
        app.renderer.print_system("Relay disconnected.")
    except Exception as e:
        app.renderer.print_error(f"Error: {e}")
