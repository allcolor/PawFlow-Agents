from core.llm_client import LLMMessage
from tasks.ai.context_usage_cache import context_usage_from_cache
from tasks.ai.agent_utils import AgentUtilsMixin


def test_context_usage_scrubs_stringified_see_image_payloads():
    blob = "A" * 150_000
    messages = [
        LLMMessage(
            role="tool",
            content=f"Image: screenshot.png\n__image_data__:image/png:{blob}",
            conversation_id="conv-1",
        ),
        LLMMessage(
            role="tool",
            content=f"Image: screenshot2.png\ndata:image/png;base64,{blob}",
            conversation_id="conv-1",
        ),
    ]

    usage = context_usage_from_cache(
        messages, 200_000, source="test", token_multiplier=1.0)

    assert usage["used"] < 200
    assert usage["pct"] < 0.001
    assert "150000" not in usage["last_marker"]


def test_agent_token_estimate_scrubs_stringified_image_payloads():
    blob = "A" * 150_000
    messages = [
        LLMMessage(
            role="tool",
            content=f"Image: screenshot.png\n__image_data__:image/png:{blob}",
            conversation_id="conv-1",
        ),
        LLMMessage(
            role="tool",
            content=f"Image: screenshot2.png\ndata:image/png;base64,{blob}",
            conversation_id="conv-1",
        ),
    ]

    tokens = AgentUtilsMixin._estimate_tokens(messages, [], token_multiplier=1.0)

    assert tokens < 200
