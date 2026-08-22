"""Grab mode: the composer types straight into the agent's live tmux.

The transport already existed -- terminal_proxy bridges the browser to the
container PTY and `terminal_input` writes raw bytes into it. What these tests
pin is the part that is easy to get wrong: WHICH path the text takes, and what
happens to a multiline prompt.
"""

import json

from chat_ui_testing import rendered_chat_html
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


def test_grab_button_lives_inside_the_unified_composer():
    html = rendered_chat_html()
    controls = html[
        html.index('<div class="prompt-controls-panel"'):
        html.index('<div class="composer-action-mount"')
    ]
    composer = html[
        html.index('<div class="input-row composer-shell"'):
        html.index('</div>\n', html.index('id="sendBtn"')) + len('</div>\n')
    ]
    assert 'id="grabBtn"' in html
    assert 'onclick="toggleGrab()"' in html
    assert 'id="grabBtn"' not in controls
    assert 'id="grabBtn"' in composer
    assert 'id="refreshConvBtn"' in controls
    # Hidden until the selected agent actually has a tmux.
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


def test_shift_enter_mirrors_the_newline_locally_and_in_the_grabbed_tui():
    """The browser draft stays readable while the TUI receives Ctrl+Enter."""
    grab = (CHAT_UI / "grab.js").read_text(encoding="utf-8")
    assert "const _GRAB_CTRL_ENTER = '\\x1b[13;5u'" in grab
    helper = grab[grab.index("function _grabInsertNewline(input)"):]
    helper = helper[:helper.index("\n}", helper.index("_grab.sentDraft")) + 2]
    assert "_grabWrite(_GRAB_CTRL_ENTER)" in helper
    assert "_composerInsertNewline(input)" in helper
    assert "_grab.sentDraft = input.value" in helper
    key = grab[grab.index("function grabHandleKey"):]
    key = key[:key.index("\n// A conversation")]
    assert "e.shiftKey || (plainEnter && composerEnterCreatesNewline())" in key
    shift_block = key[key.index("if (e.key === 'Enter'"):]
    shift_block = shift_block[:shift_block.index("if (plainEnter)")]
    assert "_grabFlush(input)" not in shift_block
    assert "_grabInsertNewline(input)" in shift_block
    assert key.index("_grabInsertNewline(input)") < key.index("if (plainEnter)")

    attach = (CHAT_UI / "attachments.js").read_text(encoding="utf-8")
    body = attach[attach.index("function handleKey(e)"):]
    assert body.index("grabHandleKey(e)") < body.index("_composerInsertNewline(input)")


def test_a_pasted_block_still_goes_as_one_bracketed_paste():
    """Text that is already multiline was pasted in, not typed. Raw newlines
    would each read as a submission -- the beta.82 bootstrap bug, exactly."""
    src = (CHAT_UI / "grab.js").read_text(encoding="utf-8")
    assert "_GRAB_PASTE_START = '\\x1b[200~'" in src
    assert "_GRAB_PASTE_END = '\\x1b[201~'" in src
    writer = src[src.index("function _grabWriteComposerText"):]
    writer = writer[:writer.index("\n/** Push what is in the composer")]
    assert "text.indexOf('\\n') !== -1" in writer
    assert "_GRAB_PASTE_START + text + _GRAB_PASTE_END" in writer
    flush = src[src.index("function _grabFlush"):]
    flush = flush[:flush.index("\n/** Send the composer")]
    assert "const text = _grabUnsentText(box)" in flush
    assert "_grabWriteComposerText(text)" in flush
    # Every non-empty terminal write needs a settle before Enter. WebSocket
    # frame ordering does not mean the TUI has ingested the first frame yet.
    send = src[src.index("function grabSend"):]
    send = send[:send.index("\n/** Composer keys")]
    assert "if (hadText) setTimeout(() => _grabWrite('\\r'), _GRAB_SUBMIT_DELAY_MS)" in send
    assert "_grabWrite('\\r')" in send


def test_single_line_grab_also_settles_before_its_only_enter():
    """Regression: immediate text-frame + Enter-frame made Codex swallow Enter,
    so the user had to press it a second time to submit a one-line prompt."""
    src = (CHAT_UI / "grab.js").read_text(encoding="utf-8")
    send = src[src.index("function grabSend"):]
    send = send[:send.index("\n/** Composer keys")]
    assert "const pending = _grabUnsentText(input)" in send
    assert "const hadText = !!pending" in send
    assert "const pasted = _grabFlush" not in send


def test_final_grab_submit_sends_only_text_not_already_mirrored_to_the_tui():
    """Visible earlier lines must not be duplicated when plain Enter submits."""
    src = (CHAT_UI / "grab.js").read_text(encoding="utf-8")
    assert "sentDraft: ''" in src
    pending = src[src.index("function _grabUnsentText"):]
    pending = pending[:pending.index("\n/** Push what is in the composer")]
    assert "full.startsWith(_grab.sentDraft)" in pending
    assert "full.slice(_grab.sentDraft.length)" in pending
    flush = src[src.index("function _grabFlush"):]
    flush = flush[:flush.index("\n/** Send the composer")]
    assert "_grab.sentDraft = ''" in flush


def test_durable_grab_response_reconciles_its_token_bubble():
    """The final new_message must finalize, not ignore, the same-id preview."""
    src = (CHAT_UI / "sse_handlers_a.js").read_text(encoding="utf-8")
    listener = src[src.index("eventSource.addEventListener('new_message'"):]
    listener = listener[:listener.index("// ── Proactive notifications")]
    assert "let existing = data.msg_id" in listener
    assert "existing.dataset.msgid = data.msg_id" in listener
    assert "_seenMsgIds.delete(previewMsgId)" in listener
    assert "_seenMsgIds.add(data.msg_id)" in listener
    assert "stream.msg_id = data.msg_id || stream.msg_id" in listener
    assert "_preview.lastEl && _preview.lastEl.isConnected" in listener
    assert "_durableText === _previewText" in listener
    assert "stream.el === existing || stream.lastEl === existing" in listener
    assert "s.lastText = s.text" in src
    assert "tcs.lastText = tcs.text" in src
    assert "existing.classList.remove('streaming')" in listener
    assert "existing.classList.add('finalized')" in listener
    assert "stream.el = null" in listener
    assert "turnViewIngest('assistant', data, existing)" in listener


def test_escape_and_ctrl_c_reach_the_tui():
    src = (CHAT_UI / "grab.js").read_text(encoding="utf-8")
    body = src[src.index("function grabHandleKey"):]
    body = body[:body.index("\n// A conversation")]
    assert "_grabWrite('\\x1b')" in body      # Esc to interrupt
    assert "_grabWrite('\\x03')" in body      # Ctrl+C
    # A selection means the user meant copy, not interrupt.
    assert "selectionStart !== input.selectionEnd" in body


def test_modified_enter_stays_a_local_newline_when_grab_does_not_consume_it():
    """Ungrabbed Shift/Ctrl+Enter still never submit the webchat prompt."""
    attach = (CHAT_UI / "attachments.js").read_text(encoding="utf-8")
    assert "function _composerInsertNewline(" in attach
    body = attach[attach.index("function handleKey(e)"):]
    assert "e.shiftKey || e.ctrlKey || e.metaKey || e.altKey" in body
    assert body.index("grabHandleKey(e)") < body.index("_composerInsertNewline(input)")
    assert body.index("_composerInsertNewline(input)") < body.index("send();")


def test_grab_gets_first_refusal_then_other_hooks_keep_their_order():
    attach = (CHAT_UI / "attachments.js").read_text(encoding="utf-8")
    send = attach[attach.index("async function send()"):]
    send = send[:send.index("messageHistory.unshift")]
    assert "grabActive()" in send and "grabSend(); return;" in send
    key = attach[attach.index("function handleKey(e)"):]
    grab = key.index("grabHandleKey(e)) return;")
    newline = key.index("_composerInsertNewline(input)")
    assert grab < newline
    assert newline < key.index("if (_skillAutocomplete.open)")


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


def test_grab_covers_every_provider_that_owns_a_tmux():
    """Antigravity has a tmux like the other two, so it is grabbable too.

    It just opens through its own action -- the CC one only looks in the CC and
    Codex pools.
    """
    src = (CHAT_UI / "grab.js").read_text(encoding="utf-8")
    for provider, action in [
        ("claude-code-interactive", "open_cc_interactive_terminal"),
        ("codex-interactive", "open_cc_interactive_terminal"),
        ("antigravity-interactive", "open_antigravity_interactive_terminal"),
    ]:
        assert f"'{provider}': '{action}'" in src
    # The open action is looked up per provider, never hardcoded.
    assert "action$(_GRAB_OPEN_ACTIONS[provider]" in src


def test_the_session_listing_includes_antigravity():
    """Button visibility comes from this listing, so a pool missing from it is
    a provider that can never be grabbed."""
    src = Path("tasks/ai/actions/_sf_k6.py").read_text(encoding="utf-8")
    block = src[src.index('if action == "list_cc_interactive_terminals"'):]
    block = block[:block.index('if action == "open_cc_interactive_terminal"')]
    assert "AntigravityObserverPool" in block
    # Its pool names the container ("antigravity-observer"); callers dispatch
    # on the LLM provider.
    assert 'row["provider"] = "antigravity-interactive"' in block


def test_grab_strings_exist_in_every_language():
    keys = ["grabTitle", "grabOnTitle", "grabPlaceholder", "grabOn", "grabOff",
            "grabNoLive", "grabFailed"]
    for lang in ("en", "fr", "es"):
        data = json.loads(
            (CHAT_UI / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        for key in keys:
            assert key in data, f"{lang}.json missing {key}"
