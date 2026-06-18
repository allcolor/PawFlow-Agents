"""PawCode SSE event consumer / approvals / scripted run_prompt."""

import logging
import queue
import sys
import threading
import time

from pawflow_cli.auth import authenticate
from pawflow_cli.api import AgentAPIClient, SSEClient
from pawflow_cli.config import load_config, save_config
# Split out of pawflow_cli/app.py for the <=800-line rule; composed back into
# PawCode (invariant 2: MRO/shared state).


class _PawCodeEventsMixin:
    """SSE event consumer / approvals / scripted run_prompt."""

    def _ensure_sse(self):
        """Ensure SSE client is connected for the current conversation."""
        if self.conversation_id and (not self.sse or not self.sse.connected):
            self.sse = SSEClient(self.server_url, self.session_token, self.gateway_cookie)
            self.sse.connect(self.conversation_id)

    def _start_event_consumer(self):
        """Start the event consumer thread (idempotent)."""
        if hasattr(self, '_event_thread') and self._event_thread and self._event_thread.is_alive():
            return
        self._event_thread = threading.Thread(target=self._event_consumer,
                                               daemon=True, name="pawcode-events")
        self._event_thread.start()

    def _event_consumer(self):
        """Background thread: continuously consume SSE events and render them."""
        streaming_agent = ""
        thinking_agent = ""

        while self._running:
            # Wait for SSE client to be available
            if not self.sse:
                time.sleep(0.2)
                continue

            try:
                event = self.sse.events.get(timeout=0.5)
            except queue.Empty:
                continue
            except Exception:
                time.sleep(0.5)
                continue

            try:
                still_waiting = self._dispatch_event(event, streaming_agent, thinking_agent)
                streaming_agent = self._ev_streaming_agent
                thinking_agent = self._ev_thinking_agent
                # On done/error/cancelled, reset streaming state
                if not still_waiting:
                    streaming_agent = ""
                    thinking_agent = ""
            except Exception as e:
                self._safe_stop_live()
                try:
                    self.renderer.print_error(f"Event error: {e}")
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    _last_session_renew = 0.0

    def _active_agents_poller(self):
        """Background thread: poll server for active agents every 3s.

        This is the SINGLE source of truth for the typing/status indicator,
        matching the web UI's syncActiveFromServer approach.
        Also renews the local session expiry every 30 minutes.
        """
        while self._running:
            time.sleep(3)
            if not self._running or not self.api or not self.conversation_id:
                continue
            try:
                data = self.api.send_action("list_active",
                                             conversation_id=self.conversation_id)
                server_active = data.get("active", [])
                server_keys = set()
                for _sa in server_active:
                    _n = _sa.get("agent_name", "").lower()
                    _t = _sa.get("task_id", "")
                    server_keys.add((_n + "::" + _t) if _t else _n)

                # Remove agents server doesn't know about
                for key in list(self._active_agents.keys()):
                    if key not in server_keys:
                        del self._active_agents[key]

                # Add/update from server
                for a in server_active:
                    _an = a.get("agent_name", "").lower()
                    _tid = a.get("task_id", "")
                    key = (_an + "::" + _tid) if _tid else _an
                    existing = self._active_agents.get(key, {})
                    self._active_agents[key] = {
                        "name": a.get("agent_name", ""),
                        "task_id": a.get("task_id", ""),
                        "iteration": a.get("iteration", existing.get("iteration", 0)),
                        "round": a.get("round", 0),
                        "max_rounds": a.get("max_rounds", 0),
                        "last_tool": a.get("last_tool", existing.get("last_tool", "")),
                        "total_tools": a.get("total_tools", existing.get("total_tools", 0)),
                        "duration_s": a.get("duration_s", 0),
                    }

                # Update status bar based on active agents (single source of truth)
                if self._active_agents:
                    from pawflow_cli.ui.renderer import _random_verb
                    parts = []
                    for info in self._active_agents.values():
                        name = info["name"]
                        if info.get("task_id"):
                            name += f" [task:{info['task_id']}]"
                        detail_parts = []
                        if info.get("iteration"):
                            detail_parts.append(f"iter {info['iteration']}")
                        # `round x/y` removed: it tracks an internal agent-loop
                        # counter that is meaningless to the user.
                        if info.get("total_tools"):
                            detail_parts.append(f"{info['total_tools']} tools")
                        if info.get("last_tool"):
                            detail_parts.append(info["last_tool"])
                        detail = " \u00b7 ".join(detail_parts) if detail_parts else _random_verb() + "..."
                        parts.append(f"{name} ({detail})")
                    self._update_status(f"\u25b6 {', '.join(parts)}")
                else:
                    # Only clear if no active streams either (avoid flicker during token streaming)
                    if not self.renderer._streams:
                        self._update_status("")
                # Renew local session expiry every 30 min (sliding window)
                now = time.time()
                if now - self._last_session_renew > 1800 and self.session_token:
                    self._last_session_renew = now
                    from pawflow_cli.config import save_session
                    save_session(self.session_token, self.username,
                                 self.server_url, now + 8 * 3600)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def _dispatch_event(self, event, streaming_agent, thinking_agent):
        """Dispatch a single SSE event. Returns True to keep waiting, False when done."""
        from pawflow_cli.event_handler import dispatch_event
        result, self._ev_streaming_agent, self._ev_thinking_agent = dispatch_event(
            self, event, streaming_agent, thinking_agent)
        return result

    def _handle_exec_approval(self, data: dict):
        """Handle exec approval request — delegate to main thread via queue."""
        self.renderer.print_exec_approval(
            data.get("command", "?"),
            data.get("risk_level", "normal"),
            data.get("request_id", ""),
        )
        # Put request in queue for main thread to handle
        self._approval_queue.put({
            "type": "exec",
            "request_id": data.get("request_id", ""),
            "result_map": {"y": "approved", "n": "denied", "s": "session_allow", "a": "always_allow"},
            "action": "exec_result",
        })
        # Wait for response from main thread (timeout 60s)
        try:
            result = self._approval_response.get(timeout=60)
        except queue.Empty:
            result = "denied"
        try:
            self.api.send_action("exec_result",
                                 request_id=data.get("request_id", ""),
                                 result=result,
                                 conversation_id=self.conversation_id)
        except Exception as e:
            self.renderer.print_error(f"Approval error: {e}")

    def _handle_tool_approval(self, data: dict):
        """Handle tool approval request — delegate to main thread via queue."""
        self.renderer.print_approval_request(
            data.get("tool_name", "?"),
            data.get("action_summary", ""),
            data.get("request_id", ""),
        )
        self._approval_queue.put({
            "type": "tool",
            "request_id": data.get("request_id", ""),
            "result_map": {"y": "allow_once", "n": "denied", "s": "session_allow", "a": "always_allow"},
            "action": "tool_approval_result",
        })
        try:
            result = self._approval_response.get(timeout=60)
        except queue.Empty:
            result = "denied"
        try:
            self.api.send_action("tool_approval_result",
                                 request_id=data.get("request_id", ""),
                                 result={"choice": result},
                                 conversation_id=self.conversation_id)
        except Exception as e:
            self.renderer.print_error(f"Approval error: {e}")
    def run_prompt(self, prompt: str, conversation_id: str = None,
                   output_format: str = "text"):
        """Prompt mode: send one prompt, stream response, exit."""
        import json as _json

        # Authenticate silently
        auth = authenticate(self.server_url, gateway_cookie=self.gateway_cookie)
        self.session_token = auth["token"]
        self.username = auth["username"]
        self.api = AgentAPIClient(self.server_url, self.session_token, self.gateway_cookie)

        # PawCode no longer owns relay lifecycle. Filesystem relays are managed
        # by webchat server resources or the standalone pawflow-relay client.

        # Resolve or create a conversation, then target its active agent.
        if not conversation_id:
            config = load_config()
            conversation_id = config.get("last_conversation_id")
        from pawflow_cli.conversation_bootstrap import ensure_conversation_and_agent
        conversation_id, target_agent = ensure_conversation_and_agent(
            self.api, conversation_id or "")

        # Send message
        resp = self.api.send_message(
            message=prompt,
            conversation_id=conversation_id,
            target_agent=target_agent,
        )
        if resp.get("error"):
            print(resp["error"], file=sys.stderr)
            self._cleanup()
            sys.exit(1)

        cid = resp.get("conversation_id")
        if cid:
            self.conversation_id = cid
            save_config({"last_conversation_id": cid})

        # Connect SSE and wait for response
        self.sse = SSEClient(self.server_url, self.session_token, self.gateway_cookie)
        self.sse.connect(cid)

        response_text = ""
        streaming_tokens = {}
        is_full = output_format == "full"

        while True:
            try:
                event = self.sse.events.get(timeout=120)
            except queue.Empty:
                print("Timeout waiting for response", file=sys.stderr)
                break

            ev_type = event.get("event", "")
            data = event.get("data", {})

            if ev_type == "token":
                agent = data.get("agent_name", "")
                text = data.get("text", "")
                streaming_tokens.setdefault(agent, "")
                streaming_tokens[agent] += text
                if output_format == "text":
                    sys.stdout.write(text)
                    sys.stdout.flush()

            elif ev_type == "tool_call" and is_full:
                tool = data.get("tool", "?")
                args = data.get("arguments", {})
                print(f"\n[tool_call] {tool}({_json.dumps(args, ensure_ascii=False)[:200]})",
                      file=sys.stderr)

            elif ev_type == "tool_result" and is_full:
                tool = data.get("tool", "?")
                result = str(data.get("result", ""))[:500]
                print(f"[tool_result] {tool}: {result}", file=sys.stderr)

            elif ev_type == "done":
                response_text = data.get("response", "")
                agent = data.get("agent_name", "")
                # If we were streaming tokens, we already printed them
                if not streaming_tokens.get(agent) and response_text:
                    if output_format == "text":
                        sys.stdout.write(response_text)
                elif streaming_tokens.get(agent) and output_format == "text":
                    pass  # already printed via tokens

                if output_format == "json":
                    result = {
                        "response": response_text or streaming_tokens.get(agent, ""),
                        "agent": agent,
                        "conversation_id": cid,
                        "tokens_in": data.get("tokens_in", 0),
                        "tokens_out": data.get("tokens_out", 0),
                        "model": data.get("model", ""),
                    }
                    print(_json.dumps(result, ensure_ascii=False))
                elif output_format == "markdown":
                    text = response_text or streaming_tokens.get(agent, "")
                    print(text)

                if not data.get("continuing"):
                    break

            elif ev_type == "error_event":
                print(f"\nError: {data.get('message', 'Unknown error')}", file=sys.stderr)
                break

            elif ev_type == "cancelled":
                print("\nCancelled", file=sys.stderr)
                break

        # Ensure trailing newline for text mode
        if output_format == "text" and (response_text or streaming_tokens):
            sys.stdout.write("\n")
            sys.stdout.flush()

        self._cleanup()
