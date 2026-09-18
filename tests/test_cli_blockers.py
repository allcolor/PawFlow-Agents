"""Blocker detection for an interactive CLI pane.

A turn whose stream went silent because the TUI is waiting for input (a
rate-limit banner, a question, a model menu) never ends on its own: nothing on
the wire says why. These tests pin the conservative reading of the pane.
"""

from core.llm_providers._cli_blockers import detect_cli_blocker, tail_lines


def test_rate_limit_banner_in_the_tail_is_detected():
    pane = "Claude Code\nThinking...\nAPI Error: 429 Too Many Requests\n"
    blocker = detect_cli_blocker(pane)
    assert blocker is not None
    assert blocker.kind == "rate_limited"
    assert "429" in blocker.excerpt


def test_usage_limit_wording_is_detected():
    pane = ("you have reached your session usage limit, upgrade for higher "
            "limits\n")
    assert detect_cli_blocker(pane).kind == "rate_limited"


def test_a_reset_notice_alone_is_detected():
    pane = "[1308][Usage limit reached for 5 hour] limit will reset at 00:30\n"
    assert detect_cli_blocker(pane).kind == "rate_limited"


def test_rate_limit_discussed_higher_up_is_not_a_blocked_turn():
    """An answer that merely mentions a 429 must not fail the turn."""
    lines = ["the API answered 429 earlier, we retried and recovered"]
    lines += [f"output line {index}" for index in range(30)]
    assert detect_cli_blocker("\n".join(lines)) is None


def test_question_in_the_last_lines_is_detected():
    blocker = detect_cli_blocker("Do you want to continue? [y/n]")
    assert blocker is not None
    assert blocker.kind == "question"
    assert "[y/n]" in blocker.excerpt


def test_menu_hint_in_the_last_lines_is_a_question():
    pane = "1. Switch to Sonnet\n2. Keep the current model\nEsc to cancel\n"
    assert detect_cli_blocker(pane).kind == "question"


def test_a_question_that_scrolled_up_is_not_a_question():
    """Only the lines a live prompt could occupy count."""
    lines = ["Do you want to apply this change? [y/n]"]
    lines += [f"later output {index}" for index in range(10)]
    assert detect_cli_blocker("\n".join(lines)) is None


def test_numbered_plan_steps_are_not_a_question():
    pane = "\n".join(f"{index}. step {index}" for index in range(1, 9))
    assert detect_cli_blocker(pane) is None


def test_an_empty_pane_is_not_blocked():
    assert detect_cli_blocker("") is None
    assert detect_cli_blocker("   \n\n") is None


def test_tail_lines_keeps_the_last_non_empty_lines():
    assert tail_lines("a\n\n b \n\n", 2) == ["a", "b"]
    assert tail_lines("", 5) == []
