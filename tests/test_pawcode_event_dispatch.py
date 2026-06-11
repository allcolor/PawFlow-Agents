"""Interactive SSE dispatcher: thinking render, cross-client messages, dedup."""

from pawflow_cli.event_handler import dispatch_event, _flatten_multipart


class _Renderer:
    def __init__(self):
        self._streams = {}
        self._thinking = {}
        self.thoughts = []
        self.user_messages = []
        self.markdown = []
        self.badges = []
        self.done_footers = []

    def start_thinking(self, agent):
        self._thinking[agent] = ""

    def thinking_token(self, agent, text, replace=False):
        self._thinking[agent] = self._thinking.get(agent, "") + text

    def end_thinking(self, agent):
        self.print_thinking_block(agent, self._thinking.pop(agent, ""))

    def print_thinking_block(self, agent, text):
        if text and text.strip():
            self.thoughts.append((agent, text))

    def print_user_message(self, content, agent=""):
        self.user_messages.append((content, agent))

    def print_markdown(self, text):
        self.markdown.append(text)

    def print_agent_badge(self, agent, svc=""):
        self.badges.append(agent)

    def print_done(self, *a, **k):
        self.done_footers.append(a)


class _App:
    def __init__(self):
        self.renderer = _Renderer()
        self._active_agents = {}
        self._seen_msg_ids = set()
        self._last_responses = []
        self._status = ""

    def _update_status(self, text):
        self._status = text


def _dispatch(app, ev_type, data, streaming="", thinking=""):
    return dispatch_event(app, {"event": ev_type, "data": data}, streaming, thinking)


def test_thinking_content_renders_without_a_prior_thinking_event():
    """Claude Code interactive emits only thinking_content (no `token`
    stream), so the block must render directly — the buffered
    end_thinking path never fires for it."""
    app = _App()
    _, _, thinking = _dispatch(
        app, "thinking_content",
        {"agent_name": "claude", "text": "step one\nstep two", "msg_id": "m1"})
    assert app.renderer.thoughts == [("claude", "step one\nstep two")]
    assert thinking == ""


def test_thinking_content_not_double_rendered_after_live_deltas():
    """A live delta buffers preview text; the durable thinking_content block
    then renders once and clears the buffer so a later flush can't repeat it."""
    app = _App()
    _, _, thinking = _dispatch(
        app, "thinking_delta", {"agent_name": "claude", "text": "prev"})
    assert thinking == "claude"
    _, _, thinking = _dispatch(
        app, "thinking_content",
        {"agent_name": "claude", "text": "final thought"}, thinking=thinking)
    # token would flush a leftover buffer; ensure nothing is left.
    assert app.renderer._thinking.get("claude", "") == ""
    assert app.renderer.thoughts == [("claude", "final thought")]


def test_new_message_user_from_other_client_is_rendered():
    """A user message posted from webchat must appear in the attached
    terminal — previously new_message was unhandled and dropped."""
    app = _App()
    _dispatch(app, "new_message",
              {"role": "user", "content": "hello from web", "msg_id": "web1"})
    assert app.renderer.user_messages == [("hello from web", "")]
    assert "web1" in app._seen_msg_ids


def test_new_message_own_echo_is_deduped():
    """Our own send pre-registers its msg_id; the server echo must not
    render a second copy."""
    app = _App()
    app._seen_msg_ids.add("mine")
    _dispatch(app, "new_message",
              {"role": "user", "content": "typed locally", "msg_id": "mine"})
    assert app.renderer.user_messages == []


def test_done_marks_turn_msg_ids_seen():
    """done seeds the seen set so the assistant text's new_message echo
    (already shown by done) is not duplicated."""
    app = _App()
    _dispatch(app, "done", {
        "agent_name": "claude", "response": "answer",
        "msg_id": "a1", "all_msg_ids": ["a0", "a1"]})
    assert {"a0", "a1"} <= app._seen_msg_ids
    # A subsequent assistant new_message for the same id renders nothing new
    # via the user-only branch.
    _dispatch(app, "new_message",
              {"role": "assistant", "content": "answer", "msg_id": "a1"})
    assert app.renderer.user_messages == []


def test_flatten_multipart_user_content():
    flat = _flatten_multipart([
        {"type": "text", "text": "look:"},
        {"type": "image_ref", "filename": "a.png"},
        {"type": "document", "filename": "b.pdf"},
    ])
    assert flat == "look:\n[Image: a.png]\n[File: b.pdf]"
