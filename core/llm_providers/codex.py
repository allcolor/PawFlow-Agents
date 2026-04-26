"""LLM provider mixin -- Codex CLI (`codex exec --json`).

Compact MVP: spawns one container per call (no live-session reuse like CC),
parses the JSONL event stream, returns the final LLMResponse. Tools are
routed to PawFlow via the MCP bridge (~/.codex/config.toml configured at
spawn).

NOT a clone of LLMClaudeCodeMixin — the two CLIs evolve independently
(see memory "Separate pools per CLI"). Sufficient overlap is in the
shared LLMCliSharedMixin (HTTP, message serialization).
"""

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# Auto-compact threshold for the codex side. Codex emits no
# compact_boundary event in --json output — it handles compaction silently
# server-side. We therefore disable codex's own auto-compact (set the
# limit absurdly high) and detect approaching saturation OURSELVES via the
# `usage` payload in `turn.completed`. When the running prompt size hits
# this fraction of the context window we raise PawFlowCompactRequired,
# which the agent loop catches to trigger PawFlow's own bucket compact.
_PAWFLOW_COMPACT_THRESHOLD = 0.80


class PawFlowCodexCompactRequired(Exception):
    """Signal: codex usage crossed the PawFlow compact threshold this turn.

    Carries the conversation context so the agent loop can route to its
    own bucket-based compact instead of waiting for codex to compact
    server-side (which would discard our deeper transcript).
    """
    def __init__(self, used: int, window: int, conv_id: str = ""):
        super().__init__(
            f"codex usage {used}/{window} >= {int(_PAWFLOW_COMPACT_THRESHOLD * 100)}% — PawFlow compact")
        self.used = used
        self.window = window
        self.conv_id = conv_id


class LLMCodexMixin:
    """Codex CLI provider — `codex exec --json` over the shared CodexPool.

    Stream events emitted by `codex exec --json` (one JSON per line on
    stdout):
      thread.started{thread_id}
      turn.started
      item.started{item:{type, ...}}
      item.updated{item:{...}}
      item.completed{item:{type:agent_message|reasoning|command_execution
                              |mcp_tool_call|file_change|web_search|plan_update,
                            text/result/...}}
      turn.completed{usage:{input_tokens, cached_input_tokens, output_tokens}}
      turn.failed
      error

    PawFlow tools live behind the MCP bridge — codex calls them via
    mcp_tool_call items. Our code only watches the events; the actual tool
    execution is owned by codex → our MCP bridge → tool relay service.
    """

    # Container default model for codex CLI calls when none is supplied.
    _CODEX_DEFAULT_MODEL = "gpt-5.2-codex"

    # Approximate context window per model. Codex doesn't surface this in
    # its events the way CC does (modelUsage.contextWindow); we use a
    # static map. Conservative defaults so the 80% gauge fires earlier
    # rather than too late on an unknown model.
    _CODEX_CONTEXT_WINDOW = {
        "gpt-5.2-codex": 400_000,
        "gpt-5.3-codex": 400_000,
        "gpt-5.4": 1_000_000,
        "gpt-5.5": 1_000_000,
        "o3": 200_000,
        "o4-mini": 200_000,
    }
    _CODEX_CONTEXT_WINDOW_DEFAULT = 200_000

    def _codex_workdir(self, user_id: str, conv_id: str, agent_name: str) -> str:
        """Per-conv working dir on the host — mounted into the container as
        `/cc_sessions/<conv>/<agent>` after the unshare bind."""
        if not user_id or not conv_id:
            raise ValueError("user_id + conversation_id required for codex provider")
        import core.paths as _paths
        base = _paths.CLAUDE_SESSIONS_DIR  # shared with CC — same /cc_sessions root
        agent = agent_name or "default"
        wd = base / user_id / conv_id / agent
        wd.mkdir(parents=True, exist_ok=True)
        # The unshare-bind layout requires <conv>/<agent> visible at
        # /cc_sessions/<conv>/<agent> from inside the container, and the
        # codex_pool's session_dir contract is exactly that path.
        return f"/cc_sessions/{user_id}/{conv_id}/{agent}"

    def _codex_resolve_session_id(self, conv_id: str, agent_name: str) -> str:
        """Resume support: load the codex thread_id pinned for this conv/agent."""
        try:
            from core.conversation_store import ConversationStore
            return ConversationStore.instance().get_extra(
                conv_id, f"codex_session:{agent_name or 'default'}") or ""
        except Exception:
            return ""

    def _codex_persist_session_id(self, conv_id: str, agent_name: str, sid: str):
        try:
            from core.conversation_store import ConversationStore
            ConversationStore.instance().set_extra(
                conv_id, f"codex_session:{agent_name or 'default'}", sid)
        except Exception:
            logger.debug("codex session_id persist failed", exc_info=True)

    def _codex_setup_auth_and_config(self, host_workdir: str,
                                       user_id: str, conv_id: str,
                                       service_id: str = "") -> Dict:
        """Materialise ~/.codex/auth.json + ~/.codex/config.toml inside
        the per-conv slot before launching codex.

        Returns {"openai_api_key": ..., "used_oauth": bool, "pool_index": int}
        for caller-side env injection.
        """
        import core.paths as _paths
        from core.llm_providers import codex_session as _cs

        # Map host_workdir (/cc_sessions/<u>/<c>/<a>) back to the host path.
        host_root = Path(str(_paths.CLAUDE_SESSIONS_DIR.resolve()))
        rel = host_workdir.lstrip("/").split("/")
        if len(rel) < 4 or rel[0] != "cc_sessions":
            raise ValueError(f"unexpected codex workdir layout: {host_workdir!r}")
        host_dir = host_root / rel[1] / rel[2] / rel[3]
        codex_home = host_dir / ".codex"
        codex_home.mkdir(parents=True, exist_ok=True)

        # Pick a credential from the pool (round-robin). Refresh if expired.
        pool = _cs._load_credentials_pool(service_id)
        used_oauth = False
        api_key = ""
        pool_index = -1
        if pool:
            now_ms = int(time.time() * 1000)
            valid = [(i, c) for i, c in enumerate(pool) if c.get("expires_at", 0) > now_ms]
            if not valid:
                # All expired — try the most recent and refresh on the way in.
                valid = [(len(pool) - 1, pool[-1])]
            pool_index, cred = valid[0]
            access_token = cred.get("access_token", "")
            refresh_token = cred.get("refresh_token", "")
            expires_at = cred.get("expires_at", 0)
            account = cred.get("account", "")
            # Refresh if within 60s of expiry (or already past).
            if expires_at < now_ms + 60_000 and refresh_token:
                try:
                    new = _cs.refresh_oauth_token(refresh_token)
                    access_token = new["access_token"]
                    refresh_token = new["refresh_token"]
                    expires_at = new["expires_at"]
                    _cs.add_credential_to_pool(
                        access_token, refresh_token, expires_at,
                        account=account, service_id=service_id)
                    logger.info("[codex] refreshed pool[%d]", pool_index)
                except Exception as e:
                    logger.warning("[codex] refresh failed: %s — trying access_token as-is", e)
            # Write auth.json in the codex format.
            auth_blob = {
                "OPENAI_API_KEY": "",
                "tokens": {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "id_token": "",
                    "account_id": account,
                },
                "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            (codex_home / "auth.json").write_text(
                json.dumps(auth_blob, ensure_ascii=False), encoding="utf-8")
            os.chmod(codex_home / "auth.json", 0o600)
            used_oauth = True
        else:
            # Fall back to the service config api_key (CODEX_API_KEY env).
            api_key = self.config.get("api_key", "") if hasattr(self, "config") else ""

        # config.toml: disable codex's own auto-compact (we drive it from
        # the agent loop via PawFlowCodexCompactRequired) + register the
        # PawFlow MCP bridge so codex can call our tools.
        from core.docker_utils import get_host_ip
        host_ip = get_host_ip()
        relay_url = self.config.get("tool_relay_url", "") or f"wss://{host_ip}:9090/ws/tools/_tool_relay"
        relay_token = self.config.get("tool_relay_token", "") or os.environ.get("PAWFLOW_TOOL_RELAY_TOKEN", "")
        internal_token = os.environ.get("PAWFLOW_INTERNAL_TOKEN", "")
        config_toml = (
            f'# Auto-generated by PawFlow — do not edit by hand.\n'
            f'model = "{self._codex_default_model_for_pool()}"\n'
            f'# Disable codex auto-compact — PawFlow tracks usage and triggers\n'
            f'# its own bucket compact at {int(_PAWFLOW_COMPACT_THRESHOLD * 100)}% (codex emits no boundary event).\n'
            f'model_auto_compact_token_limit = 999999999\n'
            f'\n'
            f'[mcp_servers.pawflow]\n'
            f'command = "python3"\n'
            f'args = ["/opt/pawflow/mcp_bridge.py"]\n'
            f'startup_timeout_sec = 20\n'
            f'tool_timeout_sec = 300\n'
            f'enabled = true\n'
            f'required = true\n'
            f'\n'
            f'[mcp_servers.pawflow.env]\n'
            f'PAWFLOW_TOOL_RELAY_URL = "{relay_url}"\n'
            f'PAWFLOW_TOOL_RELAY_TOKEN = "{relay_token}"\n'
            f'PAWFLOW_INTERNAL_TOKEN = "{internal_token}"\n'
            f'PAWFLOW_USER_ID = "{user_id}"\n'
            f'PAWFLOW_CONVERSATION_ID = "{conv_id}"\n'
            f'PAWFLOW_AGENT_NAME = "{getattr(self, "_agent_name", "") or ""}"\n'
        )
        (codex_home / "config.toml").write_text(config_toml, encoding="utf-8")
        os.chmod(codex_home / "config.toml", 0o600)
        return {"openai_api_key": api_key, "used_oauth": used_oauth, "pool_index": pool_index}

    def _codex_default_model_for_pool(self) -> str:
        try:
            return self.default_model or self._CODEX_DEFAULT_MODEL
        except AttributeError:
            return self._CODEX_DEFAULT_MODEL

    def _codex_context_window(self, model: str) -> int:
        return self._CODEX_CONTEXT_WINDOW.get(model or "", self._CODEX_CONTEXT_WINDOW_DEFAULT)

    def _stream_codex(self, messages, model, temperature, max_tokens,
                       tools=None,
                       callback=None,
                       *,
                       call_user_id: str = "",
                       call_conversation_id: str = "",
                       call_agent_name: str = "",
                       call_event_cid: str = "",
                       call_ephemeral_stream: bool = False,
                       thinking_callback=None,
                       turn_callback=None,
                       block_callback=None,
                       **_ignored) -> "LLMResponse":
        """Run a single codex turn and return the LLMResponse.

        Tools are NOT inlined in the prompt: codex talks MCP, and the
        PawFlow MCP bridge is wired in config.toml so codex calls our
        tools live during the turn (results land in mcp_tool_call items).
        """
        from core.llm_client import LLMResponse, LLMClientError
        from core.codex_pool import CodexPool

        user_id = call_user_id or getattr(self, "_user_id", "") or ""
        conv_id = call_conversation_id or getattr(self, "_conversation_id", "") or ""
        agent_name = call_agent_name or getattr(self, "_agent_name", "") or "default"
        if not user_id or not conv_id:
            raise LLMClientError("codex provider requires call_user_id and call_conversation_id")

        host_workdir = self._codex_workdir(user_id, conv_id, agent_name)
        service_id = getattr(self, "_agent_service", "") or ""
        try:
            auth_meta = self._codex_setup_auth_and_config(
                host_workdir, user_id, conv_id, service_id=service_id)
        except Exception as e:
            raise LLMClientError(f"codex auth/config setup failed: {e}")

        # Build the prompt: codex `exec` takes a single text payload (or
        # `-` for stdin). We funnel the full message history through the
        # shared CLI serializer so multi-turn context is preserved across
        # spawns; codex doesn't keep an in-process session like CC does.
        system_prompt, user_text = self._serialize_messages_for_cli(messages, tools=None)
        if system_prompt:
            prompt_payload = f"<system>\n{system_prompt}\n</system>\n\n{user_text}"
        else:
            prompt_payload = user_text or ""

        # Resume support: pass the previous thread_id if we have one.
        existing_sid = self._codex_resolve_session_id(conv_id, agent_name)
        codex_args: List[str] = ["exec"]
        if existing_sid:
            codex_args.extend(["resume", existing_sid])
        codex_args.extend([
            "--json",
            "--sandbox", "danger-full-access",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--model", model or self._codex_default_model_for_pool(),
            "-",  # read prompt from stdin so we can pipe arbitrarily long inputs
        ])

        extra_env = {}
        if auth_meta.get("openai_api_key"):
            extra_env["CODEX_API_KEY"] = auth_meta["openai_api_key"]
            extra_env["OPENAI_API_KEY"] = auth_meta["openai_api_key"]

        pool = CodexPool.instance()
        container = pool.acquire()
        proc = None
        try:
            proc = pool.exec_codex(
                container, host_workdir, codex_args,
                extra_env=extra_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            try:
                proc.stdin.write(prompt_payload + "\n")
                proc.stdin.close()
            except Exception as e:
                logger.warning("[codex] failed to write prompt to stdin: %s", e)

            return self._consume_codex_stream(
                proc, model=model or self._codex_default_model_for_pool(),
                conv_id=conv_id, agent_name=agent_name,
                callback=callback, thinking_callback=thinking_callback,
                turn_callback=turn_callback, block_callback=block_callback,
            )
        finally:
            if proc and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                pool.release(container)
            except Exception:
                logger.debug("codex container release failed", exc_info=True)

    def _consume_codex_stream(self, proc, *,
                                 model: str, conv_id: str, agent_name: str,
                                 callback, thinking_callback,
                                 turn_callback, block_callback) -> "LLMResponse":
        """Read the JSONL event stream until `turn.completed` / EOF."""
        from core.llm_client import LLMResponse, LLMClientError

        text_chunks: List[str] = []
        thinking_chunks: List[str] = []
        usage: Dict = {}
        thread_id = ""
        turn_count = 0
        last_error: Optional[str] = None
        finish_reason = "stop"

        ctx_window = self._codex_context_window(model)
        compact_required = False

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("[codex] non-json line skipped: %r", line[:200])
                continue
            etype = event.get("type", "")
            if etype == "thread.started":
                thread_id = event.get("thread_id", "") or thread_id
            elif etype == "turn.started":
                turn_count += 1
            elif etype in ("item.completed", "item.updated"):
                item = event.get("item", {}) or {}
                itype = item.get("type", "")
                if itype == "agent_message":
                    chunk = item.get("text", "") or ""
                    if chunk and chunk not in text_chunks:
                        text_chunks.append(chunk)
                        if callback:
                            try:
                                callback(chunk)
                            except Exception:
                                pass
                elif itype == "reasoning":
                    chunk = item.get("text", "") or ""
                    if chunk and chunk not in thinking_chunks:
                        thinking_chunks.append(chunk)
                        if thinking_callback:
                            try:
                                thinking_callback(chunk)
                            except Exception:
                                pass
                # command_execution / mcp_tool_call / file_change /
                # web_search / plan_update items — codex executes them
                # itself via MCP. We don't surface them as tool_calls in
                # LLMResponse because the agent loop must NOT re-run them.
                elif itype == "mcp_tool_call" and block_callback:
                    try:
                        block_callback({
                            "type": "tool_call",
                            "name": item.get("tool_name", "") or item.get("server", ""),
                            "arguments": item.get("arguments", {}),
                        })
                    except Exception:
                        pass
            elif etype == "turn.completed":
                _u = event.get("usage", {}) or {}
                # codex returns input_tokens that EXCLUDES cached_input_tokens
                # AFAIK — but to mirror CC's gauge we sum input+cached for the
                # "prompt size" comparison.
                used = (int(_u.get("input_tokens", 0) or 0)
                        + int(_u.get("cached_input_tokens", 0) or 0))
                usage["input_tokens"] = int(_u.get("input_tokens", 0) or 0)
                usage["cached_input_tokens"] = int(_u.get("cached_input_tokens", 0) or 0)
                usage["output_tokens"] = int(_u.get("output_tokens", 0) or 0)
                usage["_total_used"] = used
                if ctx_window > 0 and used >= int(ctx_window * _PAWFLOW_COMPACT_THRESHOLD):
                    logger.warning(
                        "[codex] usage %d/%d crossed PawFlow compact threshold (%d%%)",
                        used, ctx_window, int(_PAWFLOW_COMPACT_THRESHOLD * 100))
                    compact_required = True
                if turn_callback:
                    try:
                        turn_callback({"usage": usage, "model": model})
                    except Exception:
                        pass
            elif etype == "turn.failed":
                last_error = json.dumps(event.get("error", event))[:500]
                finish_reason = "error"
            elif etype == "error":
                last_error = json.dumps(event.get("error", event))[:500]
                finish_reason = "error"

        rc = proc.wait()
        if rc != 0 and not text_chunks and not last_error:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise LLMClientError(f"codex exited rc={rc}: {stderr[:500]}")

        if thread_id:
            self._codex_persist_session_id(conv_id, agent_name, thread_id)

        response = LLMResponse(
            content="\n".join(text_chunks).strip(),
            tokens_in=usage.get("input_tokens", 0) + usage.get("cached_input_tokens", 0),
            tokens_out=usage.get("output_tokens", 0),
            finish_reason=finish_reason,
            model=model,
        )
        if last_error:
            response.content = (response.content + ("\n\n" if response.content else "")
                                + f"[codex error: {last_error}]").strip()

        if compact_required:
            raise PawFlowCodexCompactRequired(
                used=usage.get("_total_used", 0),
                window=ctx_window, conv_id=conv_id)
        return response
