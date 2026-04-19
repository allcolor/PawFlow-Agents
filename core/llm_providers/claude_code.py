"""LLM provider mixin -- Claude Code CLI (subprocess-based).

Uses --input-format stream-json + --output-format stream-json for
bidirectional streaming. This enables:
- Real-time output streaming (tool calls, text, thinking)
- Preempt: send new user messages on stdin while Claude Code works
- Interrupt: send interrupt signal on stdin to stop gracefully
"""

import json
import logging
import os
import re
import subprocess
import threading
import time
from typing import List

from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin, _get_sessions_base

logger = logging.getLogger(__name__)


class _CC401Retry(Exception):
    """Internal signal: OAuth 401 mid-stream, credentials refreshed, retry the call."""


class LLMClaudeCodeMixin(ClaudeCodeSessionMixin):
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
    # _get_tool_relay_info, _get_mcp_bridge_path, _DISALLOWED_BUILTIN_TOOLS

    # ── Process management ──────────────────────────────────────────

    def send_user_message(self, text: str, attachments: list = None):
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
        _conv = getattr(self, '_conversation_id', '') or ''
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
            conv_id = getattr(self, '_conversation_id', "")
            agent_name = getattr(self, '_agent_name', "")
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
                                "content": "Stop what you're doing right now. I need your attention on something else. Finish your current response briefly and wait for the next message."},
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
            pass
        _container = getattr(self, '_pool_container_name', '') or ''
        _pid = int(getattr(self, '_cc_container_pid', 0) or 0)
        if not _container or not _pid:
            logger.error(
                "[claude-code] _kill_cc_hard SKIPPED -- container=%r "
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
        """Clean up a Claude Code subprocess. Returns captured stderr."""
        self._claude_proc = None
        # Session is over — drop the tracked PID and session id so a
        # later force-stop doesn't kill into a stale/reused container.
        self._current_session_id = ""
        self._cc_container_pid = 0
        # Pool mode: release slot (don't kill the container)
        _pool_name = getattr(self, '_pool_container_name', None)
        if _pool_name:
            self._pool_release(_pool_name)
            self._pool_container_name = None
        # Kill process FIRST so pipes become readable (no more blocking)
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
        # NOW read stderr from the drain buffer (live thread owns the fd)
        stderr = ""
        try:
            stderr = "".join(getattr(self, "_stderr_buffer", []) or [])
        except Exception:
            pass
        # Close all streams
        for stream in (proc.stdout, proc.stdin, proc.stderr):
            try:
                if stream and not stream.closed:
                    stream.close()
            except Exception:
                pass
        return stderr

    # ── Legacy session scrub ────────────────────────────────────────

    # Matches placeholders the agent used to write into user text before the
    # vision channel was wired properly, e.g. "[image: image_1234567890_2.png]".
    # The image bytes travel via the native vision path now, so the text
    # reference is stale — keeping it would make the agent pattern-match
    # "image attached → call see('image_1234567890_2.png')" on every resume.
    _LEGACY_IMAGE_RE = re.compile(
        r'\s*\[image:\s*image_\d+_\d+\.[A-Za-z0-9]+\s*\]\s*')

    def _scrub_legacy_image_placeholders(self, session_file: str) -> None:
        """Rewrite a claude-code .jsonl session file in place, stripping
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

    def _pool_popen(self, workdir: str, cmd: list, **popen_kwargs) -> tuple:
        """Launch claude in a pool container or locally.

        Returns (proc, pool_container_name). Caller must release pool_container
        when done (if not None).
        """
        _containerize = getattr(self, 'containerize', False)
        _env = self._claude_code_env(workdir)
        if _containerize:
            from core.claude_code_pool import ClaudeCodePool
            pool = ClaudeCodePool.instance()
            container = pool.acquire()
            _rel = os.path.relpath(workdir, _get_sessions_base()).replace("\\", "/")
            _session_dir = f"/cc_sessions/{_rel}"
            # Pass API key / base URL to container if configured
            _extra = {}
            if _env.get("ANTHROPIC_API_KEY"):
                _extra["ANTHROPIC_API_KEY"] = _env["ANTHROPIC_API_KEY"]
            if _env.get("ANTHROPIC_BASE_URL"):
                _extra["ANTHROPIC_BASE_URL"] = _env["ANTHROPIC_BASE_URL"]
            proc = pool.exec_claude(
                container, _session_dir, cmd[1:],  # skip 'claude' binary
                extra_env=_extra or None,
                **popen_kwargs)
            return proc, container
        else:
            proc = subprocess.Popen(
                cmd, cwd=workdir, env=_env,
                **popen_kwargs)
            return proc, None

    def _pool_release(self, container_name):
        """Release a pool container slot."""
        if container_name:
            try:
                from core.claude_code_pool import ClaudeCodePool
                ClaudeCodePool.instance().release(container_name)
            except Exception:
                pass

    # ── Streaming ───────────────────────────────────────────────────

    @staticmethod
    def _extract_images(messages, user_id: str, conversation_id: str) -> list:
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
                "_extract_images: user_id is required to resolve image_ref "
                "attachments (owner-scoped access control)")
        if not conversation_id:
            raise ValueError(
                "_extract_images: conversation_id is required to resolve "
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

            logger.info("[claude-code] catch-up: %d messages for %s", count, agent_name)
            return "\n".join(lines)
        except Exception as e:
            logger.warning("[claude-code] catch-up failed: %s", e)
            return ""

    def _stream_claude_code(
        self, messages, model, temperature, max_tokens, tools, callback=None,
        turn_callback=None, _is_auth_retry=False,
    ):
        """Stream from claude CLI using bidirectional stream-json.

        Input: JSON lines on stdin (user messages, can preempt anytime)
        Output: JSON lines on stdout (events: assistant, user, result, etc.)

        turn_callback(text, tool_calls): called at each turn boundary so
        the agent loop can persist intermediate messages. Each Claude Code
        assistant turn = one message in the conversation.

        Claude Code uses MCP for tool calls — tools param is ignored.
        """
        from core.llm_client import LLMClientError, LLMResponse

        user_id = getattr(self, '_user_id', "")
        conv_id = getattr(self, '_conversation_id', "")
        agent_name = getattr(self, '_agent_name', "")

        # Extract images BEFORE serialization (they'll be sent as content blocks).
        # user_id + conv_id are REQUIRED — FileStore enforces owner×conv
        # access control, and a missing identifier silently drops the
        # user's image. _extract_images raises if either is empty.
        image_blocks = self._extract_images(
            messages, user_id=user_id, conversation_id=conv_id)

        # Always load session_id from the store for THIS conversation
        # (never from self — the client is shared across conversations)
        session_id = ""
        if conv_id:
            try:
                from core.conversation_store import ConversationStore
                session_id = ConversationStore.instance().get_extra(
                    conv_id, f"claude_session:{agent_name or 'default'}") or ""
                if session_id:
                    logger.info("Restored claude session: %s", session_id)
            except Exception:
                pass

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

        initial_text = self._build_stdin_with_system(system_prompt, user_text)
        logger.debug("[claude-code] prompt: system=%d user=%d images=%d msgs=%d session=%s",
                     len(system_prompt), len(user_text), len(image_blocks), len(messages),
                     "resume" if session_id else "new")

        logger.info("claude-code stream: conv_id='%s' user='%s' agent='%s' session='%s'",
                     conv_id, user_id, agent_name, session_id[:12] if session_id else "new")

        workdir = self._get_session_workdir(conv_id, agent_name, user_id)
        # Resume with same credential that created the session (approach 3)
        _resume_pool_idx = -1
        if session_id and conv_id:
            try:
                _resume_pool_idx = int(ConversationStore.instance().get_extra(
                    conv_id, f"claude_pool_idx:{agent_name or 'default'}") or -1)
            except Exception:
                pass
        self._setup_credentials(workdir, pool_index=_resume_pool_idx)
        # Store pool index for this session
        if conv_id and hasattr(self, '_current_pool_index'):
            try:
                ConversationStore.instance().set_extra(
                    conv_id, f"claude_pool_idx:{agent_name or 'default'}",
                    self._current_pool_index)
            except Exception:
                pass
        mcp_path = self._setup_mcp_config(workdir, user_id, conv_id, agent_name)
        _containerize = getattr(self, 'containerize', False)

        # In pool mode, MCP config path is inside /cc_sessions/...
        _mcp_arg = mcp_path
        if _containerize and mcp_path:
            # Pool: symlink /workspace → /cc_sessions/<session_dir>
            # CC sees /workspace just like the old per-container model
            _mcp_arg = f"/workspace/{os.path.basename(mcp_path)}"
            _container_workdir = "/workspace"
        else:
            _container_workdir = workdir

        cmd = self._build_claude_cmd(model, session_id,
                                     mcp_config_path=_mcp_arg,
                                     workdir=workdir)

        logger.info("claude-code stream: cwd=%s, containerize=%s, cmd=%s",
                     workdir, _containerize, " ".join(str(c) for c in cmd[:20]))
        if session_id:
            # Verify session file exists at expected path
            _expected_session_file = os.path.join(workdir, "projects", "-workspace", f"{session_id}.jsonl")
            _exists = os.path.exists(_expected_session_file)
            _size = os.path.getsize(_expected_session_file) if _exists else 0
            logger.info("claude-code RESUME: session_id=%s file_exists=%s file_size=%d path=%s",
                         session_id, _exists, _size, _expected_session_file)
            # Scrub legacy [image: image_<ts>_<n>.<ext>] placeholders from
            # user text fields. These were written before the vision-
            # placeholder fix and make the agent pattern-match "image
            # attached → call see() with this filename" on every new
            # user turn. The image bytes are still forwarded via the
            # native vision channel, so stripping the text reference is
            # purely cosmetic for the transcript AND prevents the bogus
            # see() calls.
            if _exists:
                try:
                    self._scrub_legacy_image_placeholders(_expected_session_file)
                except Exception as _scrub_err:
                    logger.warning("[claude-code] session scrub failed (%s): %s",
                                   session_id[:8], _scrub_err)

        # Track pool container for cleanup
        self._pool_container_name = None
        _auth_retried = _is_auth_retry

        try:
            proc, self._pool_container_name = self._pool_popen(
                workdir, cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            # Ephemeral streams (btw) don't register proc — they don't
            # need preempt/cancel and must not overwrite the main agent's proc.
            if not getattr(self, '_ephemeral_stream', False):
                self._claude_proc = proc
            # Capture the container-side claude PID from the shell wrapper's
            # stderr preamble (`__PF_CLAUDE_PID=<n>` emitted by the pool's
            # shell script before `exec setpriv ... claude`). Saves us from
            # pkill/argv matching which breaks on fresh sessions where CC's
            # sid isn't in argv (no `--resume`). A tiny daemon thread drains
            # stderr continuously so the pipe can't fill; captured content
            # is buffered for post-mortem inspection.
            self._cc_container_pid = 0
            self._stderr_buffer = []
            def _drain_stderr():
                try:
                    for _line in proc.stderr:
                        self._stderr_buffer.append(_line)
                        if (not self._cc_container_pid
                                and '__PF_CLAUDE_PID=' in _line):
                            try:
                                _pid_str = _line.split(
                                    '__PF_CLAUDE_PID=', 1)[1].strip()
                                self._cc_container_pid = int(_pid_str)
                                logger.info(
                                    "[claude-code] captured container PID=%d "
                                    "(container=%s)",
                                    self._cc_container_pid,
                                    self._pool_container_name)
                            except Exception:
                                pass
                except Exception:
                    pass
            import threading as _th
            _th.Thread(target=_drain_stderr, daemon=True,
                       name="cc-stderr-drain").start()
        except FileNotFoundError:
            _bin = "docker" if _containerize else self.claude_binary
            if self._pool_container_name:
                from core.claude_code_pool import ClaudeCodePool
                ClaudeCodePool.instance().release(self._pool_container_name)
                self._pool_container_name = None
            raise LLMClientError(
                f"Binary '{_bin}' not found. "
                + ("Install Docker Desktop." if _containerize
                   else "Install with: npm install -g @anthropic-ai/claude-code"))

        # Multi-agent catch-up: when resuming a session, inject messages
        # from other agents that CC hasn't seen (arrived after CC's last turn)
        catchup_text = ""
        if session_id and conv_id and agent_name:
            catchup_text = self._build_catchup_context(conv_id, agent_name)

        # Send initial message as stream-json (keep stdin open for preempt/interrupt)
        try:
            # Prepend catch-up context to the initial message
            if catchup_text:
                initial_text = catchup_text + "\n\n" + initial_text

            if image_blocks:
                # Multipart: text + images as content array (enables vision)
                content = [{"type": "text", "text": initial_text}] + image_blocks
                msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": content},
                })
            else:
                msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": initial_text},
                })
            proc.stdin.write(msg + "\n")
            proc.stdin.flush()
        except BrokenPipeError:
            stderr = ""
            try:
                stderr = "".join(
                    getattr(self, "_stderr_buffer", []) or []
                ).strip()
            except Exception:
                pass
            proc.wait()
            raise LLMClientError(
                f"Claude CLI pipe broken (exit {proc.returncode}): {stderr[:500]}")

        # SSE publisher for webchat visibility.
        # _event_cid sentinel values:
        #   None               → publishing explicitly suppressed (sub-agent path)
        #   "" or missing attr → fall back to conv_id (main agent default)
        #   any string         → publish to that conv
        # _subagent_event_cb: if set, called INSTEAD of the bus — used by
        #   SubAgentExecutor to re-emit CC's tool_call/tool_result as
        #   sub_agent_tool/sub_agent_tool_result so they land in the
        #   delegate sub-block instead of the main chat.
        _raw_event_cid = getattr(self, '_event_cid', '')
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
                            except Exception:
                                pass
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
                                except Exception:
                                    pass
                            _u_args = _inner if isinstance(_inner, dict) else _raw_args
                        data["tool"] = _u_name
                        if event_type == "tool_call":
                            data["arguments"] = _u_args
                        logger.warning("[claude-code] _pub safety-net unwrapped %s → %s", _t, _u_name)
                    except Exception:
                        pass
            if _subagent_event_cb:
                try:
                    _subagent_event_cb(event_type, data)
                except Exception:
                    pass
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
            # Persist context-fill so the gauge survives across reloads
            # without waiting for the next turn. CC emits message_meta
            # directly here (not via agent_core's _agent_source path), so
            # this is the ONLY place where the per-agent context_usage map
            # gets written for CC turns.
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
                except Exception:
                    pass
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    _event_cid, event_type, data)
            except Exception:
                pass

        # Read streaming output — accumulate per turn
        content_parts: List[str] = []  # final result text
        last_data: dict = {}
        _turn_count = 0

        # Per-turn accumulator
        _turn_text_parts: List[str] = []
        _turn_tool_calls: list = []
        _turn_thinking: str = ""
        _tool_results: dict = {}  # tool_use_id → result text
        _current_msg_id: str = ""  # track message ID to detect incremental updates
        # Latest usage observed on an assistant event — used to publish
        # a fresh context-fill % to the webchat. The `result` event's
        # usage may sum differently; the last assistant.message.usage
        # reflects the actual prompt size of the final turn.
        _latest_usage: dict = {}
        self._preempt_pending = 0  # reset at start of each stream
        self._had_preempts_this_turn = False
        self._result_emitted = False  # set True when CC emits final result
        self._compacting = False  # set True when CC compact_boundary fires
        # Track text of every preempt sent via stdin during this stream so
        # we can locate it in CC's session jsonl by content match. Used by
        # _check_preempt_in_jsonl to determine whether CC has already
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
            catchup = self._build_catchup_context(conv_id, agent_name)
            if not catchup:
                return
            _p = getattr(self, '_claude_proc', None)
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
                logger.warning("[CC-DROPPED] %d phantom tool call(s): %s", _dropped,
                             json.dumps(_dropped_tcs, default=str, ensure_ascii=False)[:3000])
            thinking = _turn_thinking
            # Attach results to tool calls
            for t in tc:
                t["result"] = _tool_results.pop(t.get("id", ""), None)
            # Attach thinking
            for t in tc:
                t["thinking"] = thinking
                thinking = ""  # only first tc gets thinking
            _turn_text_parts = []
            _turn_tool_calls = []
            _turn_thinking = ""
            if (text or tc) and turn_callback:
                logger.info("[claude-code] flush turn %d: text=%d chars, tc=%d, callback=%s",
                            _turn_count, len(text), len(tc), bool(turn_callback))
                try:
                    turn_callback(text, tc)
                except Exception as e:
                    logger.error("[claude-code] turn_callback error: %s", e,
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
                    logger.info("[claude-code] flush turn %d (sentinel '%s'): "
                                "text=%d, tc=%d [%s]",
                                _turn_count, conv_id, len(text), len(tc),
                                _tc_names)
                else:
                    logger.warning("[claude-code] flush turn %d but NO turn_callback: "
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
                    "[claude-code] %d phantom tool calls in %ds window "
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
                        "[claude-code] pre-phantom-compact flush failed: %s",
                        _fe, exc_info=True)
                self._kill_cc_hard(proc)

        self._stall_killed = False  # set by watchdog — retry must be unconditional

        def _stall_watchdog():
            pass  # _stall_killed is on self
            while not _watchdog_stop.is_set():
                if _stall_start_time and not _got_assistant:
                    elapsed = time.monotonic() - _stall_start_time
                    if elapsed >= _STALL_TIMEOUT:
                        logger.warning(
                            "[claude-code] Stall detected (%.0fs with no assistant "
                            "response, budget=%.0fs) — killing process for retry",
                            elapsed, _STALL_TIMEOUT)
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
                            "[claude-code] Tool-result stall (%.0fs since last "
                            "tool_result, no pending tools, no assistant) "
                            "— killing for retry", elapsed)
                        self._stall_killed = True
                        try:
                            proc.kill()
                        except OSError:
                            pass
                        return
                # Debug: log watchdog state every 30s
                if not hasattr(_stall_watchdog, '_dbg_count'):
                    _stall_watchdog._dbg_count = 0
                _stall_watchdog._dbg_count += 1
                if _stall_watchdog._dbg_count % 6 == 0:  # every 30s (5s wake × 6)
                    logger.debug(
                        '[claude-code] watchdog state: stall_start=%.1f got_assistant=%s '
                        'last_tr=%.1f pending=%s budget=%.0fs',
                        _stall_start_time, _got_assistant,
                        _last_tool_result_time, _pending_tool_ids, _STALL_TIMEOUT)
                _watchdog_stop.wait(5)

        _watchdog_thread = threading.Thread(target=_stall_watchdog, daemon=True)
        _watchdog_thread.start()

        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type", "")
                _parent_tc_id = event.get("parent_tool_use_id") or ""
                # Raw event dump — too chatty for INFO (every assistant
                # delta, every tool_use block, every rate_limit_event…).
                # Stays at debug so anyone diagnosing CC behavior can
                # re-enable it via log level.
                logger.debug("[claude-code] %s %.200s", etype, json.dumps(event))

                if etype == "system":
                    # Capture AND persist session_id from init event immediately.
                    # Must be in ConversationStore before any preempt triggers
                    # _prepare_agent_context (which checks for session to skip compact).
                    sid = event.get("session_id", "")
                    if sid:
                        self._current_session_id = sid
                    if sid and conv_id:
                        if session_id and sid != session_id:
                            logger.warning(
                                "[claude-code] SESSION MISMATCH: sent --resume %s but CC returned %s "
                                "(resume FAILED — CC created new session)",
                                session_id[:12], sid[:12])
                        elif session_id and sid == session_id:
                            logger.info("[claude-code] RESUME OK: session %s reused", sid[:12])
                        else:
                            logger.info("[claude-code] NEW session: %s", sid[:12])
                        try:
                            from core.conversation_store import ConversationStore
                            ConversationStore.instance().set_extra(
                                conv_id,
                                f"claude_session:{agent_name or 'default'}",
                                sid)
                        except Exception:
                            pass
                    # compact_boundary → drain CC stream + PawFlow compact; init → arm stall watchdog
                    subtype = event.get("subtype", "")
                    if subtype == "compact_boundary" or (
                            subtype == "status" and event.get("status") == "compacting"):
                        # Sentinel sessions (_compact, _memory_extract, …)
                        # are themselves PawFlow compactions. If CC saturates
                        # mid-summarization, let it run its own internal
                        # compact — interrupting would either loop forever
                        # (compact-of-compact) or destroy the in-flight
                        # summarization. Preempt-on-compact only applies
                        # to normal user sessions where PawFlow's bucket
                        # cache produces a better result than CC's auto.
                        _is_sentinel = conv_id.startswith("_") if conv_id else False
                        if _is_sentinel:
                            logger.info("[claude-code] CC self-compacting in "
                                         "sentinel '%s' — letting it continue",
                                         conv_id)
                            continue
                        if _compact_pending[0]:
                            continue
                        logger.warning(
                            "[claude-code] CC compacting detected (subtype=%s) "
                            "— flushing pre-compact turn, killing CC, "
                            "PawFlow will compact", subtype)
                        # Set BEFORE killing so any racing send_user_message
                        # from another thread sees the flag and refuses,
                        # routing the user message via PendingQueue.
                        self._compacting = True
                        _compact_pending[0] = True
                        # compact_boundary is the LAST useful event from CC
                        # for this turn — everything that follows is CC's own
                        # summary + post-compact work we do NOT want
                        # ingested. Do not drain. But the turn that fired
                        # compact may still hold unflushed events in the
                        # per-turn accumulator: if CC streamed
                        # tool_use + tool_result + assistant text inside the
                        # same msg_id and compact_boundary fired before the
                        # next msg_id rollover, those items were only in
                        # CC's .jsonl and never made it to the PawFlow
                        # transcript / webchat. Force-flush now so nothing
                        # emitted pre-compact is lost.
                        try:
                            _flush_turn()
                        except Exception as _fe:
                            logger.error(
                                "[claude-code] pre-compact flush failed: %s",
                                _fe, exc_info=True)
                        # Kill host AND container-side claude. Without the
                        # container-side kill the claude CLI survives as an
                        # orphan inside the pool container and keeps running
                        # in parallel with the replacement session PawFlow
                        # is about to spawn.
                        self._kill_cc_hard(proc)
                        break
                    if subtype == "init":
                        _stall_start_time = time.monotonic()
                        _got_assistant = False
                        logger.info("[claude-code] init — stall watchdog armed (%.0fs timeout)",
                                    _STALL_TIMEOUT)
                    continue

                if etype == "assistant":
                    # Got a response — stall watchdog disarmed
                    _got_assistant = True
                    _last_tool_result_time = 0.0

                    msg = event.get("message", {})
                    msg_id = msg.get("id", "")
                    # Capture freshest usage — each assistant event carries
                    # message.usage with current prompt size (input + cache).
                    # Emit a live `message_meta` so the webchat gauge updates
                    # mid-turn instead of waiting for the final `result`.
                    # Skip when usage didn't actually change (CC sometimes
                    # re-emits the same assistant event for incremental
                    # blocks of the same msg_id).
                    _u = msg.get("usage")
                    if isinstance(_u, dict) and _u != _latest_usage:
                        _latest_usage = _u
                        _ctx_used_live = (_u.get("input_tokens", 0)
                                          + _u.get("cache_creation_input_tokens", 0)
                                          + _u.get("cache_read_input_tokens", 0))
                        _ctx_max_live = int(getattr(self, '_max_context_size',
                                                    0) or 200000)
                        _ctx_pct_live = (_ctx_used_live / _ctx_max_live
                                         if _ctx_max_live > 0 else 0.0)
                        _pub("message_meta", {
                            "msg_id": msg_id,
                            "agent_name": agent_name,
                            "context_used": _ctx_used_live,
                            "context_max": _ctx_max_live,
                            "context_pct": _ctx_pct_live,
                            "live": True,  # flag: mid-turn (real, not estimate)
                        })

                    # Claude Code sends INCREMENTAL updates for the same message:
                    # event 1: [thinking], event 2: [text], event 3: [tool_use]
                    # Each event has ONLY the new block, not all blocks.
                    # Same msg_id = same turn → just append (don't clear).
                    if msg_id and msg_id != _current_msg_id:
                        # New message — flush previous turn
                        if _turn_count > 0:
                            _flush_turn()
                            _inject_catchup()
                        _turn_count += 1
                        _current_msg_id = msg_id
                    for block in msg.get("content", []):
                        btype = block.get("type", "")
                        if btype == "text":
                            text = block.get("text", "")
                            if text:
                                _turn_text_parts.append(text)
                                content_parts.append(text)
                                if callback:
                                    callback(text)
                        elif btype == "tool_use":
                            logger.debug("[CC-RAW-TOOL] block=%s", json.dumps(block, default=str, ensure_ascii=False))
                            _block_id = block.get("id", "")
                            _block_entry = {
                                "name": block.get("name", ""),
                                "arguments": block.get("input", {}),
                                "id": _block_id,
                            }
                            # Dedup: Claude Code may send the same tool_use block
                            # multiple times for the same msg_id (incremental updates).
                            # First time: input={} (empty), later: input={real args}.
                            # Replace by id instead of blindly appending.
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
                            # Unwrap MCP wrapper for display:
                            # mcp__pawflow__use_tool(tool_name=X, arguments={...})
                            # → X({...})  (with alias resolution: shell→bash etc.)
                            _tc_name = block.get("name", "")
                            _tc_args = block.get("input", {})
                            from core.llm_client import unwrap_mcp_tool
                            _tc_name, _tc_args = unwrap_mcp_tool(_tc_name, _tc_args)
                            # Don't emit SSE for empty-arg tool calls — likely
                            # an incremental update that will be followed by the
                            # real one with actual arguments.
                            if not _tc_args or _tc_args == {} or _tc_args == "{}":
                                logger.warning("[claude-code] skipping SSE for empty tool_use %s (id=%s) — awaiting args",
                                             _tc_name, _block_id)
                                continue
                            # Skip bash with empty/missing/whitespace command
                            if _tc_name == "bash" and isinstance(_tc_args, dict) and not str(_tc_args.get("command", "")).strip():
                                logger.warning("[claude-code] skipping SSE for bash with empty command (id=%s)", _block_id)
                                _record_phantom(_tc_name, _block_id)
                                continue
                            # Skip any tool where ALL string values are empty
                            if isinstance(_tc_args, dict) and _tc_args and all(
                                    not str(v).strip() for v in _tc_args.values()):
                                logger.warning("[claude-code] skipping SSE for %s with all-empty args (id=%s)", _tc_name, _block_id)
                                _record_phantom(_tc_name, _block_id)
                                continue
                            # Skip meta tools from SSE
                            if _tc_name in ("get_tool_schema", "mcp__pawflow__get_tool_schema"):
                                continue
                            _tc_event = {
                                "tool": _tc_name,
                                "arguments": _tc_args,
                                "tc_id": _block_id,
                                "agent_name": agent_name,
                                "llm_service": getattr(self, '_agent_service', ""),
                                "via": "claude-code",
                                "ts": time.time(),
                            }
                            if _parent_tc_id:
                                _tc_event["parent_tc_id"] = _parent_tc_id
                            _pub("tool_call", _tc_event)
                            _emitted_sse_tcs.add(_block_id)
                            # Register the CC tool_use id so tool_relay_service
                            # can match it when the MCP bridge forwards the
                            # same call (its request_id is a different uuid).
                            # Required for kill / background to target the
                            # right in-flight call when CC runs tools in //.
                            try:
                                from core.background_tool import (
                                    enqueue_cc_tc, _args_hash,
                                )
                                from core.llm_client import unwrap_mcp_tool
                                _match_name, _match_args = unwrap_mcp_tool(
                                    _tc_name, _tc_args or {})
                                enqueue_cc_tc(
                                    conv_id, agent_name, _block_id,
                                    _match_name, _args_hash(_match_args))
                            except Exception as _ee:
                                logger.debug(
                                    "[claude-code] enqueue_cc_tc skipped: %s",
                                    _ee)
                        elif btype == "thinking":
                            thinking = block.get("thinking", "")
                            if thinking:
                                _turn_thinking = thinking
                                _pub("thinking_content", {
                                    "text": thinking,
                                    "agent_name": agent_name,
                                })
                    # Update turn count on status
                    _pub("heartbeat", {
                        "agent_name": agent_name,
                        "status": f"turn {_turn_count}",
                        "iteration": _turn_count,
                    })
                    last_data = msg

                elif etype == "user":
                    # Tool results — capture for persistence + forward to webchat
                    msg = event.get("message", {})
                    for block in msg.get("content", []):
                        if block.get("type") == "tool_result":
                            logger.debug("[CC-RAW-RESULT] block=%s", json.dumps(block, default=str, ensure_ascii=False)[:2000])
                            tc_id = block.get("tool_use_id", "")
                            result_text = block.get("content", "")
                            if isinstance(result_text, list):
                                # Content blocks format
                                result_text = " ".join(
                                    b.get("text", "") for b in result_text
                                    if isinstance(b, dict))
                            result_str = str(result_text) if result_text else "(no output)"
                            # Store for turn_callback persistence
                            if tc_id:
                                _tool_results[tc_id] = result_str
                                _pending_tool_ids.discard(tc_id)
                                if not _pending_tool_ids:
                                    _last_tool_result_time = time.monotonic()
                            # Resolve tool name from turn_tool_calls
                            _tr_name = tc_id
                            for _tc in _turn_tool_calls:
                                if _tc.get("id") == tc_id:
                                    from core.llm_client import unwrap_mcp_tool
                                    _tr_name, _ = unwrap_mcp_tool(
                                        _tc.get("name", tc_id), _tc.get("arguments", {}))
                                    break
                            # Skip meta tool results from SSE
                            if _tr_name in ("get_tool_schema", "mcp__pawflow__get_tool_schema"):
                                continue
                            # Suppress the tool_result SSE iff we suppressed the
                            # matching tool_call (phantom: empty args / empty
                            # bash command / all-empty args — see filters above).
                            # Output-based detection ("no command provided" in
                            # result_str) was wrong in two ways — it swallowed
                            # legitimate Read results containing the phrase in
                            # source, AND legitimate bash output containing it
                            # (git log picks up commit e59a188's own message:
                            # 'Fix: scope "no command provided" phantom filter
                            # to bash only' on any command touching this file).
                            # Phantom detection is input-only.
                            if tc_id and tc_id not in _emitted_sse_tcs:
                                continue
                            _tr_event = {
                                "tool": _tr_name,
                                "result": result_str[:300],
                                "tc_id": tc_id,
                                "agent_name": agent_name,
                                "llm_service": getattr(self, '_agent_service', ""),
                                "via": "claude-code",
                            }
                            if _parent_tc_id:
                                _tr_event["parent_tc_id"] = _parent_tc_id
                            _pub("tool_result", _tr_event)

                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    text = delta.get("text", "")
                    if text:
                        _turn_text_parts.append(text)
                        content_parts.append(text)
                        if callback:
                            callback(text)

                elif etype == "result":
                    # Final result — flush last turn and exit the loop
                    _flush_turn()
                    # Check for API errors (auth failure, rate limit, etc.)
                    if event.get("is_error") or event.get("subtype") == "error_during_execution":
                        _err_text = event.get("result", "")
                        _errors = event.get("errors", [])
                        if _errors:
                            _err_text = _err_text or "; ".join(
                                e.get("message", str(e)) if isinstance(e, dict) else str(e)
                                for e in _errors)
                            logger.error("[claude-code] errors: %s", _errors)
                        _lower = _err_text.lower()
                        _is_auth = (
                            "authentication" in _lower
                            or "401" in _err_text
                            or "not logged in" in _lower
                            or "please run /login" in _lower
                            or "unauthorized" in _lower
                        )
                        if _is_auth:
                            if not _auth_retried:
                                _auth_retried = True
                                # Step 1: force-refresh the current pool
                                # credential. 'Not logged in' often just
                                # means the access_token expired; the
                                # refresh_token is usually still valid.
                                # Only if refresh ALSO fails do we mark
                                # the slot dead and rotate.
                                _bad_idx = getattr(
                                    self, '_current_pool_index', _resume_pool_idx)
                                _refreshed = False
                                try:
                                    if _bad_idx >= 0:
                                        _refreshed = self._force_refresh_pool_entry(_bad_idx)
                                except Exception as _rf_err:
                                    logger.warning(
                                        "[claude-code] force-refresh pool[%s] "
                                        "failed: %s", _bad_idx, _rf_err)
                                # Always invalidate the CC session — the
                                # old jsonl was bound to the dead token
                                # and CC won't accept a new token on the
                                # same --resume session.
                                if conv_id:
                                    try:
                                        ConversationStore.instance().set_extra(
                                            conv_id,
                                            f"claude_session:{agent_name or 'default'}",
                                            "")
                                    except Exception:
                                        pass
                                try:
                                    if _refreshed:
                                        logger.warning(
                                            "[claude-code] auth failure "
                                            "('%s') — refreshed pool[%s], "
                                            "retrying same slot",
                                            _err_text[:100], _bad_idx)
                                        self._setup_credentials(
                                            workdir, pool_index=_bad_idx)
                                    else:
                                        _tried = getattr(self, '_tried_pool_idx', set())
                                        _tried = set(_tried) | {_bad_idx}
                                        self._tried_pool_idx = _tried
                                        logger.warning(
                                            "[claude-code] auth failure "
                                            "('%s') — refresh failed, "
                                            "rotating OAuth pool (tried=%s)",
                                            _err_text[:100], sorted(_tried))
                                        self._setup_credentials(
                                            workdir, pool_index=-1,
                                            exclude_indices=_tried)
                                except Exception as _ref_err:
                                    raise LLMClientError(
                                        f"Claude Code auth failed and "
                                        f"recovery failed: {_ref_err}") from None
                                raise _CC401Retry()
                            raise LLMClientError(
                                f"Claude Code auth failed (all pool "
                                f"credentials exhausted): {_err_text[:300]}")
                        if event.get("subtype") == "error_during_execution":
                            # Include the error code/text so LLMClient retry loop can match it
                            raise LLMClientError(f"Claude Code error: {_err_text[:300]}")
                        # is_error without error_during_execution: API error (500, 429, etc.)
                        # Raise so it reaches the retry loop in LLMClient
                        if _err_text:
                            raise LLMClientError(f"Claude Code API error: {_err_text[:300]}")
                        logger.warning("[claude-code] result has is_error=True but no details")
                    result_text = event.get("result", "")
                    if not turn_callback and result_text and not content_parts:
                        content_parts.append(result_text)
                        if callback:
                            callback(result_text)
                    last_data = event
                    # Publish token stats for the webchat
                    _usage = event.get("usage", {})
                    # Total input = direct + cache_read + cache_creation
                    _total_in = (_usage.get("input_tokens", 0)
                                 + _usage.get("cache_read_input_tokens", 0)
                                 + _usage.get("cache_creation_input_tokens", 0))
                    _total_out = _usage.get("output_tokens", 0)
                    # model is in modelUsage keys, not at top level
                    _model_usage = event.get("modelUsage", {})
                    # Fallback: if usage is empty, sum from modelUsage
                    if not _total_in and not _total_out and _model_usage:
                        for _mu in _model_usage.values():
                            _total_in += (_mu.get("inputTokens", 0)
                                          + _mu.get("input_tokens", 0)
                                          + _mu.get("cacheReadInputTokens", 0)
                                          + _mu.get("cache_read_input_tokens", 0)
                                          + _mu.get("cacheCreationInputTokens", 0)
                                          + _mu.get("cache_creation_input_tokens", 0))
                            _total_out += (_mu.get("outputTokens", 0)
                                           + _mu.get("output_tokens", 0))
                    logger.info("[claude-code] result: usage=%s, modelUsage_keys=%s, tokens=%d/%d",
                                _usage, list(_model_usage.keys()), _total_in, _total_out)
                    _result_model = (event.get("model")
                                     or (list(_model_usage.keys())[0] if _model_usage else "")
                                     or model)
                    if _total_in or _total_out:
                        # Get the msg_id of the last assistant message (from turn_callback)
                        _last_msg_id = ""
                        try:
                            _last_msg_id = getattr(self, '_last_turn_msg_id', "") or ""
                        except Exception:
                            pass
                        # Context-fill: exact value from CC stream's last
                        # assistant.message.usage (prompt size at that point),
                        # compared against PawFlow's configured max_context_size.
                        _ctx_used = (_latest_usage.get("input_tokens", 0)
                                     + _latest_usage.get("cache_creation_input_tokens", 0)
                                     + _latest_usage.get("cache_read_input_tokens", 0))
                        _ctx_max = int(getattr(self, '_max_context_size', 0) or 200000)
                        _ctx_pct = (_ctx_used / _ctx_max) if _ctx_max > 0 else 0.0
                        _pub("message_meta", {
                            "msg_id": _last_msg_id,
                            "agent_name": agent_name,
                            "source": {
                                "type": "agent", "name": agent_name,
                                "llm_service": getattr(self, '_agent_service', ""),
                                "provider": "claude-code",
                                "model": _result_model,
                                "tokens_in": _total_in,
                                "tokens_out": _total_out,
                            },
                            "model": _result_model,
                            "provider": "claude-code",
                            "tokens_in": _total_in,
                            "tokens_out": _total_out,
                            "context_used": _ctx_used,
                            "context_max": _ctx_max,
                            "context_pct": _ctx_pct,
                            "num_turns": event.get("num_turns", _turn_count),
                            "duration_ms": event.get("duration_ms", 0),
                        })
                    # If one or more preempts were injected via stdin
                    # BEFORE this result event, CC may already be processing
                    # them in a new turn (observed live: CC reads stdin right
                    # after emitting `result` and starts a fresh assistant
                    # turn). Breaking here would kill the subprocess while
                    # CC is generating the preempt's response, losing it.
                    # Decision flow (deterministic via CC's session jsonl):
                    #   - 'done': every queued preempt has an assistant
                    #      message AFTER it in jsonl → break safely.
                    #   - 'pending': preempt visible in jsonl AFTER last
                    #      assistant → CC has read stdin and WILL respond;
                    #      keep the stream open with NO timeout (response
                    #      may take up to ~250s for complex queries).
                    #   - 'unread'/'unknown' after 3s poll: stdin not seen
                    #      by CC → likely lost; break and let PendingQueue
                    #      re-trigger on the next turn.
                    if getattr(self, '_preempt_pending', 0) > 0:
                        _sent = list(getattr(self, '_sent_preempt_texts', []))
                        _sid = getattr(self, '_current_session_id', '') or ''
                        _jsonl = os.path.join(
                            workdir, 'projects', '-workspace',
                            f"{_sid}.jsonl") if _sid else ''
                        _pstatus = self._check_preempt_in_jsonl(_jsonl, _sent)
                        # CC writes a stdin preempt to its session jsonl
                        # the moment it reads from stdin, which can happen
                        # ~tens of ms AFTER it emits result. If we don't
                        # see the preempt yet, poll briefly for it to land
                        # before deciding the preempt was lost.
                        if _pstatus in ('unread', 'unknown'):
                            _poll_until = time.monotonic() + 3.0
                            while time.monotonic() < _poll_until:
                                time.sleep(0.2)
                                if proc.poll() is not None:
                                    break
                                _pstatus = self._check_preempt_in_jsonl(
                                    _jsonl, _sent)
                                if _pstatus not in ('unread', 'unknown'):
                                    break
                        if _pstatus == 'done':
                            # CC integrated the preempt mid-turn; the just-
                            # emitted assistant message IS the response.
                            logger.info(
                                "[claude-code] result emitted; jsonl shows "
                                "all %d preempt(s) answered inline — break",
                                len(_sent))
                            self._had_preempts_this_turn = True
                            self._preempt_pending = 0
                            self._sent_preempt_texts = []
                            self._result_emitted = True
                            break
                        if _pstatus == 'pending':
                            # CC has read stdin (preempt is in jsonl) but
                            # has not yet produced the response. CC WILL
                            # respond — there is no useful upper bound on
                            # how long that takes (could be 250s for a
                            # complex query). Keep the stream open with NO
                            # timeout: the for-loop blocks on stdout for
                            # the next assistant event, and EOF on proc
                            # death exits cleanly via the finally block.
                            logger.info(
                                "[claude-code] result emitted; CC has read "
                                "%d preempt(s) (jsonl=pending) — keeping "
                                "stream open with NO timeout, waiting for "
                                "CC's response", self._preempt_pending)
                            self._had_preempts_this_turn = True
                            self._preempt_pending = 0
                            continue
                        # 'unread' / 'unknown' after polling: CC has not
                        # acknowledged stdin. Most likely it exited or is
                        # silently stuck. Don't wait further; let pawflow
                        # re-deliver via PendingQueue on the next turn.
                        # _had_preempts_this_turn stays False so the
                        # caller knows to re-trigger if drained user msgs
                        # exist.
                        logger.warning(
                            "[claude-code] result emitted; %d preempt(s) "
                            "NOT visible in jsonl after 3s poll "
                            "(status=%s) — preempt likely lost, breaking. "
                            "PendingQueue will re-trigger.",
                            self._preempt_pending, _pstatus)
                        self._preempt_pending = 0
                        self._sent_preempt_texts = []
                        self._result_emitted = True
                        break
                    # CC emitted its final result. Mark this so future
                    # preempts are refused (caller routes via PendingQueue).
                    self._result_emitted = True
                    break

            # Loop exited naturally (result break or stdout EOF). If a
            # compact_boundary fired during this stream, raise now — all
            # pre-compact events have been drained through turn_callback
            # via the per-msg_id rollover in the main loop.
            if _compact_pending[0]:
                from core.llm_client import CCCompactDetected
                raise CCCompactDetected("CC auto-compact detected")

        except _CC401Retry:
            # 401 mid-stream: credentials already refreshed, retry once
            logger.info("[claude-code] retrying after 401 token refresh")
            return self._stream_claude_code(
                messages, model, temperature, max_tokens, tools, callback,
                turn_callback=turn_callback, _is_auth_retry=True)
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
                pass
            # Flush any pending turn (ensures last text is persisted even if interrupted)
            try:
                _flush_turn()
            except Exception:
                pass
            # Cleanup process — _cleanup_proc captures stderr internally
            _stderr = self._cleanup_proc(proc)
            # Recover refreshed tokens from workdir (Claude Code may have refreshed them)
            self._recover_tokens(workdir)

        # Don't error on non-zero exit if we got a successful result
        # (process was killed after break on result event — that's expected)
        _got_result = bool(last_data.get("session_id") or last_data.get("result"))
        _was_compact_stall = (proc.returncode == -9 and _stall_start_time > 0 and not _got_assistant)
        # Tool-result / no-assistant stalls are PawFlow-watchdog kills. CC
        # produced work up to that point; the kill is our own recovery
        # action, not a user-facing failure. Tag the exception so the
        # retry loop in LLMClient.complete_stream treats it as retryable
        # (same path as compact_stall) instead of surfacing an error to
        # the user on the first attempt.
        _was_tool_stall = bool(self._stall_killed) and not _was_compact_stall
        if proc.returncode and proc.returncode != 0 and not _got_result:
            if _stderr:
                logger.error("Claude CLI stderr: %.500s", _stderr)
            if _was_compact_stall:
                _reason = "compact_stall"
            elif _was_tool_stall:
                _reason = "tool_stall"
            else:
                _reason = ""
            raise LLMClientError(
                f"Claude CLI stream exited with code {proc.returncode}"
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
                        conv_id, f"claude_session:{agent_name or 'default'}",
                        new_session)
                except Exception:
                    pass

        # Context-fill semantics: report the LAST assistant event's per-call
        # usage (real prompt size at end of turn, ≤ context_max), NOT the
        # `result.usage` summed across sub-calls (which balloons cache_read to
        # N×prefix and makes the UI clamp at 100%). `_latest_usage` is captured
        # at every assistant event in the stream loop.
        _u_final = _latest_usage or last_data.get("usage", {})
        _ti_in = _u_final.get("input_tokens", 0)
        _ti_creation = _u_final.get("cache_creation_input_tokens", 0)
        _ti_read = _u_final.get("cache_read_input_tokens", 0)
        _to = _u_final.get("output_tokens", 0)
        if not (_ti_in or _ti_creation or _ti_read or _to):
            for _mu in last_data.get("modelUsage", {}).values():
                _ti_in += _mu.get("inputTokens", 0) + _mu.get("input_tokens", 0)
                _ti_read += _mu.get("cacheReadInputTokens", 0) + _mu.get("cache_read_input_tokens", 0)
                _ti_creation += _mu.get("cacheCreationInputTokens", 0) + _mu.get("cache_creation_input_tokens", 0)
                _to += _mu.get("outputTokens", 0) + _mu.get("output_tokens", 0)
        _ti = _ti_in + _ti_creation + _ti_read
        return LLMResponse(
            content=full_content,
            model=last_data.get("model", model),
            tokens_in=_ti_in,
            tokens_out=_to,
            total_tokens=_ti + _to,
            cache_creation_tokens=_ti_creation,
            cache_read_tokens=_ti_read,
            finish_reason="stop",
            raw=last_data,
        )
