"""Context commands: /compact, /model, /rebuild, /rebuild-full, /restart, /summary, /context, /llm."""


def handle_context_commands(app, cmd, arg, text):
    """Handle context commands. Returns True if handled, False otherwise."""

    if cmd == "/compact":
        if not app.conversation_id:
            app.renderer.print_error("No active conversation")
            return True
        try:
            app.api.send_action("compact", conversation_id=app.conversation_id,
                                 agent_name=arg or "")
            app.renderer.print_system("Compaction started")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/model":
        if not arg:
            app.renderer.print_error("Usage: /model <model_name> or /model reset")
            return True
        try:
            data = app.api.send_action("model", model=arg, agent=app.selected_agent or "",
                                         conversation_id=app.conversation_id or "")
            app.renderer.print_system(data.get("message", "Model updated"))
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/rebuild":
        if not app.conversation_id:
            app.renderer.print_error("No active conversation")
            return True
        try:
            app.api.send_action("rebuild", conversation_id=app.conversation_id, agent_name=arg or "")
            app.renderer.print_system("Rebuild started")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd in ("/rebuild-full", "/rebuild_clean"):
        if not app.conversation_id:
            app.renderer.print_error("No active conversation")
            return True
        try:
            app.api.send_action("rebuild_full", conversation_id=app.conversation_id, agent_name=arg or "")
            app.renderer.print_system("Full rebuild started")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/restart":
        if not app.conversation_id:
            app.renderer.print_error("No active conversation")
            return True
        parts = arg.split()
        agent = parts[0] if parts else ""
        keep = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 5
        try:
            app.api.send_action("restart_from", conversation_id=app.conversation_id, agent_name=agent, keep_last=keep)
            app.renderer.print_system(f"Context restarted (keeping last {keep})")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/summary":
        if not app.conversation_id:
            app.renderer.print_error("No active conversation")
            return True
        parts = arg.split()
        agent = parts[0] if parts else ""
        tokens = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 4000
        try:
            app.api.send_action("resume_conversation", conversation_id=app.conversation_id, agent_name=agent, max_tokens=tokens)
            app.renderer.print_system("Summary started")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/context":
        if not app.conversation_id:
            app.renderer.print_error("No active conversation")
            return True
        # /context delete task:t_xxx — delete a sub-context
        if arg and arg.startswith("delete "):
            sub_name = arg[7:].strip()
            try:
                data = app.api.send_action("delete_sub_context",
                                             conversation_id=app.conversation_id,
                                             agent_name=sub_name)
                if data.get("error"):
                    app.renderer.print_error(data["error"])
                else:
                    app.renderer.print_system(f"Sub-context '{sub_name}' deleted")
            except Exception as e:
                app.renderer.print_error(str(e))
            return True
        try:
            data = app.api.send_action("get_context", conversation_id=app.conversation_id, agent_name=arg or "")
            messages = data.get("context", data.get("messages", []))
            tokens = data.get("token_estimate", data.get("estimated_tokens", 0))
            diverged = data.get("diverged", False)
            agent_name = data.get("agent_name", arg or "shared")
            label = f"{agent_name} ({'diverged' if diverged else 'shared'})"
            app.renderer.print_system(f"Context [{label}]: {len(messages)} messages, ~{tokens:,} tokens")
            # Show available sub-contexts
            agent_ctxs = data.get("agent_contexts", {})
            if agent_ctxs:
                ctx_list = ", ".join(f"{k} ({v})" for k, v in agent_ctxs.items() if k != "*")
                if ctx_list:
                    app.renderer.print_system(f"Available: {ctx_list}")
            for i, m in enumerate(messages[-20:]):
                role = m.get("role", "?")
                content = m.get("content", "")[:100]
                app.renderer.print(f"  [{i}] {role}: {content}...")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    if cmd == "/llm":
        parts = arg.split(None, 1)
        if len(parts) < 2:
            app.renderer.print_error("Usage: /llm <agent> <service|restore>")
            return True
        try:
            app.api.send_action("set_llm_service", conversation_id=app.conversation_id or "", agent_name=parts[0], llm_service=parts[1])
            app.renderer.print_system(f"LLM service for '{parts[0]}' set to '{parts[1]}'")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    return False
