"""LLM provider mixin -- Claude Code CLI (subprocess-based).

Bidirectional stream-json. The streaming turn machinery lives in the
_cc_stream* sub-mixins (<=800-line split); this module keeps the process
lifecycle + session helpers and recombines everything via MRO.
"""

import json  # noqa: F401
import logging
import os  # noqa: F401
import subprocess  # nosec B404
import time  # noqa: F401
from typing import Dict, List, Optional  # noqa: F401

from core.agent_prompt_policy import append_cli_mcp_system_prompt  # noqa: F401
from core.cc_live_registry import CCLiveSession, LiveSessionRegistry  # noqa: F401
from core.interrupt_policy import SOFT_INTERRUPT_USER_COMMAND
from core.llm_providers.claude_code_session import (
    ClaudeCodeSessionMixin, _get_sessions_base, recover_tokens_from_workdir)  # noqa: F401
from core.llm_providers._cc_base import (  # noqa: F401
    _CC_READER_EOF, _CC401Retry, _CCStreamState)
from core.llm_providers._cc_io import _CCIoMixin
from core.llm_providers._cc_stream import _CCStreamMixin
from core.llm_providers._cc_stream_loop import _CCStreamLoopMixin
from core.llm_providers._cc_stream_turn import _CCStreamTurnMixin
from core.llm_providers._cc_stream_result import _CCStreamResultMixin

logger = logging.getLogger(__name__)


class LLMClaudeCodeMixin(
        _CCStreamMixin, _CCStreamLoopMixin, _CCStreamTurnMixin,
        _CCStreamResultMixin, _CCIoMixin, ClaudeCodeSessionMixin):
    """Claude Code CLI provider using bidirectional stream-json.

    Claude Code uses MCP natively for tool access. PawFlow tools are
    exposed via the MCP bridge (tools/mcp_bridge.py) configured with
    --mcp-config.

    Each session runs in its own working directory under
    data/claude_sessions/<conversation_id>/<agent_name>/ so Claude Code
    doesn't read/modify the PawFlow server code.

    stdin (stream-json input):
        {"type": "user", "content": "initial prompt"}
        {"type": "user", "content": "follow-up while working"}  # preempt

    stdout (stream-json output):
        {"type": "system", "subtype": "init", ...}
        {"type": "assistant", "message": {...}}
        {"type": "user", "message": {...}}  # tool results
        {"type": "result", ...}
    """

    # Session/workdir methods inherited from ClaudeCodeSessionMixin:
    # _get_session_workdir, _claude_code_env, _setup_credentials,
    # _recover_tokens, _setup_mcp_config, _build_claude_cmd,
    # _get_tool_relay_info, _DISALLOWED_BUILTIN_TOOLS


    @staticmethod
    def _cc_namespace_workdir(workdir: str) -> str:
        """Return the session path as seen inside claude_code_pool's namespace."""
        rel = os.path.relpath(workdir, _get_sessions_base()).replace("\\", "/")
        parts = [part for part in rel.split("/") if part]
        if len(parts) < 3:
            raise ValueError(f"invalid Claude Code workdir layout: {workdir}")
        return "/cc_sessions/" + "/".join(parts[1:])

    # ── Process management ──────────────────────────────────────────

    def _cc_send_user_message(self, text: str, attachments: list = None, *,
                               user_id: str = "", conversation_id: str = "",
                               agent_name: str = ""):
        """Send a user message to the running Claude Code subprocess (preempt).

        Uses stream-json input format to inject a new user message while
        Claude Code is working — enables the same preempt behavior as
        other LLM providers.

        Args:
            text: User message text.
            attachments: Optional list of attachment dicts with base64 image data.

        Sets _preempt_pending so _stream_claude_code knows to NOT break
        at the next result event — it must wait for the preempt's result too.
        """
        # During PawFlow's sentinel sessions (_compact, _memory_extract, ...)
        # the SAME client instance is repurposed: _conversation_id is set to
        # the sentinel name and _claude_proc points to a fresh subprocess
        # spawned for that one-shot job. Writing a preempt to that stdin
        # would land in the wrong stream. Refuse so the caller routes via
        # the PendingQueue path.
        # Prefer the explicit per-call identity (the dispatch passes the
        # active conversation/agent/user). self._conversation_id /
        # self._agent_name on the shared LLMClient singleton are clobbered by
        # concurrent background streams (memory-extract / compact / sub-agent)
        # — the same footgun the _stream_claude_code call_* kwargs fix exists
        # for. self.* is only a fallback when the caller didn't pass them.
        _conv = conversation_id or getattr(self, '_conversation_id', '') or ''
        _agent = agent_name or getattr(self, '_agent_name', '') or ''
        _uid = user_id or getattr(self, '_user_id', '') or ''
        _svc = getattr(self, '_agent_service', '') or ''
        if _conv.startswith('_'):
            logger.info("Preempt arrived during sentinel '%s' — refusing send: %.100s",
                        _conv, text)
            return False
        # Compact-in-progress: the reader thread has already (or is about to)
        # kill the CC subprocess. Refuse immediately so the caller routes via
        # the PendingQueue path — writing to a dying stdin would land the
        # message in _inflight_preempts with a narrow race where the rescue
        # handler may have already cleared the list.
        if getattr(self, '_compacting', False):
            logger.info("Preempt arrived during CC compact — refusing send: %.100s", text)
            return False
        # Resolve the target proc from the live registry (source of truth)
        # instead of self._claude_proc, which is singleton state clobbered by
        # concurrent streams — writing to a stale/wrong proc would corrupt
        # another conversation's turn. The registry pins the live session by
        # (user, conv, agent, service); fall back to self._claude_proc only
        # when none is registered (cold-start first turn, which has no prior
        # background stream to have clobbered it).
        proc = None
        try:
            from core.cc_live_registry import LiveSessionRegistry
            _live = LiveSessionRegistry.instance().find_for_agent(
                _uid, _conv, _agent, _svc)
            if _live is not None and _live.is_alive():
                proc = _live.proc
        except Exception:
            logger.debug("live-registry preempt lookup failed", exc_info=True)
        if proc is None:
            proc = getattr(self, '_claude_proc', None)
        if not proc or proc.poll() is not None:
            logger.warning("No running Claude Code process to send message to")
            return False
        # If CC has already emitted its final result, sending via stdin is
        # racy — CC may be tearing down. Refuse so the caller routes via
        # PendingQueue.
        if getattr(self, '_result_emitted', False):
            logger.info("Preempt arrived after CC result — refusing send: %.100s", text)
            return False
        # Capture user-supplied text BEFORE catchup-prefix mutation; the
        # original (or its non-empty suffix) is what we'll match against
        # CC's session jsonl in _check_preempt_in_jsonl.
        _original_text = text
        try:
            # Multi-agent catch-up: inject messages from other agents before user msg
            conv_id = _conv
            agent_name = _agent
            if conv_id and agent_name:
                catchup = self._build_catchup_context(conv_id, agent_name)
                if catchup:
                    text = catchup + "\n\n" + text

            # Build content: text + optional images
            if attachments:
                content = []
                if text:
                    content.append({"type": "text", "text": text})
                for att in attachments:
                    if isinstance(att, dict) and att.get("data"):
                        mime = att.get("mime_type", "image/png")
                        content.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime,
                                "data": att["data"],
                            },
                        })
            else:
                content = text

            msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": content},
            })
            proc.stdin.write(msg + "\n")
            proc.stdin.flush()
            self._preempt_pending = getattr(self, '_preempt_pending', 0) + 1
            # Remember the EXACT text we sent so the result-time jsonl
            # check can locate it. Skip the multi-agent catchup prefix
            # (built locally above) — only the user-supplied tail is
            # written verbatim by CC. Use the original `text` parameter
            # before catchup mutation for a clean match key.
            try:
                self._sent_preempt_texts.append(_original_text)
            except AttributeError:
                self._sent_preempt_texts = [_original_text]
            logger.info("Sent preempt message to Claude Code (pending=%d): %.100s",
                        self._preempt_pending, text)
            return True
        except (OSError, BrokenPipeError) as e:
            logger.warning("Failed to send to Claude Code stdin: %s", e)
            return False

    def cancel_claude_code(self, force: bool = False):
        """Cancel Claude Code subprocess.

        force=False: graceful interrupt on stdin — Claude Code acknowledges
                     and responds with a summary, then the stream loop exits
                     normally on the result event. No kill.
        force=True: kill immediately.
        """
        proc = getattr(self, '_claude_proc', None)
        if not proc or proc.poll() is not None:
            return

        if force:
            logger.info("FORCE KILLING Claude Code subprocess (pid=%d)", proc.pid)
            self._claude_proc = None
            # Kill the container-side claude CLI by its captured PID
            # (from the shell wrapper's __PF_CLAUDE_PID=$$ preamble) AND
            # the host-side docker exec wrapper. Without the container-side
            # kill, the CLI becomes an orphan (reparented to PID 1) and
            # keeps emitting tool calls / running auto-compact / writing
            # to its session .jsonl.
            self._kill_cc_hard(proc)
            # Pool mode: release the slot (the container stays up for
            # other sessions; _kill_cc_hard only killed the CLI inside it).
            _pool_name = getattr(self, '_pool_container_name', None)
            if _pool_name:
                from core.claude_code_pool import ClaudeCodePool
                ClaudeCodePool.instance().release(_pool_name)
                self._pool_container_name = None
            self._current_session_id = ""
            return

        # Graceful interrupt: send interrupt message on stdin.
        # Claude Code will stop what it's doing, summarize, and send
        # a result event — the stream loop exits normally.
        logger.info("Interrupting Claude Code subprocess (pid=%d)", proc.pid)
        try:
            if proc.stdin and not proc.stdin.closed:
                msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user",
                                "content": SOFT_INTERRUPT_USER_COMMAND},
                })
                proc.stdin.write(msg + "\n")
                proc.stdin.flush()
        except (OSError, BrokenPipeError):
            pass

    def _kill_cc_hard(self, proc) -> None:
        """Kill the claude-code subprocess on BOTH the host and inside
        the pool container, deterministically by PID.

        `proc.kill()` only reaps the host-side `docker exec` wrapper.
        Without a container-side kill, the claude CLI becomes an orphan
        (reparented to PID 1 inside the container) and keeps running —
        emitting tool calls via MCP, running its own auto-compact,
        writing to its session .jsonl — while PawFlow spawns a fresh
        session in the SAME pool container, creating zombie races on
        the same files.

        The container-side PID is captured at spawn from the shell
        wrapper's `__PF_CLAUDE_PID=$$` stderr preamble (see
        `ClaudeCodePool.exec_claude`). Bash chain-execs into
        setpriv → claude, so $$ stays constant across the three
        processes — the captured PID IS claude's PID. Under docker
        exec, bash is the session leader of its exec, so claude's PGID
        equals its PID. `kill -9 -<PID>` (negative) SIGKILLs the WHOLE
        group, reaping claude AND every Node worker it forked. Without
        the minus sign, orphaned workers survive and keep writing to
        the session jsonl.
        """
        try:
            proc.kill()
        except Exception:
            logger.debug("exception suppressed", exc_info=True)
        # Read per-stream tags pinned on `proc` at spawn time — NOT from
        # `self` which is a singleton shared with concurrent streams
        # (main agent, compact, memory_extract, btw, sub-agents). A
        # later-spawned stream clobbers `self._pool_container_name` and
        # `self._cc_container_pid` mid-flight; reading from proc ties
        # the kill to the exact subprocess we're targeting.
        _container = getattr(proc, '_pf_container', '') or ''
        try:
            _pid = int(getattr(proc, '_pf_pid', 0) or 0)
        except (TypeError, ValueError):
            _pid = 0
        if not _container or not _pid:
            logger.error(
                "[claude-code] _kill_cc_hard SKIPPED -- container=%r "
                "pid=%d -- CC PROCESS LIKELY ORPHANED", _container, _pid)
            return
        import subprocess as _sp  # nosec B404
        from pawflow_relay.utils import docker_cmd
        # SIGKILL the entire process group (negative PID). claude CLI
        # forks Node workers; killing only the root PID leaves them
        # orphaned (reparented to PID 1) and they keep running.
        _r = _sp.run(  # nosec B603
            docker_cmd() + ["exec", _container,
                             "kill", "-9", f"-{_pid}"],
            capture_output=True, timeout=5,
        )
        if _r.returncode == 0:
            logger.info(
                "[claude-code] container-side kill OK: pgid=%d "
                "container=%s", _pid, _container)
        else:
            # rc=1 typically means group already gone (kill ESRCH).
            logger.warning(
                "[claude-code] kill -9 -pgid=%d returned rc=%d in "
                "container=%s (stderr=%s) -- likely already dead",
                _pid, _r.returncode, _container,
                _r.stderr.decode('utf-8', 'replace')[:200].strip())

    def _check_preempt_in_jsonl(self, jsonl_path: str,
                                 sent_texts: list) -> str:
        """Inspect CC's session jsonl to determine if a queued preempt has
        already been answered by the just-completed result event.

        Returns one of:
          - 'done'    — every sent preempt is followed by an assistant
                        event in the jsonl. CC integrated/answered it
                        inline; safe to break the stream loop.
          - 'pending' — at least one preempt sits at a position AFTER the
                        last assistant event. CC has read stdin but hasn't
                        responded yet — keep the stream open for the next
                        turn's events.
          - 'unread'  — our preempt text(s) are not yet in the jsonl. CC
                        has not yet read stdin; keep the stream open and
                        let the watchdog enforce the timeout if it never
                        does.
          - 'unknown' — jsonl unreadable / no sent_texts to match. Caller
                        should fall back to the default \"keep open with
                        budget\" behavior.

        Match strategy: literal substring search of each sent text in the
        user-message content. We use the ORIGINAL user text (without the
        catchup prefix) so the substring is what CC stored verbatim.
        """
        if not sent_texts or not jsonl_path:
            return 'unknown'
        try:
            with open(jsonl_path, 'rb') as _f:
                _raw = _f.read()
        except OSError:
            return 'unknown'
        last_assistant_pos = -1
        last_preempt_pos = -1
        # Per-preempt found flag so 'done' really means ALL of them got
        # an assistant event after — not just the most recent.
        _found_flags = [False] * len(sent_texts)
        # Position (line index) at which each preempt was last seen.
        _preempt_positions = [-1] * len(sent_texts)
        for i, _line in enumerate(_raw.splitlines()):
            if not _line.strip():
                continue
            try:
                _entry = json.loads(_line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            _etype = _entry.get('type', '')
            if _etype == 'assistant':
                last_assistant_pos = i
                continue
            if _etype != 'user':
                continue
            _msg = _entry.get('message', {}) or {}
            _content = _msg.get('content', '')
            _text_blob = ''
            if isinstance(_content, str):
                _text_blob = _content
            elif isinstance(_content, list):
                for _p in _content:
                    if isinstance(_p, dict) and _p.get('type') == 'text':
                        _text_blob += _p.get('text', '') or ''
            if not _text_blob:
                continue
            for _idx, _sent in enumerate(sent_texts):
                # Substring match — CC may prefix our text with catchup,
                # so we look for the user-supplied tail. Skip empties.
                if _sent and _sent in _text_blob:
                    _found_flags[_idx] = True
                    _preempt_positions[_idx] = i
                    last_preempt_pos = max(last_preempt_pos, i)
        # Decide.
        if not any(_found_flags):
            return 'unread'
        # Any preempt with no assistant event after it → still pending.
        for _idx, _pos in enumerate(_preempt_positions):
            if _found_flags[_idx] and _pos > last_assistant_pos:
                return 'pending'
        # Every found preempt has an assistant event after it.
        # If some preempts are still unfound (unread), 'pending' wins
        # (we treat partially-unread as 'wait' — conservative).
        if not all(_found_flags):
            return 'unread'
        return 'done'

    def _cleanup_proc(self, proc) -> str:
        """Clean up a Claude Code subprocess. Returns captured stderr.

        Pool/proc state pinned to `proc` (via _pf_container set at spawn)
        is the authoritative source — `self._pool_container_name` /
        `self._claude_proc` / `self._current_session_id` /
        `self._cc_container_pid` are SINGLETON state on a shared
        provider used by concurrent streams (main agent, compact,
        memory-extract, btw, sub-agent). Reading the pool name from
        `self` would race: a concurrent _spawn_cc_stream that just
        clobbered self._pool_container_name with ITS container would
        steer this cleanup to release the wrong slot. Read from proc.
        """
        # Release pool slot from proc-pinned container (race-safe).
        _pool_name = getattr(proc, '_pf_container', '') or ''
        if _pool_name:
            self._pool_release(_pool_name)
        # Clear self.* mirrors only if they still point at THIS stream's
        # values — leave another concurrent stream's mirror intact.
        if getattr(self, '_claude_proc', None) is proc:
            self._claude_proc = None
        if (getattr(self, '_pool_container_name', None) == _pool_name
                and _pool_name):
            self._pool_container_name = None
        try:
            _self_pid = int(getattr(self, '_cc_container_pid', 0) or 0)
        except (TypeError, ValueError):
            _self_pid = 0
        try:
            _proc_pid = int(getattr(proc, '_pf_pid', 0) or 0)
        except (TypeError, ValueError):
            _proc_pid = 0
        if _self_pid and _self_pid == _proc_pid:
            self._cc_container_pid = 0
            # Session id is paired with this PID — drop it too only
            # when we owned the slot.
            self._current_session_id = ""
        # Kill process FIRST so pipes become readable (no more blocking)
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            logger.debug("exception suppressed", exc_info=True)
        # NOW read stderr from the drain buffer (live thread owns the fd)
        stderr = ""
        try:
            stderr = "".join(getattr(self, "_stderr_buffer", []) or [])
        except Exception:
            logger.debug("exception suppressed", exc_info=True)
        # Close all streams
        for stream in (proc.stdout, proc.stdin, proc.stderr):
            try:
                if stream and not stream.closed:
                    stream.close()
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
        return stderr

    # ── Non-streaming (complete) ────────────────────────────────────

    @staticmethod
    def _cc_project_key(workdir: str) -> str:
        """Derive the project subdir name CC uses to bucket session files.

        CC encodes its cwd into a project key by stripping the leading
        slash and replacing every non-alphanum character (including `_`)
        with `-`. The pool's per-exec mount-namespace gives CC
        `cwd=/cc_sessions/<conv>/<agent>`, so the key becomes
        `-cc-sessions-<conv>-<agent>`.

        If this derivation drifts from CC's real algorithm, `--resume`
        would silently fall through to a fresh NEW session; the
        file_exists guard in _stream_claude_code drops --resume in that
        case rather than losing history without warning.
        """
        rel = os.path.relpath(workdir, _get_sessions_base()).replace(
            "\\", "/").split("/", 1)[-1]
        return "-cc-sessions-" + rel.replace("/", "-").replace("_", "-")

    def _pool_popen(self, workdir: str, cmd: list, user_id: str = "",
                    conversation_id: str = "", agent_name: str = "",
                    **popen_kwargs) -> tuple:
        """Launch claude inside a pool container via docker exec.

        Returns (proc, pool_container_name). Caller must release
        pool_container when done — release destroys the container
        (`docker rm -f`) under the 1:1 model.
        """
        _env = self._claude_code_env(workdir)
        from core.claude_code_pool import ClaudeCodePool
        from core.cli_workspace_mounts import (
            build_cli_workspace_mount_args, build_skill_mount_args,
        )
        pool = ClaudeCodePool.instance()
        workspace_mounts = build_cli_workspace_mount_args(
            conversation_id, agent_name, user_id=user_id)
        # Bind-mount assigned-skill directories at /skills/<name> so SKILL.md
        # asset references resolve inside the container.
        workspace_mounts = workspace_mounts + build_skill_mount_args(
            conversation_id, agent_name, user_id=user_id)
        container = pool.acquire(workspace_mount_args=workspace_mounts)
        _rel = os.path.relpath(workdir, _get_sessions_base()).replace("\\", "/")
        _session_dir = f"/cc_sessions/{_rel}"
        # Pass API key / base URL / TLS skip to container if configured
        _extra = {}
        if _env.get("ANTHROPIC_API_KEY"):
            _extra["ANTHROPIC_API_KEY"] = _env["ANTHROPIC_API_KEY"]
        if _env.get("ANTHROPIC_BASE_URL"):
            _extra["ANTHROPIC_BASE_URL"] = _env["ANTHROPIC_BASE_URL"]
        # NODE_TLS_REJECT_UNAUTHORIZED=0 is set by _claude_code_env only
        # for HTTPS relay-proxy URLs pointing at a LAN IP with a self-
        # signed cert. Without this passthrough the container still
        # refuses the TLS handshake (self-signed cert not in the trust
        # store) and CC surfaces "empty or malformed response (HTTP 200)".
        if _env.get("NODE_TLS_REJECT_UNAUTHORIZED"):
            _extra["NODE_TLS_REJECT_UNAUTHORIZED"] = _env["NODE_TLS_REJECT_UNAUTHORIZED"]
        proc = pool.exec_claude(
            container, _session_dir, cmd[1:],  # skip 'claude' binary
            extra_env=_extra or None,
            **popen_kwargs)
        return proc, container

    def _pool_release(self, container_name):
        """Release a pool container slot."""
        if container_name:
            try:
                from core.claude_code_pool import ClaudeCodePool
                ClaudeCodePool.instance().release(container_name)
            except Exception:
                logger.debug("exception suppressed", exc_info=True)

    # ── Streaming ───────────────────────────────────────────────────


    @staticmethod
    def _build_stdin_with_system(system_prompt: str, user_text: str) -> str:
        """Combine system prompt and user text for text-mode input."""
        if not system_prompt:
            return user_text
        return (
            "<system_instructions>\n" + system_prompt
            + "\n</system_instructions>\n\n" + user_text
        )

    def _build_catchup_context(self, conv_id: str, agent_name: str) -> str:
        """Build catch-up text from messages other agents sent since our last turn.

        Reads the PARENT conversation's context for this agent (sub-agents
        included — a delegate still sees new activity in the main chat
        while it works). Finds messages after the last known index
        (tracked in self._cc_catchup_idx). Returns formatted text block or "".
        Also updates self._cc_catchup_idx so the same messages aren't sent twice
        (shared between initial, preempt, and inter-turn catch-up).
        """
        if not conv_id:
            return ""
        # Internal sentinel (e.g. "_compact" summarizer session) — no
        # real conversation to catch up from.
        if conv_id.startswith("_"):
            return ""
        # Sub-conv (delegate / task) → catch up from the parent conv so
        # the delegate sees messages arriving in the main chat while it
        # works. The sub-conv itself has no multi-agent dialog of its own.
        _lookup_cid = conv_id
        for _sep in ("::task::", "::task_verify::", "::delegate::", "::flash::"):
            if _sep in _lookup_cid:
                _lookup_cid = _lookup_cid.split(_sep, 1)[0]
                break
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            ctx_data = store.load_agent_context(_lookup_cid, agent_name)
            if not ctx_data:
                return ""

            # Initialize tracking index: find last own message as baseline
            if not hasattr(self, '_cc_catchup_idx') or self._cc_catchup_idx == 0:
                last_own = -1
                for i, m in enumerate(ctx_data):
                    src = m.get("source") or {}
                    if src.get("type") == "agent" and src.get("name") == agent_name:
                        last_own = i
                self._cc_catchup_idx = (last_own + 1) if last_own >= 0 else len(ctx_data)

            # Collect messages since last check
            new_msgs = ctx_data[self._cc_catchup_idx:]
            self._cc_catchup_idx = len(ctx_data)

            if not new_msgs:
                return ""

            # Filter: only messages from OTHER agents or user→other agent
            # Messages from this agent or user→this agent are already in CC's session
            filtered = []
            for m in new_msgs:
                src = m.get("source") or {}
                src_type = src.get("type", "")
                src_name = src.get("name", "")
                # Skip our own agent's messages (CC already has them)
                if src_type == "agent" and src_name == agent_name:
                    continue
                # Skip user messages directed at this agent (CC already has them)
                if src_type == "user":
                    target = src.get("target_agent", "")
                    if not target or target == agent_name:
                        continue
                # Skip tool results and context injections
                if m.get("role") == "tool" or m.get("tool_calls"):
                    continue
                if src_type == "context":
                    continue
                filtered.append(m)

            if not filtered:
                return ""

            # Format as XML block
            lines = ["<catch_up_context>",
                     "New messages from other participants since your last response:"]
            count = 0
            for m in filtered:
                content = m.get("content", "")
                if not content or not isinstance(content, str):
                    continue
                role = m.get("role", "user")
                lines.append(f"<message role=\"{role}\">\n{content}\n</message>")
                count += 1
            lines.append("</catch_up_context>")

            if count == 0:
                return ""

            logger.info("[claude-code] catch-up: %d messages for %s", count, agent_name)
            return "\n".join(lines)
        except Exception as e:
            logger.warning("[claude-code] catch-up failed: %s", e)
            return ""

    def _spawn_cc_stream(self, workdir: str, user_id: str, conv_id: str,
                         agent_name: str, session_id: str, model,
                         *, ephemeral_stream: bool = False):
        """Spawn a fresh Claude Code subprocess (CC container exec + CLI).

        Extracted from _stream_claude_code so the live-session reuse path
        can skip spawning and pull proc + mcp token from a cached session.
        This is a pure move; behavior is identical when called.

        Writes .mcp.json + mints an internal-auth token, computes the
        effective --resume session id (dropped if the jsonl is missing), builds
        the CLI command, and launches via the pool.

        Side effects:
            - self._claude_proc  (unless _ephemeral_stream)
            - self._pool_container_name
            - self._cc_container_pid (= 0 initially; drain thread fills it)
            - self._stderr_buffer
            - proc._pf_container / proc._pf_pid
            - Starts a daemon cc-stderr-drain thread.

        Returns: (proc, pool_container_name, mcp_internal_token).
        """
        from core.llm_client import LLMClientError
        mcp_path, _mcp_internal_token = self._setup_mcp_config(
            workdir, user_id, conv_id, agent_name)

        # The pool's per-exec mount-namespace binds /cc_sessions/<user>
        # over /cc_sessions; CC's working directory is
        # /cc_sessions/<conv>/<agent>. MCP config path inside that
        # namespace = workdir basename joined to it.
        _mcp_arg = mcp_path
        if mcp_path:
            _rel_no_user = os.path.relpath(
                workdir, _get_sessions_base()).replace("\\", "/").split("/", 1)[-1]
            _container_workdir = f"/cc_sessions/{_rel_no_user}"
            _mcp_arg = f"{_container_workdir}/{os.path.basename(mcp_path)}"

        # Gate --resume on the jsonl actually existing at the expected
        # path. If it doesn't, CC would silently create a NEW empty
        # session under the same sid and we'd lose all history without
        # any error. Fall back to a true NEW session (drop --resume),
        # which is a well-defined state the caller can detect via the
        # SESSION MISMATCH check downstream.
        _effective_session_id = session_id
        _expected_session_file = ""
        _exists = False
        if session_id:
            _proj_key = self._cc_project_key(workdir)
            _expected_session_file = os.path.join(
                workdir, "projects", _proj_key, f"{session_id}.jsonl")
            _exists = os.path.exists(_expected_session_file)
            _size = os.path.getsize(_expected_session_file) if _exists else 0
            if _exists:
                logger.info(
                    "claude-code RESUME: session_id=%s file_size=%d path=%s",
                    session_id, _size, _expected_session_file)
            else:
                logger.warning(
                    "claude-code NEW (resume jsonl MISSING at expected path): "
                    "session_id=%s expected=%s — dropping --resume, "
                    "starting fresh CC session",
                    session_id, _expected_session_file)
                _effective_session_id = ""

        cmd = self._build_claude_cmd(model, _effective_session_id,
                                     mcp_config_path=_mcp_arg,
                                     workdir=workdir)

        logger.info("claude-code stream: cwd=%s cmd=%s",
                     workdir, " ".join(str(c) for c in cmd[:20]))
        # Track pool container for cleanup
        self._pool_container_name = None

        try:
            proc, self._pool_container_name = self._pool_popen(
                workdir, cmd, user_id=user_id,
                conversation_id=conv_id, agent_name=agent_name,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            # Ephemeral streams (btw) don't register proc — they don't
            # need preempt/cancel and must not overwrite the main agent's proc.
            if not ephemeral_stream:
                self._claude_proc = proc
            # Per-stream session info pinned on the proc object. The
            # provider instance is a SINGLETON shared across concurrent
            # streams (main agent, compact, memory_extract, btw, sub-agents);
            # storing container name / container PID on `self` means any
            # later-spawned stream clobbers the in-flight one's tracking
            # state. Pinning to `proc` ties the info to the exact subprocess
            # the kill targets, so clobbers on `self` don't matter. Drain
            # thread updates proc._pf_pid in place.
            proc._pf_container = self._pool_container_name
            proc._pf_pid = 0
            # Keep self.* mirrors for the codepath that still reads them
            # (cancel_claude_code force-stop, cleanup_proc) — but treat them
            # as best-effort hints, not authoritative for kill.
            self._cc_container_pid = 0
            self._stderr_buffer = []
            def _drain_stderr():
                try:
                    for _line in proc.stderr:
                        # setsid --wait prints "setsid: child NN did not
                        # exit normally: Success" whenever the session
                        # leader is SIGKILL'd. Our _kill_cc_hard ALWAYS
                        # uses SIGKILL (it IS the kill mechanism), so
                        # this message fires on every clean compact/
                        # cancel. Keeping it in _stderr_buffer poisons
                        # the "Claude CLI stream exited ...: <stderr>"
                        # exception string, which the outer retry logic
                        # then has to special-case. Drop it at the source.
                        if (_line.startswith("setsid: child ")
                                and "did not exit normally" in _line):
                            continue
                        # __PF_CLAUDE_PID=<pid>\n is the shell wrapper's
                        # spawn-time preamble. We capture it into
                        # proc._pf_pid below, log it at INFO as "captured
                        # container PID=<pid>", and that's the only value
                        # it has. Keeping the raw line in _stderr_buffer
                        # means every clean post-kill log surfaces as
                        # "Claude CLI stderr: __PF_CLAUDE_PID=<pid>" at
                        # ERROR level — misleading (no error, just a
                        # leftover PID dump). Drop it after capture.
                        if '__PF_CLAUDE_PID=' in _line:
                            if not proc._pf_pid:
                                try:
                                    _pid_str = _line.split(
                                        '__PF_CLAUDE_PID=', 1)[1].strip()
                                    _pid_int = int(_pid_str)
                                    proc._pf_pid = _pid_int
                                    self._cc_container_pid = _pid_int
                                    logger.info(
                                        "[claude-code] captured container PID=%d "
                                        "(container=%s)",
                                        _pid_int, proc._pf_container)
                                except Exception:
                                    logger.debug("exception suppressed", exc_info=True)
                            continue
                        self._stderr_buffer.append(_line)
                except Exception:
                    logger.debug("exception suppressed", exc_info=True)
            import threading as _th
            _th.Thread(target=_drain_stderr, daemon=True,
                       name="cc-stderr-drain").start()
        except FileNotFoundError:
            if self._pool_container_name:
                from core.claude_code_pool import ClaudeCodePool
                ClaudeCodePool.instance().release(self._pool_container_name)
                self._pool_container_name = None
            raise LLMClientError(
                "Binary 'docker' not found. Install Docker Desktop.")

        return proc, self._pool_container_name, _mcp_internal_token

