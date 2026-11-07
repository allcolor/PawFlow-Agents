"""A CLI session error names the CLI it came from.

Incident 2026-11-07 08:15Z: a Codex delivery failure was shown to the user as
"Claude Code prompt not delivered".
"""

import pytest

from tasks.ai._alc_llm_turn import _cli_label


@pytest.mark.parametrize("provider, label", [
    ("codex-interactive", "Codex"),
    ("codex-app-server", "Codex"),
    ("codex_mcp", "Codex"),
    ("antigravity-interactive", "Antigravity"),
    ("agy_mcp", "Antigravity"),
    ("gemini", "Gemini CLI"),
    ("claude-code-interactive", "Claude Code"),
    ("claude-code", "Claude Code"),
    ("cc_mcp", "Claude Code"),
])
def test_cli_label_follows_the_provider(provider, label):
    assert _cli_label(provider) == label
