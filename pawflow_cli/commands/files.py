"""File commands: /upload, /paste, /clear-files, /copy, /view, /files, /run, /diff, /multi, /watch, /call, /plan."""

import os
import threading


def handle_files_commands(app, cmd, arg, text):
    """Handle file and dev tool commands. Returns True if handled, False otherwise."""

    if cmd == "/files":
        try:
            data = app.api.send_action("list_conv_files", conversation_id=app.conversation_id or "")
            files = data.get("files", [])
            for f in files:
                app.renderer.print(f"  {f.get('file_id', '?')[:8]}  {f.get('filename', '?')}  ({f.get('size', 0):,} bytes)")
            if not files:
                app.renderer.print_system("No files in this conversation.")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/upload":
        if not arg:
            app.renderer.print_error("Usage: /upload <file_path>")
            return True
        app._upload_file(arg.strip().strip('"').strip("'"))
        return True

    if cmd == "/paste":
        app._paste_clipboard_image()
        return True

    if cmd in ("/clear-files", "/detach"):
        app._pending_attachments.clear()
        app.renderer.print_system("Pending attachments cleared.")
        return True

    if cmd == "/copy":
        app._copy_last_message(arg)
        return True

    if cmd == "/view":
        if not arg:
            app.renderer.print_error("Usage: /view <file_path_or_url>")
            return True
        target = arg.strip().strip('"').strip("'")
        import webbrowser
        # If it's a URL, open directly
        if target.startswith("http://") or target.startswith("https://") or target.startswith("/files/"):
            if target.startswith("/files/"):
                target = f"{app.server_url}{target}"
            webbrowser.open(target)
            app.renderer.print_system(f"Opened: {target}")
        # If it's a local file, open it
        elif os.path.isfile(target):
            webbrowser.open(f"file:///{os.path.abspath(target)}")
            app.renderer.print_system(f"Opened: {target}")
        # If it's a path on the relay filesystem
        else:
            # Try to get the file via the agent API and open from FileStore
            try:
                data = app.api.send_action("fs_copy_to_store",
                                             service=app.relay.relay_id if app.relay else "",
                                             path=target)
                if data.get("url"):
                    url = f"{app.server_url}{data['url']}"
                    webbrowser.open(url)
                    app.renderer.print_system(f"Opened: {url}")
                elif data.get("error"):
                    app.renderer.print_error(data["error"])
            except Exception as e:
                app.renderer.print_error(f"Cannot open: {e}")
        return True

    if cmd == "/run":
        if not arg:
            app.renderer.print_error("Usage: /run <command>")
            return True
        try:
            result = app.api.send_action("fs_exec",
                service=app.relay.relay_id if app.relay else "",
                command=arg, timeout=30)
            if result.get("error"):
                app.renderer.print_error(result["error"])
            else:
                stdout = result.get("stdout", "")
                stderr = result.get("stderr", "")
                rc = result.get("returncode", -1)
                app.renderer.print_exec_output(arg, rc, stdout, stderr)
        except Exception as e:
            app.renderer.print_error(f"Exec failed: {e}")
        return True

    if cmd == "/diff":
        if not arg:
            arg = "."
        try:
            data = app.api.send_action("fs_exec",
                service=app.relay.relay_id if app.relay else "",
                command=f"git diff {arg}", timeout=15)
            output = data.get("stdout", "")
            if not output:
                app.renderer.print_system("No changes.")
            else:
                app.renderer.print_tool_result("diff", output)
        except Exception as e:
            app.renderer.print_error(f"Diff failed: {e}")
        return True

    if cmd == "/multi":
        app.renderer.print_system("Multiline mode: type your message. Press Alt+Enter or Escape then Enter to send.")
        try:
            try:
                from prompt_toolkit import PromptSession as _PS
                HAS_PT = True
            except ImportError:
                HAS_PT = False

            if HAS_PT:
                from prompt_toolkit import prompt as pt_prompt
                text_input = pt_prompt("... ", multiline=True)
            else:
                lines = []
                app.renderer.print_system("Type lines, empty line to send:")
                while True:
                    line = input("... ")
                    if line == "":
                        break
                    lines.append(line)
                text_input = "\n".join(lines)
            if text_input.strip():
                app._send_message(text_input.strip())
        except (EOFError, KeyboardInterrupt):
            app.renderer.print_system("Cancelled.")
        return True

    if cmd == "/watch":
        if not arg:
            app.renderer.print_error("Usage: /watch <file_path> | /watch stop")
            return True
        if arg.strip() == "stop":
            if hasattr(app, '_watch_thread') and app._watch_thread:
                app._watch_stop.set()
                app._watch_thread = None
                app.renderer.print_system("File watch stopped.")
            else:
                app.renderer.print_system("No active watch.")
            return True
        # Start watching in background
        app._watch_stop = threading.Event()
        filepath = arg.strip()
        def _watch():
            import hashlib
            last_hash = ""
            while not app._watch_stop.is_set():
                try:
                    data = app.api.send_action("fs_read_file",
                        service=app.relay.relay_id if app.relay else "",
                        path=filepath)
                    content = data.get("content", "")
                    h = hashlib.md5(content.encode()).hexdigest()
                    if last_hash and h != last_hash:
                        app.renderer.print_system(f"File changed: {filepath}")
                        import sys
                        sys.stdout.write("\a")
                        sys.stdout.flush()
                    last_hash = h
                except Exception:
                    pass
                app._watch_stop.wait(3)
        app._watch_thread = threading.Thread(target=_watch, daemon=True)
        app._watch_thread.start()
        app.renderer.print_system(f"Watching {filepath} (poll every 3s). /watch stop to cancel.")
        return True

    if cmd == "/call":
        if not arg:
            app.renderer.print_error("Usage: /call <tool_name> {json_args}")
            return True
        parts = arg.split(None, 1)
        tool_name = parts[0]
        try:
            import json as _json
            args_dict = _json.loads(parts[1]) if len(parts) > 1 else {}
            data = app.api.send_action("call_tool", tool_name=tool_name, arguments=args_dict, conversation_id=app.conversation_id or "")
            result = data.get("result", str(data))
            app.renderer.print_system(f"Tool result:\n{result[:1000]}")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/plan":
        if not arg:
            app.renderer.print_error("Usage: /plan <description of what to do>")
            return True
        # Send as a message with plan-mode instruction prefix
        plan_msg = (
            "[PLAN MODE — Read-only strategy. Analyze the request, identify affected files, "
            "outline the approach step by step. Do NOT make any changes yet. "
            "Just present the plan and wait for approval.]\n\n" + arg
        )
        app._send_message(plan_msg)
        return True

    return False
