"""LLM provider mixin -- Gemini CLI (subprocess-based).

Uses --input-format stream-json + --output-format stream-json for
bidirectional streaming. This enables:
- Real-time output streaming (tool calls, text, thinking)
- Preempt: send new user messages on stdin while Codex works
- Interrupt: send interrupt signal on stdin to stop gracefully
"""

import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
from typing import Dict, List, Optional

from core.gemini_live_registry import GeminiLiveSession, GeminiLiveRegistry

# Sentinel pushed onto the per-session event queue when the reader daemon
# exits (proc stdout EOF). Module-level so the SAME object identity holds
# across turns for a reused session — the dispatch loop does `event is
# _GEMINI_READER_EOF` to break; a new sentinel per turn would wrongly treat
# the ORIGINAL reader's EOF as a regular event.
_GEMINI_READER_EOF = object()

from core.llm_providers.gemini_session import GeminiSessionMixin, _get_sessions_base

logger = logging.getLogger(__name__)


class _Gemini401Retry(Exception):
    """Internal signal: OAuth 401 mid-stream, credentials refreshed, retry the call."""


class LLMGeminiMixin(GeminiSessionMixin):
    """Gemini CLI provider using bidirectional stream-json.

    Codex uses MCP natively for tool access. PawFlow tools are
    exposed via the MCP bridge (tools/mcp_bridge.py) configured with
    --mcp-config.

    Each session runs in its own working directory under
    data/claude_sessions/<conversation_id>/<agent_name>/ so Codex
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

    # Session/workdir methods inherited from GeminiSessionMixin:
    # _gemini_get_session_workdir, _gemini_env, _gemini_setup_credentials,
    # _gemini_recover_tokens, _gemini_setup_mcp_config, _build_gemini_cmd,
    # _get_tool_relay_info, _DISALLOWED_BUILTIN_TOOLS

    def _gemini_context_window(self, model: str) -> int:
        """Return Gemini's effective context window for `model`.

        Runtime provider/API metadata is authoritative when available.
        Otherwise PawFlow's required LLM service `max_context_size` is
        the source of truth. Missing/invalid config is a hard service
        configuration error, never a silent numeric fallback.
        """
        runtime_windows = getattr(self, "_gemini_context_windows", None)
        if isinstance(runtime_windows, dict):
            for key in (model, (model or "").lower()):
                try:
                    value = int(runtime_windows.get(key, 0) or 0)
                except (TypeError, ValueError):
                    value = 0
                if value > 0:
                    return value

        cfg = getattr(self, "_config_ref", None) or {}
        try:
            value = int(cfg.get("max_context_size", 0) or 0)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            from core.llm_client import LLMClientError
            raise LLMClientError(
                "Gemini LLM service is missing required max_context_size")
        return value

    # ── Process management ──────────────────────────────────────────

    def _gemini_send_user_message(self, text: str, attachments: list = None):
        """Inline-preempt entrypoint for Gemini CLI.

        Gemini headless mode reads stdin as a single prompt blob. The stream
        process closes stdin after the initial prompt so the CLI starts
        processing, so we cannot inject another user message into the same
        process. Preempt therefore mirrors Codex: kill the in-flight CLI and
        return False so the agent loop routes the message through PendingQueue
        and resumes on the next turn.
        """
        proc = getattr(self, '_gemini_proc', None)
        if proc is not None and proc.poll() is None:
            logger.info(
                "[gemini] preempt: killing in-flight CLI to interrupt "
                "current turn — next turn will resume with new prompt: %.100s",
                text)
            # Mark this stream so the post-loop exit-code check does
            # NOT raise on the non-zero return code — the kill is OUR
            # action, not a CLI failure. The agent loop will pick up
            # the queued message via PendingQueue on the next iter.
            self._preempt_killed = True
            try:
                self._kill_gemini_hard(proc)
            except Exception as _ke:
                logger.warning("[gemini] preempt kill failed: %s", _ke)
        else:
            logger.info(
                "[gemini] preempt: no in-flight proc to interrupt — "
                "PendingQueue will pick up: %.100s", text)
        return False

    def cancel_gemini(self, force: bool = False):
        """Cancel Gemini CLI subprocess.

        Gemini headless mode has no reliable stdin interrupt after the prompt
        pipe has been closed, so both graceful and force cancellation tear down
        the active CLI process and release its pool slot.
        """
        proc = getattr(self, '_gemini_proc', None)
        if not proc or proc.poll() is not None:
            return

        logger.info("KILLING Gemini subprocess (pid=%d, force=%s)", proc.pid, force)
        self._gemini_proc = None
        self._kill_gemini_hard(proc)
        _pool_name = getattr(self, '_pool_container_name', None)
        if _pool_name:
            from core.gemini_pool import GeminiPool
            GeminiPool.instance().release(_pool_name)
            self._pool_container_name = None
        self._current_session_id = ""

    def _kill_gemini_hard(self, proc) -> None:
        """Kill the gemini subprocess on BOTH the host and inside
        the pool container, deterministically by PID.

        `proc.kill()` only reaps the host-side `docker exec` wrapper.
        Without a container-side kill, the claude CLI becomes an orphan
        (reparented to PID 1 inside the container) and keeps running —
        emitting tool calls via MCP, running its own auto-compact,
        writing to its session .jsonl — while PawFlow spawns a fresh
        session in the SAME pool container, creating zombie races on
        the same files.

        The container-side PID is captured at spawn from the shell
        wrapper's `__PF_GEMINI_PID=$$` stderr preamble (see
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
        # `self._gemini_container_pid` mid-flight; reading from proc ties
        # the kill to the exact subprocess we're targeting.
        _container = getattr(proc, '_pf_container', '') or ''
        try:
            _pid = int(getattr(proc, '_pf_pid', 0) or 0)
        except (TypeError, ValueError):
            _pid = 0
        if not _container or not _pid:
            logger.error(
                "[gemini] _kill_gemini_hard SKIPPED -- container=%r "
                "pid=%d -- CC PROCESS LIKELY ORPHANED", _container, _pid)
            return
        import subprocess as _sp
        from pawflow_relay.utils import docker_cmd
        # SIGKILL the entire process group (negative PID). claude CLI
        # forks Node workers; killing only the root PID leaves them
        # orphaned (reparented to PID 1) and they keep running.
        _r = _sp.run(
            docker_cmd() + ["exec", _container,
                             "kill", "-9", f"-{_pid}"],
            capture_output=True, timeout=5,
        )
        if _r.returncode == 0:
            logger.info(
                "[gemini] container-side kill OK: pgid=%d "
                "container=%s", _pid, _container)
        else:
            # rc=1 typically means group already gone (kill ESRCH).
            logger.warning(
                "[gemini] kill -9 -pgid=%d returned rc=%d in "
                "container=%s (stderr=%s) -- likely already dead",
                _pid, _r.returncode, _container,
                _r.stderr.decode('utf-8', 'replace')[:200].strip())

    def _check_gemini_preempt_in_jsonl(self, jsonl_path: str,
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

    def _gemini_cleanup_proc(self, proc) -> str:
        """Clean up a Codex subprocess. Returns captured stderr.

        Pool/proc state pinned to `proc` (via _pf_container set at spawn)
        is the authoritative source — `self._pool_container_name` /
        `self._gemini_proc` / `self._current_session_id` /
        `self._gemini_container_pid` are SINGLETON state on a shared
        provider used by concurrent streams (main agent, compact,
        memory-extract, btw, sub-agent). Reading the pool name from
        `self` would race: a concurrent _spawn_gemini_stream that just
        clobbered self._pool_container_name with ITS container would
        steer this cleanup to release the wrong slot. Read from proc.
        """
        # Release pool slot from proc-pinned container (race-safe).
        _pool_name = getattr(proc, '_pf_container', '') or ''
        if _pool_name:
            self._gemini_pool_release(_pool_name)
        # Clear self.* mirrors only if they still point at THIS stream's
        # values — leave another concurrent stream's mirror intact.
        if getattr(self, '_gemini_proc', None) is proc:
            self._gemini_proc = None
        if (getattr(self, '_pool_container_name', None) == _pool_name
                and _pool_name):
            self._pool_container_name = None
        try:
            _self_pid = int(getattr(self, '_gemini_container_pid', 0) or 0)
        except (TypeError, ValueError):
            _self_pid = 0
        try:
            _proc_pid = int(getattr(proc, '_pf_pid', 0) or 0)
        except (TypeError, ValueError):
            _proc_pid = 0
        if _self_pid and _self_pid == _proc_pid:
            self._gemini_container_pid = 0
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

    # ── Legacy session scrub ────────────────────────────────────────

    # Matches placeholders the agent used to write into user text before the
    # vision channel was wired properly, e.g. "[image: image_1234567890_2.png]".
    # The image bytes travel via the native vision path now, so the text
    # reference is stale — keeping it would make the agent pattern-match
    # "image attached → call see('image_1234567890_2.png')" on every resume.
    _LEGACY_IMAGE_RE = re.compile(
        r'\s*\[image:\s*image_\d+_\d+\.[A-Za-z0-9]+\s*\]\s*')

    def _gemini_scrub_legacy_image_placeholders(self, session_file: str) -> None:
        """Rewrite a codex .jsonl session file in place, stripping
        legacy ``[image: image_<ts>_<n>.<ext>]`` markers from user text.

        Only user-authored text is touched. Lines that aren't valid JSON or
        that aren't user messages pass through unchanged. No-op if nothing
        matches, so repeated resumes on a clean file cost only a read.
        """
        import json
        try:
            with open(session_file, "r", encoding="utf-8") as _f:
                _raw_lines = _f.readlines()
        except OSError:
            return

        _pat = self._LEGACY_IMAGE_RE
        _changed = False
        _out = []
        for _line in _raw_lines:
            try:
                _obj = json.loads(_line)
            except (json.JSONDecodeError, ValueError):
                _out.append(_line)
                continue
            if isinstance(_obj, dict) and _obj.get("type") == "user":
                _msg = _obj.get("message")
                if isinstance(_msg, dict):
                    _content = _msg.get("content")
                    if isinstance(_content, str):
                        _new = _pat.sub(" ", _content).strip()
                        if _new != _content:
                            _msg["content"] = _new
                            _changed = True
                    elif isinstance(_content, list):
                        for _part in _content:
                            if isinstance(_part, dict) and _part.get("type") == "text":
                                _t = _part.get("text", "")
                                _new = _pat.sub(" ", _t).strip()
                                if _new != _t:
                                    _part["text"] = _new
                                    _changed = True
                _out.append(json.dumps(_obj, ensure_ascii=False) + "\n")
            else:
                _out.append(_line)

        if not _changed:
            return

        # Atomic rewrite: write .tmp then rename so a crash mid-scrub
        # doesn't leave the session file truncated.
        _tmp = session_file + ".scrubtmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            _f.writelines(_out)
        os.replace(_tmp, session_file)

    # ── Non-streaming (complete) ────────────────────────────────────

    @staticmethod
    def _gemini_project_key(workdir: str) -> str:
        """Derive the project subdir name CC uses to bucket session files.

        CC encodes its cwd into a project key by stripping the leading
        slash and replacing every non-alphanum character (including `_`)
        with `-`. The pool's per-exec mount-namespace gives CC
        `cwd=/cc_sessions/<conv>/<agent>`, so the key becomes
        `-cc-sessions-<conv>-<agent>`.

        If this derivation drifts from CC's real algorithm, `--resume`
        would silently fall through to a fresh NEW session; the
        file_exists guard in _stream_gemini drops --resume in that
        case rather than losing history without warning.
        """
        rel = os.path.relpath(workdir, _get_sessions_base()).replace(
            "\\", "/").split("/", 1)[-1]
        return "-cc-sessions-" + rel.replace("/", "-").replace("_", "-")

    def _gemini_pool_popen(self, workdir: str, cmd: list, **popen_kwargs) -> tuple:
        """Launch claude inside a pool container via docker exec.

        Returns (proc, pool_container_name). Caller must release
        pool_container when done — release destroys the container
        (`docker rm -f`) under the 1:1 model.
        """
        _env = self._gemini_env(workdir)
        from core.gemini_pool import GeminiPool
        pool = GeminiPool.instance()
        container = pool.acquire()
        _rel = os.path.relpath(workdir, _get_sessions_base()).replace("\\", "/")
        _session_dir = f"/cc_sessions/{_rel}"
        # Pass API key / base URL / TLS skip to container if configured
        _extra = {}
        if _env.get("GEMINI_API_KEY"):
            _extra["GEMINI_API_KEY"] = _env["GEMINI_API_KEY"]
        if _env.get("GEMINI_BASE_URL"):
            _extra["GEMINI_BASE_URL"] = _env["GEMINI_BASE_URL"]
        # NODE_TLS_REJECT_UNAUTHORIZED=0 is set by _gemini_env only
        # for HTTPS relay-proxy URLs pointing at a LAN IP with a self-
        # signed cert. Without this passthrough the container still
        # refuses the TLS handshake (self-signed cert not in the trust
        # store) and CC surfaces "empty or malformed response (HTTP 200)".
        if _env.get("NODE_TLS_REJECT_UNAUTHORIZED"):
            _extra["NODE_TLS_REJECT_UNAUTHORIZED"] = _env["NODE_TLS_REJECT_UNAUTHORIZED"]
        # Pass the FULL cmd list — gemini_pool prepends only the `gemini`
        # binary; `cmd[0]=-p` is the prompt-mode flag and must stay (gemini
        # falls into interactive REPL without it). CC strips its own `-p`
        # because stream-json overrides; gemini doesn't have that fallback.
        proc = pool.exec_gemini(
            container, _session_dir, cmd,
            extra_env=_extra or None,
            **popen_kwargs)
        return proc, container

    def _gemini_pool_release(self, container_name):
        """Release a pool container slot."""
        if container_name:
            try:
                from core.gemini_pool import GeminiPool
                GeminiPool.instance().release(container_name)
            except Exception:
                logger.debug("exception suppressed", exc_info=True)

    # ── Streaming ───────────────────────────────────────────────────

    @staticmethod
    def _gemini_extract_images(messages, user_id: str, conversation_id: str) -> list:
        """Extract images from the LAST user message only.

        Removes image blocks from ALL messages (so they don't bloat the text
        prompt). Only returns image blocks from the LAST user message as
        content blocks for the stream-json message (native vision).

        Older images are replaced with a placeholder text.

        user_id and conversation_id are REQUIRED — image_ref blocks point
        to private attachments stored under (owner × conv × file_id).
        A missing identifier means the caller has a bug; raise loudly
        instead of dropping the image and pretending nothing happened.
        """
        if not user_id:
            raise ValueError(
                "_gemini_extract_images: user_id is required to resolve image_ref "
                "attachments (owner-scoped access control)")
        if not conversation_id:
            raise ValueError(
                "_gemini_extract_images: conversation_id is required to resolve "
                "image_ref attachments (files belong to a conversation)")
        import base64 as _b64
        image_blocks = []

        # Find the last user message index
        _last_user_idx = -1
        for i, m in enumerate(messages):
            if m.role == "user" and isinstance(m.content, list):
                _last_user_idx = i

        for idx, m in enumerate(messages):
            if not isinstance(m.content, list):
                continue
            new_content = []
            for block in m.content:
                if not isinstance(block, dict):
                    new_content.append(block)
                    continue
                btype = block.get("type", "")

                _is_last_user = (idx == _last_user_idx)

                # Placeholder policy: when we extract an image from the LAST
                # user message into image_blocks, that image is sent to the
                # model natively via vision — emitting a text placeholder on
                # top of it is actively harmful: the agent reads
                # "[image: foo.png]" and calls see() / read() on it, duplicating
                # the image in its context (tokens ×2) for zero benefit.
                # For OLDER user messages we keep a text placeholder so the
                # model knows an image was there, but we DON'T re-send it via
                # vision (would bloat context with every historical image).

                if btype == "image_url":
                    url = (block.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        if _is_last_user:
                            try:
                                header, data_b64 = url.split(",", 1)
                                mime = header.split(":")[1].split(";")[0]
                                image_blocks.append({
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": mime,
                                        "data": data_b64,
                                    },
                                })
                                logger.info("Extracted image for vision: %s (%d chars b64)",
                                            mime, len(data_b64))
                            except Exception as e:
                                logger.warning("Failed to extract image: %s", e)
                            # Image is in vision — no text placeholder.
                        else:
                            new_content.append({"type": "text", "text": "[image]"})
                        continue

                elif btype == "image":
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        if _is_last_user:
                            image_blocks.append(block)
                            logger.info("Extracted image for vision: %s",
                                        source.get("media_type", "?"))
                            # Image is in vision — no text placeholder.
                        else:
                            new_content.append({"type": "text", "text": "[image]"})
                        continue

                elif btype == "image_ref":
                    # Image stored in FileStore — load for vision on last user message only.
                    # Older image_ref blocks (from prior turns already seen by
                    # the model via session resume) are intentionally dropped
                    # to text to keep the prompt compact.
                    if _is_last_user:
                        from core.file_store import FileStore
                        import base64 as _b64
                        _fid = block.get("file_id", "")
                        if not _fid:
                            raise ValueError(
                                "image_ref block missing file_id — producer bug")
                        _fname, _data, _ct = FileStore.instance().get_required(
                            _fid, user_id=user_id,
                            conversation_id=conversation_id)
                        _data_b64 = _b64.b64encode(_data).decode("ascii")
                        image_blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": block.get("mime_type", _ct),
                                "data": _data_b64,
                            },
                        })
                        logger.info("Loaded image from FileStore for vision: %s (%d bytes)",
                                    _fid, len(_data))
                        # Image is in vision — no text placeholder that would
                        # make the agent see()/read() it redundantly.
                    else:
                        new_content.append({"type": "text", "text": f"[image: {block.get('filename', '?')}]"})
                    continue

                new_content.append(block)

            m.content = new_content

        return image_blocks

    @staticmethod
    def _gemini_build_stdin_with_system(system_prompt: str, user_text: str) -> str:
        """Combine system prompt and user text for text-mode input."""
        if not system_prompt:
            return user_text
        return (
            "<system_instructions>\n" + system_prompt
            + "\n</system_instructions>\n\n" + user_text
        )

    def _gemini_build_catchup_context(self, conv_id: str, agent_name: str) -> str:
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
        for _sep in ("::delegate::", "::task::"):
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

            logger.info("[gemini] catch-up: %d messages for %s", count, agent_name)
            return "\n".join(lines)
        except Exception as e:
            logger.warning("[gemini] catch-up failed: %s", e)
            return ""

    def _spawn_gemini_stream(self, workdir: str, user_id: str, conv_id: str,
                         agent_name: str, session_id: str, model,
                         *, ephemeral_stream: bool = False):
        """Spawn a fresh Codex subprocess (CC container exec + CLI).

        Extracted from _stream_gemini so the live-session reuse path
        can skip spawning and pull proc + mcp token from a cached session.
        This is a pure move; behavior is identical when called.

        Writes .mcp.json + mints an internal-auth token, computes the
        effective --resume session id (dropped if the jsonl is missing),
        scrubs legacy image placeholders in the resumed jsonl, builds the
        CLI command, and launches via the pool.

        Side effects:
            - self._gemini_proc  (unless _ephemeral_stream)
            - self._pool_container_name
            - self._gemini_container_pid (= 0 initially; drain thread fills it)
            - self._stderr_buffer
            - proc._pf_container / proc._pf_pid
            - Starts a daemon cc-stderr-drain thread.

        Returns: (proc, pool_container_name, mcp_internal_token).
        """
        from core.llm_client import LLMClientError
        mcp_path, _mcp_internal_token = self._gemini_setup_mcp_config(
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

        # Gemini CLI stores session history under
        # ~/.gemini/tmp/<project_hash>/chats/ and supports `--resume <id>`.
        # Unlike the old CC-specific guard, do not drop --resume just because
        # PawFlow cannot predict the project hash. We only locate the file as
        # a best-effort hook for legacy scrub logic below.
        _effective_session_id = session_id
        _expected_session_file = ""
        _exists = False
        if session_id:
            try:
                import glob as _glob
                _matches = []
                for _ext in ("json", "jsonl"):
                    _matches.extend(_glob.glob(os.path.join(
                        workdir, ".gemini", "tmp", "*", "chats",
                        f"{session_id}.{_ext}")))
                if _matches:
                    _expected_session_file = _matches[0]
                    _exists = os.path.exists(_expected_session_file)
                    _size = os.path.getsize(_expected_session_file) if _exists else 0
                    logger.info(
                        "gemini RESUME: session_id=%s file_size=%d path=%s",
                        session_id, _size, _expected_session_file)
                else:
                    logger.info(
                        "gemini RESUME: session_id=%s (session file not prelocated; passing --resume)",
                        session_id)
            except Exception as _loc_err:
                logger.debug("[gemini] resume file lookup skipped: %s", _loc_err)

        cmd = self._build_gemini_cmd(model, _effective_session_id,
                                      mcp_config_path=_mcp_arg,
                                      workdir=workdir)

        logger.info("gemini stream: cwd=%s cmd=%s",
                     workdir, " ".join(str(c) for c in cmd[:20]))
        # Scrub legacy [image: image_<ts>_<n>.<ext>] placeholders from
        # user text fields. These were written before the vision-
        # placeholder fix and make the agent pattern-match "image
        # attached → call see() with this filename" on every new
        # user turn. The image bytes are still forwarded via the
        # native vision channel, so stripping the text reference is
        # purely cosmetic for the transcript AND prevents the bogus
        # see() calls. Only meaningful when we actually --resume an
        # existing jsonl; skip for NEW sessions.
        if session_id and _exists and _expected_session_file.endswith(".jsonl"):
            try:
                self._gemini_scrub_legacy_image_placeholders(_expected_session_file)
            except Exception as _scrub_err:
                logger.warning("[gemini] session scrub failed (%s): %s",
                               session_id[:8], _scrub_err)

        # Track pool container for cleanup
        self._pool_container_name = None

        try:
            proc, self._pool_container_name = self._gemini_pool_popen(
                workdir, cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            # Ephemeral streams (btw) don't register proc — they don't
            # need preempt/cancel and must not overwrite the main agent's proc.
            if not ephemeral_stream:
                self._gemini_proc = proc
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
            # (cancel_codex force-stop, cleanup_proc) — but treat them
            # as best-effort hints, not authoritative for kill.
            self._gemini_container_pid = 0
            self._stderr_buffer = []
            def _drain_stderr():
                try:
                    for _line in proc.stderr:
                        # setsid --wait prints "setsid: child NN did not
                        # exit normally: Success" whenever the session
                        # leader is SIGKILL'd. Our _kill_gemini_hard ALWAYS
                        # uses SIGKILL (it IS the kill mechanism), so
                        # this message fires on every clean compact/
                        # cancel. Keeping it in _stderr_buffer poisons
                        # the "Gemini CLI stream exited ...: <stderr>"
                        # exception string, which the outer retry logic
                        # then has to special-case. Drop it at the source.
                        if (_line.startswith("setsid: child ")
                                and "did not exit normally" in _line):
                            continue
                        # __PF_GEMINI_PID=<pid>\n is the shell wrapper's
                        # spawn-time preamble. We capture it into
                        # proc._pf_pid below, log it at INFO as "captured
                        # container PID=<pid>", and that's the only value
                        # it has. Keeping the raw line in _stderr_buffer
                        # means every clean post-kill log surfaces as
                        # "Gemini CLI stderr: __PF_GEMINI_PID=<pid>" at
                        # ERROR level — misleading (no error, just a
                        # leftover PID dump). Drop it after capture.
                        if '__PF_GEMINI_PID=' in _line:
                            if not proc._pf_pid:
                                try:
                                    _pid_str = _line.split(
                                        '__PF_GEMINI_PID=', 1)[1].strip()
                                    _pid_int = int(_pid_str)
                                    proc._pf_pid = _pid_int
                                    self._gemini_container_pid = _pid_int
                                    logger.info(
                                        "[gemini] captured container PID=%d "
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
                from core.gemini_pool import GeminiPool
                GeminiPool.instance().release(self._pool_container_name)
                self._pool_container_name = None
            raise LLMClientError(
                "Binary 'docker' not found. Install Docker Desktop.")

        return proc, self._pool_container_name, _mcp_internal_token

    def _stream_gemini(
        self, messages, model, temperature, max_tokens, tools, callback=None,
        turn_callback=None, block_callback=None, _is_auth_retry=False,
        *,
        call_user_id: Optional[str] = None,
        call_conversation_id: Optional[str] = None,
        call_agent_name: Optional[str] = None,
        call_event_cid: Optional[str] = None,
        call_ephemeral_stream: Optional[bool] = None,
    ):
        """Stream from claude CLI using bidirectional stream-json.

        Input: JSON lines on stdin (user messages, can preempt anytime)
        Output: JSON lines on stdout (events: assistant, user, result, etc.)

        turn_callback(text, tool_calls): called at each turn boundary so
        the agent loop can persist intermediate messages. Each Codex
        assistant turn = one message in the conversation.

        Codex uses MCP for tool calls — tools param is ignored.

        Per-call identity (user_id / conversation_id / agent_name /
        event_cid / ephemeral_stream) MUST be passed via the call_*
        kwargs by every caller. The shared client instance no longer
        carries these as state — the previous self._user_id /
        self._conversation_id / etc. pattern was a footgun: concurrent
        compact / memory-extract / sub-agent streams would clobber
        each other's identity via try/finally save-restore on the
        same instance, leaving the values empty for whichever stream
        won the race. Each call now passes its own scope explicitly.
        """
        from core.llm_client import LLMClientError, LLMResponse

        # Resolve per-call identity. Fall back to self.* only as a
        # transitional safety net so a caller that hasn't yet been
        # updated to pass kwargs doesn't crash; the goal is for every
        # call site to pass these explicitly, at which point the
        # fallback can be tightened to raise.
        user_id = (call_user_id if call_user_id is not None
                    else getattr(self, '_user_id', ""))
        conv_id = (call_conversation_id if call_conversation_id is not None
                    else getattr(self, '_conversation_id', ""))
        agent_name = (call_agent_name if call_agent_name is not None
                       else getattr(self, '_agent_name', ""))
        _is_ephemeral = (bool(call_ephemeral_stream)
                          if call_ephemeral_stream is not None
                          else bool(getattr(self, '_ephemeral_stream', False)))
        _raw_event_cid = (call_event_cid if call_event_cid is not None
                           else getattr(self, '_event_cid', ''))

        # Extract images BEFORE serialization (they'll be sent as content blocks).
        # user_id + conv_id are REQUIRED — FileStore enforces owner×conv
        # access control, and a missing identifier silently drops the
        # user's image. _gemini_extract_images raises if either is empty.
        image_blocks = self._gemini_extract_images(
            messages, user_id=user_id, conversation_id=conv_id)

        # Always load session_id from the store for THIS conversation
        # (never from self — the client is shared across conversations)
        session_id = ""
        if conv_id:
            try:
                from core.conversation_store import ConversationStore
                session_id = ConversationStore.instance().get_extra(
                    conv_id, f"gemini_session:{agent_name or 'default'}") or ""
                if session_id:
                    logger.info("[%s/%s/%s] Restored claude session: %s",
                                user_id[:6] or '?', conv_id[:8] or '?',
                                agent_name or 'default', session_id)
            except Exception:
                logger.debug("exception suppressed", exc_info=True)

        # Session-aware serialization:
        # - New session (no session_id): feed the full PawFlow ctx ONCE.
        # - Resume (session_id set): CC already has the history; send only
        #   the new user message. The catch-up mechanism below injects
        #   anything that arrived from other agents since last turn.
        if session_id:
            system_prompt = ""
            last_user = ""
            for _m in reversed(messages):
                if _m.role == "user":
                    _c = _m.content
                    if isinstance(_c, list):
                        last_user = _m.text_content
                    else:
                        last_user = _c or ""
                    break
            user_text = last_user
        else:
            system_prompt, user_text = self._serialize_messages_for_cli(messages, None)

        initial_text = self._gemini_build_stdin_with_system(system_prompt, user_text)
        logger.debug("[gemini] prompt: system=%d user=%d images=%d msgs=%d session=%s",
                     len(system_prompt), len(user_text), len(image_blocks), len(messages),
                     "resume" if session_id else "new")

        logger.info("gemini stream: conv_id='%s' user='%s' agent='%s' session='%s'",
                     conv_id, user_id, agent_name, session_id[:12] if session_id else "new")

        workdir = self._gemini_get_session_workdir(conv_id, agent_name, user_id)
        # Resume with same credential that created the session (approach 3)
        _resume_pool_idx = -1
        if session_id and conv_id:
            try:
                _resume_pool_idx = int(ConversationStore.instance().get_extra(
                    conv_id, f"gemini_pool_idx:{agent_name or 'default'}") or -1)
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
        self._gemini_setup_credentials(workdir, pool_index=_resume_pool_idx)
        # Store pool index for this session
        if conv_id and hasattr(self, '_current_pool_index'):
            try:
                ConversationStore.instance().set_extra(
                    conv_id, f"gemini_pool_idx:{agent_name or 'default'}",
                    self._current_pool_index)
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
        _auth_retried = _is_auth_retry

        # Live-session reuse: look up a warm CC process pinned by
        # (user, conv, agent, service, pool_idx). A hit skips the spawn
        # (mcp setup, container exec, CC startup, --resume load) and
        # pushes the new user message onto the existing stdin. A miss
        # (or a dead proc — auto-evicted by get()) falls through to
        # _spawn_gemini_stream. Ephemeral streams (compact / memory_extract
        # / btw) never reuse: they're short-lived by design and must
        # not inherit nor leak another stream's proc.
        _svc_id = getattr(self, '_agent_service', '') or 'default'
        # Intentionally NOT `getattr(...) or -1`: `or` coerces 0 to -1,
        # silently mapping OAuth pool slot 0 onto the api-key sentinel.
        # The getattr default handles the "attr never set" case (api-key
        # mode: _gemini_setup_credentials early-returns before assigning the
        # attr).
        _svc_pool_idx = int(getattr(self, '_current_pool_index', -1))
        # _is_ephemeral resolved earlier at function entry from
        # call_ephemeral_stream (with self._ephemeral_stream fallback).
        _live_reg = GeminiLiveRegistry.instance()
        _live_key = None
        _live_session: Optional[GeminiLiveSession] = None
        if conv_id and not _is_ephemeral:
            _live_key = (user_id, conv_id, agent_name or 'default',
                         _svc_id, _svc_pool_idx)
            _live_session = _live_reg.get(_live_key)
            if _live_session is not None:
                # Bump reuse_count for the new stream call. The idle
                # invariant is handled by the stdout reader daemon
                # (per-line touch) so we don't need to bump last_used
                # for that here — but touch() does it anyway, which
                # closes the tiny window between get() and the first
                # incoming line as a defence-in-depth.
                _live_reg.touch(_live_key)
        _is_reuse = _live_session is not None

        if _is_reuse:
            # Serialise concurrent _stream_gemini calls targeting the
            # same live session. Without this, bg_bucket_builder's
            # auto_extract_memories (or any other background caller that
            # reuses the same client) can enter _stream while the main
            # stream is still mid-turn, clobber proc.stdin with a rogue
            # message, and end the main turn with an empty stop. The lock
            # is RLock so one thread can re-enter (nested flush/retries
            # during teardown) without deadlocking itself.
            _turn_lock_acquired = _live_session.turn_lock.acquire()
            _owns_turn_lock = _turn_lock_acquired
            try:
                proc = _live_session.proc
                self._pool_container_name = _live_session.pool_container
                _mcp_internal_token = _live_session.mcp_internal_token
                # Non-ephemeral by construction (see guard above); mirror the
                # spawn path's self._gemini_proc assignment so preempt /
                # cancel_codex targets the reused process.
                self._gemini_proc = proc
                # The live session pins CC's actual session_id; that's
                # the source of truth for the jsonl filename CC writes
                # to. Local `session_id` (read from extras at line
                # 1017) and `self._current_session_id` (volatile) MAY
                # diverge from it under concurrent code paths that
                # touch extras or self — _live_session.session_id was
                # captured at register time and is immune to those.
                if not _live_session.session_id:
                    # Invariant from the register site: keep-alive
                    # never registers without a session_id. If we ever
                    # observe an empty one here, registration violated
                    # the contract — fail loudly.
                    raise RuntimeError(
                        f"[gemini-live] REUSE entry for "
                        f"{user_id[:6]}/{conv_id[:8]}/{agent_name} has "
                        f"empty session_id on the live session — the "
                        f"register site should have refused to create "
                        f"this. Pawflow data corruption?")
                # Override the local session_id from extras if it
                # disagrees — the live session's value wins (extras
                # could have been cleared by a sibling code path).
                if session_id and session_id != _live_session.session_id:
                    logger.warning(
                        "[gemini-live] REUSE: extras session_id=%s "
                        "DIVERGES from live session_id=%s — using "
                        "live (CC's reality)",
                        session_id[:12], _live_session.session_id[:12])
                session_id = _live_session.session_id
                # Sync self too so any code path that still reads from
                # the singleton sees the right value. Defence-in-depth.
                self._current_session_id = session_id
                try:
                    self._gemini_container_pid = int(getattr(
                        proc, '_pf_pid', 0) or 0)
                except (TypeError, ValueError):
                    pass
                logger.info(
                    "[gemini-live] REUSE %s/%s/%s@%s#%d (reuse_count=%d, "
                    "lived=%.1fs, session=%s)",
                    user_id[:6], conv_id[:8], agent_name or 'default',
                    _svc_id, _svc_pool_idx, _live_session.reuse_count,
                    time.monotonic() - _live_session.spawn_at,
                    session_id[:12])
            except BaseException:
                if _owns_turn_lock:
                    try: _live_session.turn_lock.release()
                    except Exception: pass
                raise
        else:
            _owns_turn_lock = False
            proc, self._pool_container_name, _mcp_internal_token = (
                self._spawn_gemini_stream(workdir, user_id, conv_id, agent_name,
                                      session_id, model,
                                      ephemeral_stream=_is_ephemeral))

        # Multi-agent catch-up: when resuming a session, inject messages
        # from other agents that CC hasn't seen (arrived after CC's last turn)
        catchup_text = ""
        if session_id and conv_id and agent_name:
            catchup_text = self._gemini_build_catchup_context(conv_id, agent_name)

        # Send initial message as PLAIN TEXT on stdin. Per gemini --help:
        # `-p <prompt>` is required to enable headless mode and is appended
        # to whatever is read from stdin. We pass `-p ""` (or whatever the
        # cmd builder picked) and pipe the real conversation via stdin so
        # the arg-list size never explodes for long histories. CC's
        # `--input-format stream-json` JSON envelope does NOT apply to
        # gemini — it would leak the wrapper into the prompt.
        try:
            if catchup_text:
                initial_text = catchup_text + "\n\n" + initial_text
            if image_blocks:
                logger.warning(
                    "[gemini] %d image attachment(s) ignored — gemini -p "
                    "stdin is text-only; pipe vision via MCP tools instead",
                    len(image_blocks))
            # Gauge source-of-truth: PawFlow `messages` (see codex.py
            # for the rationale). Tokenise via core.token_counter so it
            # matches agent_utils._estimate_tokens and the service-config
            # `token_multiplier`.
            try:
                from core.token_counter import (
                    count_messages_tokens as _count_msgs,
                    resolve_token_multiplier as _resolve_mult,
                )
                _mult = _resolve_mult(getattr(self, "_config_ref", None) or {})
                prompt_tokens = _count_msgs(
                    [{"content": (m.content if hasattr(m, "content") else str(m))}
                     for m in messages],
                    multiplier=_mult)
            except Exception:
                prompt_tokens = int(len(initial_text) / 3.5)
            proc.stdin.write(initial_text)
            proc.stdin.flush()
            try:
                proc.stdin.close()
            except Exception:
                logger.debug("gemini stdin close failed", exc_info=True)
        except BrokenPipeError:
            stderr = ""
            try:
                stderr = "".join(
                    getattr(self, "_stderr_buffer", []) or []
                ).strip()
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
            proc.wait()
            raise LLMClientError(
                f"Gemini CLI pipe broken (exit {proc.returncode}): {stderr[:500]}")

        # SSE publisher for webchat visibility.
        # _event_cid sentinel values:
        #   None               → publishing explicitly suppressed (sub-agent path)
        #   "" or missing attr → fall back to conv_id (main agent default)
        #   any string         → publish to that conv
        # _subagent_event_cb: if set, called INSTEAD of the bus — used by
        #   SubAgentExecutor to re-emit CC's tool_call/tool_result as
        #   sub_agent_tool/sub_agent_tool_result so they land in the
        #   delegate sub-block instead of the main chat.
        # _raw_event_cid resolved earlier at function entry from
        # call_event_cid (with self._event_cid fallback).
        if _raw_event_cid is None:
            _event_cid = ""
        else:
            _event_cid = _raw_event_cid or conv_id
        _subagent_event_cb = getattr(self, '_subagent_event_cb', None)
        # Extract task_id from sub-conv ID so frontend can group task events
        _task_id = ''
        if '::task::' in conv_id:
            _task_id = conv_id.split('::task::')[-1].split('::')[0]
        _agent_ctx = getattr(self, '_agent_ctx', {}) or {}

        def _pub(event_type, data):
            # Safety net: any tool_call/tool_result that escaped unwrap
            # (raw `mcp__pawflow__use_tool` / `use_tool` name with wrapped
            # args) gets unwrapped here before it reaches the UI / subagent
            # relay. Prevents `use_tool(tool_name=read, arguments=[object
            # Object])` from ever being displayed.
            if event_type in ("tool_call", "tool_result") and isinstance(data, dict):
                _t = data.get("tool", "")
                if _t in ("mcp__pawflow__use_tool", "use_tool"):
                    try:
                        from core.llm_client import unwrap_mcp_tool
                        _raw_args = data.get("arguments", {}) or {}
                        # Defensive parse: CC can forward arguments as a JSON
                        # string. unwrap_mcp_tool handles that, but only if the
                        # outer value is a dict-shaped str. Try once more here.
                        if isinstance(_raw_args, str):
                            try:
                                _raw_args = json.loads(_raw_args)
                            except Exception as _parse_err:
                                logger.debug(
                                    "[gemini] _pub outer args not JSON, "
                                    "keeping as string: %s", _parse_err)
                        _u_name, _u_args = unwrap_mcp_tool(_t, _raw_args)
                        # If unwrap didn't resolve (still the wrapper name),
                        # fall back to reading tool_name from the raw args
                        # so the UI never shows `use_tool(...)`.
                        if _u_name in ("mcp__pawflow__use_tool", "use_tool") and isinstance(_raw_args, dict):
                            _u_name = _raw_args.get("tool_name", _t) or _t
                            _inner = _raw_args.get("arguments", _raw_args)
                            if isinstance(_inner, str):
                                try:
                                    _inner = json.loads(_inner)
                                except Exception as _inner_err:
                                    logger.debug(
                                        "[gemini] _pub inner args not "
                                        "JSON, keeping as string: %s",
                                        _inner_err)
                            _u_args = _inner if isinstance(_inner, dict) else _raw_args
                        data["tool"] = _u_name
                        if event_type == "tool_call":
                            data["arguments"] = _u_args
                        # Only log when the unwrap actually produced a
                        # different name — "X → X" is noise from the
                        # no-op branch where raw_args had no usable
                        # tool_name to peel.
                        if _u_name != _t:
                            logger.warning(
                                "[gemini] _pub safety-net unwrapped %s → %s",
                                _t, _u_name)
                    except Exception as _unwrap_err:
                        logger.warning(
                            "[gemini] _pub safety-net unwrap failed "
                            "for tool=%s event=%s: %s",
                            _t, event_type, _unwrap_err, exc_info=True)
            if _subagent_event_cb:
                try:
                    _subagent_event_cb(event_type, data)
                except Exception as _sub_err:
                    # Never-swallow: log loudly. Do NOT raise — raising
                    # here would kill the CC stream parse loop for the
                    # rest of the turn. Log is the pragmatic floor
                    # (user rule: at minimum log).
                    logger.error(
                        "[gemini] subagent_event_cb failed for "
                        "event=%s: %s", event_type, _sub_err, exc_info=True)
                # Subagent events relay to parent via the callback ONLY;
                # they must NOT also hit the parent conv's event bus,
                # otherwise the UI gets duplicates.
                return
            if not _event_cid:
                return
            if _task_id:
                data['task_id'] = _task_id
                data['task_iteration'] = _agent_ctx.get("_task_iteration", 0)
            # If this turn is a delegate reply, tag the event with
            # agent_delegate source so the UI groups it under the private
            # delegate block instead of the main chat.
            _tm = _agent_ctx.get("_turn_mode") or {}
            if (_tm.get("type") == "delegate_reply"
                    and _tm.get("source_agent")
                    and "source" not in data):
                data["source"] = {
                    "type": "agent_delegate",
                    "from": agent_name or "",
                    "to": _tm["source_agent"],
                }
            # Invariant: user-visible state MUST be on disk before we
            # publish the SSE that makes it visible. For message_meta with
            # context_usage, persist the extras synchronously first.
            # (Earlier commit 056b99e moved this AFTER publish on a daemon
            # thread to dodge a lock contention issue; that violated the
            # "visible = persisted" invariant — if the extras lock blocks,
            # we log loudly and still publish so the gauge doesn't freeze,
            # but we never skip logging the failure.)
            # Persist BEFORE publish (strict visible=persisted invariant):
            # if the gauge value fails to hit disk, don't show a live SSE
            # value that will disappear on reload — the UI and the
            # persisted state would disagree. Log loudly and skip the
            # publish so the inconsistency is visible in logs rather than
            # silently drifting.
            #
            # With `get_extra*` readers now holding the same per-conv
            # lock as `set_extra`, there's no concurrent file handle on
            # `extras.json` during the atomic rename — `os.replace`
            # cannot be blocked by our own reads anymore. A
            # PermissionError here now signals a real OS-level problem
            # (disk full, genuine permission issue) and is a bug worth
            # investigating, not masking.
            _persist_ok = True
            if (event_type == "message_meta"
                    and isinstance(data, dict)
                    and (data.get("context_used") or 0) > 0
                    and (data.get("context_max") or 0) > 0
                    and agent_name):
                try:
                    from core.conversation_store import ConversationStore as _CS_pub
                    _store_pub = _CS_pub.instance()
                    _cu_map = _store_pub.get_extra(
                        _event_cid, "context_usage") or {}
                    _cu_map[agent_name] = {
                        "used": int(data["context_used"]),
                        "max": int(data["context_max"]),
                        "pct": float(data.get("context_pct") or 0),
                        "updated_at": int(time.time()),
                    }
                    _store_pub.set_extra(
                        _event_cid, "context_usage", _cu_map)
                except Exception as _ctx_err:
                    _persist_ok = False
                    logger.error(
                        "[gemini] context_usage persist FAILED "
                        "for cid=%s agent=%s: %s — SKIPPING SSE publish "
                        "to keep visible=persisted invariant. This is a "
                        "real bug to investigate (not a transient retry "
                        "case).",
                        _event_cid, agent_name, _ctx_err, exc_info=True)
            if not _persist_ok:
                return
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    _event_cid, event_type, data)
            except Exception as _pub_err:
                logger.error(
                    "[gemini] publish_event failed for event=%s "
                    "cid=%s: %s", event_type, _event_cid, _pub_err,
                    exc_info=True)

        # Read streaming output — accumulate per turn
        content_parts: List[str] = []  # final result text
        last_data: dict = {}
        _turn_count = 0

        # Per-turn accumulator
        _turn_text_parts: List[str] = []
        _turn_tool_calls: list = []
        _turn_thinking: str = ""
        # Redacted thinking tracking. CC/Anthropic return extended-thinking
        # blocks with thinking="" + signature="..." — the content is
        # encrypted at the API level. We can't show the reasoning but we
        # CAN surface "Thought for Xs" so the user sees the agent did
        # reason, and the chat bubble stays visually aligned with the
        # pre-redaction UX.
        _turn_thinking_redacted: bool = False
        _turn_thinking_start: float = 0.0
        _turn_thinking_end: float = 0.0
        _tool_results: dict = {}  # tool_use_id → result text
        # Persistent tool_call_id → unwrapped tool name map. _turn_tool_calls
        # is cleared on every _flush_turn, so by the time a tool_result for
        # tool T arrives (potentially several turns after the tool_use that
        # issued it), the per-turn list can't resolve the name and we'd
        # fall back to the raw tc_id. Keep a stream-scoped map so the
        # tool_result handler can always recover the name — critical for
        # the compact_result short-circuit kill.
        _stream_tc_names: Dict[str, str] = {}
        _current_msg_id: str = ""  # track message ID to detect incremental updates
        # Latest usage observed on an assistant event — used to publish
        # a fresh context-fill % to the webchat. The `result` event's
        # usage may sum differently; the last assistant.message.usage
        # reflects the actual prompt size of the final turn.
        _latest_usage: dict = {}
        self._preempt_pending = 0  # reset at start of each stream
        self._had_preempts_this_turn = False
        self._preempt_killed = False  # set True when send_user_message kills mid-turn
        self._result_emitted = False  # set True when CC emits final result
        self._compacting = False  # set True when CC compact_boundary fires
        # Gemini may report an authoritative context window in
        # result.modelUsage[model].contextWindow. When present, the
        # result handler caches it in self._gemini_context_windows; until
        # then _gemini_context_window() uses the service max_context_size.
        # Track text of every preempt sent via stdin during this stream so
        # we can locate it in CC's session jsonl by content match. Used by
        # _check_gemini_preempt_in_jsonl to determine whether CC has already
        # responded to the preempt (last assistant after preempt) or not.
        self._sent_preempt_texts: list = []

        # Compact-drain state: when CC emits compact_boundary we don't
        # kill+raise immediately anymore. We close CC's stdin (EOF signal),
        # let the parse loop drain remaining events (per-msg_id flushes
        # persist each turn through turn_callback), then raise
        # CCCompactDetected once CC has finished streaming. Killing too
        # early lost already-emitted tool_use/tool_result blocks that were
        # still in the pipe buffer — resulting in gaps in shared.jsonl
        # between the last persisted turn and the compact trigger.
        _compact_pending = [False]
        _compact_drain_timer = [None]

        def _inject_catchup():
            """Check for new messages from other agents and inject via stdin."""
            if not conv_id or not agent_name:
                return
            catchup = self._gemini_build_catchup_context(conv_id, agent_name)
            if not catchup:
                return
            _p = getattr(self, '_gemini_proc', None)
            if _p and _p.poll() is None:
                msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": catchup},
                })
                _p.stdin.write(msg + "\n")
                _p.stdin.flush()
                self._preempt_pending = getattr(self, '_preempt_pending', 0) + 1

        def _flush_turn():
            """Emit the accumulated turn via turn_callback."""
            nonlocal _turn_text_parts, _turn_tool_calls, _turn_thinking, content_parts
            nonlocal _turn_thinking_redacted, _turn_thinking_start, _turn_thinking_end
            text = "".join(_turn_text_parts).strip()
            # Drop phantom tool calls: empty inner args + no result (never executed)
            # For MCP wrapped calls, check the inner arguments, not the wrapper
            def _has_real_args(t):
                # Phantom detection is input-only: empty args, empty bash
                # command, or all-whitespace values. Output-based matching
                # ("no command provided" in result) was removed — a tool's
                # OUTPUT can legitimately contain any phrase (git log of a
                # commit whose message is about the filter itself, grep
                # over this source file, etc.) and we were silently
                # dropping real calls + results from the transcript.
                args = t.get("arguments", {})
                if not args or args == {}:
                    return False
                # MCP wrapper: check inner arguments
                if t.get("name") == "mcp__pawflow__use_tool" and isinstance(args, dict):
                    inner = args.get("arguments", {})
                    # Tolerate flat args: LLM sometimes forgets the "arguments"
                    # wrapper and places tool args at the top level next to
                    # tool_name. Harvest them so the call isn't dropped as
                    # phantom. Symmetric with mcp_bridge.py's flat-args harvest.
                    if not inner or inner == {}:
                        _flat = {k: v for k, v in args.items() if k != "tool_name"}
                        if not _flat:
                            return False
                        inner = _flat
                    # bash with empty/whitespace command (resolve aliases)
                    from core.llm_client import _TOOL_ALIASES
                    inner_tool = args.get("tool_name", "")
                    inner_tool = _TOOL_ALIASES.get(inner_tool, inner_tool)
                    if inner_tool == "bash" and isinstance(inner, dict) and not str(inner.get("command", "")).strip():
                        return False
                    # Any tool where all string values are empty
                    if isinstance(inner, dict) and inner and all(
                            not str(v).strip() for v in inner.values()):
                        return False
                # Non-MCP bash with empty command
                if t.get("name") == "bash" and isinstance(args, dict) and not str(args.get("command", "")).strip():
                    return False
                return True
            tc = [t for t in _turn_tool_calls if _has_real_args(t)]
            _dropped = len(_turn_tool_calls) - len(tc)
            if _dropped:
                _dropped_tcs = [t for t in _turn_tool_calls if not _has_real_args(t)]
                logger.warning("[GEMINI-DROPPED] %d phantom tool call(s): %s", _dropped,
                             json.dumps(_dropped_tcs, default=str, ensure_ascii=False)[:3000])
            turn_thinking = _turn_thinking
            # Redacted thinking synthesis: if CC sent thinking blocks with
            # signature but no content (Anthropic API policy — reasoning
            # is encrypted at the API level), synthesize a user-visible
            # placeholder so the UI still renders a "Thought for Xs"
            # bubble instead of silently dropping the signal.
            if (not turn_thinking) and _turn_thinking_redacted:
                _dur_s = max(0.0, _turn_thinking_end - _turn_thinking_start)
                turn_thinking = (
                    f"[Thought for {_dur_s:.1f}s — reasoning content "
                    f"redacted by the Anthropic API; the signature is "
                    f"preserved in the session so the chain of thought "
                    f"is carried forward on resume.]")
            # Attach results to tool calls
            for t in tc:
                t["result"] = _tool_results.pop(t.get("id", ""), None)
            # Attach thinking to first tool_call (legacy tc_msg carrier)
            # AND pass it through as a 3rd positional to turn_callback so
            # text-only turns (no tool_calls) can still persist it on the
            # assistant text message. Without the 3rd positional, thinking
            # is lost whenever the LLM's reply is pure text.
            _tc_thinking = turn_thinking
            for t in tc:
                t["thinking"] = _tc_thinking
                _tc_thinking = ""  # only first tc gets thinking
            _turn_text_parts = []
            _turn_tool_calls = []
            _turn_thinking = ""
            _turn_thinking_redacted = False
            _turn_thinking_start = 0.0
            _turn_thinking_end = 0.0
            # Mark the most recent turn flush — the sentinel-session
            # EOF nudger in _stall_watchdog uses this as its silence
            # threshold anchor.
            _hb_state["last_turn_flush_ts"] = time.monotonic()
            # gemini-live idle is reset by the stdout reader daemon on every
            # line received — see the touch in _reader_daemon. No need
            # for a per-turn touch here.
            # Phantom-only turn: CC emitted a tool_call we dropped at
            # phantom detection (typo in param name, empty bash command,
            # whitespace-only args) AND nothing else. Without `text` or
            # surviving `tc`, the only thing left is `turn_thinking` —
            # but that thinking was the model "explaining" the phantom
            # call. Keeping it would persist an orphan assistant row
            # (content_len=0, tool_calls=0, thinking_len>0) that the
            # UI renders as a stray "Thought for Xs" bubble polluting
            # the chat. Drop the turn entirely.
            _phantom_only = (_dropped > 0 and not tc and not text)
            if _phantom_only:
                logger.info(
                    "[gemini] flush turn %d SKIPPED: phantom-only "
                    "(dropped %d tc, no text, thinking=%d) — not persisted",
                    _turn_count, _dropped, len(turn_thinking))
            if (text or tc or turn_thinking) and turn_callback and not _phantom_only:
                logger.info("[gemini] flush turn %d: text=%d chars, tc=%d, thinking=%d, callback=%s",
                            _turn_count, len(text), len(tc), len(turn_thinking), bool(turn_callback))
                try:
                    # Back-compat: old callbacks accept (text, tc). New
                    # callbacks accept (text, tc, thinking). Introspect
                    # once so we don't break the surface for anyone.
                    import inspect as _insp
                    try:
                        _nparams = len(_insp.signature(turn_callback).parameters)
                    except (TypeError, ValueError):
                        _nparams = 2
                    if _nparams >= 3:
                        turn_callback(text, tc, turn_thinking)
                    else:
                        turn_callback(text, tc)
                except Exception as e:
                    logger.error("[gemini] turn_callback error: %s", e,
                                 exc_info=True)
            elif text or tc:
                # Internal sentinel sessions (e.g. "_compact" summarizer,
                # "_memory_extract") run without a turn_callback by design —
                # they aggregate the result in content_parts. Log at INFO
                # so that summarizer / memory-extract behavior is visible
                # when these sessions misbehave (CC saturating, looping on
                # phantom tool calls, etc.). Includes a tool-name digest
                # so debugging doesn't require enabling DEBUG everywhere.
                _is_sentinel = conv_id.startswith("_") if conv_id else False
                # mcp__pawflow__use_tool is the meta-dispatch tool — the
                # ACTUAL useful info is in its `tool_name` argument
                # ("read", "compact_result", …). Without unwrapping it,
                # every log line just says "use_tool" and you can't tell
                # the summarizer apart from a phantom call.
                def _tc_label(t):
                    name = t.get("name", "?")
                    args = t.get("arguments") or {}
                    if name == "mcp__pawflow__use_tool" and isinstance(args, dict):
                        inner = args.get("tool_name") or "?"
                        inner_args = args.get("arguments") or {}
                        # Add a single distinguishing arg per inner tool
                        if inner == "read":
                            _p = (inner_args.get("path") or "")[:24]
                            _o = inner_args.get("offset")
                            _l = inner_args.get("limit")
                            return (f"use_tool/read({_p}"
                                    + (f",off={_o}" if _o else "")
                                    + (f",lim={_l}" if _l else "") + ")")
                        if inner == "compact_result":
                            _slen = len(str(inner_args.get("summary", "")))
                            return f"use_tool/compact_result(summary={_slen}c)"
                        return f"use_tool/{inner}"
                    return name
                _tc_names = ",".join(_tc_label(t) for t in tc)[:200]
                if _is_sentinel:
                    logger.info("[gemini] flush turn %d (sentinel '%s'): "
                                "text=%d, tc=%d [%s]",
                                _turn_count, conv_id, len(text), len(tc),
                                _tc_names)
                else:
                    logger.warning("[gemini] flush turn %d but NO turn_callback: "
                                   "text=%d, tc=%d [%s]",
                                   _turn_count, len(text), len(tc), _tc_names)
                # Tell webchat to finalize current streaming element
                _pub("turn_complete", {
                    "agent_name": agent_name,
                    "turn": _turn_count,
                })
                # Clear content_parts — intermediate turns are persisted
                # by turn_callback. Only the LAST turn stays in content_parts
                content_parts.clear()

        # Stall watchdog: if CC emits a system event (init or compact_boundary)
        # but produces no assistant response within _STALL_TIMEOUT seconds,
        # kill the process. The retry loop in stream_chat will relaunch
        # a fresh CC process with the same session.
        # Read from the LLM service config (timeout property) so users can
        # tune it without touching code.
        _STALL_TIMEOUT = int(getattr(self, "timeout", 120) or 120)
        _stall_start_time = 0.0  # time.monotonic() when stall watch begins
        _got_assistant = False   # set True on first assistant event
        _last_tool_result_time = 0.0  # monotonic time of last tool_result with no pending tools
        _pending_tool_ids = set()     # tool_use ids awaiting results
        _emitted_sse_tcs = set()      # tool_use ids for which we sent a SSE tool_call
        _compact_result_done = False  # flip when compact_result tool delivers

        # Phantom tool call detector: if CC emits too many empty/phantom
        # tool calls in a short window, it likely lost context after a bad
        # internal compact. Trigger a PawFlow compact to recover.
        _PHANTOM_WINDOW = 300   # 5 minutes
        _PHANTOM_THRESHOLD = 10
        _phantom_timestamps: list = []  # monotonic timestamps of phantom detections

        _watchdog_stop = threading.Event()

        def _record_phantom(tool_name: str, block_id: str):
            """Record a phantom tool call. If threshold exceeded, trigger compact."""
            now = time.monotonic()
            _phantom_timestamps.append(now)
            # Prune entries outside window
            cutoff = now - _PHANTOM_WINDOW
            while _phantom_timestamps and _phantom_timestamps[0] < cutoff:
                _phantom_timestamps.pop(0)
            count = len(_phantom_timestamps)
            if count >= _PHANTOM_THRESHOLD:
                logger.warning(
                    "[gemini] %d phantom tool calls in %ds window "
                    "(latest: %s id=%s) -- killing CC, PawFlow will compact",
                    count, _PHANTOM_WINDOW, tool_name, block_id)
                if _compact_pending[0]:
                    return
                self._compacting = True
                _compact_pending[0] = True
                # Same rationale as the compact_boundary branch: flush any
                # real pre-phantom turn still sitting in the per-turn
                # accumulator, then kill host + container-side claude CLI
                # immediately. No drain window — phantom tool calls are
                # the symptom of a blown context and keeping the stream
                # open only lets CC emit more garbage that we'd pollute
                # the transcript with.
                try:
                    _flush_turn()
                except Exception as _fe:
                    logger.error(
                        "[gemini] pre-phantom-compact flush failed: %s",
                        _fe, exc_info=True)
                self._kill_gemini_hard(proc)

        self._stall_killed = False  # set by watchdog — retry must be unconditional

        # Heartbeat state for observability — updated by the main event
        # loop so the watchdog (and anyone reading logs) can see WHERE
        # we are when nothing moves for long stretches.
        #
        # On reuse we share the session's hb_state dict by reference so
        # the original reader daemon (captured at spawn time) keeps
        # writing into the SAME object this turn's watchdog reads. We
        # reset the per-turn counters in place rather than allocating a
        # fresh dict, which would orphan the reader's closure.
        if _is_reuse and _live_session.hb_state is not None:
            _hb_state = _live_session.hb_state
            _hb_state.update({
                "last_event_ts": 0.0,
                "last_event_kind": "",
                "last_dispatched_tc": "",
                "last_tool_result_id": "",
                "stream_line_count": 0,
                "last_turn_flush_ts": 0.0,
                "stdin_closed": False,
            })
        else:
            _hb_state = {
                "last_event_ts": 0.0,       # time.monotonic() of last stdout line read
                "last_event_kind": "",      # 'assistant', 'user', 'system', 'result', ...
                "last_dispatched_tc": "",   # last tool_use dispatched (id + name)
                "last_tool_result_id": "",  # last tool_result received
                "stream_line_count": 0,     # total lines read from CC stdout
                "last_turn_flush_ts": 0.0,  # monotonic of last _flush_turn
                "stdin_closed": False,      # True once we sent EOF on stdin
            }
        # Sentinel-session EOF nudge: after _SENTINEL_EOF_INTERVAL
        # seconds of silence on a _compact/_memory_extract session,
        # close proc.stdin to signal EOF to CC. CC interprets this as
        # "no more user input" and finalises its current turn (LLM
        # reply included), which in practice flushes the buffered
        # JSON events to stdout so our reader sees them. Does NOT
        # kill CC (the process keeps running until it decides to
        # exit on its own). The 300s stall watchdog remains as a
        # hard fallback if EOF doesn't suffice.
        _is_sentinel_conv = bool(conv_id) and conv_id.startswith("_")
        _SENTINEL_EOF_INTERVAL = 10.0

        def _stall_watchdog():
            pass  # _stall_killed is on self
            while not _watchdog_stop.is_set():
                if _stall_start_time and not _got_assistant:
                    elapsed = time.monotonic() - _stall_start_time
                    if elapsed >= _STALL_TIMEOUT:
                        logger.warning(
                            "[gemini] Stall detected (%.0fs with no assistant "
                            "response, budget=%.0fs) — killing process for retry. "
                            "hb: lines_read=%d last_event=%s@%.0fs last_tc=%s "
                            "last_tr=%s pending=%s",
                            elapsed, _STALL_TIMEOUT,
                            _hb_state["stream_line_count"],
                            _hb_state["last_event_kind"] or "(none)",
                            time.monotonic() - _hb_state["last_event_ts"]
                              if _hb_state["last_event_ts"] else -1,
                            _hb_state["last_dispatched_tc"] or "(none)",
                            _hb_state["last_tool_result_id"] or "(none)",
                            sorted(_pending_tool_ids)[:5])
                        self._stall_killed = True
                        try:
                            proc.kill()
                        except OSError:
                            pass
                        return
                # Tool result stall: all tools resolved but no assistant response
                if _last_tool_result_time and not _pending_tool_ids:
                    elapsed = time.monotonic() - _last_tool_result_time
                    if elapsed >= _STALL_TIMEOUT:
                        logger.warning(
                            "[gemini] Tool-result stall (%.0fs since last "
                            "tool_result, no pending tools, no assistant) "
                            "— killing for retry. hb: lines_read=%d "
                            "last_event=%s@%.0fs last_tc=%s last_tr=%s",
                            elapsed,
                            _hb_state["stream_line_count"],
                            _hb_state["last_event_kind"] or "(none)",
                            time.monotonic() - _hb_state["last_event_ts"]
                              if _hb_state["last_event_ts"] else -1,
                            _hb_state["last_dispatched_tc"] or "(none)",
                            _hb_state["last_tool_result_id"] or "(none)")
                        self._stall_killed = True
                        try:
                            proc.kill()
                        except OSError:
                            pass
                        return
                # Sentinel-session EOF nudge: when a _compact /
                # _memory_extract session goes silent for
                # _SENTINEL_EOF_INTERVAL AND we're not waiting on a
                # pending tool, close proc.stdin. CC sees EOF on its
                # stdin (stream-json input is done) and finalises its
                # current turn — LLM reply, any pending tool_use,
                # compact_result — then exits cleanly. This replicates
                # what the stall watchdog's proc.kill() incidentally
                # achieves (pipe close on our side → Python unblocks
                # from readline), but WITHOUT killing CC. One-shot per
                # stream: once stdin is closed we can't re-open it, so
                # stdin_closed flag guards re-entry.
                if (_is_sentinel_conv
                        and not _hb_state["stdin_closed"]
                        and _hb_state["last_turn_flush_ts"]
                        and not _pending_tool_ids):
                    _since_turn = (time.monotonic()
                                    - _hb_state["last_turn_flush_ts"])
                    if _since_turn >= _SENTINEL_EOF_INTERVAL:
                        try:
                            if proc.stdin and not proc.stdin.closed:
                                proc.stdin.close()
                                _hb_state["stdin_closed"] = True
                                logger.info(
                                    "[gemini] sentinel '%s' idle "
                                    "%.0fs since last turn — closed "
                                    "stdin (EOF nudge, NOT a kill)",
                                    conv_id, _since_turn)
                        except (OSError, BrokenPipeError) as _eof_err:
                            logger.debug(
                                "[gemini] EOF nudge failed: %s",
                                _eof_err)

                # DEBUG heartbeat every 30s. Kept at debug so default
                # deployments don't log every half-minute on every
                # healthy stream; enable when chasing a specific hang
                # via the usual logger config. The stall watchdog's
                # kill log still fires at WARNING with the same state
                # snapshot for the worst case.
                if not hasattr(_stall_watchdog, '_dbg_count'):
                    _stall_watchdog._dbg_count = 0
                _stall_watchdog._dbg_count += 1
                if _stall_watchdog._dbg_count % 6 == 0:  # every 30s
                    _now = time.monotonic()
                    _since_evt = (_now - _hb_state["last_event_ts"]
                                   if _hb_state["last_event_ts"] else -1)
                    _since_tr = (_now - _last_tool_result_time
                                  if _last_tool_result_time else -1)
                    logger.debug(
                        "[gemini] hb: lines_read=%d last_event=%s (%.0fs ago) "
                        "last_tc=%s last_tr=%s pending=%s got_asst=%s since_tr=%.0fs",
                        _hb_state["stream_line_count"],
                        _hb_state["last_event_kind"] or "(none)",
                        _since_evt,
                        _hb_state["last_dispatched_tc"] or "(none)",
                        _hb_state["last_tool_result_id"] or "(none)",
                        sorted(_pending_tool_ids)[:5],
                        _got_assistant, _since_tr)
                _watchdog_stop.wait(5)

        _watchdog_thread = threading.Thread(target=_stall_watchdog, daemon=True)
        _watchdog_thread.start()

        # Reader daemon: pure stdout → event queue pump. Decouples IO
        # from dispatch so the dispatch loop can block on a single
        # queue.get() and react promptly to proc death / sentinel EOF
        # without polling stdout directly.
        #
        # On reuse: the original reader is still draining the same
        # proc.stdout. We adopt its queue + thread + stop_event; any
        # stale events sitting in the queue from between-turn idle are
        # unexpected (CC stays quiet after `result`) but the dispatch
        # loop below still short-circuits on `result` so they'd be
        # harmless at worst.
        if _is_reuse:
            _event_q = _live_session.event_q
            _reader_thread = _live_session.reader_thread
            _reader_stop = _live_session.stop_event
        else:
            _event_q = queue.Queue()
            _reader_stop = threading.Event()

            def _reader_daemon():
                try:
                    for _line in proc.stdout:
                        if _reader_stop.is_set():
                            break
                        _hb_state["stream_line_count"] += 1
                        _hb_state["last_event_ts"] = time.monotonic()
                        # Reset gemini-live idle on EVERY line received from
                        # CC's stdout. This is the simplest correct
                        # invariant: any byte coming back from CC means
                        # the session is actively streaming — the idle
                        # sweeper must not race with any in-flight
                        # turn, init handshake, slow tool reply, long
                        # thinking block, etc. bump_reuse=False because
                        # one stream call is one logical reuse — the
                        # counter is bumped at REUSE entry, not per
                        # line.
                        if _live_key is not None:
                            try:
                                _live_reg.touch(_live_key, bump_reuse=False)
                            except Exception:
                                pass
                        _line = _line.strip()
                        if not _line:
                            continue
                        try:
                            _ev = json.loads(_line)
                        except json.JSONDecodeError:
                            continue
                        # Defensive: event must be a dict. When the stream
                        # is wrapped in a PTY (script -qfc), extra terminal
                        # output like "Script started" banners or control
                        # sequences can produce parseable-but-non-dict
                        # JSON (e.g. a bare string literal). Log once and
                        # skip instead of exploding on .get().
                        if not isinstance(_ev, dict):
                            logger.warning(
                                "[gemini] non-dict JSON ignored (%s): %r",
                                type(_ev).__name__, str(_ev)[:200])
                            continue
                        _event_q.put(_ev)
                except Exception as _re_err:
                    logger.debug("[gemini-reader] stdout read failed: %s", _re_err)
                finally:
                    _event_q.put(_GEMINI_READER_EOF)

            _reader_thread = threading.Thread(
                target=_reader_daemon, daemon=True, name="gemini-reader")
            _reader_thread.start()

        # Live-session reuse decision: set to True ONLY after a clean
        # result-event break AND no compact/stall/auth failure. Any
        # other exit path (EOF, exception, compact, stall) leaves this
        # False so the `finally` block tears down the proc as usual.
        _keep_alive = False
        # Defensive init: post-finally code reads _stderr inside an
        # `if proc.returncode ...` branch that stays skipped on the
        # keep-alive path (proc still running → returncode=None). Setting
        # to "" here keeps the name bound even if finally takes the
        # keep-alive branch that skips _gemini_cleanup_proc.
        _stderr = ""

        try:
            while True:
                event = _event_q.get()
                if event is _GEMINI_READER_EOF:
                    break

                etype = event.get("type", "")
                _hb_state["last_event_kind"] = etype
                _parent_tc_id = event.get("parent_tool_use_id") or ""
                # Raw event dump at DEBUG. Confirmed CC 1.0+ sends
                # complete `assistant` events (no content_block_delta)
                # with thinking blocks redacted (thinking="" + signature).
                logger.debug("[gemini-raw] %s %.500s", etype, json.dumps(event))

                if etype == "init":
                    sid = event.get("session_id", "") or ""
                    if sid:
                        self._current_session_id = sid
                    if sid and conv_id:
                        _tag = (f"{user_id[:6] or '?'}/{conv_id[:8] or '?'}/"
                                f"{agent_name or 'default'}")
                        if session_id and sid != session_id:
                            logger.warning(
                                "[gemini][%s] SESSION MISMATCH: sent --resume %s but gemini returned %s",
                                _tag, session_id[:12], sid[:12])
                        elif session_id and sid == session_id:
                            logger.info("[gemini][%s] RESUME OK: session %s reused", _tag, sid[:12])
                        else:
                            logger.info("[gemini][%s] NEW session: %s", _tag, sid[:12])
                        try:
                            from core.conversation_store import ConversationStore
                            ConversationStore.instance().set_extra(
                                conv_id,
                                f"gemini_session:{agent_name or 'default'}",
                                sid)
                        except Exception:
                            logger.debug("exception suppressed", exc_info=True)
                    _stall_start_time = time.monotonic()
                    logger.info("[gemini][%s/%s/%s] init — stall watchdog armed (%.0fs timeout)",
                                user_id[:6] or '?', conv_id[:8] or '?',
                                agent_name or 'default', _STALL_TIMEOUT)
                    continue

                if etype == "message":
                    role = event.get("role", "assistant")
                    content = event.get("content", "") or ""
                    if role == "assistant" and content:
                        _got_assistant = True
                        _last_tool_result_time = 0.0
                        _turn_text_parts.append(content)
                        content_parts.append(content)
                        if callback:
                            callback(content)
                    continue

                if etype == "thought":
                    # Same pattern as CC: accumulate into _turn_thinking, let
                    # _flush_turn pass it via turn_callback(text, tc, thinking).
                    content = event.get("content", "") or event.get("text", "") or ""
                    if content:
                        _turn_thinking = (_turn_thinking + content
                                          if _turn_thinking else content)
                    continue

                if etype == "tool_use":
                    _block_id = event.get("call_id", "") or event.get("id", "") or f"gemini-{_turn_count}-{len(_turn_tool_calls)}"
                    _raw_name = event.get("name", "") or ""
                    _raw_args = event.get("args", {}) or event.get("arguments", {}) or {}
                    _block_entry = {
                        "name": _raw_name,
                        "arguments": _raw_args,
                        "id": _block_id,
                    }
                    _existing_idx = None
                    for _i, _tc in enumerate(_turn_tool_calls):
                        if _tc.get("id") == _block_id:
                            _existing_idx = _i
                            break
                    if _existing_idx is not None:
                        _turn_tool_calls[_existing_idx] = _block_entry
                    else:
                        _turn_tool_calls.append(_block_entry)
                    _pending_tool_ids.add(_block_id)
                    from core.llm_client import unwrap_mcp_tool
                    _tc_name, _tc_args = unwrap_mcp_tool(_raw_name, _raw_args)
                    if _block_id and _tc_name:
                        _stream_tc_names[_block_id] = _tc_name
                        _hb_state["last_dispatched_tc"] = (
                            f"{_tc_name}({_block_id[:8]})")
                    if not _tc_args or _tc_args == {} or _tc_args == "{}":
                        logger.warning("[gemini] skipping SSE for empty tool_use %s (id=%s)",
                                       _tc_name, _block_id)
                        continue
                    if _tc_name == "bash" and isinstance(_tc_args, dict) and not str(_tc_args.get("command", "")).strip():
                        logger.warning("[gemini] skipping SSE for bash with empty command (id=%s)", _block_id)
                        _record_phantom(_tc_name, _block_id)
                        continue
                    if isinstance(_tc_args, dict) and _tc_args and all(
                            not str(v).strip() for v in _tc_args.values()):
                        logger.warning("[gemini] skipping SSE for %s with all-empty args (id=%s)", _tc_name, _block_id)
                        _record_phantom(_tc_name, _block_id)
                        continue
                    if _tc_name in ("get_tool_schema", "mcp__pawflow__get_tool_schema"):
                        continue
                    _tc_event = {
                        "tool": _tc_name,
                        "arguments": _tc_args,
                        "tc_id": _block_id,
                        "agent_name": agent_name,
                        "llm_service": getattr(self, '_agent_service', ""),
                        "via": "gemini",
                        "ts": time.time(),
                    }
                    if _parent_tc_id:
                        _tc_event["parent_tc_id"] = _parent_tc_id
                    _emitted_sse_tcs.add(_block_id)
                    # Enqueue BEFORE block_callback — the bridge in the
                    # gemini container forwards the MCP call to the relay
                    # concurrently with the block_callback store write. See
                    # claude_code.py for the long form of this comment.
                    try:
                        from core.background_tool import enqueue_cc_tc, _args_hash
                        enqueue_cc_tc(conv_id, agent_name, _block_id,
                                      _tc_name, _args_hash(_tc_args))
                    except Exception as _ee:
                        logger.debug("[gemini] enqueue_cc_tc skipped: %s", _ee)
                    if block_callback:
                        try:
                            _bc_payload = {
                                "id": _block_id,
                                "name": _raw_name,
                                "arguments": _raw_args,
                                "thinking": _turn_thinking,
                            }
                            if _parent_tc_id:
                                _bc_payload["parent_tc_id"] = _parent_tc_id
                            block_callback("tool_use", _bc_payload)
                            _turn_thinking = ""
                        except Exception as _bc_err:
                            logger.error("[gemini] block_callback tool_use failed: %s",
                                         _bc_err, exc_info=True)
                    continue

                if etype == "tool_result":
                    tc_id = event.get("call_id", "") or event.get("id", "") or ""
                    _result = event.get("output", event.get("result", event.get("content", None)))
                    # Unwrap MCP envelope — see codex.py for the same fix.
                    try:
                        if (isinstance(_result, dict)
                                and isinstance(_result.get("content"), list)):
                            _parts = []
                            for _b in _result["content"]:
                                if isinstance(_b, dict) and _b.get("type") == "text":
                                    _parts.append(_b.get("text", ""))
                            result_str = "\n".join(p for p in _parts if p)
                            if not result_str:
                                result_str = json.dumps(_result, ensure_ascii=False, default=str)
                        elif isinstance(_result, (dict, list)):
                            result_str = json.dumps(_result, ensure_ascii=False, default=str)
                        else:
                            result_str = str(_result) if _result is not None else ""
                    except Exception:
                        result_str = repr(_result)
                    if tc_id:
                        _tool_results[tc_id] = result_str
                        _pending_tool_ids.discard(tc_id)
                        _hb_state["last_tool_result_id"] = (
                            f"{tc_id[:8]}={len(result_str)}c")
                        if not _pending_tool_ids:
                            _last_tool_result_time = time.monotonic()
                    _tr_name = _stream_tc_names.get(tc_id, "") or tc_id
                    if _tr_name in ("get_tool_schema", "mcp__pawflow__get_tool_schema"):
                        continue
                    if tc_id and tc_id not in _emitted_sse_tcs:
                        continue
                    if block_callback:
                        try:
                            _br_payload = {
                                "tc_id": tc_id,
                                "tool": _tr_name,
                                "result": result_str,
                            }
                            if _parent_tc_id:
                                _br_payload["parent_tc_id"] = _parent_tc_id
                            block_callback("tool_result", _br_payload)
                            _tool_results.pop(tc_id, None)
                        except Exception as _br_err:
                            logger.error("[gemini] block_callback tool_result failed: %s",
                                         _br_err, exc_info=True)
                    # Flush this tool's pair immediately — same fix as codex.
                    _flush_turn()
                    # Live gauge update: tool result now joins gemini's
                    # in-memory context. Bump prompt_tokens by the result
                    # token count and publish a fresh `message_meta` so
                    # the UI gauge moves forward at every tool, not only
                    # at end-of-turn. Mirrors codex's per-result bump.
                    try:
                        from core.token_counter import (
                            count_tokens as _ct,
                            resolve_token_multiplier as _rtm,
                        )
                        _mult2 = _rtm(getattr(self, "_config_ref", None) or {})
                        prompt_tokens += _ct(result_str or "", multiplier=_mult2)
                        _ctx_max_now = self._gemini_context_window(model)
                        _pub("message_meta", {
                            "agent_name": agent_name,
                            "context_used": prompt_tokens,
                            "context_max": _ctx_max_now,
                            "context_pct": (prompt_tokens / _ctx_max_now)
                                           if _ctx_max_now > 0 else 0.0,
                            "live": True,
                        })
                    except Exception:
                        logger.debug("live-gauge update failed", exc_info=True)
                    # Mid-turn compact threshold check. Without this, the
                    # only check happens at end-of-stream (`result`), so
                    # a long tool-heavy turn can blow past the threshold
                    # and keep growing. Re-evaluate after every tool
                    # result; on cross, set _compact_pending, kill the
                    # CLI, break the loop, post-loop raises
                    # CCCompactDetected. Mirrors codex.
                    if not _compact_pending[0]:
                        try:
                            _cthp_mid = int(
                                (getattr(self, "_config_ref", None) or {})
                                .get("compact_threshold_pct", 0) or 0)
                        except (TypeError, ValueError):
                            _cthp_mid = 0
                        _ctx_max_mid = self._gemini_context_window(model)
                        if (_cthp_mid > 0 and _ctx_max_mid > 0
                                and prompt_tokens >= int(
                                    _ctx_max_mid * _cthp_mid / 100)):
                            logger.warning(
                                "[gemini] mid-turn usage %d/%d crossed "
                                "PawFlow compact threshold (%d%%) — "
                                "killing gemini to compact NOW",
                                prompt_tokens, _ctx_max_mid, _cthp_mid)
                            self._compacting = True
                            _compact_pending[0] = True
                            try:
                                self._kill_gemini_hard(proc)
                            except Exception as _ke:
                                logger.warning(
                                    "[gemini] mid-turn compact kill "
                                    "failed: %s", _ke)
                            break
                    continue

                if etype == "result":
                    _flush_turn()
                    _stats = event.get("stats", {}) or {}
                    _u = _stats.get("usage", {}) or event.get("usage", {}) or {}
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
                    _latest_usage = {
                        "input_tokens": sum_in,
                        "cached_input_tokens": sum_cached,
                        "output_tokens": sum_out,
                    }
                    _model_usage = (
                        event.get("modelUsage") or event.get("model_usage")
                        or _stats.get("modelUsage") or _stats.get("model_usage")
                        or {})
                    if isinstance(_model_usage, dict):
                        for _m_key in (model, (model or "").lower()):
                            _mu = _model_usage.get(_m_key)
                            if not isinstance(_mu, dict):
                                continue
                            try:
                                _ctx_win = int(
                                    _mu.get("contextWindow")
                                    or _mu.get("context_window")
                                    or _mu.get("contextWindowTokens")
                                    or _mu.get("context_window_tokens")
                                    or 0)
                            except (TypeError, ValueError):
                                _ctx_win = 0
                            if _ctx_win > 0:
                                if not hasattr(self, "_gemini_context_windows"):
                                    self._gemini_context_windows = {}
                                self._gemini_context_windows[model] = _ctx_win
                                self._gemini_context_windows[(model or "").lower()] = _ctx_win
                                break
                    last_data = {"usage": _latest_usage,
                                 "session_id": getattr(self, '_current_session_id', '') or '',
                                 "model": model,
                                 "num_turns": _turn_count or 1,
                                 "modelUsage": _model_usage}
                    _ctx_used_live = prompt_tokens
                    _ctx_max_live = self._gemini_context_window(model)
                    _ctx_pct_live = _ctx_used_live / _ctx_max_live if _ctx_max_live > 0 else 0.0
                    _pub("message_meta", {
                        "agent_name": agent_name,
                        "context_used": _ctx_used_live,
                        "context_max": _ctx_max_live,
                        "context_pct": _ctx_pct_live,
                        "live": True,
                    })
                    # Service-config compact threshold. 0 = no end-of-turn
                    # compact trigger; N>0 = trigger end-of-turn compact at N%,
                    # matching codex and the pre-call check in agent_core.
                    try:
                        _cthp = int((getattr(self, "_config_ref", None) or {})
                                    .get("compact_threshold_pct", 0) or 0)
                    except (TypeError, ValueError):
                        _cthp = 0
                    if (_cthp > 0 and _ctx_max_live > 0
                            and _ctx_used_live >= int(_ctx_max_live * _cthp / 100)):
                        logger.warning(
                            "[gemini] usage %d/%d crossed PawFlow compact threshold (%d%%)",
                            _ctx_used_live, _ctx_max_live, _cthp)
                        self._compacting = True
                        _compact_pending[0] = True
                    self._result_emitted = True
                    continue

                if etype == "error":
                    _err = event.get("error", event)
                    raise LLMClientError(f"gemini error: {json.dumps(_err)[:300]}")

            # Loop exited naturally (result break or stdout EOF). If a
            # compact_boundary fired during this stream, raise now — all
            # pre-compact events have been drained through turn_callback
            # via the per-msg_id rollover in the main loop.
            if _compact_pending[0]:
                from core.llm_client import CCCompactDetected
                raise CCCompactDetected("CC auto-compact detected")

            # Clean result-event exit: no compact, no watchdog stall kill.
            # Promote to keep-alive so `finally` retains proc + reader +
            # pool container for the next turn's reuse. proc.poll() must
            # still be None — a racy EOF break between here and finally
            # would leave us registering a dead session. Ephemeral streams
            # (_live_key is None) never keep alive.
            _stall_killed_flag = bool(getattr(self, '_stall_killed', False))
            _keep_alive = (
                _live_key is not None
                and bool(getattr(self, '_result_emitted', False))
                and not _stall_killed_flag
                and proc.poll() is None
            )

        except _Gemini401Retry:
            # 401 mid-stream: credentials already refreshed, retry once.
            # Evict BEFORE recursing so the retry doesn't re-adopt the
            # about-to-be-killed proc from the registry.
            if _live_key is not None:
                _live_reg.evict(_live_key, "auth_401")
            logger.info("[gemini] retrying after 401 token refresh")
            return self._stream_gemini(
                messages, model, temperature, max_tokens, tools, callback,
                turn_callback=turn_callback, block_callback=block_callback,
                _is_auth_retry=True)
        except BaseException as _dispatch_exc:
            # ANY other exception in the dispatch loop (CCCompactDetected,
            # KeyboardInterrupt, AgentCancelled, programming bugs, etc.):
            # 1. Evict the live-session entry IMMEDIATELY so a concurrent
            #    reuse lookup cannot adopt this about-to-die proc. The
            #    finally block evicts too, but that runs AFTER `raise`
            #    propagates through intermediate frames — a concurrent
            #    turn that calls `_live_reg.get(key)` in that window
            #    would get a session pointing at a dying subprocess.
            # 2. Force `_keep_alive = False` so the finally path takes
            #    the teardown branch unconditionally (the normal keep-
            #    alive computation happens AFTER the while loop; if an
            #    exception escapes the loop, that computation never
            #    ran — but defense-in-depth in case someone later adds
            #    a keep-alive assignment earlier in the flow).
            # 3. Kill hard NOW, not just in finally. `_gemini_cleanup_proc` in
            #    finally does `proc.kill()` + pool release (which is
            #    `docker rm -f` in the 1:1 model, so the container IS
            #    nuked). Calling `_kill_gemini_hard` here is belt-and-
            #    suspenders: it adds a container-side pgid kill that
            #    reaps any Node workers CC forked BEFORE the docker rm
            #    tears down the namespace. Redundant but cheap; keeps
            #    the exception teardown path symmetric with the explicit
            #    kill paths (compact_boundary, compact_result, phantom).
            # BaseException (not just Exception) catches AgentCancelled /
            # SystemExit / KeyboardInterrupt too — those also leave a
            # live proc behind if we don't tear down here.
            if _live_key is not None:
                try:
                    _live_reg.evict(_live_key, "dispatch_exception")
                except Exception:
                    logger.debug("early-evict failed", exc_info=True)
            _keep_alive = False
            try:
                self._kill_gemini_hard(proc)
            except Exception:
                logger.debug("kill_cc_hard in except failed", exc_info=True)
            logger.info(
                "[gemini] dispatch loop aborted by %s: %.200s",
                type(_dispatch_exc).__name__, str(_dispatch_exc))
            raise
        finally:
            # Stop compact stall watchdog
            _watchdog_stop.set()
            # Cancel compact-drain timeout timer if still pending
            # (loop exited cleanly before deadline, or via an exception
            # that wasn't the compact path at all).
            try:
                _t = _compact_drain_timer[0]
                if _t is not None:
                    _t.cancel()
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
            # Flush any pending turn (ensures last text is persisted even if interrupted)
            try:
                _flush_turn()
            except Exception:
                logger.debug("exception suppressed", exc_info=True)

            if _keep_alive:
                # Retain proc + reader + pool container for reuse. Skip
                # _gemini_cleanup_proc / pool release / token revoke — those are
                # lifecycle-scoped to the live session, not the turn.
                # The reader daemon's per-line touch already keeps
                # last_used fresh — no end-of-stream touch needed.
                try:
                    if _is_reuse:
                        pass  # nothing to do; reader keeps last_used fresh
                    else:
                        # CC's session_id captured during this stream's
                        # init event (line 1908) AND/OR returned in the
                        # final result event. Either source is the
                        # authoritative jsonl filename CC is writing
                        # to — pin it on the live session so post-
                        # result preempt checks (and any future
                        # introspection) can locate the file without
                        # going through volatile state (extras, self).
                        _live_session_id = (
                            getattr(self, '_current_session_id', '')
                            or last_data.get('session_id', '')
                            or '')
                        if not _live_session_id:
                            # Hard invariant: a live session that gets
                            # registered must know its CC session_id —
                            # that's the whole point of keep-alive.
                            # Without it, future REUSEs cannot inspect
                            # CC's jsonl, preempt-loss check goes blind,
                            # and the bug we're fixing returns.
                            raise RuntimeError(
                                "[gemini-live] keep-alive register called "
                                "without a session_id (init event not "
                                "seen and result event lacks session_id) "
                                "— refusing to register a blind live "
                                "session. Falling through to teardown.")
                        # Pass live-session fields through to the registry
                        # — see codex for the same fix. Without this, REUSE
                        # finds an entry with empty session_id / proc and
                        # raises "data corruption?".
                        _live_reg.register(
                            _live_key, self._pool_container_name, workdir,
                            service_id=_svc_id,
                            session_id=_live_session_id,
                            proc=proc,
                            event_q=_event_q,
                            reader_thread=_reader_thread,
                            stop_event=_reader_stop,
                            mcp_internal_token=_mcp_internal_token,
                            hb_state=_hb_state,
                        )
                        # Start the idle sweeper on first register — no
                        # work until there's a session to sweep.
                        _live_reg.ensure_sweeper(
                            killer=self._kill_gemini_hard)
                except Exception:
                    logger.warning(
                        "[gemini-live] register/touch failed; falling back "
                        "to full teardown", exc_info=True)
                    _keep_alive = False  # fall through to teardown below
                else:
                    # Still recover refreshed OAuth tokens from workdir
                    # — CC may have refreshed mid-turn and we want them
                    # persisted for resume-without-live-session paths.
                    try:
                        self._gemini_recover_tokens(workdir)
                    except Exception:
                        logger.debug(
                            "_gemini_recover_tokens failed", exc_info=True)

            if not _keep_alive:
                # Full teardown: evict any live-session entry first so
                # the next turn doesn't re-adopt the dead proc, then
                # kill + recover + revoke as before.
                if _is_reuse and _live_key is not None:
                    _live_reg.evict(_live_key, "turn_failed")
                # Cleanup process — _gemini_cleanup_proc captures stderr internally
                _stderr = self._gemini_cleanup_proc(proc)
                # Recover refreshed tokens from workdir (Codex may have refreshed them)
                self._gemini_recover_tokens(workdir)
                # Revoke the internal-auth token minted for this CC invocation —
                # scoped to the lifetime of this stream, not retained across calls.
                # Without this, tokens accumulate in core.internal_auth._tokens
                # until server restart (memory-only, but a lingering valid token
                # leaked from .mcp.json or process env stays replayable).
                if _mcp_internal_token:
                    try:
                        from core.internal_auth import revoke_token
                        revoke_token(_mcp_internal_token)
                    except Exception:
                        logger.debug("internal-auth revoke failed", exc_info=True)

            # Release the live-session turn lock acquired at REUSE entry
            # (line 1111). Held across the entire stream call so concurrent
            # callers — bg_bucket_builder threads, next user turn's
            # bg_streaming thread, etc. — don't push rogue input onto the
            # in-flight session's stdin. WITHOUT this release, the first
            # successful REUSE held the lock forever (acquire had no
            # corresponding release on success path) and every subsequent
            # turn that hit REUSE blocked indefinitely on
            # turn_lock.acquire(). Symptom in the wild: user sends a
            # second message, agent stream starts, log stops on
            # `codex stream: conv_id=...`, no spawn / no REUSE log,
            # 4+ minutes of silent freeze before the user gives up.
            # RLock release is balanced with the single acquire at 1111;
            # do it inside the finally so we cover normal return AND any
            # exception that propagates through the streaming body.
            if _owns_turn_lock and _live_session is not None:
                try:
                    _live_session.turn_lock.release()
                except Exception:
                    logger.debug(
                        "turn_lock release failed (likely already released "
                        "via the early-error path at line 1129)",
                        exc_info=True)
                _owns_turn_lock = False

        # Don't error on non-zero exit if we got a successful result
        # (process was killed after break on result event — that's expected).
        # `_compact_result_done` counts: when the sentinel compact session
        # delivers its payload via the compact_result tool, we
        # intentionally SIGKILL CC before the final result event can
        # fire (otherwise CC stalls waiting for another input). The
        # payload IS the successful outcome; treat the 137 exit the
        # same as a clean result-event break.
        _got_result = (
            bool(last_data.get("session_id") or last_data.get("result"))
            or _compact_result_done)
        _was_compact_stall = (proc.returncode == -9 and _stall_start_time > 0 and not _got_assistant)
        # Tool-result / no-assistant stalls are PawFlow-watchdog kills. CC
        # produced work up to that point; the kill is our own recovery
        # action, not a user-facing failure. Tag the exception so the
        # retry loop in LLMClient.complete_stream treats it as retryable
        # (same path as compact_stall) instead of surfacing an error to
        # the user on the first attempt.
        _was_tool_stall = bool(self._stall_killed) and not _was_compact_stall
        # Preempt kills are OUR action — the user typed a new message and
        # we tore down the in-flight CLI on purpose. Swallow the
        # non-zero exit cleanly so the agent loop sees a normal end-of-
        # turn and the queued message gets a fresh turn via PendingQueue.
        if self._preempt_killed and proc.returncode and proc.returncode != 0:
            logger.info(
                "[gemini] preempt-kill exit=%s ignored — the next "
                "iteration will pick up the queued message via "
                "PendingQueue", proc.returncode)
        elif proc.returncode and proc.returncode != 0 and not _got_result:
            if _stderr:
                logger.error("Gemini CLI stderr: %.500s", _stderr)
            if _was_compact_stall:
                _reason = "compact_stall"
            elif _was_tool_stall:
                _reason = "tool_stall"
            else:
                _reason = ""
            raise LLMClientError(
                f"Gemini CLI stream exited with code {proc.returncode}"
                + (f" ({_reason})" if _reason else "")
                + (f": {_stderr[:200]}" if _stderr else ""))

        # If turn_callback handled all turns, don't return content
        # (prevents agent loop from persisting the same text again)
        full_content = "" if turn_callback else "".join(content_parts)

        new_session = last_data.get("session_id", "")
        if new_session:
            # Persist session_id in conversation store (NOT on self — client is shared)
            if conv_id:
                try:
                    from core.conversation_store import ConversationStore
                    ConversationStore.instance().set_extra(
                        conv_id, f"gemini_session:{agent_name or 'default'}",
                        new_session)
                except Exception:
                    logger.debug("exception suppressed", exc_info=True)

        # Context-fill semantics: report the LAST assistant event's per-call
        # usage (real prompt size at end of turn, ≤ context_max), NOT the
        # `result.usage` summed across sub-calls (which balloons cache_read to
        # N×prefix and makes the UI clamp at 100%). `_latest_usage` is captured
        # at every assistant event in the stream loop.
        _u_final = _latest_usage or last_data.get("usage", {})
        _ti_in_raw = _u_final.get("input_tokens", 0)
        _to = _u_final.get("output_tokens", 0)
        if not (_ti_in_raw or _to):
            for _mu in last_data.get("modelUsage", {}).values():
                _ti_in_raw += _mu.get("inputTokens", 0) + _mu.get("input_tokens", 0)
                _to += _mu.get("outputTokens", 0) + _mu.get("output_tokens", 0)
        # Same fix as codex: gemini's `input_tokens` sums every internal
        # iteration's prompt size, so it balloons the gauge and triggers
        # spurious compacts. Use a prompt-based estimate.
        _ti_estimate = prompt_tokens if prompt_tokens > 0 else _ti_in_raw
        return LLMResponse(
            content=full_content,
            model=last_data.get("model", model),
            tokens_in=_ti_estimate,
            tokens_out=_to,
            total_tokens=_ti_estimate + _to,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            finish_reason="stop",
            raw=last_data,
        )
