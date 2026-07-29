"""What the caller -- and the reader -- actually see of a delegate's answer.

The full response goes to the FileStore; what lands in the context and in the
transcript is a preview. Cut from the head, that preview showed the sub-agent
announcing its plan and stopped before anything it found, which on a provider
whose turn is several messages long meant the conclusion was never in it.
"""
from core.handlers._spawn_delivery import (
    DELEGATE_PREVIEW_CHARS,
    _delegate_result_preview,
)

OPENING = "I'll load the full bootstrap context first, then audit the tests."
CONCLUSION = "Three concrete failures, all reproduced against the tests."


def _long_answer():
    middle = "\n\n".join(f"Step {i}: still working through the modules."
                         for i in range(80))
    return f"{OPENING}\n\n{middle}\n\n{CONCLUSION}"


def test_a_short_answer_is_shown_whole():
    preview, truncated = _delegate_result_preview(CONCLUSION)
    assert preview == CONCLUSION
    assert truncated is False


def test_a_long_answer_is_previewed_by_its_conclusion():
    answer = _long_answer()
    assert len(answer) > DELEGATE_PREVIEW_CHARS
    preview, truncated = _delegate_result_preview(answer)
    assert truncated is True
    assert preview.endswith(CONCLUSION), "the end of the answer is what matters"
    assert OPENING not in preview, "the opening plan is not the result"
    assert len(preview) <= DELEGATE_PREVIEW_CHARS


def test_the_preview_starts_on_a_boundary_when_one_is_near_the_cut():
    # A cut that lands mid-sentence reads as corruption; when a line break sits
    # close to it, the preview starts there instead.
    answer = "x" * 400 + "\n" + "y" * (DELEGATE_PREVIEW_CHARS - 100)
    preview, truncated = _delegate_result_preview(answer)
    assert truncated is True
    assert preview.startswith("y")
    assert "x" not in preview


def test_an_empty_or_missing_response_is_not_an_error():
    assert _delegate_result_preview("") == ("", False)
    assert _delegate_result_preview(None) == ("", False)
