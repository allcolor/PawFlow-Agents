"""PawCode stream-json mode — Claude Code compatible NDJSON protocol.

When launched with --input-format stream-json --output-format stream-json,
PawCode speaks the same NDJSON protocol as Claude Code on stdin/stdout.
This allows any tool that integrates with Claude Code (VS Code, Agent SDK, etc.)
to use PawCode instead.
"""
import logging

import json
import queue
import signal
import sys
import time
from pathlib import Path
from uuid import uuid4

from pawflow_cli.api import AgentAPIClient, SSEClient
from pawflow_cli.auth import authenticate
from pawflow_cli.stream_events import translate_sse_event


class StreamJsonMode:
    """NDJSON stream-json protocol handler — drop-in for Claude Code."""

    def __init__(self, server_url, directory, session_token="", username="",  # nosec B107
                 gateway_cookie="", docker_image="", allow_exec=True):
        self.server_url = server_url
        self.directory = str(Path(directory).resolve())
        self.session_token = session_token
        self.username = username
        self.gateway_cookie = gateway_cookie
        self.docker_image = docker_image
        self.allow_exec = allow_exec
        self.conversation_id = ""
        self.session_id = ""
        self._api = None   # AgentAPIClient
        self._sse = None   # SSEClient

    def run(self):
        """Main loop: emit init, read stdin NDJSON, dispatch, emit results."""
        try:
            # Authenticate
            auth = authenticate(self.server_url, gateway_cookie=self.gateway_cookie)
            self.session_token = auth["token"]
            self.username = auth["username"]

            # API client
            self._api = AgentAPIClient(
                self.server_url, self.session_token, self.gateway_cookie)

            # PawCode stream-json is a chat client only. Filesystem relay
            # lifecycle is managed by the standalone pawflow-relay client.

            # Handle SIGINT gracefully
            def _sig(sig, frame):
                self._cleanup()
                sys.exit(0)
            signal.signal(signal.SIGINT, _sig)

            # Emit system/init
            self._emit_init()

            # Read stdin line by line (NDJSON)
            for raw_line in sys.stdin:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    msg = json.loads(raw_line)
                except json.JSONDecodeError as e:
                    self._emit_error(f"Invalid JSON: {e}")
                    continue

                msg_type = msg.get("type", "")
                try:
                    if msg_type == "user":
                        self._handle_user_message(msg)
                    elif msg_type == "control":
                        self._handle_control_response(msg)
                    else:
                        self._log(f"Unknown message type: {msg_type}")
                except Exception as e:
                    self._emit_error(f"Error handling message: {e}")

        except Exception as e:
            self._emit_error(f"Fatal error: {e}")
            return 1
        finally:
            self._cleanup()

        return 0

    def _handle_user_message(self, msg):
        """Handle an incoming user message — send to PawFlow, stream response."""
        # Extract session_id → map to conversation_id
        sid = msg.get("session_id", "")
        if sid:
            self.session_id = sid
            # If we have a session_id, use it as conversation_id
            if not self.conversation_id:
                self.conversation_id = sid

        # Extract message content
        message_obj = msg.get("message", {})
        content = message_obj.get("content", "")
        if isinstance(content, list):
            # Content blocks — extract text
            text_parts = []
            for block in content:
                if isinstance(block, str):
                    text_parts.append(block)
                elif isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        # Tool result from control flow — forward as-is
                        text_parts.append(str(block.get("content", "")))
            content = "\n".join(text_parts)
        elif not isinstance(content, str):
            content = str(content)

        if not content:
            self._log("Empty message content, skipping")
            return

        # Send to PawFlow API
        resp = self._api.send_message(
            message=content,
            conversation_id=self.conversation_id or None,
        )

        if resp.get("error"):
            self._emit_error(resp["error"])
            return

        # Update conversation_id from response
        cid = resp.get("conversation_id")
        if cid:
            self.conversation_id = cid
            if not self.session_id:
                self.session_id = cid

        # Connect SSE and stream response
        self._ensure_sse()
        self._stream_response()

    def _handle_control_response(self, msg):
        """Handle control messages (tool approval etc). Not yet implemented."""
        # Acknowledge — tool approval will be added later
        self._log(f"Control message received: {msg.get('subtype', 'unknown')}")

    def _stream_response(self):
        """Read SSE events and translate to stream-json output."""
        accumulated_text = ""
        stream_state = {}

        while True:
            try:
                event = self._sse.events.get(timeout=120)
            except queue.Empty:
                self._emit_error("Timeout waiting for response")
                break

            ev_type = event.get("event", "")
            data = event.get("data", {})

            # Skip internal SSE events
            if ev_type.startswith("_sse_"):
                if ev_type == "_sse_error":
                    self._log(f"SSE error: {data.get('error', '')}")
                continue

            # Translate and emit
            events, accumulated_text = translate_sse_event(
                ev_type, data, self.session_id, accumulated_text,
                stream_state)

            for ev in events:
                self._emit(ev)

            # Terminal events
            if ev_type in ("done", "error_event", "cancelled"):
                # Check if agent is continuing
                if ev_type == "done" and data.get("continuing"):
                    continue
                break

    def _ensure_sse(self):
        """Ensure SSE client is connected for current conversation."""
        if self.conversation_id and (not self._sse or not self._sse.connected):
            if self._sse:
                self._sse.disconnect()
            self._sse = SSEClient(
                self.server_url, self.session_token, self.gateway_cookie)
            self._sse.connect(self.conversation_id)

    def _emit(self, event):
        """Emit NDJSON event on stdout."""
        line = json.dumps(event, ensure_ascii=False)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def _emit_init(self):
        """Emit system/init event."""
        if not self.session_id:
            self.session_id = self.conversation_id or uuid4().hex

        self._emit({
            "type": "system",
            "subtype": "init",
            "session_id": self.session_id,
            "cwd": self.directory,
            "tools": ["get_tool_schema", "use_tool"],
            "mcp_servers": [],
            "model": "",
            "uuid": uuid4().hex,
        })

    def _emit_error(self, message):
        """Emit an error result event."""
        self._emit({
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": message,
            "errors": [message],
            "duration_ms": 0,
            "duration_api_ms": 0,
            "num_turns": 0,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            "model": "",
            "cost_usd": 0,
            "session_id": self.session_id,
            "uuid": uuid4().hex,
        })

    def _log(self, message):
        """Log to stderr (stdout is protocol-only)."""
        sys.stderr.write(f"[PawCode] {message}\n")
        sys.stderr.flush()

    def _cleanup(self):
        """Disconnect SSE."""
        if self._sse:
            try:
                self._sse.disconnect()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            self._sse = None
