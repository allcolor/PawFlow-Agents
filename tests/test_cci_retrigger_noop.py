"""An inbox retrigger submits its notice even when there is no user tail."""

import pytest
from types import SimpleNamespace

from core.llm_client import LLMClient, LLMClientError, LLMMessage


NOTICE = ("[PawFlow inbox: ACTION REQUIRED] You have 1 unread item. "
          "STOP. Call read_inbox() NOW, before any other tool call or answer.")


@pytest.fixture(params=["claude-code-interactive", "codex-interactive",
                        "cc_mcp", "codex_mcp"])
def client(request):
    return LLMClient(request.param)


def test_an_inbox_retrigger_pastes_the_catchup_notice(client, monkeypatch, tmp_path):
    calls = []

    def catchup(conversation_id, agent_name):
        calls.append((conversation_id, agent_name))
        return NOTICE

    monkeypatch.setattr(client, "_build_catchup_context", catchup)
    prompt = client._cci_prompt(
        [LLMMessage(role="assistant", content="done", conversation_id="conv")],
        None, str(tmp_path), "/cc", "u", "conv", agent_name="a")

    assert prompt == NOTICE + "\n"
    assert calls == [("conv", "a")]


def test_the_notice_precedes_current_user_text(client, monkeypatch, tmp_path):
    monkeypatch.setattr(client, "_build_catchup_context", lambda cid, agent: NOTICE)
    prompt = client._cci_prompt(
        [LLMMessage(role="user", content="continue", conversation_id="conv")],
        None, str(tmp_path), "/cc", "u", "conv", agent_name="a")

    assert prompt == NOTICE + "\n\ncontinue\n"


def test_the_prompt_guard_stays_for_a_really_blank_turn(client, monkeypatch, tmp_path):
    monkeypatch.setattr(client, "_build_catchup_context", lambda cid, agent: "")

    with pytest.raises(LLMClientError, match="nothing to submit"):
        client._cci_prompt([], None, str(tmp_path), "/cc", "u", "conv",
                           agent_name="a")


def test_user_image_preempt_keeps_the_catchup_notice(client, monkeypatch, tmp_path):
    calls = []

    def catchup(cid, agent):
        calls.append((cid, agent))
        return NOTICE if len(calls) == 1 else ""

    monkeypatch.setattr(client, "_build_catchup_context", catchup)
    monkeypatch.setattr(client, "_cci_attachment_block", lambda *args: {
        "type": "image_ref", "file_id": "image", "filename": "image.png"})
    monkeypatch.setattr(client, "_cci_materialize_images", lambda *args: [
        "@/cc/image.png"])
    state = SimpleNamespace(workdir=str(tmp_path), container_workdir="/cc")

    prompt = client._cci_preempt_prompt(
        "check this image", [{"file_id": "image"}], state, "u", "conv", "a")

    assert NOTICE in prompt
    assert "check this image" in prompt
    assert "@/cc/image.png" in prompt
    assert calls == [("conv", "a")]
