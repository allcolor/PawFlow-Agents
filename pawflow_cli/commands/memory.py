"""Memory commands: /memory."""


def handle_memory_commands(app, cmd, arg, text):
    """Handle memory commands. Returns True if handled, False otherwise."""

    if cmd == "/memory":
        if not app.conversation_id:
            app.renderer.print_error("No active conversation")
            return True
        parts = arg.split(None, 1) if arg else ["list"]
        subcmd = parts[0].lower()
        subarg = parts[1] if len(parts) > 1 else ""

        try:
            if subcmd == "list":
                data = app.api.send_action("list_memories", conversation_id=app.conversation_id, agent_name=subarg or "")
                memories = data.get("memories", [])
                if not memories:
                    app.renderer.print_system("No memories.")
                else:
                    for m in memories:
                        tags = " ".join(f"#{t}" for t in m.get("tags", []))
                        app.renderer.print(f"  [{m.get('id', '?')[:8]}] {m.get('content', '')[:80]} {tags}")

            elif subcmd == "add":
                if not subarg:
                    app.renderer.print_error("Usage: /memory add <text> [@agent] [#tag1 #tag2]")
                    return True
                app.api.send_action("add_memory", conversation_id=app.conversation_id, content=subarg)
                app.renderer.print_system("Memory added")

            elif subcmd in ("del", "delete"):
                if not subarg:
                    app.renderer.print_error("Usage: /memory del <id>")
                    return True
                app.api.send_action("delete_memory", conversation_id=app.conversation_id, memory_id=subarg)
                app.renderer.print_system("Memory deleted")

            elif subcmd == "edit":
                edit_parts = subarg.split(None, 1)
                if len(edit_parts) < 2:
                    app.renderer.print_error("Usage: /memory edit <id> <new text>")
                    return True
                app.api.send_action("edit_memory", conversation_id=app.conversation_id, memory_id=edit_parts[0], content=edit_parts[1])
                app.renderer.print_system("Memory updated")

            elif subcmd == "search":
                data = app.api.send_action("search_memories", conversation_id=app.conversation_id, query=subarg)
                results = data.get("results", [])
                for r in results:
                    app.renderer.print(f"  [{r.get('id', '?')[:8]}] ({r.get('score', 0):.2f}) {r.get('content', '')[:80]}")

            else:
                app.renderer.print_error("Usage: /memory list|add|del|edit|search")
        except Exception as e:
            app.renderer.print_error(str(e))
        return True

    return False
