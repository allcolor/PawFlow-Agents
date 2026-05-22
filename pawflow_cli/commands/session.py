"""Session commands: /quit, /login, /clear, /new, /link, /connect, /disconnect."""

import os
import shlex
import subprocess  # nosec B404


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
            subprocess.run(["cmd", "/c", "cls"] if os.name == "nt" else ["clear"], check=False)  # nosec B603
        return True

    if cmd == "/new":
        _create_new_conversation(app, arg or "")
        return True

    if cmd == "/login":
        from pawflow_cli.auth import authenticate
        auth = authenticate(app.server_url, force=True,
                           gateway_cookie=getattr(app, 'gateway_cookie', ''))
        app.session_token = auth["token"]
        app.username = auth["username"]
        app.api.session_token = app.session_token
        app.renderer.print_system(f"Re-authenticated as {app.username}")
        return True

    if cmd == "/bg":
        _list_bg_tools(app)
        return True

    if cmd == "/cancel":
        _cancel_bg_tool(app, arg.strip() if arg else "")
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


def _parse_new_args(arg: str):
    """Parse /new [agent] [--llm svc] [--relay rid] [--title text]."""
    try:
        parts = shlex.split(arg or "")
    except ValueError:
        parts = (arg or "").split()
    opts = {"agent": "", "llm": "", "relays": [], "title": ""}
    title_parts = []
    i = 0
    while i < len(parts):
        p = parts[i]
        if p in ("--agent", "-a") and i + 1 < len(parts):
            opts["agent"] = parts[i + 1].lstrip("@")
            i += 2
            continue
        if p in ("--llm", "--service") and i + 1 < len(parts):
            opts["llm"] = parts[i + 1]
            i += 2
            continue
        if p == "--relay" and i + 1 < len(parts):
            opts["relays"].append(parts[i + 1])
            i += 2
            continue
        if p == "--title" and i + 1 < len(parts):
            opts["title"] = " ".join(parts[i + 1:]).strip()
            break
        if not opts["agent"] and not p.startswith("-"):
            opts["agent"] = p.lstrip("@")
        else:
            title_parts.append(p)
        i += 1
    if not opts["title"] and title_parts:
        opts["title"] = " ".join(title_parts).strip()
    return opts


def _create_new_conversation(app, arg: str):
    opts = _parse_new_args(arg)
    try:
        from pawflow_cli.conversation_bootstrap import create_conversation
        cid, agent_name, llm_service, _payload = create_conversation(
            app.api,
            requested_agent=opts["agent"],
            llm_service=opts["llm"],
            relays=opts["relays"],
            title=opts["title"],
        )

        app.conversation_id = cid
        app.selected_agent = agent_name
        if app.sse:
            app.sse.disconnect()
        app.sse = None
        from pawflow_cli.config import save_config
        save_config({"last_conversation_id": cid})
        app._ensure_sse()
        title = f" — {opts['title']}" if opts["title"] else ""
        relay_info = f"; relay {opts['relays'][0]}" if opts["relays"] else ""
        app.renderer.print_system(
            f"Created {cid[:8]} with agent {agent_name} via {llm_service}{relay_info}{title}")
    except Exception as e:
        app.renderer.print_error(str(e))


def _connect_relay(app, path: str):
    """Relay lifecycle moved to webchat resources or pawflow-relay."""
    app.renderer.print_error(
        "PawCode no longer manages relays. Use webchat resources for server "
        "relays, or run `pawflow-relay workspace add ...` and `pawflow-relay start ...`."
    )


def _list_bg_tools(app):
    """List background tasks."""
    if not app.api or not app.conversation_id:
        app.renderer.print_error("No active conversation.")
        return
    try:
        data = app.api.send_action("list_bg_tools",
                                    conversation_id=app.conversation_id)
        tasks = data.get("tasks", [])
        if not tasks:
            app.renderer.print_system("No background tasks.")
            return
        for t in tasks:
            tc_id = t.get("tc_id", "?")[:8]
            tool = t.get("tool", "?")
            status = t.get("status", "?")
            app.renderer.print_system(f"  {tc_id}  {tool}  [{status}]")
    except Exception as e:
        app.renderer.print_error(f"Failed: {e}")


def _cancel_bg_tool(app, tc_id: str):
    """Cancel a background task."""
    if not app.api or not app.conversation_id:
        app.renderer.print_error("No active conversation.")
        return
    if not tc_id:
        app.renderer.print_error("Usage: /cancel <tc_id>")
        return
    try:
        data = app.api.send_action("cancel_bg_tool",
                                    conversation_id=app.conversation_id,
                                    tc_id=tc_id)
        if data.get("ok"):
            app.renderer.print_system(f"Cancelled {tc_id}")
        else:
            app.renderer.print_error(data.get("error", "Cancel failed"))
    except Exception as e:
        app.renderer.print_error(f"Failed: {e}")


def _disconnect_relay(app, path: str):
    """Relay lifecycle moved to webchat resources or pawflow-relay."""
    app.renderer.print_system("PawCode has no managed relay to disconnect.")
