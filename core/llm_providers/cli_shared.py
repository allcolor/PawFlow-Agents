"""Shared utilities for CLI-based LLM providers and HTTP helpers.

Contains methods used by multiple providers: HTTP POST and CLI message
serialization.
"""

import copy
import json
import http.client
import logging
import os
import re
import threading
from html import escape
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple
from urllib.parse import urlparse

from core._llm_types import ColdStartRequired, DeltaContextRequired

logger = logging.getLogger(__name__)


# ── Token recovery memo ────────────────────────────────────────────────────
#
# Every CLI provider copies the OAuth token its container rotated back into
# the pool slot the session owns. That used to happen only at teardown, where
# one redundant write cost nothing. It now also happens on every sweeper tick,
# for sessions that are still running -- because teardown is not a moment one
# can rely on: a server killed hard, an update whose stop grace expires, never
# reaches it, and the token the container rotated is then lost with it.
#
# A periodic call must therefore be free when nothing rotated. The memo holds
# what each session workdir last handed over; the file is the only thing that
# says whether the CLI rotated anything, so an identical signature means there
# is nothing to copy, whoever is asking.
_RECOVERED_SIGNATURES: dict = {}
_RECOVERED_LOCK = threading.Lock()
#: Enough for any plausible number of concurrent session workdirs. Cleared
#: wholesale rather than aged: a dropped entry costs one redundant write.
_RECOVERED_MAX = 512

# ── The credential-pool lock ───────────────────────────────────────────────
#
# Every pool update is a read-modify-write of ONE file: each provider loads
# the whole pool, edits a slot, and writes the whole pool back into
# GLOBAL_SECRETS_FILE -- itself read and rewritten whole, key by key. Nothing
# about that is atomic, and the writers are genuinely concurrent: the Claude,
# codex and gemini sweepers tick independently, a refresh can land mid-tick,
# and a login or a teardown writes the same file from a third thread.
#
# Interleaved, two recoveries each write a pool built from a snapshot taken
# before the other's edit, so the last writer restores the OTHER slot's
# previous token. For Anthropic that is not a transient glitch: the
# refresh_token is single-use, so the resurrected one is already dead and the
# account is logged out for good once the container is gone.
#
# One process-wide lock, held across the whole load/mutate/save cycle by every
# writer, is what makes the cycle atomic. It is shared across providers on
# purpose -- they collide on the secrets file, not on their own pool key. It
# is reentrant because a persist may fall through to add_credential_to_pool,
# which loads and saves again on its own.
_CREDENTIALS_POOL_LOCK = threading.RLock()


def credentials_pool_lock():
    """The lock every credential-pool read-modify-write must hold.

    Hold it across load, mutate AND save. Guarding the halves separately
    buys nothing: the lost update happens between them.
    """
    return _CREDENTIALS_POOL_LOCK


def token_recovery_is_stale(workdir: str, service_id: str, pool_index: int,
                            signature: str) -> bool:
    """True when this exact token was already copied back to that slot."""
    with _RECOVERED_LOCK:
        return _RECOVERED_SIGNATURES.get(
            (workdir, service_id, int(pool_index))) == signature


def note_token_recovered(workdir: str, service_id: str, pool_index: int,
                         signature: str) -> None:
    """Record a copy that REACHED THE POOL, so the next tick can skip it.

    Only ever call this after a persist confirmed it wrote. Recording an
    attempt instead of a result is how a token gets lost permanently: the
    memo makes every later tick skip the slot, so the one write that failed
    is never retried and the rotated token dies with the container.
    """
    with _RECOVERED_LOCK:
        if len(_RECOVERED_SIGNATURES) >= _RECOVERED_MAX:
            _RECOVERED_SIGNATURES.clear()
        _RECOVERED_SIGNATURES[(workdir, service_id, int(pool_index))] = signature


def request_path(base_url: str, endpoint_path: str = "") -> str:
    """The request line for ``base_url`` plus an endpoint suffix.

    Rebuilding it from ``parsed.path`` alone dropped the base URL's query
    string. That is fatal for Azure: an operator who pastes the complete
    target from the portal gets
    ``.../chat/completions?api-version=2024-10-21``, the suffix is then empty
    because the path already names the endpoint, and the version -- which
    Azure requires -- lived only in the query. Every request came back
    rejected.

    A suffix that carries its own query wins; there is only ever one.
    """
    parsed = urlparse(base_url or "")
    suffix, _, suffix_query = (endpoint_path or "").partition("?")
    path = (parsed.path.rstrip("/") + suffix).replace("//", "/") or "/"
    query = suffix_query or parsed.query
    return f"{path}?{query}" if query else path


# ── Tool-call synopsis helpers ───────────────────────────────────
# Shared by _serialize_messages_for_cli (CC prompt) AND the compaction
# summarizer input (old_conversation). Without them, assistant messages
# that only contain tool_calls (no text) and role='tool' results are
# dropped on serialization, erasing all evidence of work done between
# two free-text turns (commit SHAs, test results, file edits…).

_TOOL_ARG_TRUNC = 120
_TOOL_PARALLEL_ARG_TRUNC = 80
_TOOL_RESULT_TRUNC = 400
_BOOTSTRAP_CONTEXT_HEADER_RE = re.compile(
    r"(?:\A|\r?\n)# PawFlow Initial Context(?:\r?\n|\Z)")


def summarize_tool_call(name: str, args: Any) -> str:
    """One-line synopsis: ``name(key="val", key=<list:N>, ...)``.

    Unwraps the MCP wrapper (``mcp__pawflow__use_tool``) so the real
    inner tool is shown. String values are truncated to ``_TOOL_ARG_TRUNC``.
    """
    if not name:
        name = "<tool>"
    if name in ("multi_tool_use.parallel", "parallel") and isinstance(args, dict):
        tool_uses = args.get("tool_uses") or []
        if isinstance(tool_uses, list) and tool_uses:
            rendered = []
            for item in tool_uses:
                if not isinstance(item, dict):
                    continue
                inner_name = item.get("recipient_name") or item.get("name") or "<tool>"
                inner_args = item.get("parameters") or item.get("arguments") or {}
                rendered.append(summarize_tool_call(inner_name, inner_args))
            if rendered:
                return "parallel(" + "; ".join(rendered) + ")"
    # Unwrap MCP bridge wrapper
    if name in (
        "mcp__pawflow__use_tool", "mcp__pawflow__.use_tool",
        "pawflow.use_tool", "pawflow/use_tool", "use_tool",
    ) and isinstance(args, dict):
        inner_name = args.get("tool_name") or args.get("name") or ""
        inner_args = args.get("arguments", {})
        if inner_name:
            return summarize_tool_call(inner_name, inner_args)
    if not isinstance(args, dict):
        return f"{name}(...)"
    parts: List[str] = []
    for k, v in args.items():
        if isinstance(v, str):
            limit = _TOOL_PARALLEL_ARG_TRUNC if name in ("multi_tool_use.parallel", "parallel") else _TOOL_ARG_TRUNC
            vs = v if len(v) <= limit else v[:limit - 3] + "..."
            # escape double quotes in value
            vs = vs.replace('"', '\\"')
            parts.append(f'{k}="{vs}"')
        elif isinstance(v, (list, tuple)):
            parts.append(f"{k}=<list:{len(v)}>")
        elif isinstance(v, dict):
            parts.append(f"{k}=<dict:{len(v)}>")
        elif v is None:
            parts.append(f"{k}=None")
        else:
            parts.append(f"{k}={v}")
    return f"{name}({', '.join(parts)})"


def textualize_message(
    m: Any, *, tool_result_trunc: Optional[int] = _TOOL_RESULT_TRUNC,
) -> Optional[str]:
    """Return a text-only representation of an arbitrary LLMMessage.

    - assistant with free text → the text (tool_calls appended as synopsis)
    - assistant tool-call-only → ``[ran: NAME(args); NAME(args)]``
    - tool result → ``[tool_result: <snippet>]``, truncated to
      ``tool_result_trunc`` chars; pass ``None`` to keep the result intact
    - user / system → text content (multipart collapsed)
    - empty / unknown → None (caller may skip)

    This is used both when serializing history for a fresh CC session
    and when building the summarizer's input — both contexts need every
    tool action to leave a readable trace. The summarizer input may be
    truncated (its job is to compress); the cold-start context injection
    must NOT truncate — stripping tool results there is not compaction,
    it just hides the real context size from the compaction trigger.
    """
    role = getattr(m, "role", "")
    content = getattr(m, "content", "")
    text = m.text_content if isinstance(content, list) else (content or "")
    tool_calls = getattr(m, "tool_calls", None) or []

    if role == "assistant":
        body = text.strip() if isinstance(text, str) else ""
        if tool_calls:
            synopsis = "; ".join(
                summarize_tool_call(
                    getattr(tc, "name", "") or "",
                    getattr(tc, "arguments", {}) or {},
                )
                for tc in tool_calls
            )
            if body:
                return f"{body}\n[ran: {synopsis}]"
            return f"[ran: {synopsis}]"
        return body or None

    if role == "tool":
        if not isinstance(text, str):
            text = str(text)
        snippet = text.strip()
        if not snippet:
            return None
        if tool_result_trunc is not None and len(snippet) > tool_result_trunc:
            snippet = snippet[:tool_result_trunc] + f"...[+{len(text) - tool_result_trunc}c]"
        return f"[tool_result: {snippet}]"

    if role in ("user", "system"):
        return text.strip() if isinstance(text, str) and text.strip() else None

    return None


def bootstrap_read_call_ids(messages: List[Any]) -> Set[str]:
    """IDs of the native calls that read our own cold-start bootstrap file.

    A cold CLI start writes the serialized history to ``initial_context.md``
    and the agent opens it. That call and its result are persisted like any
    other -- the transcript and the UI must show what the agent did, and a
    suppressed call is indistinguishable from a lost one.

    They must not come back as *context*, though. The result body IS the
    previous bootstrap file, so serializing it into the next one embeds a
    verbatim copy of a file the agent is already reading, one layer deeper on
    every cold start. Two surfaces, two rules: the transcript keeps the pair,
    the agent context drops it.

    Normally the same predicate as the gauge identifies the call from its
    arguments. Codex Interactive code mode is the exception: before persistence
    it deliberately replaces the script -- and therefore the bootstrap path --
    with a size marker. Its linked result still contains the bootstrap's exact
    first-line header, so use that as a narrow fallback for native calls.
    """
    from tasks.ai.context_usage_cache import _is_cli_bootstrap_read

    call_ids: Set[str] = set()
    native_call_ids: Set[str] = set()
    for msg in messages or []:
        for tool_call in (getattr(msg, "tool_calls", None) or []):
            call_id = str(getattr(tool_call, "id", "") or "")
            if not call_id:
                continue
            if str(getattr(tool_call, "tool_origin", "") or "").lower() == "native":
                native_call_ids.add(call_id)
            if _is_cli_bootstrap_read(tool_call):
                call_ids.add(call_id)

    for msg in messages or []:
        if str(getattr(msg, "role", "") or "") != "tool":
            continue
        call_id = str(getattr(msg, "tool_call_id", "") or "")
        if not call_id or call_id not in native_call_ids:
            continue
        content = getattr(msg, "content", "")
        text = msg.text_content if isinstance(content, list) else content
        if (_BOOTSTRAP_CONTEXT_HEADER_RE.search(
                text if isinstance(text, str) else str(text or ""))):
            call_ids.add(call_id)
    return call_ids


def drop_bootstrap_calls(msg: Any, call_ids: Set[str]) -> Any:
    """Return ``msg`` without its bootstrap-read calls (unchanged if none).

    Free text on the same message survives: only the call synopsis goes. A
    message left with no text and no other call textualizes to None, and the
    caller skips it.
    """
    tool_calls = getattr(msg, "tool_calls", None) or []
    if not tool_calls or not call_ids:
        return msg
    kept = [tc for tc in tool_calls
            if str(getattr(tc, "id", "") or "") not in call_ids]
    if len(kept) == len(tool_calls):
        return msg
    trimmed = copy.copy(msg)
    trimmed.tool_calls = kept
    return trimmed


def is_bootstrap_read_result(msg: Any, call_ids: Set[str]) -> bool:
    """Whether ``msg`` is the result of one of ``call_ids``."""
    if not call_ids or str(getattr(msg, "role", "") or "") != "tool":
        return False
    return str(getattr(msg, "tool_call_id", "") or "") in call_ids


class LLMCliSharedMixin:
    """Methods shared across CLI and HTTP providers."""

    def record_observed_cli_context(self, conversation_id: str,
                                    agent_name: str, tokens: int) -> None:
        """Store the prompt size the CLI provider reported for this stream.

        Read back by the context gauge (``tasks.ai.context_usage``), which
        otherwise has nothing to measure: the window belongs to the CLI
        session, not to PawFlow -- provider system prompt, tool schemas and
        session history included, none of which PawFlow can enumerate from
        its own messages. Every observed CLI provider records here (Codex from
        the rollout ``token_count``, claude-code and claude-code-interactive
        from the wire ``usage``), so one number feeds the gauge and the
        auto-compact threshold.

        The dict is created in ``LLMClient.__init__`` and shared by reference
        with call clones, so the clone that runs the turn and the resolver
        client the gauge reads expose one authoritative value.
        """
        if not conversation_id or not agent_name:
            return
        try:
            measured = int(tokens or 0)
        except (TypeError, ValueError):
            return
        if measured <= 0:
            return
        counts = getattr(self, "_cli_observed_context_tokens_by_stream", None)
        if not isinstance(counts, dict):
            counts = {}
            self._cli_observed_context_tokens_by_stream = counts
        counts[(conversation_id, agent_name)] = measured

    def record_observed_wire_usage(self, usage: Any, conversation_id: str,
                                   agent_name: str) -> None:
        """Record the prompt occupancy carried by an observed usage dict.

        For every provider PawFlow watches through a proxy, the prompt size is
        the sum of what the API charges as input: uncached tokens, cache reads
        and cache creation. Cached tokens occupy the window exactly like
        uncached ones -- for a Claude Code session they are most of it -- so a
        gauge built on ``input_tokens`` alone would report a fraction of the
        real prompt.

        Providers whose usage carries no cache fields (the Antigravity
        observer normalizes Gemini's ``promptTokenCount`` to ``input_tokens``)
        simply contribute those missing terms as 0.
        """
        if not isinstance(usage, dict):
            return
        total = 0
        for field in ("input_tokens", "cache_read_input_tokens",
                      "cache_creation_input_tokens"):
            try:
                total += int(usage.get(field, 0) or 0)
            except (TypeError, ValueError):
                continue
        self.record_observed_cli_context(conversation_id, agent_name, total)

    def record_observed_cli_window(self, conversation_id: str,
                                   agent_name: str, window: int) -> None:
        """Store the window a measured prompt size is divided by, when native.

        Only providers that report their own window call this. Without it the
        gauge divides by whatever ``max_context_size`` happens to say, which is
        a configured guess rather than the session's real budget.
        """
        if not conversation_id or not agent_name:
            return
        try:
            measured = int(window or 0)
        except (TypeError, ValueError):
            return
        if measured <= 0:
            return
        windows = getattr(self, "_cli_observed_context_window_by_stream", None)
        if not isinstance(windows, dict):
            windows = {}
            self._cli_observed_context_window_by_stream = windows
        windows[(conversation_id, agent_name)] = measured

    def _cli_require_cold_context(self, provider: str, *,
                                  release=None) -> None:
        """Refuse to launch a process holding a resume's context.

        Two cases, no third one: no process -> we launch -> cold start -> full
        context; a process is running -> delta. The context phase built this
        turn for the second case because a process WAS running, and we are now
        on the first: it crashed, or its container was stopped.

        Launching anyway would send a bare delta to a process that knows
        nothing -- no transcript, no persona, no skills, no tool config. So we
        do not launch. ColdStartRequired sends the turn back to the context
        phase, which rebuilds it as the cold start it now is, through the same
        code every ordinary cold start uses. Nothing has reached the model
        yet, so the restart costs no tokens.

        An ordinary cold start carries no marker and this returns at once.

        ``release`` is called just before raising. Callers reach this point
        holding things their own ``finally`` gives back -- the live session's
        turn lock, most of all -- and that ``finally`` belongs to a ``try``
        this raise never enters. A caller that took something before asking
        must hand ``release`` a callable that gives it back, or the lock is
        held forever and the next turn on that session waits for a turn that
        already ended.
        """
        if not getattr(self, "_pawflow_context_is_delta", False):
            return
        # One shot: the rebuilt context is a real cold context, and a stale
        # marker must never bounce a turn that is already correct.
        self._pawflow_context_is_delta = False
        logger.warning(
            "[%s] the live process is gone; this turn has to launch one, so "
            "it needs the full context and not a resume delta — restarting "
            "the turn as a cold start", provider)
        if release is not None:
            try:
                release()
            except Exception:
                logger.debug("[%s] cold-start release hook failed", provider,
                             exc_info=True)
        raise ColdStartRequired(
            f"{provider}: cold start required, context was built as a delta")

    def _cli_require_delta_context(self, provider: str, *,
                                   release=None) -> None:
        """Refuse to hand a cold start's full context to a live process.

        The other half of the same rule, and it applies to every CLI. Two
        cases, no third one: no process -> we launch -> cold start -> full
        context; a process is running -> delta. The context phase built this
        turn for the first case because it found no live process, and we are
        now on the second: the process answers.

        Continuing would run the turn in neither case. The full transcript was
        loaded and compacted for nothing, the gauge was zeroed against a
        session that never restarted, and the delta actually sent came from a
        context assembled for a process that does not need it.

        DeltaContextRequired sends the turn back to the context phase, which
        rebuilds it as the delta it is, through the same code every ordinary
        delta uses. Nothing has reached the model yet, so the restart costs no
        tokens.

        A turn already built as a delta carries the marker and this returns at
        once -- which is the ordinary case-2 path.

        ``release`` has the same contract as in _cli_require_cold_context: a
        caller holding the live session's turn lock must give it back here,
        because the ``finally`` that would release it belongs to a ``try``
        this raise never enters.
        """
        if getattr(self, "_pawflow_context_is_delta", False):
            return
        # One shot, same reason as the cold guard: the rebuilt context is a
        # real delta, and a stale marker must not bounce a correct turn.
        self._pawflow_context_is_delta = True
        logger.warning(
            "[%s] the process is alive; this turn was built as a cold start "
            "with the full context, so it needs a delta instead — restarting "
            "the turn as a delta", provider)
        if release is not None:
            try:
                release()
            except Exception:
                logger.debug("[%s] delta release hook failed", provider,
                             exc_info=True)
        raise DeltaContextRequired(
            f"{provider}: delta required, context was built as a cold start")

    @staticmethod
    def _cli_escape_text(text: str, *, quote: bool = False) -> str:
        return escape(str(text or ""), quote=quote)

    def _cli_message_block(self, role: str, rendered: str,
                           agent_name: str = "") -> str:
        attr = f' role="{self._cli_escape_text(role or "message", quote=True)}"'
        if agent_name:
            attr += f' agent="{self._cli_escape_text(agent_name, quote=True)}"'
        return (
            f"<message{attr}>\n"
            f"{self._cli_escape_text(rendered, quote=False)}\n"
            "</message>"
        )

    def _cli_current_turn_text(self, messages: List[Any]) -> str:
        if not messages:
            return ""
        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if (getattr(msg, "role", "") == "user"
                    and getattr(msg, "_pawflow_current_user_message", False)):
                rendered = textualize_message(msg, tool_result_trunc=None)
                if rendered:
                    return self._cli_message_block("user", rendered) + "\n\nContinue from this latest turn."
                return ""
        last_user_idx = -1
        for idx in range(len(messages) - 1, -1, -1):
            if getattr(messages[idx], "role", "") == "user":
                last_user_idx = idx
                break
        start = last_user_idx if last_user_idx >= 0 else max(0, len(messages) - 3)
        lines = []
        bootstrap_ids = bootstrap_read_call_ids(messages)
        for msg in messages[start:]:
            role = getattr(msg, "role", "") or "message"
            if role == "system":
                continue
            if is_bootstrap_read_result(msg, bootstrap_ids):
                continue
            msg = drop_bootstrap_calls(msg, bootstrap_ids)
            rendered = textualize_message(msg, tool_result_trunc=None)
            if rendered:
                lines.append(self._cli_message_block(role, rendered))
        if not lines:
            return ""
        return "\n".join(lines) + "\n\nContinue from this latest turn."

    def _cli_context_before_latest_text(self, messages: List[Any]) -> str:
        if not messages:
            return ""
        last_user_idx = -1
        for idx in range(len(messages) - 1, -1, -1):
            if getattr(messages[idx], "role", "") == "user":
                last_user_idx = idx
                break
        end = last_user_idx if last_user_idx >= 0 else len(messages)
        lines = []
        bootstrap_ids = bootstrap_read_call_ids(messages)
        for msg in messages[:end]:
            role = getattr(msg, "role", "") or "message"
            if role == "system":
                continue
            if is_bootstrap_read_result(msg, bootstrap_ids):
                continue
            msg = drop_bootstrap_calls(msg, bootstrap_ids)
            rendered = textualize_message(msg, tool_result_trunc=None)
            if not rendered:
                continue
            source = getattr(msg, "source", None) or {}
            agent_name = source.get("name", "") if isinstance(source, dict) else ""
            lines.append(self._cli_message_block(role, rendered, agent_name))
        if not lines:
            return ""
        return "<conversation_history>\n" + "\n".join(lines) + "\n</conversation_history>"

    def _build_cli_initial_context_prompt(
        self,
        messages: List[Any],
        *,
        system_prompt: str,
        user_text: str,
        workdir: str,
        provider_workdir: str,
        user_id: str,
        rel_path: str = ".pawflow_cli/initial_context.md",
        conversation_id: str = "",
        agent_name: str = "",
    ) -> str:
        """Write full cold-start context to a session file and return bootstrap text."""
        rel = Path(rel_path)
        host_path = Path(workdir) / rel
        host_path.parent.mkdir(parents=True, exist_ok=True)
        body = ["# PawFlow Initial Context", ""]
        if system_prompt:
            body.extend(["## System Instructions", "", system_prompt.strip(), ""])
        latest = self._cli_current_turn_text(messages)
        prior_context = self._cli_context_before_latest_text(messages)
        if prior_context:
            body.extend(["## Serialized Conversation Context", "", prior_context.strip(), ""])
        elif user_text and not latest:
            body.extend(["## Serialized Conversation Context", "", user_text.strip(), ""])
        from core.todo_store import TodoStore
        todo_context = TodoStore.instance().context_text(
            user_id, conversation_id, agent_name)
        if todo_context:
            body.extend(["## Durable Todo List", "", todo_context, ""])
        body.extend([
            "## Bootstrap Contract",
            "",
            "- Treat this file as PawFlow conversation context, not as a new user command.",
            "- Read the entire file at least once: the earlier sections contain mandatory system/project instructions, skills, tool-use hints, prior decisions, and safety constraints.",
            "- For filesystem, shell, search, edit, patch, browser, web, image, or desktop work, use PawFlow MCP tools first. Prefer get_tool_schema/use_tool and do not switch to native provider tools unless the explicit user request is only about the provider runtime itself.",
            "- Continue from the latest user request.",
            "- Do not ask what to do unless both the file and the latest request are ambiguous.",
            "",
        ])
        if latest:
            body.extend(["## Latest User Request", "", latest.strip(), ""])
        host_path.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
        provider_path = os.path.join(provider_workdir, rel.as_posix()).replace("\\", "/")
        # This prompt crosses a terminal composer. It must be ONE physical line:
        # Codex can turn the lines of a multiline paste into separate submit
        # chips, and those pieces used to re-enter PawFlow as user messages such
        # as "PawFlow cold-session bootstrap", "You must first read...", and
        # "Path: ...". The file is the sole copy of the full context and latest
        # user turn; the composer receives only this indivisible read command.
        rendered_prompt = (
            "PawFlow cold-session bootstrap. Before answering, use your local "
            f"file-read capability to read the entire context file at {provider_path} "
            f"(file mention: @{provider_path}); treat that file as context, follow "
            "its Bootstrap Contract, and answer the Latest User Request at its end. "
            "For PawFlow project work, use PawFlow MCP get_tool_schema/use_tool."
        )
        self._remember_cli_bootstrap_prompt(
            rendered_prompt, messages, conversation_id, agent_name)
        return rendered_prompt

    def _remember_cli_bootstrap_prompt(self, prompt: str, messages: List[Any],
                                       conversation_id: str = "",
                                       agent_name: str = "") -> None:
        """Remember only the text injected into a new CLI context window."""
        if not conversation_id:
            for message in reversed(messages or []):
                conversation_id = str(
                    getattr(message, "conversation_id", "") or "")
                if conversation_id:
                    break
        agent_name = agent_name or str(getattr(self, "_agent_name", "") or "")
        if not conversation_id or not agent_name:
            return
        from core.token_counter import (
            count_messages_tokens, resolve_token_multiplier)
        cfg = (getattr(self, "_config_ref", None)
               or getattr(self, "config", None) or {})
        token_count = int(count_messages_tokens(
            [{"role": "user", "content": str(prompt or "")}],
            multiplier=resolve_token_multiplier(cfg),
        ) or 0)
        token_counts = getattr(self, "_cli_bootstrap_tokens_by_stream", None)
        if not isinstance(token_counts, dict):
            token_counts = {}
            self._cli_bootstrap_tokens_by_stream = token_counts
        token_counts[(conversation_id, agent_name)] = token_count

    @staticmethod
    def _clean_control_chars(text: str) -> str:
        """Remove control characters that break JSON parsing on some APIs."""
        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)

    def _http_post(self, path: str, body: dict, headers: dict, *, base_url: str = "") -> dict:
        """Send POST and return parsed JSON."""
        base_url = base_url or self.base_url
        parsed = urlparse(base_url or "https://api.openai.com")
        host = parsed.hostname
        port = parsed.port
        scheme = parsed.scheme

        if scheme == "https":
            from core.relay_proxy_url import relay_proxy_ssl_context
            ctx = relay_proxy_ssl_context(base_url)
            conn = http.client.HTTPSConnection(host, port, timeout=self.timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=self.timeout)

        try:
            raw_json = json.dumps(body)
            # Strip control characters that some LLM APIs can't parse
            json_body = self._clean_control_chars(raw_json).encode("utf-8")
            headers["Content-Length"] = str(len(json_body))
            full_path = request_path(
                base_url, ("/" + path.lstrip("/")) if path else "")
            conn.request("POST", full_path, body=json_body, headers=headers)
            response = conn.getresponse()
            response_body = response.read().decode("utf-8")
            if response.status >= 400:
                from core.llm_client import LLMClientError
                raise LLMClientError(f"LLM API error {response.status}: {response_body[:500]}")
            return json.loads(response_body)
        finally:
            conn.close()

    def _serialize_messages_for_cli(
        self, messages: List[Any], tools: Optional[List[Any]],
    ) -> Tuple[str, str]:
        """Convert messages to (system_prompt, user_text) for the CLI.

        System messages -> system_prompt. Tool definitions are handled by each
        provider's native tool channel and are not serialized into prompt text.
        Conversation history -> marked transcript text in user_text so the
        model understands it's a multi-turn conversation to continue.
        """
        if tools:
            raise ValueError(
                "CLI message serialization does not accept tools; providers "
                "must use native tool channels")

        system_parts: List[str] = []
        history_lines: List[str] = []
        last_user_text = ""
        has_history = False

        import re as _re
        _b64_pattern = _re.compile(r'data:[^;]+;base64,[A-Za-z0-9+/=]{100,}')

        bootstrap_ids = bootstrap_read_call_ids(messages)

        for m in messages:
            text = m.text_content if isinstance(m.content, list) else (m.content or "")
            if m.role == "system":
                system_parts.append(text)
            elif m.role == "user":
                if isinstance(m.content, list):
                    _text_parts = []
                    for p in m.content:
                        if not isinstance(p, dict):
                            continue
                        pt = p.get("type", "")
                        if pt == "text":
                            _text_parts.append(p.get("text", ""))
                        elif pt == "image_ref":
                            fid = p.get("file_id", "")
                            fname = p.get("filename", "image") or "image"
                            if fid:
                                _text_parts.append(
                                    f"Attached image: fs://filestore/{fid}/{fname}")
                            else:
                                _text_parts.append(f"[image: {fname}]")
                        elif pt == "file_ref":
                            _text_parts.append(
                                f"[attached file: {p.get('filename', '?')} ({p.get('mime_type', '?')}) "
                                f"— read via: read(path='{p.get('file_id', '?')}', source='filestore')]")
                        # Other multipart payloads are unsupported here.
                    text = "\n".join(p for p in _text_parts if p.strip())
                else:
                    text = text or ""
                # Safety: strip any remaining base64 data URIs from string content
                text = _b64_pattern.sub('[image]', text)
                last_user_text = text
                if text.strip():
                    history_lines.append(self._cli_message_block("user", text))
            elif m.role == "assistant":
                # Keep tool-call-only messages as a synopsis so CC sees the
                # full trail of work (commits, tests, edits) after compaction
                # — dropping them erased the evidence between two free-text
                # turns and made CC rediscover its own work on every resume.
                rendered = textualize_message(
                    drop_bootstrap_calls(m, bootstrap_ids))
                if not rendered:
                    continue
                source = getattr(m, "source", None) or {}
                agent_name = source.get("name", "") if isinstance(source, dict) else ""
                history_lines.append(self._cli_message_block("assistant", rendered, agent_name))
                has_history = True
            elif m.role == "tool":
                # Truncated tool result — providers dispatch tools live, but
                # on resume/compact the historical results are needed to
                # understand what happened.
                if is_bootstrap_read_result(m, bootstrap_ids):
                    continue
                rendered = textualize_message(m)
                if not rendered:
                    continue
                history_lines.append(self._cli_message_block("tool", rendered))
                has_history = True

        system_prompt = "\n\n".join(system_parts)


        if has_history:
            user_text = (
                "<conversation_history>\n"
                + "\n".join(history_lines)
                + "\n</conversation_history>\n\n"
                "Continue the conversation. Reply to the latest user message. "
                "You are a participant in this conversation — read the full "
                "history above and respond naturally, referencing previous "
                "messages from any participant (user or other agents) as needed."
            )
        else:
            user_text = last_user_text

        return system_prompt, user_text
