"""The summarizer is told to drop duplicated and nested content.

2026-09-29: an agent found nested context left in its compacted summary. The
code drops the duplicates it can recognize; the rest only the summarizer sees.
"""

from __future__ import annotations

import pytest

import core.file_store as file_store
import core.handlers.compact_result as compact_result
from tasks.ai.agent_summarize import AgentSummarizeMixin


class _Store:
    def store(self, *_args, **_kwargs):
        return "fid"

    def delete(self, _fid):
        pass


class _Client:
    provider = "openai"


class _Harness(AgentSummarizeMixin):
    def __init__(self):
        self.prompts = []

    def _get_summarizer_client(self, user_id, conversation_id=""):
        return _Client(), 100_000, "svc"

    def _summarize_via_api(self, client, prompt, *_args, **_kwargs):
        self.prompts.append(prompt)
        return "summary"


@pytest.mark.parametrize("final", [True, False])
def test_summarizer_prompt_asks_to_drop_duplicates(monkeypatch, final):
    monkeypatch.setattr(file_store.FileStore, "instance", staticmethod(_Store))
    monkeypatch.setattr(compact_result, "set_compact_key", lambda _k: None)
    harness = _Harness()

    harness._call_summarize(_Client(), "text", target_tokens=500,
                            conversation_id="cid", final=final)

    assert "DEDUPLICATE" in harness.prompts[0]
    assert "previous summary nested inside a later one" in harness.prompts[0]
