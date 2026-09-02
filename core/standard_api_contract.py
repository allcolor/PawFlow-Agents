"""Frozen Phase 0 wire-contract fixtures for inbound standard APIs."""

from __future__ import annotations


OPENAI_CHAT_ACCEPTED_FIELDS = frozenset({
    "model",
    "messages",
    "stream",
    "stream_options",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "temperature",
    "top_p",
    "seed",
    "presence_penalty",
    "frequency_penalty",
    "stop",
    "user",
    "metadata",
    "response_format",
    "store",
    "max_tokens",
    "max_completion_tokens",
    "n",
})

OPENAI_RESPONSES_ACCEPTED_FIELDS = frozenset({
    "model",
    "input",
    "instructions",
    "stream",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "previous_response_id",
    "store",
    "temperature",
    "top_p",
    "max_output_tokens",
    "metadata",
})

ANTHROPIC_MESSAGES_ACCEPTED_FIELDS = frozenset({
    "model",
    "max_tokens",
    "messages",
    "system",
    "stream",
    "tools",
    "tool_choice",
    "temperature",
    "top_p",
    "top_k",
    "stop_sequences",
    "metadata",
})

OPENAI_SSE_DONE = "data: [DONE]\n\n"

ANTHROPIC_SUPPORTED_VERSIONS = frozenset({"2023-06-01"})
ANTHROPIC_SUPPORTED_BETAS = frozenset()


__all__ = [
    "ANTHROPIC_MESSAGES_ACCEPTED_FIELDS",
    "ANTHROPIC_SUPPORTED_BETAS",
    "ANTHROPIC_SUPPORTED_VERSIONS",
    "OPENAI_CHAT_ACCEPTED_FIELDS",
    "OPENAI_RESPONSES_ACCEPTED_FIELDS",
    "OPENAI_SSE_DONE",
]
