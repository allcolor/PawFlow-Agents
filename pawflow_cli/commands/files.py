"""File commands: /upload, /paste, /clear-files, /copy, /view, /files, /run, /diff, /multi, /watch, /call, /plan, /autoconv."""
import logging

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
                command=arg)
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
                command=f"git diff {arg}")
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
                    h = hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()
                    if last_hash and h != last_hash:
                        app.renderer.print_system(f"File changed: {filepath}")
                        import sys
                        sys.stdout.write("\a")
                        sys.stdout.flush()
                    last_hash = h
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
        if not app.conversation_id:
            app.renderer.print_error("No active conversation")
            return True
        parts = arg.split(None, 1) if arg else ["list"]
        subcmd = parts[0].lower()
        subarg = parts[1] if len(parts) > 1 else ""

        try:
            if subcmd == "list" or not arg:
                data = app.api.send_action("get_plans", conversation_id=app.conversation_id)
                plans_list = data.get("plans", [])
                if not plans_list:
                    app.renderer.print_system("No plans.")
                else:
                    for p in plans_list:
                        done = sum(1 for s in p.get("steps", []) if s.get("status") == "done")
                        total = len(p.get("steps", []))
                        app.renderer.print(f"  [{p['status']}] {p['id']} \u2014 {p['title']} ({done}/{total})")
            elif subcmd == "show":
                if not subarg:
                    app.renderer.print_error("Usage: /plan show <plan_id>")
                    return True
                data = app.api.send_action("get_plan", conversation_id=app.conversation_id, plan_id=subarg)
                plan = data.get("plan", {})
                if not plan:
                    app.renderer.print_error(f"Plan '{subarg}' not found")
                    return True
                done = sum(1 for s in plan.get("steps", []) if s.get("status") == "done")
                total = len(plan.get("steps", []))
                app.renderer.print(f"  **{plan['title']}** [{plan['status']}] ({done}/{total})")
                for s in plan.get("steps", []):
                    icon = {"pending": "\u25cb", "in_progress": "\u25d4", "done": "\u2713", "skipped": "\u2013", "error": "\u2717"}.get(s["status"], "\u25cb")
                    assigned = f" \u2192 {s['assigned_to']}" if s.get("assigned_to") else ""
                    note = f" \u2014 {s['note']}" if s.get("note") else ""
                    app.renderer.print(f"    {icon} {s['index']}. {s['description']}{assigned}{note}")
            elif subcmd == "create":
                # /plan create "title" "step1" "step2" ...
                import shlex
                try:
                    parts_q = shlex.split(subarg)
                except ValueError:
                    parts_q = subarg.split('"')
                    parts_q = [p.strip() for p in parts_q if p.strip()]
                if len(parts_q) < 2:
                    app.renderer.print_error('Usage: /plan create "title" "step1" "step2" ...')
                    return True
                title = parts_q[0]
                steps = parts_q[1:]
                data = app.api.send_action("create_plan_user", conversation_id=app.conversation_id, title=title, steps=steps)
                plan = data.get("plan", {})
                app.renderer.print_system(f"Plan '{plan.get('id', '?')}' created: {title} ({len(steps)} steps)")
            elif subcmd == "approve":
                if not subarg:
                    app.renderer.print_error("Usage: /plan approve <plan_id>")
                    return True
                data = app.api.send_action("approve_plan", conversation_id=app.conversation_id, plan_id=subarg)
                app.renderer.print_system(f"Plan '{subarg}' approved")
            elif subcmd == "reject":
                pid_parts = subarg.split(None, 1)
                pid = pid_parts[0] if pid_parts else ""
                reason = pid_parts[1] if len(pid_parts) > 1 else ""
                if not pid:
                    app.renderer.print_error("Usage: /plan reject <plan_id> [reason]")
                    return True
                data = app.api.send_action("reject_plan", conversation_id=app.conversation_id, plan_id=pid, reason=reason)
                app.renderer.print_system(f"Plan '{pid}' rejected")
            elif subcmd == "assign":
                # /plan assign <plan_id> <agent> [step_range]
                assign_parts = subarg.split()
                if len(assign_parts) < 2:
                    app.renderer.print_error("Usage: /plan assign <plan_id> <agent> [1-3]")
                    return True
                pid = assign_parts[0]
                agent = assign_parts[1]
                sr = assign_parts[2] if len(assign_parts) > 2 else ""
                data = app.api.send_action("assign_plan", conversation_id=app.conversation_id, plan_id=pid, agent=agent, step_range=sr)
                app.renderer.print_system(f"Plan '{pid}' assigned to {agent}" + (f" (steps {sr})" if sr else ""))
            elif subcmd == "skip":
                skip_parts = subarg.split()
                if len(skip_parts) < 2:
                    app.renderer.print_error("Usage: /plan skip <plan_id> <step>")
                    return True
                data = app.api.send_action("update_plan_step", conversation_id=app.conversation_id, plan_id=skip_parts[0], step=int(skip_parts[1]), status="skipped")
                app.renderer.print_system(f"Step {skip_parts[1]} skipped")
            elif subcmd == "cancel":
                if not subarg:
                    app.renderer.print_error("Usage: /plan cancel <plan_id>")
                    return True
                data = app.api.send_action("cancel_plan", conversation_id=app.conversation_id, plan_id=subarg)
                app.renderer.print_system(f"Plan '{subarg}' cancelled")
            elif subcmd == "delete":
                if not subarg:
                    app.renderer.print_error("Usage: /plan delete <plan_id>")
                    return True
                data = app.api.send_action("delete_plan", conversation_id=app.conversation_id, plan_id=subarg)
                app.renderer.print_system(f"Plan '{subarg}' deleted")
            else:
                app.renderer.print_error("Usage: /plan [list|show|create|approve|reject|assign|skip|cancel|delete]")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/autoconv":
        if not app.conversation_id:
            app.renderer.print_error("No active conversation")
            return True
        parts = arg.split() if arg else []
        if not parts or parts[0].lower() not in ("on", "off", "status", "now"):
            app.renderer.print_error("Usage: /autoconv <on|off|status|now> <agent|ALL> [freq]")
            return True
        sub = parts[0].lower()
        agent = parts[1] if len(parts) > 1 else ""
        if not agent:
            app.renderer.print_error(f"Usage: /autoconv {sub} <agent|ALL> [freq]")
            return True
        try:
            kwargs = {"conversation_id": app.conversation_id, "sub": sub, "agent": agent}
            if sub == "on":
                kwargs["frequency"] = parts[2] if len(parts) > 2 else "6/1m"
            data = app.api.send_action("random_thought", **kwargs)
            if data.get("error"):
                app.renderer.print_error(data["error"])
            elif sub == "on":
                app.renderer.print_system(f"Auto-conversation enabled for {agent} ({data.get('frequency', '?')})")
            elif sub == "off":
                app.renderer.print_system(f"Auto-conversation disabled for {agent}")
            elif sub == "now":
                app.renderer.print_system(f"Auto-conversation triggered for {agent}")
            else:
                # status
                agents = data.get("agents", [])
                if isinstance(agents, list):
                    for a in agents:
                        status = "on" if a.get("enabled") else "off"
                        app.renderer.print(f"  {a.get('agent', '?')}: {status} ({a.get('frequency', 'N/A')})")
                else:
                    app.renderer.print_system(str(data))
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    return False
