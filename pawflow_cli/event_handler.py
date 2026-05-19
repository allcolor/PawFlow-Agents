"""SSE event dispatcher — extracted from PawCode._dispatch_event."""

import sys


def dispatch_event(app, event, streaming_agent, thinking_agent):
    """Dispatch a single SSE event. Returns (keep_waiting, streaming_agent, thinking_agent)."""
    ev_type = event.get("event", "")
    data = event.get("data", {})

    if ev_type == "thinking" or ev_type == "thinking_content":
        agent = data.get("agent_name", "")
        if ev_type == "thinking" and not thinking_agent:
            thinking_agent = agent
            app.renderer.start_thinking(agent)
        elif ev_type == "thinking_content":
            app.renderer.thinking_token(agent, data.get("text", ""))

    elif ev_type == "token":
        agent = data.get("agent_name", "")
        if thinking_agent:
            app.renderer.end_thinking(thinking_agent)
            thinking_agent = ""
        # Multi-agent: each agent accumulates independently
        if agent not in app.renderer._streams:
            source = data.get("source", {})
            svc = source.get("llm_service", "") if isinstance(source, dict) else ""
            app.renderer.start_stream(agent, svc)
        app.renderer.stream_token(agent, data.get("text", ""))

    elif ev_type == "turn_complete":
        # Finalize current stream between Claude Code turns
        agent = data.get("agent_name", "")
        if thinking_agent:
            app.renderer.end_thinking(thinking_agent)
            thinking_agent = ""
        if agent in app.renderer._streams:
            source = data.get("source", {})
            model = source.get("model", "") if isinstance(source, dict) else ""
            tokens_out = data.get("tokens_out", 0)
            app.renderer.end_stream(agent, model=model, tokens_out=tokens_out)

    elif ev_type == "tool_call":
        # Don't end other agents' streams — they continue independently
        agent = data.get("agent_name", "")
        svc = data.get("llm_service", "")
        app.renderer.print_tool_call(
            data.get("tool", "?"),
            data.get("arguments", {}),
            agent, svc,
        )

    elif ev_type == "tool_result":
        app.renderer.print_tool_result(
            data.get("tool", "?"),
            data.get("result", ""),
            data.get("agent_name", ""),
            path=data.get("path", ""),
        )

    elif ev_type == "iteration_status":
        app.renderer.print_iteration(
            data.get("agent_name", ""),
            data.get("iteration", 0),
            data.get("round", 0),
            data.get("max_rounds", 0),
            data.get("total_tools", 0),
        )

    elif ev_type == "exec_approval_request":
        app._handle_exec_approval(data)

    elif ev_type == "tool_approval_request":
        app._handle_tool_approval(data)

    elif ev_type == "ask_user":
        app.renderer.print_ask_user(
            data.get("question", ""),
            data.get("options", []),
        )

    elif ev_type == "btw_thinking":
        agent = data.get("agent_name", "")
        app.renderer.print_system(f"[{agent} btw] thinking...")

    elif ev_type == "btw_token":
        agent = data.get("agent_name", "")
        btw_key = f"btw:{agent}"
        if btw_key != streaming_agent:
            if streaming_agent:
                app.renderer.end_stream(streaming_agent)
            streaming_agent = btw_key
            app.renderer.print(f"[dim italic]  [{agent} btw][/dim italic]")
            app.renderer.start_stream(btw_key)
        app.renderer.stream_token(btw_key, data.get("text", ""))

    elif ev_type == "btw_done":
        agent = data.get("agent_name", "")
        btw_key = f"btw:{agent}"
        if streaming_agent == btw_key:
            app.renderer.end_stream(btw_key, data.get("response", ""))
            streaming_agent = ""

    elif ev_type == "sub_agent_start":
        app.renderer.print_system(f"Sub-agent [{data.get('agent_name', '?')}] started")

    elif ev_type == "sub_agent_done":
        agent = data.get("agent_name", "?")
        tokens = data.get("tokens_in", 0) + data.get("tokens_out", 0)
        app.renderer.print_system(f"Sub-agent [{agent}] done ({tokens} tokens)")
        resp = data.get("response", "")
        if resp:
            app.renderer.print_agent_badge(agent, data.get("llm_service", ""))
            app.renderer.print_markdown(resp[:500])

    elif ev_type == "exec_output":
        app.renderer.print_exec_output(
            data.get("command", ""), data.get("exit_code", -1),
            data.get("stdout", ""), data.get("stderr", ""))

    elif ev_type == "notification":
        msg = data.get("message", "")
        if data.get("urgency") == "high":
            app.renderer.print_error(msg)
        else:
            app.renderer.print_system(msg)

    elif ev_type == "done":
        response_text = data.get("response", "")
        agent = data.get("agent_name", "")
        # End this specific agent's stream (multi-agent safe)
        if agent in app.renderer._streams:
            app.renderer.end_stream(agent, response_text)
        elif response_text:
            app.renderer.print_agent_badge(agent)
            app.renderer.print_markdown(response_text)
        # Track for /copy
        if response_text:
            app._last_responses.append(response_text)
            if len(app._last_responses) > 10:
                app._last_responses.pop(0)
        app.renderer.print_done(
            data.get("agent_name", ""),
            data.get("tokens_in", 0),
            data.get("tokens_out", 0),
            data.get("duration_ms", 0),
            data.get("model", ""),
        )
        # Optimistic removal from active agents (poller will confirm on next tick)
        _tid = data.get("task_id", "")
        agent_key = (agent.lower() + "::" + _tid) if _tid else agent.lower()
        if agent_key in app._active_agents:
            del app._active_agents[agent_key]
        if not app._active_agents and not app.renderer._streams:
            app._update_status("")
        if not data.get("continuing"):
            return False, streaming_agent, thinking_agent

    elif ev_type == "error_event":
        app.renderer.print_error(data.get("message", "Unknown error"))
        # Clear active agents on error (agent loop terminated)
        app._active_agents.clear()
        app._update_status("")
        return False, streaming_agent, thinking_agent

    elif ev_type == "cancelled":
        agent = data.get('agent_name', '?')
        app.renderer.print_system(f"[{agent}] Cancelled")
        # Optimistic removal from active agents
        _tid = data.get("task_id", "")
        agent_key = (agent.lower() + "::" + _tid) if _tid else agent.lower()
        if agent_key in app._active_agents:
            del app._active_agents[agent_key]
        if not app._active_agents and not app.renderer._streams:
            app._update_status("")
        return False, streaming_agent, thinking_agent

    elif ev_type == "compact_progress":
        stage = data.get("stage", "")
        detail = data.get("detail", "")
        if stage == "done":
            if data.get("operation") == "git_prune":
                before = data.get("size_before", 0) / 1048576
                after = data.get("size_after", 0) / 1048576
                app.renderer.print_system(
                    f"Git history pruned: {before:.1f} MB -> {after:.1f} MB")
                if not app._active_agents:
                    app._update_status("")
                return False, streaming_agent, thinking_agent
            before = data.get("before", 0)
            after = data.get("after", 0)
            app.renderer.print_system(f"Compacted: {before} \u2192 {after} messages")
            # Only clear status if no active agents (poller is source of truth)
            if not app._active_agents:
                app._update_status("")
        else:
            label = "Pruning Git" if data.get("operation") == "git_prune" or stage == "git_prune" else "Compacting"
            app._update_status(f"\u25b6 {label}... {stage} {detail}")

    elif ev_type == "task_progress":
        stage = data.get("stage", "")
        agent = data.get("agent", "")
        task = data.get("task", "")
        if stage == "done":
            app.renderer.print_system(f"Task '{task}' completed by {agent}")
            if not app._active_agents:
                app._update_status("")
        else:
            app._update_status(f"\u25b6 {agent} task: {stage}")

    elif ev_type == "thought_scheduled":
        agent = data.get("agent", "")
        delay = data.get("delay", 0)
        app.renderer.print_system(f"[{agent}] next auto-message in ~{delay}s")

    elif ev_type == "thought_firing":
        agent = data.get("agent", "")
        app._update_status(f"▶ {agent} thinking...")

    elif ev_type == "sub_agent_iteration":
        agent = data.get("agent_name", "")
        iteration = data.get("iteration", 0)
        tools = data.get("total_tools", 0)
        app._update_status(f"▶ sub:{agent} iter {iteration} · {tools} tools")

    elif ev_type == "sub_agent_tool":
        agent = data.get("agent_name", "")
        tool = data.get("tool", "")
        app._update_status(f"▶ sub:{agent} {tool}...")

    elif ev_type == "interrupting":
        agent = data.get("agent", "")
        app.renderer.print_system(f"Interrupting {agent}...")

    elif ev_type == "discard":
        pass  # silently discard

    elif ev_type == "agent_response":
        agent = data.get("agent_name", data.get("source", {}).get("name", "") if isinstance(data.get("source"), dict) else "")
        response = data.get("response", "")
        if response:
            app.renderer.print_system("")  # spacing
            app.renderer.end_stream(agent, response)

    elif ev_type == "broadcast_done":
        count = data.get("agent_count", 0)
        app.renderer.print_system(f"Broadcast complete — {count} agent(s) responded")

    elif ev_type == "plan_created":
        plan = data.get("plan", data)
        title = plan.get("title", data.get("title", ""))
        steps = plan.get("steps", [])
        step_count = len(steps) if isinstance(steps, list) else data.get("steps", 0)
        app.renderer.print_system(f"\U0001f4cb Plan created: {title} ({step_count} steps)")

    elif ev_type == "plan_updated":
        plan = data.get("plan", data)
        title = plan.get("title", data.get("title", ""))
        done = data.get("done", sum(1 for s in plan.get("steps", []) if s.get("status") == "done"))
        total = data.get("total", len(plan.get("steps", [])))
        status = plan.get("status", "")
        app.renderer.print_system(f"\U0001f4cb Plan updated: {title} [{status}] {done}/{total} done")

    elif ev_type == "plan_deleted":
        plan_id = data.get("plan_id", "")
        app.renderer.print_system(f"\U0001f4cb Plan deleted: {plan_id}")

    elif ev_type == "bg_task_update":
        tc_id = data.get("tc_id", "")
        tool_name = data.get("tool", tc_id[:8] if tc_id else "?")
        status = data.get("status", "")
        result = data.get("result", "")
        app.renderer.print_bg_task_update(tool_name, status, result)

    elif ev_type == "command_result":
        _action = data.get("action", "")
        # Push to SSE result queue so send_action() unblocks
        if hasattr(app, 'api') and app.api and hasattr(app.api, '_sse_result_queue'):
            app.api._sse_result_queue.push(_action, data)
        if data.get("error"):
            app.renderer.print_system(f"[{_action}] Error: {data['error']}")
        else:
            _result = data.get("result", "")
            try:
                import json as _json_ev
                _parsed = _json_ev.loads(_result) if isinstance(_result, str) else _result
            except Exception:
                _parsed = {}
            # Silent data actions — don't print raw JSON
            _silent = {"list_active", "list_params_secrets", "list_links",
                        "list_conversations", "list_resources", "list_agents",
                        "list_tools", "list_skills", "get_tool_schemas",
                        "get_permission_mode", "get_context", "get_context_full",
                        "get_resource_detail", "get_plan", "get_plans",
                        "get_cost", "get_usage", "poll", "ping",
                        "list_repo_agents", "list_secrets", "list_variables",
                        "list_schedules", "list_memories", "list_prompts",
                        "task_status", "task_log", "stats", "insights",
                        "check_files", "port_forward_list", "list_services"}
            if _action in _silent:
                pass  # silently consumed
            elif isinstance(_parsed, dict) and (_parsed.get("error") or _parsed.get("message")):
                _msg = _parsed.get("error") or _parsed.get("message")
                app.renderer.print_system(f"[{_action}] {_msg}")
            elif isinstance(_parsed, dict) and _parsed.get("status") == "ok":
                app.renderer.print_system(f"[{_action}] OK")
            elif _result and not isinstance(_parsed, dict):
                app.renderer.print_system(f"[{_action}] {_result}")

    return True, streaming_agent, thinking_agent
