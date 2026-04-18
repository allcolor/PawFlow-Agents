"""Resource commands: /resources, /tools, /cost, /skill, /task, /service, /flow, /activate, /deactivate, /prompt, /vidservice, /imgservice, /share, /install, /uninstall."""


def handle_resources_commands(app, cmd, arg, text):
    """Handle resource commands. Returns True if handled, False otherwise."""

    if cmd == "/resources":
        try:
            data = app.api.send_action("list_resources",
                                         conversation_id=app.conversation_id or "")
            for rtype, items in data.items():
                if isinstance(items, list) and items:
                    app.renderer.print(f"\n  [bold]{rtype}[/bold]")
                    for item in items:
                        name = item.get("name", "?")
                        active = " ✓" if item.get("active") else ""
                        app.renderer.print(f"    {name}{active}")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/tools":
        try:
            data = app.api.send_action("list_tools",
                                         conversation_id=app.conversation_id or "")
            tools = data.get("tools", [])
            for t in tools:
                app.renderer.print(f"  {t.get('name', '?')}: {t.get('description', '')[:80]}")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/cost":
        try:
            data = app.api.send_action("cost", agent=arg or "ALL")
            app.renderer.print_markdown(f"```\n{data}\n```" if isinstance(data, str) else str(data))
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/skill":
        parts = arg.split(None, 2) if arg else ["list"]
        subcmd = parts[0].lower()
        try:
            if subcmd == "list":
                data = app.api.send_action("list_resources", conversation_id=app.conversation_id or "")
                skills = data.get("skill", data.get("skills", []))
                if isinstance(skills, list):
                    for s in skills:
                        name = s.get("name", "?")
                        active = " ✓" if s.get("active") else ""
                        app.renderer.print(f"  {name}{active}: {s.get('description', '')[:60]}")
                else:
                    app.renderer.print_system("No skills.")
            elif subcmd == "add":
                if len(parts) < 3:
                    app.renderer.print_error("Usage: /skill add <name> <prompt>")
                    return True
                app.api.send_action("create_resource", resource_type="skill", name=parts[1], prompt=parts[2], conversation_id=app.conversation_id or "")
                app.renderer.print_system(f"Skill '{parts[1]}' created")
            elif subcmd in ("del", "delete"):
                if len(parts) < 2:
                    app.renderer.print_error("Usage: /skill del <name>")
                    return True
                app.api.send_action("delete_resource", resource_type="skill", name=parts[1])
                app.renderer.print_system(f"Skill '{parts[1]}' deleted")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/task":
        parts = arg.split(None, 2) if arg else ["list"]
        subcmd = parts[0].lower()
        try:
            if subcmd == "list":
                data = app.api.send_action("task_status", conversation_id=app.conversation_id or "")
                tasks = data.get("tasks", [])
                for t in tasks:
                    status = t.get("status", "?")
                    app.renderer.print(f"  [{status}] {t.get('name', '?')}: {t.get('description', '')[:60]}")
                if not tasks:
                    app.renderer.print_system("No tasks.")
            elif subcmd == "create":
                if len(parts) < 3:
                    app.renderer.print_error("Usage: /task create <name> <prompt>")
                    return True
                app.api.send_action("create_task_def", name=parts[1], prompt=parts[2], conversation_id=app.conversation_id or "")
                app.renderer.print_system(f"Task '{parts[1]}' created")
            elif subcmd == "assign":
                assign_parts = (parts[1] if len(parts) > 1 else "").split(None, 1)
                if len(assign_parts) < 2:
                    app.renderer.print_error("Usage: /task assign <agent> <task> [--context last:10]")
                    return True
                # Parse optional --context
                task_arg = assign_parts[1]
                context = "isolated"
                if "--context" in task_arg:
                    task_parts = task_arg.split("--context", 1)
                    task_arg = task_parts[0].strip()
                    context = task_parts[1].strip()
                app.api.send_action("assign_task", agent_name=assign_parts[0],
                                     task_name=task_arg, context=context,
                                     conversation_id=app.conversation_id or "")
                app.renderer.print_system(f"Task assigned to {assign_parts[0]} (context: {context})")
            elif subcmd in ("del", "delete"):
                if len(parts) < 2:
                    app.renderer.print_error("Usage: /task del <name>")
                    return True
                app.api.send_action("delete_task_def", name=parts[1])
                app.renderer.print_system(f"Task '{parts[1]}' deleted")
            elif subcmd == "log":
                task_name = parts[1] if len(parts) > 1 else ""
                data = app.api.send_action("task_log", name=task_name,
                                             conversation_id=app.conversation_id or "")
                if task_name:
                    log = data.get("log", [])
                    if not log:
                        app.renderer.print_system(f"No log for task '{task_name}'")
                    else:
                        import datetime
                        for entry in log[-30:]:  # last 30 entries
                            ts = datetime.datetime.fromtimestamp(entry.get("ts", 0))
                            t = entry.get("type", "?")
                            agent = entry.get("agent", "")
                            detail = entry.get("detail", "")
                            badge = f"[{agent}] " if agent else ""
                            app.renderer.print(f"  {ts.strftime('%H:%M:%S')} {badge}{t}: {detail[:100]}")
                else:
                    logs = data.get("logs", {})
                    for tname, entries in logs.items():
                        app.renderer.print(f"  {tname}: {len(entries)} entries")
            elif subcmd in ("pause", "resume", "cancel"):
                if len(parts) < 2:
                    app.renderer.print_error(f"Usage: /task {subcmd} <task_id|agent>")
                    return True
                app.api.send_action(f"{subcmd}_task", task_id=parts[1], conversation_id=app.conversation_id or "")
                app.renderer.print_system(f"Task {subcmd}d: {parts[1]}")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/service":
        parts = arg.split(None, 2) if arg else ["list"]
        subcmd = parts[0].lower()
        try:
            if subcmd == "list":
                data = app.api.send_action("list_services")
                services = data.get("services", [])
                for s in services:
                    status = "+" if s.get("enabled") or s.get("connected") else "-"
                    app.renderer.print(f"  [{status}] {s.get('service_id', '?')} ({s.get('service_type', '?')}): {s.get('description', '')[:50]}")
                if not services:
                    app.renderer.print_system("No services.")
            elif subcmd == "install":
                if len(parts) < 3:
                    app.renderer.print_error("Usage: /service install <type> <name> [key=val,...]")
                    return True
                rest = parts[2].split(None, 1)
                name = rest[0]
                config_str = rest[1] if len(rest) > 1 else ""
                app.api.send_action("service_install", service_type=parts[1], service_name=name, config_str=config_str)
                app.renderer.print_system(f"Service '{name}' installed")
            elif subcmd == "uninstall":
                if len(parts) < 2:
                    app.renderer.print_error("Usage: /service uninstall <name>")
                    return True
                app.api.send_action("service_uninstall", service_id=parts[1])
                app.renderer.print_system(f"Service '{parts[1]}' removed")
            elif subcmd == "profiles":
                data = app.api.get("/v1/services/llm-profiles")
                profiles = data.get("profiles", [])
                for p in profiles:
                    req_key = " [api_key required]" if p.get("requires_api_key") else ""
                    models = ", ".join(p.get("models", [])[:4])
                    if models:
                        models = f" — {models}"
                    app.renderer.print(f"  {p['name']} ({p.get('provider', '?')}){req_key}{models}")
                    if p.get("description"):
                        app.renderer.print(f"    {p['description']}")
                if not profiles:
                    app.renderer.print_system("No profiles.")
            elif subcmd == "add":
                if len(parts) < 2:
                    app.renderer.print_error("Usage: /service add <profile> [name] [key=val ...]")
                    return True
                profile = parts[1]
                rest = parts[2].split(None, 1) if len(parts) > 2 else []
                name = rest[0] if rest else profile
                config_str = rest[1] if len(rest) > 1 else ""
                app.api.send_action("service_install", profile=profile, service_name=name, config_str=config_str)
                app.renderer.print_system(f"Service '{name}' installed from profile '{profile}'")
            elif subcmd in ("enable", "disable"):
                if len(parts) < 2:
                    app.renderer.print_error(f"Usage: /service {subcmd} <name>")
                    return True
                app.api.send_action(f"service_{subcmd}", service_id=parts[1])
                app.renderer.print_system(f"Service '{parts[1]}' {subcmd}d")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/flow":
        parts = arg.split(None, 2) if arg else ["list"]
        subcmd = parts[0].lower()
        try:
            if subcmd == "list":
                data = app.api.send_action("list_conv_flows")
                flows = data.get("flows", [])
                for f in flows:
                    status = "\u25b6" if f.get("status") == "running" else "\u23f9"
                    app.renderer.print(f"  {status} {f.get('id', '?')} — {f.get('name', '?')} [{f.get('status', '?')}]")
                if not flows:
                    app.renderer.print_system("No deployed flows.")
            elif subcmd == "templates":
                data = app.api.send_action("list_available_flows")
                templates = data.get("templates", [])
                for t in templates:
                    ver = f" v{t['version']}" if t.get("version") else ""
                    app.renderer.print(f"  {t['id']}{ver} — {t['name']} ({t['tasks_count']} tasks, {t['services_count']} services)")
                    if t.get("description"):
                        app.renderer.print(f"    {t['description'][:80]}")
                if not templates:
                    app.renderer.print_system("No templates in flows/")
            elif subcmd == "deploy":
                if len(parts) < 2:
                    app.renderer.print_error("Usage: /flow deploy <template_id> [user|conversation]")
                    return True
                template_id = parts[1]
                scope = parts[2] if len(parts) > 2 else "user"
                data = app.api.send_action("deploy_flow",
                    template_id=template_id, scope=scope,
                    conversation_id=app.conversation_id or "")
                if data.get("error"):
                    app.renderer.print_error(data["error"])
                else:
                    app.renderer.print_system(f"Deployed: {data.get('instance_id', '?')} ({scope})")
            elif subcmd == "start":
                if len(parts) < 2:
                    app.renderer.print_error("Usage: /flow start <instance_id> [key=val ...]")
                    return True
                iid = parts[1]
                # Parse optional param overrides: /flow start myflow key1=val1 key2=val2
                overrides = {}
                if len(parts) > 2:
                    for kv in parts[2].split():
                        if "=" in kv:
                            k, v = kv.split("=", 1)
                            overrides[k.strip()] = v.strip()
                if overrides:
                    upd = app.api.send_action("update_flow_params", instance_id=iid, parameters=overrides)
                    if upd.get("error"):
                        app.renderer.print_error(f"Param update: {upd['error']}")
                        return True
                    app.renderer.print_system(f"Updated {len(overrides)} param(s)")
                data = app.api.send_action("start_flow", instance_id=iid)
                if data.get("error"):
                    app.renderer.print_error(data["error"])
                else:
                    app.renderer.print_system(f"Flow '{iid}' started")
            elif subcmd == "params":
                if len(parts) < 2:
                    app.renderer.print_error("Usage: /flow params <instance_id>")
                    return True
                data = app.api.send_action("get_flow_instance", instance_id=parts[1])
                if data.get("error"):
                    app.renderer.print_error(data["error"])
                else:
                    app.renderer.print(f"  Flow: {data.get('flow_name', '?')} [{data.get('status', '?')}]")
                    params = {**data.get("template_parameters", {}), **data.get("parameters", {})}
                    for k, v in params.items():
                        app.renderer.print(f"    {k} = {v}")
            elif subcmd == "stop":
                if len(parts) < 2:
                    app.renderer.print_error("Usage: /flow stop <instance_id>")
                    return True
                data = app.api.send_action("stop_flow", instance_id=parts[1])
                if data.get("error"):
                    app.renderer.print_error(data["error"])
                else:
                    app.renderer.print_system(f"Flow '{parts[1]}' stopped")
            elif subcmd == "undeploy":
                if len(parts) < 2:
                    app.renderer.print_error("Usage: /flow undeploy <instance_id>")
                    return True
                data = app.api.send_action("undeploy_flow", instance_id=parts[1])
                if data.get("error"):
                    app.renderer.print_error(data["error"])
                else:
                    app.renderer.print_system(f"Flow '{parts[1]}' undeployed")
            elif subcmd == "promote":
                if len(parts) < 2:
                    app.renderer.print_error("Usage: /flow promote <instance_id>")
                    return True
                data = app.api.send_action("promote_flow",
                    instance_id=parts[1], target_scope="user")
                if data.get("error"):
                    app.renderer.print_error(data["error"])
                else:
                    app.renderer.print_system(f"Flow '{parts[1]}' promoted to user scope")
            else:
                app.renderer.print_error(f"Unknown /flow subcommand: {subcmd}")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/activate":
        parts = arg.split(None, 1)
        if len(parts) < 2:
            app.renderer.print_error("Usage: /activate <type> <name>")
            return True
        try:
            app.api.send_action("activate_resource", conversation_id=app.conversation_id or "", resource_type=parts[0], name=parts[1])
            app.renderer.print_system(f"Activated {parts[0]} '{parts[1]}'")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/deactivate":
        parts = arg.split(None, 1)
        if len(parts) < 2:
            app.renderer.print_error("Usage: /deactivate <type> <name>")
            return True
        try:
            app.api.send_action("deactivate_resource", conversation_id=app.conversation_id or "", resource_type=parts[0], name=parts[1])
            app.renderer.print_system(f"Deactivated {parts[0]} '{parts[1]}'")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/prompt":
        parts = arg.split(None, 1) if arg else ["list"]
        subcmd = parts[0].lower()
        try:
            if subcmd == "list":
                data = app.api.send_action("list_prompts", conversation_id=app.conversation_id or "")
                prompts = data.get("prompts", [])
                for p in prompts:
                    app.renderer.print(f"  {p.get('name', '?')}: {p.get('description', p.get('content', ''))[:60]}")
                if not prompts:
                    app.renderer.print_system("No prompts.")
            elif subcmd == "use":
                name = parts[1] if len(parts) > 1 else ""
                data = app.api.send_action("get_prompt", conversation_id=app.conversation_id or "", name=name)
                content = data.get("content", "")
                if content:
                    app.renderer.print_system(f"Prompt '{name}':")
                    app.renderer.print_markdown(content)
                else:
                    app.renderer.print_error(f"Prompt '{name}' not found")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/vidservice":
        parts = arg.split(None, 2) if arg else ["list"]
        subcmd = parts[0].lower()
        try:
            if subcmd == "list":
                data = app.api.send_action("list_video_services", conversation_id=app.conversation_id or "")
                services = data if isinstance(data, list) else []
                for s in services:
                    selected = " ← " + ", ".join(s.get("selected_for", [])) if s.get("selected_for") else ""
                    app.renderer.print(f"  {s.get('id', '?')} ({s.get('type', '?')}, {s.get('scope', '?')}){selected}")
                if not services:
                    app.renderer.print_system("No video services.")
            elif subcmd == "select":
                name = parts[1] if len(parts) > 1 else ""
                agent = parts[2] if len(parts) > 2 else "*"
                if not name:
                    app.renderer.print_error("Usage: /vidservice select <name> [agent]")
                    return True
                app.api.send_action("set_video_service", conversation_id=app.conversation_id or "", service_name=name, agent_name=agent)
                target = "all agents" if agent == "*" else agent
                app.renderer.print_system(f"Video service set to '{name}' for {target}")
            elif subcmd == "clear":
                agent = parts[1] if len(parts) > 1 else ""
                app.api.send_action("clear_video_service", conversation_id=app.conversation_id or "", agent_name=agent)
                app.renderer.print_system("Video service preference cleared")
            else:
                app.renderer.print_error("Usage: /vidservice list | select <name> [agent] | clear [agent]")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/imgservice":
        parts = arg.split(None, 2) if arg else ["list"]
        subcmd = parts[0].lower()
        try:
            if subcmd == "list":
                data = app.api.send_action("list_image_services", conversation_id=app.conversation_id or "")
                services = data if isinstance(data, list) else []
                for s in services:
                    selected = " ← " + ", ".join(s.get("selected_for", [])) if s.get("selected_for") else ""
                    app.renderer.print(f"  {s.get('id', '?')} ({s.get('type', '?')}, {s.get('scope', '?')}){selected}")
                if not services:
                    app.renderer.print_system("No image services.")
            elif subcmd == "select":
                name = parts[1] if len(parts) > 1 else ""
                agent = parts[2] if len(parts) > 2 else "*"
                if not name:
                    app.renderer.print_error("Usage: /imgservice select <name> [agent]")
                    return True
                app.api.send_action("set_image_service", conversation_id=app.conversation_id or "", service_name=name, agent_name=agent)
                target = "all agents" if agent == "*" else agent
                app.renderer.print_system(f"Image service set to '{name}' for {target}")
            elif subcmd == "clear":
                agent = parts[1] if len(parts) > 1 else ""
                app.api.send_action("clear_image_service", conversation_id=app.conversation_id or "", agent_name=agent)
                app.renderer.print_system("Image service preference cleared")
            else:
                app.renderer.print_error("Usage: /imgservice list | select <name> [agent] | clear [agent]")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/share":
        parts = arg.split(None, 2) if arg else []
        if len(parts) < 3:
            app.renderer.print_error("Usage: /share <type> <name> <conversation_id>")
            return True
        try:
            app.api.send_action("share_resource", conversation_id=app.conversation_id or "",
                                resource_type=parts[0], name=parts[1], target_conversation_id=parts[2])
            app.renderer.print_system(f"Shared {parts[0]} '{parts[1]}' to {parts[2][:8]}")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/install":
        if not arg:
            app.renderer.print_error("Usage: /install <filename.py>")
            return True
        import os
        fpath = arg.strip().strip('"').strip("'")
        if not os.path.isfile(fpath):
            app.renderer.print_error(f"File not found: {fpath}")
            return True
        try:
            content = open(fpath, "r").read()
            data = app.api.send_action("install_tool", filename=os.path.basename(fpath), code=content)
            if data.get("error"):
                app.renderer.print_error(data["error"])
            else:
                app.renderer.print_system(f"Tool installed: {data.get('name', os.path.basename(fpath))}")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/uninstall":
        if not arg:
            app.renderer.print_error("Usage: /uninstall <tool_name>")
            return True
        try:
            data = app.api.send_action("uninstall_tool", name=arg.strip())
            if data.get("error"):
                app.renderer.print_error(data["error"])
            else:
                app.renderer.print_system(f"Tool '{arg.strip()}' uninstalled")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    return False
