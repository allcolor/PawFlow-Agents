"""Native question workers for the authenticated interactive event channel."""

import asyncio
import json
import logging

from core.native_hook_input import answer_claude_hook, answer_codex_hook


async def answer_native_input(state, raw, cancel_event, writer):
    from services.filesystem_service import _ws_send_frame

    try:
        if state.provider in {"claude-code-interactive", "claude-code", "cc_mcp"}:
            answer_hook = answer_claude_hook
        elif state.provider in {"codex-interactive", "codex_mcp"}:
            answer_hook = answer_codex_hook
        else:
            raise ValueError("Unsupported native interaction provider")
        if not all((state.user_id, state.conversation_id, state.agent_name)):
            raise ValueError("Native input requires a bound session")
        if not isinstance(raw, dict) or len(json.dumps(raw)) > 64000:
            raise ValueError("Invalid native input payload")
        output = await asyncio.wait_for(asyncio.to_thread(
            answer_hook, raw, provider=state.provider,
            user_id=state.user_id, conversation_id=state.conversation_id,
            agent_name=state.agent_name, cancel_event=cancel_event), timeout=3600)
        if not cancel_event.is_set():
            await _ws_send_frame(writer, json.dumps({
                "type": "native_input_result", "output": output}).encode())
    except Exception:
        logging.getLogger(__name__).debug("Native CLI hook failed", exc_info=True)
        if not cancel_event.is_set():
            await _ws_send_frame(writer, json.dumps({
                "type": "error", "message": "Invalid native interaction request"}).encode())
    finally:
        cancel_event.set()
        # Each hook connection carries one request. Also release the socket
        # reader if collection or writing failed before a result was sent.
        writer.close()
