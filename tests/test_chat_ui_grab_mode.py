"""Grab mode: the composer types straight into the agent's live tmux.

The transport already existed -- terminal_proxy bridges the browser to the
container PTY and `terminal_input` writes raw bytes into it. What these tests
pin is the part that is easy to get wrong: WHICH path the text takes, and what
happens to a multiline prompt.
"""

import json
import re
from pathlib import Path

CHAT_UI = Path("tasks/io/chat_ui")


def _code(text: str) -> str:
    """Source with comments removed -- these rules are about the code."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())


def test_grab_module_registered_after_terminal_js():
    src = Path("tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")
    assert '"grab.js"' in src
    assert (CHAT_UI / "grab.js").exists()
    # grab.js calls _terminalInputB64 / _estimateTerminalSize / _agentLlmProvider,
    # all defined in terminal.js.
    assert src.index('"terminal.js"') < src.index('"grab.js"')


def test_grab_button_sits_before_the_reload_button():
    html = (CHAT_UI / "template.html").read_text(encoding="utf-8")
    assert 'id="grabBtn"' in html
    assert 'onclick="toggleGrab()"' in html
    # Hidden until the selected agent actually has a tmux.
    assert html.index('id="grabBtn"') < html.index('id="refreshConvBtn"')
    grab_btn = html[html.index('id="grabBtn"'):]
    assert 'style="display:none"' in grab_btn[:grab_btn.index(">")]
    # Grabbed, the composer must not look like the composer.
    assert "#grabBtn.on" in html
    assert ".input-area.grab-on textarea" in html


def test_grab_never_routes_through_the_pool_send_path():
    """The one rule that decides whether the message exists in the conversation.

    pool.send_text() files a SHA-256 ticket in injected_prompts.jsonl so the
    UserPromptSubmit hook does NOT mirror the prompt -- right for a prompt
    PawFlow injected, wrong for one a human typed. Grab writes to the PTY and
    lets the hook file it.
    """
    src = _code((CHAT_UI / "grab.js").read_text(encoding="utf-8"))
    assert "terminal_input" in src
    assert "_terminalInputB64" in src
    for forbidden in ("send_text", "cc_interactive_send", "/api/agent"):
        assert forbidden not in src
    # And nothing is echoed locally, or the hook's copy would double it.
    assert "addMsg('user'" not in src


def test_a_typed_newline_is_the_ctrl_enter_key_forwarded():
    """Codex, Claude Code and Antigravity all break the line on Ctrl+Enter.

    So grabbed, that key is FORWARDED and the TUI makes the newline in its own
    composer. Assembling a multiline block here and pushing it over is the
    thing that unfolds one prompt into several submissions.
    """
    src = (CHAT_UI / "grab.js").read_text(encoding="utf-8")
    # CSI u, the encoding modern terminals use — same sequences PawCode binds.
    assert "_GRAB_CTRL_ENTER = '\\x1b[13;5u'" in src
    assert "_GRAB_SHIFT_ENTER = '\\x1b[13;2u'" in src
    body = src[src.index("function grabHandleKey"):]
    body = body[:body.index("\n// A conversation")]
    # Composer contents go over first, then the key: the break lands after
    # what was typed, not before it.
    assert body.index("_grabFlush(input)") < body.index("_GRAB_CTRL_ENTER")
    assert "_composerInsertNewline" not in body


def test_a_pasted_block_still_goes_as_one_bracketed_paste():
    """Text that is already multiline was pasted in, not typed. Raw newlines
    would each read as a submission -- the beta.82 bootstrap bug, exactly."""
    src = (CHAT_UI / "grab.js").read_text(encoding="utf-8")
    assert "_GRAB_PASTE_START = '\\x1b[200~'" in src
    assert "_GRAB_PASTE_END = '\\x1b[201~'" in src
    flush = src[src.index("function _grabFlush"):]
    flush = flush[:flush.index("\n/** Send the composer")]
    assert "text.indexOf('\\n') !== -1" in flush
    assert "_GRAB_PASTE_START + text + _GRAB_PASTE_END" in flush
    # A paste needs a settle before the Enter that submits it.
    send = src[src.index("function grabSend"):]
    send = send[:send.index("\n/** Composer keys")]
    assert "setTimeout(() => _grabWrite('\\r'), _GRAB_SUBMIT_DELAY_MS)" in send
    assert "_grabWrite('\\r')" in send


def test_escape_and_ctrl_c_reach_the_tui():
    src = (CHAT_UI / "grab.js").read_text(encoding="utf-8")
    body = src[src.index("function grabHandleKey"):]
    body = body[:body.index("\n// A conversation")]
    assert "_grabWrite('\\x1b')" in body      # Esc to interrupt
    assert "_grabWrite('\\x03')" in body      # Ctrl+C
    # A selection means the user meant copy, not interrupt.
    assert "selectionStart !== input.selectionEnd" in body


def test_both_newline_keys_work_grabbed_and_ungrabbed():
    """The TUIs newline on Ctrl+Enter, the webchat only ever had Shift+Enter."""
    grab = (CHAT_UI / "grab.js").read_text(encoding="utf-8")
    body = grab[grab.index("function grabHandleKey"):]
    assert "e.shiftKey || e.ctrlKey" in body
    # Grabbed, each is forwarded as itself rather than folded into one key.
    assert "_GRAB_SHIFT_ENTER : _GRAB_CTRL_ENTER" in body

    attach = (CHAT_UI / "attachments.js").read_text(encoding="utf-8")
    assert "function _composerInsertNewline(" in attach
    # Ctrl+Enter now inserts a newline instead of doing nothing at all, and it
    # is checked before the plain-Enter send.
    assert "if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {" in attach
    assert (attach.index("e.key === 'Enter' && (e.ctrlKey || e.metaKey)")
            < attach.index("e.key === 'Enter' && !e.shiftKey && !e.ctrlKey"))


def test_composer_hooks_defer_to_grab_first():
    attach = (CHAT_UI / "attachments.js").read_text(encoding="utf-8")
    send = attach[attach.index("async function send()"):]
    send = send[:send.index("messageHistory.unshift")]
    assert "grabActive()" in send and "grabSend(); return;" in send
    key = attach[attach.index("function handleKey(e)"):]
    assert "grabHandleKey(e)) return;" in key[:400]


def test_grab_is_released_when_the_session_or_selection_moves():
    src = (CHAT_UI / "grab.js").read_text(encoding="utf-8")
    assert "function grabOnAgentSwitch(" in src
    assert "function grabOnConversationSwitch(" in src
    # A dead session must not leave the composer wired to nothing.
    assert "ws.onclose" in src and "releaseGrab(true)" in src

    agents = (CHAT_UI / "active_agents.js").read_text(encoding="utf-8")
    assert "updateGrabButton()" in agents
    cmd_agent = (CHAT_UI / "cmd_agent.js").read_text(encoding="utf-8")
    assert "grabOnAgentSwitch()" in cmd_agent
    conversations = (CHAT_UI / "conversations.js").read_text(encoding="utf-8")
    assert "grabOnConversationSwitch()" in conversations


def test_grab_strings_exist_in_every_language():
    keys = ["grabTitle", "grabOnTitle", "grabPlaceholder", "grabOn", "grabOff",
            "grabNoLive", "grabFailed"]
    for lang in ("en", "fr", "es"):
        data = json.loads(
            (CHAT_UI / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        for key in keys:
            assert key in data, f"{lang}.json missing {key}"
