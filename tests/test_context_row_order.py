"""A stored ``thinking`` / ``tool_call`` child must attach whatever the row order.

The agent-context reader sorts rows by ``(ts, seq)`` (``_read_ctx_file``),
because multi-producer writes land out of creation order. A child persisted
without a ``seq`` weighs 0 in that sort and lands *in front of* its own parent.
The deserializer used to attach children positionally -- ``by_msg_id`` only
held rows already walked -- so every one of those children was dropped as an
orphan.

Live evidence (2026-09-18, conversation 80c37670429e4f56): 650 stored rows, 139
of them unstamped (60 ``thinking`` + 79 ``tool_call``), and under the real read
sort 138 children moved in front of their parent. That is how an assistant turn
reached an Anthropic-compatible gateway as ``tool_use`` without its thinking,
and the whole request came back 400 "The content[].thinking in the thinking
mode must be passed back to the API".
"""

from tasks.ai.agent_loop import AgentLoopTask


def _task():
    return AgentLoopTask.__new__(AgentLoopTask)


def _rows():
    """A user turn and an assistant turn whose children precede it."""
    return [
        {"role": "user", "content": "go", "msg_id": "u1", "ts": 1.0,
         "conversation_id": "cid", "user_id": "u"},
        {"role": "thinking", "content": "I must call the tool", "msg_id": "t1",
         "parent_message_id": "a1", "ts": 2.0, "conversation_id": "cid",
         "user_id": "u", "thinking_signature": "sig"},
        {"role": "tool_call", "content": "", "msg_id": "c1",
         "parent_message_id": "a1", "tool_call_id": "call_1",
         "tool_name": "bash", "arguments": {"command": "ls"}, "ts": 2.0,
         "conversation_id": "cid", "user_id": "u"},
        {"role": "assistant", "content": "looking", "msg_id": "a1", "ts": 2.0,
         "conversation_id": "cid", "user_id": "u"},
        {"role": "tool", "content": "out", "msg_id": "r1", "ts": 3.0,
         "tool_call_id": "call_1", "parent_message_id": "c1",
         "conversation_id": "cid", "user_id": "u"},
    ]


def _assistant(messages):
    return [m for m in messages if m.role == "assistant"][0]


class TestChildAttachmentIgnoresRowOrder:
    def test_children_before_their_parent_still_attach(self):
        messages = _task()._deserialize_messages(_rows(), conversation_id="cid")
        assistant = _assistant(messages)

        assert assistant.thinking == "I must call the tool"
        assert assistant.thinking_signature == "sig"
        assert [tc.id for tc in assistant.tool_calls] == ["call_1"]
        assert assistant.tool_calls[0].name == "bash"
        assert assistant.tool_calls[0].arguments == {"command": "ls"}

    def test_children_after_their_parent_still_attach(self):
        rows = _rows()
        ordered = [rows[0], rows[3], rows[1], rows[2], rows[4]]

        messages = _task()._deserialize_messages(ordered, conversation_id="cid")
        assistant = _assistant(messages)

        assert assistant.thinking == "I must call the tool"
        assert [tc.id for tc in assistant.tool_calls] == ["call_1"]

    def test_a_child_without_its_parent_has_no_home(self):
        rows = [r for r in _rows() if r["msg_id"] != "a1"]

        messages = _task()._deserialize_messages(rows, conversation_id="cid")

        # The orphan children vanish; the tool result stays its own message,
        # exactly as before the change.
        assert [m.role for m in messages] == ["user", "tool"]

    def test_several_thinking_rows_keep_their_stored_order(self):
        rows = _rows()
        rows.insert(1, {"role": "thinking", "content": "first", "msg_id": "t0",
                        "parent_message_id": "a1", "ts": 2.0,
                        "conversation_id": "cid", "user_id": "u"})

        messages = _task()._deserialize_messages(rows, conversation_id="cid")

        assert _assistant(messages).thinking == "first\nI must call the tool"
