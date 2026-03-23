"""Agent commands: /agent, /msg, /btw, /stop, /interrupt, /setname."""


def handle_agent_commands(app, cmd, arg, text):
    """Handle agent commands. Returns True if handled, False otherwise."""

    if cmd == "/agent":
        if not arg or arg == "list":
            try:
                data = app.api.send_action("list_agents",
                                             conversation_id=app.conversation_id or "")
                agents = data.get("agents", [])
                for a in agents:
                    name = a.get("name", "?")
                    active = " (active)" if a.get("active") else ""
                    app.renderer.print(f"  {name}{active}")
            except Exception as e:
                app.renderer.print_error(str(e))
        else:
            parts = arg.split(None, 2)
            subcmd = parts[0].lower()

            if subcmd == "create":
                if len(parts) < 3:
                    app.renderer.print_error("Usage: /agent create <name> <prompt>")
                    return True
                try:
                    app.api.send_action("create_agent", conversation_id=app.conversation_id or "", name=parts[1], prompt=parts[2])
                    app.renderer.print_system(f"Agent '{parts[1]}' created")
                except Exception as e:
                    app.renderer.print_error(str(e))

            elif subcmd == "delete":
                if len(parts) < 2:
                    app.renderer.print_error("Usage: /agent delete <name>")
                    return True
                try:
                    app.api.send_action("delete_agent", name=parts[1])
                    app.renderer.print_system(f"Agent '{parts[1]}' deleted")
                except Exception as e:
                    app.renderer.print_error(str(e))

            elif subcmd == "setname":
                if len(parts) < 2:
                    app.renderer.print_error("Usage: /agent setname <real> [nickname]")
                    return True
                nick = parts[2] if len(parts) > 2 else ""
                try:
                    app.api.send_action("set_agent_nickname", conversation_id=app.conversation_id, real_name=parts[1], nickname=nick)
                    app.renderer.print_system(f"Nickname set: {parts[1]} → {nick or '(cleared)'}")
                except Exception as e:
                    app.renderer.print_error(str(e))

            elif subcmd in ("disable", "enable", "promote"):
                if len(parts) < 2:
                    app.renderer.print_error(f"Usage: /agent {subcmd} <name>")
                    return True
                try:
                    action = f"agent_{subcmd}"
                    app.api.send_action(action, agent_name=parts[1], conversation_id=app.conversation_id or "")
                    app.renderer.print_system(f"Agent '{parts[1]}' {subcmd}d")
                except Exception as e:
                    app.renderer.print_error(str(e))

            else:
                app.selected_agent = arg
                app.renderer.print_system(f"Switched to agent: {arg}")
        return True

    if cmd in ("/msg", "/message"):
        parts = arg.split(None, 1)
        if len(parts) < 2:
            app.renderer.print_error("Usage: /msg <agent|ALL> <text>")
            return True
        target, message = parts
        try:
            if target.upper() == "ALL":
                app.api.send_action("broadcast_agents", conversation_id=app.conversation_id, message=message)
                app.renderer.print_system("Broadcast sent")
            else:
                app.api.send_message(message, conversation_id=app.conversation_id, target_agent=target)
                app.renderer.print_system(f"Message sent to {target}")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/btw":
        parts = arg.split(None, 1)
        if len(parts) < 1 or not arg.strip():
            app.renderer.print_error("Usage: /btw [agent] <question> (defaults to selected agent)")
            return True
        if len(parts) < 2:
            # No agent specified — use selected agent
            target = app.selected_agent or ""
            question = arg
        else:
            target, question = parts
        if not target:
            app.renderer.print_error("No agent selected. Use /btw <agent> <question>")
            return True
        try:
            app.api.send_action("btw", conversation_id=app.conversation_id, message=question, agent_name=target)
            app.renderer.print_system(f"Side question sent to {target}")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd in ("/stop", "/interrupt"):
        if not arg:
            app.renderer.print_error("Usage: /stop <agent|ALL> [-f]")
            return True
        force = "-f" in arg
        target = arg.replace("-f", "").strip()
        try:
            action = "cancel" if force else "interrupt"
            app.api.send_action(action, conversation_id=app.conversation_id, target=target, agent_name=target)
            app.renderer.print_system(f"{'Cancelled' if force else 'Interrupted'} {target}")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/setname":
        parts = arg.split(None, 1) if arg else []
        if not parts:
            app.renderer.print_error("Usage: /setname <agent> [nickname]")
            return True
        real = parts[0]
        nick = parts[1] if len(parts) > 1 else ""
        try:
            app.api.send_action("set_agent_nickname", conversation_id=app.conversation_id, real_name=real, nickname=nick)
            app.renderer.print_system(f"Nickname set: {real} → {nick or '(cleared)'}")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    return False
