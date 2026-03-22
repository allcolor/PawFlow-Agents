"""Secrets commands: /add-secret, /secrets, /add-variable, /variables, /schedules."""


def handle_secrets_commands(app, cmd, arg, text):
    """Handle secrets and variables commands. Returns True if handled, False otherwise."""

    if cmd in ("/add-secret", "/secret"):
        parts = arg.split(None, 1)
        if len(parts) < 2:
            app.renderer.print_error("Usage: /add-secret <name> <value>")
            return True
        try:
            app.api.send_action("add_secret", name=parts[0], value=parts[1])
            app.renderer.print_system(f"Secret '{parts[0]}' stored")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd in ("/secrets", "/list-secrets"):
        try:
            data = app.api.send_action("list_secrets")
            secrets = data.get("secrets", [])
            for s in secrets:
                app.renderer.print(f"  {s}")
            if not secrets:
                app.renderer.print_system("No secrets.")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd in ("/add-variable", "/add-var"):
        parts = arg.split(None, 1)
        if len(parts) < 2:
            app.renderer.print_error("Usage: /add-variable <name> <value>")
            return True
        try:
            app.api.send_action("add_variable", name=parts[0], value=parts[1])
            app.renderer.print_system(f"Variable '{parts[0]}' set")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd in ("/variables", "/vars", "/list-variables"):
        try:
            data = app.api.send_action("list_variables")
            variables = data.get("variables", {})
            for k, v in variables.items() if isinstance(variables, dict) else []:
                app.renderer.print(f"  {k} = {v}")
            if not variables:
                app.renderer.print_system("No variables.")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd in ("/schedules", "/tasks"):
        parts = arg.split(None, 1) if arg else ["list"]
        subcmd = parts[0].lower()
        try:
            if subcmd == "list" or not arg:
                data = app.api.send_action("list_schedules", conversation_id=app.conversation_id or "")
                scheds = data.get("schedules", [])
                for s in scheds:
                    import datetime
                    at = datetime.datetime.fromtimestamp(s.get("recheck_at", 0))
                    app.renderer.print(f"  {at.strftime('%Y-%m-%d %H:%M')} — {s.get('reason', 'recheck')}")
                if not scheds:
                    app.renderer.print_system("No scheduled tasks.")
            elif subcmd == "add":
                subarg = parts[1] if len(parts) > 1 else ""
                app.api.send_action("add_schedule", conversation_id=app.conversation_id or "", when=subarg)
                app.renderer.print_system("Schedule added")
            elif subcmd in ("del", "delete", "clear"):
                app.api.send_action("delete_schedule", conversation_id=app.conversation_id or "")
                app.renderer.print_system("Schedules cleared")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    return False
