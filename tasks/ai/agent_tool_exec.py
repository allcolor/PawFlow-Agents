"""AgentLoopTask mixin — AgentContext methods

Auto-extracted from tasks/ai/agent_loop.py.
All methods access self (AgentLoopTask instance).
"""
import json
import logging
import threading
import time
from typing import Dict, Any, List, Optional


from core import FlowFile
from core.llm_client import (
    LLMClient, LLMMessage, LLMResponse, LLMToolDefinition,
    LLMToolCall, LLMToolResult, LLMClientError,
)
from core.tool_registry import ToolRegistry, create_default_registry

logger = logging.getLogger(__name__)



class AgentContextMixin:
    """Methods extracted from AgentLoopTask."""



class AgentToolExecMixin:
    """Tool call execution."""

    def _execute_tool_calls(self, tool_calls, registry, consecutive_tracker: dict,
                            max_consecutive: int, *, parallel: bool = True,
                            agent_name: str = "", agent_svc: str = "",
                            conversation_id: str = "", user_id: str = "",
                            is_claude_code: bool = False,
                            cancel_check: callable = None,
                            event_cid: str = ""):
        """Execute tool calls with consecutive-call limiting + approval gate.

        Returns list of (tool_call, result_text) in original order.
        """
        # Load env vars (for $VAR resolution) and secret values (for redaction)
        _secret_values = set()
        _secret_names = {}
        _all_env = {}
        if user_id:
            try:
                from services.tool_relay_service import (
                    resolve_secrets_env, resolve_secret_values,
                    _redact_secrets, _resolve_vars_in_args)
                _scid = conversation_id.split('::task::')[0] if '::task::' in conversation_id else conversation_id
                _all_env = resolve_secrets_env(user_id, _scid)
                _secret_values, _secret_names = resolve_secret_values(user_id, _scid)
            except Exception:
                logger.debug("exception suppressed", exc_info=True)

        # Determine blocked tools
        blocked = set()
        if max_consecutive > 0:
            for tc in tool_calls:
                consecutive_tracker[tc.name] = consecutive_tracker.get(tc.name, 0) + 1
                for tn in list(consecutive_tracker):
                    if tn != tc.name:
                        consecutive_tracker[tn] = 0
                if consecutive_tracker[tc.name] > max_consecutive:
                    blocked.add(tc.name)

        def _handler_for_result_shape(tc):
            tool_name = tc.name
            tool_args = tc.arguments if isinstance(tc.arguments, dict) else {}
            unwrap_budget = 3
            wrapper_names = {"use_tool", "mcp__pawflow__use_tool", "mcp_pawflow_use_tool"}
            while tool_name in wrapper_names and isinstance(tool_args, dict) and unwrap_budget > 0:
                nested_name = (tool_args.get("tool_name")
                               or tool_args.get("name")
                               or tool_args.get("tool")
                               or "")
                nested_args = tool_args.get("arguments")
                if nested_args is None:
                    nested_args = tool_args.get("params")
                if nested_args is None:
                    nested_args = tool_args.get("input")
                if nested_args is None:
                    nested_args = {}
                for _ in range(3):
                    if isinstance(nested_args, str):
                        try:
                            nested_args = json.loads(nested_args)
                        except (json.JSONDecodeError, TypeError):
                            nested_args = {}
                            break
                    else:
                        break
                if not nested_name:
                    break
                tool_name = nested_name
                tool_args = nested_args
                unwrap_budget -= 1
            return next((h for h in registry.list_tools()
                         if getattr(h, "name", "") == tool_name), None)

        def _exec_one(tc):
            if tc.name in blocked:
                return tc, (
                    f"Tool '{tc.name}' has been called {consecutive_tracker.get(tc.name, 0)} times "
                    f"consecutively (limit: {max_consecutive}). "
                    f"Stop and explain to the user what you've tried so far, "
                    f"and ask if they want you to continue."
                )
            # Build agent key for per-agent permissions
            # For tasks: agent_name::task::task_id (derived from conversation_id)
            _agent_key = agent_name
            if "::task::" in conversation_id:
                _task_suffix = conversation_id.split("::task::", 1)[1]
                _agent_key = f"{agent_name}::task::{_task_suffix}"
            # Fine-grained tool permissions (override global mode)
            _tool_perm = ""
            _perm_mode = ""
            _perm_cid = event_cid or conversation_id
            try:
                from core.conversation_store import ConversationStore
                _cs = ConversationStore.instance()
                _perm_mode = _cs.get_extra(conversation_id, "permission_mode") or "default"
                from core.tool_approval import ToolApprovalGate as _TAG
                _tperms = _TAG._get_permissions(_perm_cid, _agent_key)
                _tool_perm = _tperms.get(tc.name, "")
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
            if _tool_perm == "deny":
                return tc, f"Error: Tool '{tc.name}' is denied by permission settings."
            elif _tool_perm == "allow":
                pass  # explicitly allowed — skip all further permission checks
            elif _tool_perm == "confirm":
                # Force user confirmation regardless of global mode (even auto)
                from core.tool_approval import ToolApprovalGate
                _approval_cid = event_cid or conversation_id
                approval = ToolApprovalGate.check(
                    tc.name, f"{tc.name}({json.dumps(tc.arguments)[:200]})",
                    _approval_cid, user_id,
                    arguments=tc.arguments,
                    agent_name=_agent_key,
                )
                if approval != "approved":
                    return tc, f"Error: Tool '{tc.name}' was {approval} by the user."
            else:
                # No per-tool override — use global permission_mode
                if _perm_mode == "read_only":
                    _write_tools = {"write", "edit", "batch_edit", "apply_patch", "find_replace",
                                    "delete", "mkdir", "bash", "notebook_edit"}
                    if tc.name in _write_tools:
                        return tc, "Error: write operations blocked (read-only mode). Change permission mode to allow writes."
                    # Also block filesystem write actions
                    if tc.name == "filesystem" and tc.arguments.get("action", "") not in (
                            "list_dir", "read_file", "stat", "exists", "search", "grep",
                            "git_status", "git_log", "git_diff", ""):
                        return tc, "Error: write operations blocked (read-only mode). Change permission mode to allow writes."
                elif _perm_mode == "auto":
                    pass  # skip approval gate entirely — auto-approve all tools
                else:
                    # default / approve_edits — use normal approval gate
                    from core.tool_approval import ToolApprovalGate
                    _approval_cid = event_cid or conversation_id
                    approval = ToolApprovalGate.check(
                        tc.name, f"{tc.name}({json.dumps(tc.arguments)[:200]})",
                        _approval_cid, user_id,
                        arguments=tc.arguments,
                        agent_name=_agent_key,
                    )
                    if approval != "approved":
                        return tc, f"Error: Tool '{tc.name}' was {approval} by the user."
            # Re-inject thread-local source agent + delegate tc_id (needed in pool threads)
            from core.tool_registry import SpawnAgentsHandler
            for h in registry.list_tools():
                if isinstance(h, SpawnAgentsHandler):
                    h.set_source_agent(agent_name, agent_svc)
                    h.set_delegate_tc_id(tc.id)
                    break
            _tool_relay_bound = False
            _kill_hooks = []
            _cancel_event = threading.Event()
            _background_event = threading.Event()
            try:
                # API-provider tools execute directly in this pool, not through
                # ToolRelayService._handle_execute. Register the same in-flight
                # metadata so UI Kill can target tc.id and invoke hooks from
                # nested relay requests (screen/local screenshots, bash, etc.).
                from services.tool_relay_service import (
                    ToolRelayService, _set_current_cancel_event,
                    _set_current_kill_hooks)
                with ToolRelayService._inflight_lock:
                    ToolRelayService._inflight[tc.id] = {
                        "conv": conversation_id,
                        "agent": agent_name,
                        "cancel": _cancel_event,
                        "background": _background_event,
                        "cc_tc_id": tc.id,
                        "bg_tc_id": tc.id,
                        "tool_name": tc.name,
                        "args_hash": "",
                        "started_at": time.time(),
                        "kill_hooks": _kill_hooks,
                    }
                _set_current_cancel_event(_cancel_event)
                _set_current_kill_hooks(_kill_hooks)
                _tool_relay_bound = True
                # Resolve $VAR / ${VAR} in arguments before execution
                if _all_env:
                    _skip = set()
                    if tc.name == "bash":
                        _skip = {"command"}
                    elif tc.name == "execute_script":
                        _skip = {"code"}
                    _resolve_vars_in_args(tc.arguments, _all_env, skip_keys=_skip)
                # Pre-hook execution
                self._run_hook("pre", tc.name, tc.arguments, conversation_id, user_id)
                logger.info("Agent calling tool '%s' with args: %s", tc.name, tc.arguments)
                result = registry.execute(tc.name, tc.arguments) or ""
                # Redact secrets from tool output
                if _secret_values and isinstance(result, str):
                    result = _redact_secrets(result, _secret_values,
                                             secret_names=_secret_names)
                # Post-hook execution
                self._run_hook("post", tc.name, tc.arguments, conversation_id, user_id)
                # Check for ask_user pause signal
                if isinstance(result, str) and result.startswith("__ASK_USER__:"):
                    # Strip the prefix — the question text becomes the tool result
                    result = result[len("__ASK_USER__:"):]
                # Hint: prefer write() over share_file when FS is available
                if tc.name == "share_file":
                    from core.handlers._fs_base import BaseFsHandler as _BFH
                    for _h in registry.list_tools():
                        if isinstance(_h, _BFH) and _h._find_service():
                            result += "\n[Hint: a filesystem service is available — use write(path=..., content=...) to write directly to the user's machine instead of share_file]"
                            break
                # Auto-suggest related tests after file modifications
                if tc.name in ("write", "edit"):
                    modified_path = tc.arguments.get("path", "")
                    if modified_path and modified_path.endswith(".py"):
                        from core.handlers.devops import _detect_related_tests
                        candidates = _detect_related_tests(modified_path)
                        if candidates:
                            hint = ", ".join(candidates[:3])
                            result += f"\n[Related tests may exist: {hint} — use run_tests to verify]"
                # No truncation here — registry.execute() handles the 50K cap
                # for ALL callers (agent loop, MCP bridge, /call command).
                # Anti-injection wrap happens ONCE at the single caller
                # (agent_core._run_agent_loop) via AgentCoreMixin._wrap_tool_output
                # so we return the raw result here.
                # Extract multimodal image data for LLM vision.
                # The image is sent for the CURRENT LLM call only.
                # After the call, the message is deflated to text-only
                # (see _deflate_image_messages) so base64 doesn't bloat context.
                # Gate on handler's _returns_images flag — a grep match on the
                # literal "__image_data__:" string must NOT be split into blocks.
                _h = _handler_for_result_shape(tc)
                _ri = bool(getattr(_h, '_returns_images', False))
                if _ri and isinstance(result, str) and "__image_data__:" in result:
                    lines = result.split("\n")
                    text_lines = []
                    image_parts = []
                    for line in lines:
                        if line.startswith("__image_data__:"):
                            parts = line.split(":", 2)
                            if len(parts) == 3:
                                mime, b64 = parts[1], parts[2]
                                image_parts.append({
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                                })
                        else:
                            text_lines.append(line)
                    if image_parts:
                        content = [{"type": "text", "text": "\n".join(text_lines)}]
                        content.extend(image_parts)
                        return tc, content
                return tc, result
            except Exception as e:
                logger.error("Tool '%s' failed: %s", tc.name, e)
                return tc, f"Error: {e}"
            finally:
                if _tool_relay_bound:
                    try:
                        _set_current_cancel_event(None)
                        _set_current_kill_hooks(None)
                        with ToolRelayService._inflight_lock:
                            ToolRelayService._inflight.pop(tc.id, None)
                    except Exception:
                        logger.debug("exception suppressed", exc_info=True)

        from concurrent.futures import ThreadPoolExecutor, wait
        import core.background_tool as _bg
        import time as _time_mod

        # Always use thread pool (even for single tool) so user can background it
        pool = ThreadPoolExecutor(max_workers=max(len(tool_calls), 1))
        futures = {pool.submit(_exec_one, tc): tc for tc in tool_calls}
        results_map = {}
        pending = set(futures.keys())
        _started_at = {tc.id: _time_mod.time() for tc in tool_calls}
        _auto_bg_after = 300.0  # 5 min — matches tool-relay auto-BG

        _cancelled = False
        while pending:
            done, pending = wait(pending, timeout=1.0, return_when='FIRST_COMPLETED')
            for f in done:
                tc = futures[f]
                tc_result, result_text = f.result()
                results_map[tc.id] = (tc_result, result_text)
            # Check cancel/interrupt/new user message — preempt immediately
            if cancel_check and not _cancelled:
                try:
                    cancel_check()
                except Exception:
                    _cancelled = True
                    # Cancel all remaining futures
                    for f in list(pending):
                        f.cancel()
                        tc = futures[f]
                        results_map[tc.id] = (tc, "[Cancelled — agent was interrupted]")
                    pending.clear()
                    break
            # Check if any pending tools were backgrounded by user, OR
            # auto-background after 5 minutes (project rule: long-running
            # tools must not block the agent loop forever).
            _now = _time_mod.time()
            for f in list(pending):
                tc = futures[f]
                _user_bg = _bg.is_backgrounded(tc.id)
                _auto_bg = (_now - _started_at.get(tc.id, _now)) >= _auto_bg_after
                if _user_bg or _auto_bg:
                    if _auto_bg and not _user_bg:
                        logger.info("[agent-tool] auto-background after %ds for tc_id=%s",
                                    int(_auto_bg_after), tc.id)
                    _bg.register(tc.id, f, conversation_id, agent_name,
                                 tool_name=tc.name, is_claude_code=is_claude_code,
                                 user_id=getattr(self, '_user_id', '') or '')
                    results_map[tc.id] = (tc, (
                        f"[Running in background (tc_id={tc.id})]\n"
                        f"The actual result will be delivered in a separate "
                        f"user message once the tool completes. Continue "
                        f"your work — do not wait for it."
                    ))
                    pending.discard(f)

        pool.shutdown(wait=False)
        return [results_map[tc.id] for tc in tool_calls]


    def _run_hook(self, phase: str, tool_name: str, arguments: dict,
                  conversation_id: str, user_id: str) -> None:
        """Run a pre/post tool execution hook if configured.

        Hooks are stored in conv extra "hooks" as a dict:
          {"pre:filesystem.write_file": "eslint --fix ${path}", ...}
        The hook command is run via the relay executor if available.
        """
        if not conversation_id:
            return
        try:
            from core.conversation_store import ConversationStore
            from tasks.ai.agent_utils import _resolve_extra_dict
            hooks = _resolve_extra_dict(
                ConversationStore.instance(), conversation_id,
                "hooks", user_id)
            if not hooks:
                return

            # Build action key: "pre:tool_name" or "pre:tool_name.action"
            action = arguments.get("action", "") if isinstance(arguments, dict) else ""
            keys_to_check = [f"{phase}:{tool_name}"]
            if action:
                keys_to_check.insert(0, f"{phase}:{tool_name}.{action}")

            for key in keys_to_check:
                cmd = hooks.get(key)
                if not cmd:
                    continue
                # Substitute ${path}, ${action} etc. from arguments
                for k, v in (arguments or {}).items():
                    if isinstance(v, str):
                        cmd = cmd.replace(f"${{{k}}}", v)
                logger.info(f"[hook] {key}: {cmd}")
                # Execute via relay if available
                try:
                    exec_svc = self._find_executor_service(user_id)
                    if exec_svc:
                        exec_svc.execute(cmd)
                except Exception as he:
                    logger.warning(f"[hook] {key} failed: {he}")
        except Exception as e:
            logger.debug(f"[hook] check failed: {e}")


    def _handle_response_no_tools(self, response_text: str, client_provider: str,
                                  tool_defs, need_more_retried: bool,
                                  source: dict = None,
                                  conversation_id: str = ""):
        """Handle an LLM response with no tool calls.

        Returns (action, msgs_to_append, final_text, need_more_retried).
        - action="continue": append msgs_to_append and loop again
        - action="break": final_text is the agent's response; append msgs_to_append
        """
        # [NEED_MORE] signal: model requests another turn
        if "[NEED_MORE]" in response_text:
            clean = self._strip_echo_prefix(response_text.replace("[NEED_MORE]", "").strip())
            msgs = []
            if clean:
                msgs.append(LLMMessage(role="assistant", content=clean, source=source,
                                        conversation_id=conversation_id))
            msgs.append(LLMMessage(role="system", content=(
                "Continue. You have another turn. "
                "Use <tool_call> tags if you need tools, "
                "or provide your final answer."
            ), conversation_id=conversation_id))
            return "continue", msgs, "", need_more_retried

        # Heuristic: tool mentioned by name without <tool_call> tag
        if client_provider == "claude-code" and tool_defs:
            tool_names = [td.name for td in tool_defs]
            mentioned = [tn for tn in tool_names if tn in response_text]
            if mentioned and not need_more_retried:
                msgs = [
                    LLMMessage(role="assistant", content=response_text, source=source,
                                conversation_id=conversation_id),
                    LLMMessage(role="system", content=(
                        f"You mentioned tool(s) {mentioned} but did not emit <tool_call> tags. "
                        "You MUST use <tool_call> tags to invoke tools. Example:\n"
                        '<tool_call>{"name": "' + mentioned[0] + '", "arguments": {...}}</tool_call>\n'
                        "Please emit the correct <tool_call> tag(s) now, "
                        "or provide your final answer without mentioning tools."
                    ), conversation_id=conversation_id),
                ]
                return "continue", msgs, "", True

        # Final response. An empty provider response is handled by the caller's
        # forced-synthesis path; do not persist a blank assistant message first.
        final = self._strip_echo_prefix(response_text)
        msgs = []
        if final.strip():
            msgs.append(LLMMessage(role="assistant", content=final, source=source,
                                   conversation_id=conversation_id))
        return "break", msgs, final, need_more_retried


    def _append_task_log(self, conversation_id: str, task_id: str, entry: dict):
        """Append an entry to the persistent task timeline log."""
        import time
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        key = f"task_log:{task_id}"
        log = store.get_extra(conversation_id, key) or []
        entry["ts"] = time.time()
        log.append(entry)
        # Cap at 500 entries per task
        if len(log) > 500:
            log = log[-500:]
        store.set_extra(conversation_id, key, log)

