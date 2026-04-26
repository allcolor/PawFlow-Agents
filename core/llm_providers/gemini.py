"""LLM provider mixin -- Gemini CLI (`gemini -p ... --output-format stream-json`).

Compact MVP, mirror of LLMCodexMixin but with Google's flavour:
- writes ~/.gemini/oauth_creds.json + ~/.gemini/settings.json (with the
  pawflow MCP server) under the per-conv slot before launching gemini;
- reads the NDJSON event stream documented in gemini-cli/docs/cli/headless
  (init / message / tool_use / tool_result / error / result);
- raises PawFlowGeminiCompactRequired at 80% usage so the agent loop can
  trigger PawFlow's bucket compact (gemini emits no compaction event in
  the stream-json output — ChatCompressionService runs internally).

Not mutualised with codex/CC mixins by design — see memory "Separate
pools per CLI".
"""

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


_PAWFLOW_COMPACT_THRESHOLD = 0.80


class PawFlowGeminiCompactRequired(Exception):
    """Signal: gemini usage crossed the PawFlow compact threshold."""
    def __init__(self, used: int, window: int, conv_id: str = ""):
        super().__init__(
            f"gemini usage {used}/{window} >= {int(_PAWFLOW_COMPACT_THRESHOLD * 100)}% — PawFlow compact")
        self.used = used
        self.window = window
        self.conv_id = conv_id


class LLMGeminiMixin:
    """Gemini CLI provider — `gemini -p "<prompt>" --output-format stream-json`.

    Stream events (per gemini-cli docs):
      init{session_id, model}
      message{role, content}
      tool_use{name, args}
      tool_result{output}
      error{...}
      result{stats: {...}, usage: {...}}

    Tools route through the MCP bridge declared in ~/.gemini/settings.json.
    """

    _GEMINI_DEFAULT_MODEL = "gemini-2.5-pro"

    _GEMINI_CONTEXT_WINDOW = {
        "gemini-2.5-pro": 1_000_000,
        "gemini-2.5-flash": 1_000_000,
        "gemini-1.5-pro": 2_000_000,
        "gemini-1.5-flash": 1_000_000,
    }
    _GEMINI_CONTEXT_WINDOW_DEFAULT = 1_000_000

    def _gemini_workdir(self, user_id: str, conv_id: str, agent_name: str) -> str:
        if not user_id or not conv_id:
            raise ValueError("user_id + conversation_id required for gemini provider")
        import core.paths as _paths
        base = _paths.CLAUDE_SESSIONS_DIR  # shared /cc_sessions root
        agent = agent_name or "default"
        wd = base / user_id / conv_id / agent
        wd.mkdir(parents=True, exist_ok=True)
        return f"/cc_sessions/{user_id}/{conv_id}/{agent}"

    def _gemini_resolve_session_id(self, conv_id: str, agent_name: str) -> str:
        try:
            from core.conversation_store import ConversationStore
            return ConversationStore.instance().get_extra(
                conv_id, f"gemini_session:{agent_name or 'default'}") or ""
        except Exception:
            return ""

    def _gemini_persist_session_id(self, conv_id: str, agent_name: str, sid: str):
        try:
            from core.conversation_store import ConversationStore
            ConversationStore.instance().set_extra(
                conv_id, f"gemini_session:{agent_name or 'default'}", sid)
        except Exception:
            logger.debug("gemini session_id persist failed", exc_info=True)

    def _gemini_setup_auth_and_settings(self, host_workdir: str,
                                            user_id: str, conv_id: str,
                                            service_id: str = "") -> Dict:
        """Materialise ~/.gemini/oauth_creds.json + settings.json under the
        per-conv slot before launching gemini. Disables ChatCompressionService
        auto-trigger by setting contextPercentageThreshold high — PawFlow
        compacts via PawFlowGeminiCompactRequired instead.
        """
        import core.paths as _paths
        from core.llm_providers import gemini_session as _gs

        host_root = Path(str(_paths.CLAUDE_SESSIONS_DIR.resolve()))
        rel = host_workdir.lstrip("/").split("/")
        if len(rel) < 4 or rel[0] != "cc_sessions":
            raise ValueError(f"unexpected gemini workdir layout: {host_workdir!r}")
        host_dir = host_root / rel[1] / rel[2] / rel[3]
        gemini_home = host_dir / ".gemini"
        gemini_home.mkdir(parents=True, exist_ok=True)

        pool = _gs._load_credentials_pool(service_id)
        used_oauth = False
        api_key = ""
        pool_index = -1
        if pool:
            now_ms = int(time.time() * 1000)
            valid = [(i, c) for i, c in enumerate(pool) if c.get("expires_at", 0) > now_ms]
            if not valid:
                valid = [(len(pool) - 1, pool[-1])]
            pool_index, cred = valid[0]
            access_token = cred.get("access_token", "")
            refresh_token = cred.get("refresh_token", "")
            expires_at = cred.get("expires_at", 0)
            account = cred.get("account", "")
            if expires_at < now_ms + 60_000 and refresh_token:
                try:
                    new = _gs.refresh_oauth_token(refresh_token)
                    access_token = new["access_token"]
                    refresh_token = new["refresh_token"]
                    expires_at = new["expires_at"]
                    _gs.add_credential_to_pool(
                        access_token, refresh_token, expires_at,
                        account=account, service_id=service_id)
                    logger.info("[gemini] refreshed pool[%d]", pool_index)
                except Exception as e:
                    logger.warning("[gemini] refresh failed: %s — trying access_token as-is", e)
            creds_blob = {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expiry_date": int(expires_at),
                "token_type": "Bearer",
                "scope": "https://www.googleapis.com/auth/cloud-platform",
            }
            (gemini_home / "oauth_creds.json").write_text(
                json.dumps(creds_blob, ensure_ascii=False), encoding="utf-8")
            os.chmod(gemini_home / "oauth_creds.json", 0o600)
            if account:
                (gemini_home / "google_accounts.json").write_text(
                    json.dumps({account: {}}, ensure_ascii=False), encoding="utf-8")
            used_oauth = True
        else:
            api_key = self.config.get("api_key", "") if hasattr(self, "config") else ""

        from core.docker_utils import get_host_ip
        host_ip = get_host_ip()
        relay_url = self.config.get("tool_relay_url", "") or f"wss://{host_ip}:9090/ws/tools/_tool_relay"
        relay_token = self.config.get("tool_relay_token", "") or os.environ.get("PAWFLOW_TOOL_RELAY_TOKEN", "")
        internal_token = os.environ.get("PAWFLOW_INTERNAL_TOKEN", "")

        settings = {
            "theme": "Default",
            "selectedAuthType": "oauth-personal" if used_oauth else "api-key",
            "general": {
                "sessionRetention": {"enabled": True, "maxAge": "30d"},
            },
            "model": {
                # Disable ChatCompressionService auto-trigger — PawFlow drives it.
                "chatCompression": {"contextPercentageThreshold": 0.99},
                "maxSessionTurns": -1,
            },
            "mcpServers": {
                "pawflow": {
                    "command": "python3",
                    "args": ["/opt/pawflow/mcp_bridge.py"],
                    "env": {
                        "PAWFLOW_TOOL_RELAY_URL": relay_url,
                        "PAWFLOW_TOOL_RELAY_TOKEN": relay_token,
                        "PAWFLOW_INTERNAL_TOKEN": internal_token,
                        "PAWFLOW_USER_ID": user_id,
                        "PAWFLOW_CONVERSATION_ID": conv_id,
                        "PAWFLOW_AGENT_NAME": getattr(self, "_agent_name", "") or "",
                    },
                    "timeout": 30000,
                },
            },
        }
        (gemini_home / "settings.json").write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(gemini_home / "settings.json", 0o600)
        return {"gemini_api_key": api_key, "used_oauth": used_oauth, "pool_index": pool_index}

    def _gemini_default_model_for_pool(self) -> str:
        try:
            return self.default_model or self._GEMINI_DEFAULT_MODEL
        except AttributeError:
            return self._GEMINI_DEFAULT_MODEL

    def _gemini_context_window(self, model: str) -> int:
        return self._GEMINI_CONTEXT_WINDOW.get(model or "", self._GEMINI_CONTEXT_WINDOW_DEFAULT)

    def _stream_gemini(self, messages, model, temperature, max_tokens,
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
        from core.llm_client import LLMResponse, LLMClientError
        from core.gemini_pool import GeminiPool

        user_id = call_user_id or getattr(self, "_user_id", "") or ""
        conv_id = call_conversation_id or getattr(self, "_conversation_id", "") or ""
        agent_name = call_agent_name or getattr(self, "_agent_name", "") or "default"
        if not user_id or not conv_id:
            raise LLMClientError("gemini provider requires call_user_id and call_conversation_id")

        host_workdir = self._gemini_workdir(user_id, conv_id, agent_name)
        service_id = getattr(self, "_agent_service", "") or ""
        try:
            auth_meta = self._gemini_setup_auth_and_settings(
                host_workdir, user_id, conv_id, service_id=service_id)
        except Exception as e:
            raise LLMClientError(f"gemini auth/settings setup failed: {e}")

        system_prompt, user_text = self._serialize_messages_for_cli(messages, tools=None)
        if system_prompt:
            prompt_payload = f"<system>\n{system_prompt}\n</system>\n\n{user_text}"
        else:
            prompt_payload = user_text or ""

        existing_sid = self._gemini_resolve_session_id(conv_id, agent_name)
        gemini_args: List[str] = [
            "--output-format", "stream-json",
            "--yolo",  # auto-approve all tool calls (equivalent of CC --dangerously-skip-permissions)
            "--skip-trust",  # don't prompt to trust the workspace
            "--model", model or self._gemini_default_model_for_pool(),
        ]
        if existing_sid:
            gemini_args.extend(["--resume", existing_sid])
        # `-p -` is not documented; gemini takes the prompt as a positional
        # arg or via `-p "text"`. We feed the entire prompt as a single -p
        # value (gemini reads it whole, no length limit observed in docs).
        gemini_args.extend(["-p", prompt_payload])

        extra_env = {}
        if auth_meta.get("gemini_api_key"):
            extra_env["GEMINI_API_KEY"] = auth_meta["gemini_api_key"]

        pool = GeminiPool.instance()
        container = pool.acquire()
        proc = None
        try:
            proc = pool.exec_gemini(
                container, host_workdir, gemini_args,
                extra_env=extra_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            return self._consume_gemini_stream(
                proc, model=model or self._gemini_default_model_for_pool(),
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
                logger.debug("gemini container release failed", exc_info=True)

    def _consume_gemini_stream(self, proc, *,
                                  model: str, conv_id: str, agent_name: str,
                                  callback, thinking_callback,
                                  turn_callback, block_callback) -> "LLMResponse":
        from core.llm_client import LLMResponse, LLMClientError

        text_chunks: List[str] = []
        thinking_chunks: List[str] = []
        usage: Dict = {}
        session_id = ""
        last_error: Optional[str] = None
        finish_reason = "stop"

        ctx_window = self._gemini_context_window(model)
        compact_required = False

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("[gemini] non-json line skipped: %r", line[:200])
                continue
            etype = event.get("type", "")
            if etype == "init":
                session_id = event.get("session_id", "") or session_id
            elif etype == "message":
                role = event.get("role", "assistant")
                if role == "assistant":
                    chunk = event.get("content", "") or ""
                    if chunk:
                        text_chunks.append(chunk)
                        if callback:
                            try:
                                callback(chunk)
                            except Exception:
                                pass
            elif etype == "thought":
                chunk = event.get("content", "") or ""
                if chunk:
                    thinking_chunks.append(chunk)
                    if thinking_callback:
                        try:
                            thinking_callback(chunk)
                        except Exception:
                            pass
            elif etype == "tool_use" and block_callback:
                try:
                    block_callback({
                        "type": "tool_call",
                        "name": event.get("name", ""),
                        "arguments": event.get("args", {}),
                    })
                except Exception:
                    pass
            elif etype == "error":
                last_error = json.dumps(event)[:500]
                finish_reason = "error"
            elif etype == "result":
                _stats = event.get("stats", {}) or {}
                _u = _stats.get("usage", {}) or event.get("usage", {}) or {}
                # Gemini may break usage down per-model; sum if so.
                if isinstance(_u, dict) and any(
                        isinstance(v, dict) for v in _u.values()):
                    sum_in = sum(int((v or {}).get("input_tokens", 0))
                                 for v in _u.values() if isinstance(v, dict))
                    sum_out = sum(int((v or {}).get("output_tokens", 0))
                                  for v in _u.values() if isinstance(v, dict))
                    sum_cached = sum(int((v or {}).get("cached_input_tokens", 0))
                                     for v in _u.values() if isinstance(v, dict))
                else:
                    sum_in = int(_u.get("input_tokens", 0) or 0)
                    sum_out = int(_u.get("output_tokens", 0) or 0)
                    sum_cached = int(_u.get("cached_input_tokens", 0) or 0)
                used = sum_in + sum_cached
                usage["input_tokens"] = sum_in
                usage["cached_input_tokens"] = sum_cached
                usage["output_tokens"] = sum_out
                usage["_total_used"] = used
                if ctx_window > 0 and used >= int(ctx_window * _PAWFLOW_COMPACT_THRESHOLD):
                    logger.warning(
                        "[gemini] usage %d/%d crossed PawFlow compact threshold (%d%%)",
                        used, ctx_window, int(_PAWFLOW_COMPACT_THRESHOLD * 100))
                    compact_required = True
                if turn_callback:
                    try:
                        turn_callback({"usage": usage, "model": model})
                    except Exception:
                        pass

        rc = proc.wait()
        if rc != 0 and not text_chunks and not last_error:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise LLMClientError(f"gemini exited rc={rc}: {stderr[:500]}")

        if session_id:
            self._gemini_persist_session_id(conv_id, agent_name, session_id)

        response = LLMResponse(
            content="".join(text_chunks).strip(),
            tokens_in=usage.get("input_tokens", 0) + usage.get("cached_input_tokens", 0),
            tokens_out=usage.get("output_tokens", 0),
            finish_reason=finish_reason,
            model=model,
        )
        if last_error:
            response.content = (response.content + ("\n\n" if response.content else "")
                                + f"[gemini error: {last_error}]").strip()

        if compact_required:
            raise PawFlowGeminiCompactRequired(
                used=usage.get("_total_used", 0),
                window=ctx_window, conv_id=conv_id)
        return response
