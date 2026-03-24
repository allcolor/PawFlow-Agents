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
        if not data.get("continuing"):
            return False, streaming_agent, thinking_agent

    elif ev_type == "error_event":
        app.renderer.print_error(data.get("message", "Unknown error"))
        return False, streaming_agent, thinking_agent

    elif ev_type == "cancelled":
        app.renderer.print_system(f"[{data.get('agent_name', '?')}] Cancelled")
        return False, streaming_agent, thinking_agent

    elif ev_type == "compact_progress":
        stage = data.get("stage", "")
        detail = data.get("detail", "")
        if stage == "done":
            before = data.get("before", 0)
            after = data.get("after", 0)
            app.renderer.print_system(f"Compacted: {before} → {after} messages")
            app._update_status("")
        else:
            app._update_status(f"▶ Compacting... {stage} {detail}")

    elif ev_type == "task_progress":
        stage = data.get("stage", "")
        agent = data.get("agent", "")
        task = data.get("task", "")
        if stage == "done":
            app.renderer.print_system(f"Task '{task}' completed by {agent}")
            app._update_status("")
        else:
            app._update_status(f"▶ {agent} task: {stage}")

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

    return True, streaming_agent, thinking_agent
