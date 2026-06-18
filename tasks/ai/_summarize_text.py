"""Text-shaping helpers, prompt-too-long retry constants, and summarizer
tool definitions for AgentSummarizeMixin.

Split out of agent_summarize.py so both the mixin and the provider-backend
mixin (_summarize_backends) can share these without a circular import.
"""
from __future__ import annotations

from core.llm_client import LLMToolDefinition


# When the summarizer LLM call itself hits prompt_too_long (rare — we
# already chunk inputs above _CHUNK_CHAR_LIMIT — but possible if the
# provider's count is tighter than ours, the CC session's own tool-loop
# overhead bloated context, or the model counts ours + system prompt +
# tool schemas differently), drop the oldest 25% of the input text and
# retry with the tail. Inspired by CC's truncateHeadForPTLRetry but
# applied to our text-based input instead of API-round groups.
_PTL_MARKERS = (
    "prompt_too_long",
    "prompt is too long",
    "exceed_context_size",
    "context_length_exceeded",
    "n_prompt_tokens",
    "maximum context length",
)
_PTL_MAX_RETRIES = 3
# How much of the head to drop per retry. 25% first, 50%, 75% — bounded
# so we never fully empty the input (would produce garbage summary).
_PTL_DROP_SCHEDULE = (0.25, 0.50, 0.75)


def _is_ptl_error(exc: BaseException) -> bool:
    """True when an exception matches the prompt-too-long family."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _PTL_MARKERS)


def _compact_scope_id(conversation_id: str, compact_key: str) -> str:
    """Unique provider scope for one summarizer run.

    Background bucket jobs can run concurrently for the same user. A shared
    sentinel like `_compact` makes CLI-backed providers share session locks and
    workdirs across unrelated rollups. Keep the call outside the real
    conversation, but give every compact its own ephemeral provider scope.
    """
    safe_cid = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in (conversation_id or "compact"))[:48]
    safe_key = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in (compact_key or "run"))[:24]
    return f"_compact_{safe_cid}_{safe_key}"


def _strip_analysis_wrapper(text: str) -> str:
    """Remove <analysis>...</analysis> blocks and outer <summary> tags.

    The 9-section summarizer prompt asks the model to produce an
    <analysis> scratchpad followed by the final <summary>. The tool-call
    arg should already contain ONLY the summary body, but models
    sometimes include the wrapper anyway. Defensive one-pass strip.
    """
    import re as _re
    if not text:
        return text
    t = _re.sub(r"<analysis>[\s\S]*?</analysis>\s*", "", text, flags=_re.IGNORECASE)
    # Strip outer <summary>...</summary> if the model kept them.
    _m = _re.match(r"\s*<summary>\s*([\s\S]*?)\s*</summary>\s*$", t,
                   flags=_re.IGNORECASE)
    if _m:
        t = _m.group(1)
    return t.strip() or text  # fall back to raw if strip emptied it


def _truncate_head(text: str, drop_fraction: float) -> str:
    """Drop the oldest `drop_fraction` of `text` on line boundaries.

    Prefix a one-line marker so the summarizer knows older content was
    cut (matches CC's PTL_RETRY_MARKER intent). Returns empty string if
    the drop would leave nothing useful — caller must detect and raise.
    """
    if drop_fraction <= 0 or drop_fraction >= 1:
        return text
    lines = text.split("\n")
    if len(lines) < 4:
        return text
    cut = int(len(lines) * drop_fraction)
    kept = lines[cut:]
    if not kept:
        return ""
    return ("[earlier conversation truncated for compaction retry]\n"
            + "\n".join(kept))

# Tool definitions for the mini summarizer loop (API providers)
_READ_TOOL = LLMToolDefinition(
    name="read",
    description=(
        "Read a file. Use source='filestore' for compaction files. "
        "Supports pagination via offset (1-based line) and limit."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path or FileStore ID"},
            "offset": {"type": "integer", "description": "Start line (1-based)"},
            "limit": {"type": "integer", "description": "Max lines to read"},
            "source": {"type": "string", "description": "Filesystem service (use 'filestore')"},
        },
        "required": ["path"],
    },
)

_COMPACT_RESULT_TOOL = LLMToolDefinition(
    name="compact_result",
    description=(
        "Return the compaction summary. Call this ONCE after reading all pages. "
        "This is the ONLY way to return a summary — do NOT respond with text."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "The summary text"},
            "compact_key": {"type": "string", "description": "The compact key from instructions"},
        },
        "required": ["summary", "compact_key"],
    },
)
