"""Conversation commands: /conv, /resume, /history, /export, /delete, /rename, /delete-msg, /search."""

from pawflow_cli.api import SSEClient
from pawflow_cli.config import save_config


def handle_conversation_commands(app, cmd, arg, text):
    """Handle conversation commands. Returns True if handled, False otherwise."""

    if cmd in ("/conv", "/conversations"):
        if arg:
            # /conv <id> — switch to conversation
            _switch_conversation(app, arg.strip())
        else:
            # /conv — list conversations
            try:
                data = app.api.send_action("list_conversations")
                convs = data.get("conversations", [])
                app.renderer.print_conversation_list(convs)
                if convs:
                    app.renderer.print_system("Use /conv <id> or /resume <id> to switch.")
            except Exception as e:
                app.renderer.print_error(str(e))
        return True

    if cmd == "/resume":
        if not arg:
            # No arg — let server handle (tell agent to continue)
            return False
        parts = arg.split()
        cid_partial = parts[0]
        show_n = int(parts[1]) if len(parts) > 1 else 50
        full_cid = app._resolve_conversation_id(cid_partial)
        if not full_cid:
            app.renderer.print_error(f"No conversation matching '{cid_partial}'")
            return True
        try:
            data = app.api.send_action("load_history",
                                         conversation_id=full_cid,
                                         limit=show_n, offset=0)
            if data.get("error"):
                app.renderer.print_error(data["error"])
            else:
                app.conversation_id = full_cid
                app._last_history = data.get("messages", [])
                save_config({"last_conversation_id": full_cid})
                if app.sse:
                    app.sse.disconnect()
                app.sse = SSEClient(app.server_url, app.session_token)
                app.sse.connect(full_cid)
                total = data.get("message_count", 0)
                has_more = data.get("has_more", False)
                shown = len(app._last_history)
                more_hint = " — /history for older" if has_more else ""
                app.renderer.print_system(
                    f"Resumed {full_cid[:8]} (showing {shown} of {total}{more_hint})")
                app._display_history(app._last_history, show_n)
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/history":
        if not app.conversation_id:
            app.renderer.print_error("No active conversation")
            return True
        parts = arg.split() if arg else []
        n = int(parts[0]) if parts and parts[0].isdigit() else 50
        offset = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        try:
            data = app.api.send_action("load_history",
                                         conversation_id=app.conversation_id,
                                         limit=n, offset=offset)
            if data.get("error"):
                app.renderer.print_error(data["error"])
                return True
            messages = data.get("messages", [])
            total = data.get("message_count", 0)
            has_more = data.get("has_more", False)
            app._display_history(messages, len(messages))
            more_hint = f" — /history {n} {offset + len(messages)} for older" if has_more else ""
            app.renderer.print_system(f"Showing {len(messages)} of {total}{more_hint}")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/export":
        fmt = arg or "markdown"
        if not app.conversation_id:
            app.renderer.print_error("No active conversation")
            return True
        try:
            data = app.api.send_action("export", conversation_id=app.conversation_id,
                                         format=fmt)
            url = data.get("url", "")
            fname = data.get("filename", "")
            app.renderer.print_system(f"Exported: {url} ({fname})")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/delete":
        if not arg:
            app.renderer.print_error("Usage: /delete <conversation_id>")
            return True
        try:
            data = app.api.send_action("delete_conversation", conversation_id=arg)
            if data.get("deleted"):
                app.renderer.print_system(f"Deleted {arg[:8]}")
                if app.conversation_id == arg:
                    app.conversation_id = None
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/rename":
        if not arg:
            app.renderer.print_error("Usage: /rename <new title>")
            return True
        if not app.conversation_id:
            app.renderer.print_error("No active conversation")
            return True
        try:
            app.api.send_action("set_conv_title", conversation_id=app.conversation_id, title=arg)
            app.renderer.print_system(f"Conversation renamed to: {arg}")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/delete-msg":
        if not arg or not arg.strip().isdigit():
            app.renderer.print_error("Usage: /delete-msg <index>")
            return True
        if not app.conversation_id:
            app.renderer.print_error("No active conversation")
            return True
        try:
            app.api.send_action("delete_message", conversation_id=app.conversation_id, index=int(arg.strip()))
            app.renderer.print_system(f"Message {arg.strip()} deleted")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/search":
        if not arg:
            app.renderer.print_error("Usage: /search <query>")
            return True
        if not app.conversation_id:
            app.renderer.print_error("No active conversation")
            return True
        try:
            data = app.api.send_action("load_history", conversation_id=app.conversation_id, limit=500, offset=0)
            messages = data.get("messages", [])
            query = arg.lower()
            found = []
            for i, m in enumerate(messages):
                content = m.get("content", "")
                if isinstance(content, str) and query in content.lower():
                    preview = content[:100].replace("\n", " ")
                    found.append(f"  [{i}] {m.get('type', m.get('role', '?'))}: {preview}")
            if found:
                app.renderer.print_system(f"Found {len(found)} matches:")
                for f in found[:20]:
                    app.renderer.print(f)
                if len(found) > 20:
                    app.renderer.print_system(f"... and {len(found) - 20} more")
            else:
                app.renderer.print_system("No matches found")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    return False


def _switch_conversation(app, cid_partial: str):
    """Switch to a conversation by partial ID."""
    full_cid = app._resolve_conversation_id(cid_partial)
    if not full_cid:
        app.renderer.print_error(f"No conversation matching '{cid_partial}'")
        return
    try:
        data = app.api.send_action("load_history",
                                     conversation_id=full_cid,
                                     limit=20, offset=0)
        if data.get("error"):
            app.renderer.print_error(data["error"])
            return
        app.conversation_id = full_cid
        app._last_history = data.get("messages", [])
        save_config({"last_conversation_id": full_cid})
        if app.sse:
            app.sse.disconnect()
        app.sse = SSEClient(app.server_url, app.session_token)
        app.sse.connect(full_cid)
        total = data.get("message_count", 0)
        app.renderer.print_system(f"Switched to {full_cid[:8]} ({total} messages)")
        app._display_history(app._last_history, len(app._last_history))
    except Exception as e:
        app.renderer.print_error(str(e))
