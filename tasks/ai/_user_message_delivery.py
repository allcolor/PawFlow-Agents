"""Deliver a user message to live agents the way the composer does.

A user message reaches its agent at once: it goes through the streaming
ingress, which interrupts a running agent (cancels its tool calls, submits the
message live) or starts a turn for an idle one. `/msg @agent` and `/msg @all`
used to bypass it: the first waited in the PendingQueue until the running turn
ended, the second ran throw-away clones of every agent in sub-conversations
and published their answers when the slowest one finished -- the live agents
never saw the message (2026-09-29).

Delivery is sequential per agent: one lane per (conversation, agent) submits
its messages in arrival order, each one accepted before the next. Different
agents are served in parallel.
"""

import json
import logging
import threading
import uuid
from collections import deque

logger = logging.getLogger(__name__)

_lanes: dict = {}
_lanes_guard = threading.Lock()


def _run_lane(key) -> None:
    while True:
        with _lanes_guard:
            lane = _lanes[key]
            if not lane["pending"]:
                lane["running"] = False
                return
            executor, flowfile = lane["pending"].popleft()
        conversation_id, agent_name = key
        try:
            result = executor._execute_streaming(flowfile)
            ack = (result[0] if result else flowfile)
            status = ack.get_attribute("http.response.status") or "200"
            if status != "200":
                logger.error(
                    "[user-delivery] %s/%s refused the message (status=%s): %s",
                    conversation_id[:8], agent_name, status,
                    ack.get_content()[:300])
        except Exception:
            logger.exception("[user-delivery] delivery to %s/%s failed",
                             conversation_id[:8], agent_name)


def _enqueue(executor, conversation_id: str, agent_name: str, flowfile) -> None:
    key = (conversation_id, agent_name.lower())
    with _lanes_guard:
        lane = _lanes.setdefault(key, {"pending": deque(), "running": False})
        lane["pending"].append((executor, flowfile))
        if lane["running"]:
            return
        lane["running"] = True
    threading.Thread(target=_run_lane, args=(key,), daemon=True,
                     name=f"user-delivery-{agent_name}").start()


def _ingress_flowfile(conversation_id: str, agent_name: str, text: str,
                      user_id: str, msg_id: str, channel: str):
    from core import FlowFile
    body = {"message": text, "conversation_id": conversation_id,
            "target_agent": agent_name, "msg_id": msg_id}
    ff = FlowFile(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    ff.set_attribute("http.auth.principal", user_id)
    ff.set_attribute("target_agent", agent_name)
    ff.set_attribute("agent.client_channel", channel)
    return ff


def _new_msg_id() -> str:
    return uuid.uuid4().hex[:12]


def deliver_user_message(executor, conversation_id: str, agent_name: str,
                         text: str, user_id: str, *,
                         channel: str = "web") -> str:
    """Send ``text`` from the user to one agent, interrupting it if busy.

    Returns the msg_id of the user row the ingress persists.
    """
    if not conversation_id or not agent_name or not user_id:
        raise ValueError("conversation_id, agent_name and user_id are required")
    msg_id = _new_msg_id()
    _enqueue(executor, conversation_id, agent_name, _ingress_flowfile(
        conversation_id, agent_name, text, user_id, msg_id, channel))
    return msg_id


def broadcast_user_message(executor, conversation_id: str, agent_names,
                           text: str, user_id: str, *,
                           channel: str = "web") -> str:
    """Send one user message to every agent in ``agent_names``.

    The message is persisted once, addressed to ALL: the transcript shows it
    once and every agent context receives it. Each agent then gets it through
    its own ingress, which skips the write and interrupts a running turn.
    """
    if not conversation_id or not user_id:
        raise ValueError("conversation_id and user_id are required")
    agents = [name for name in agent_names if name]
    if not agents:
        raise ValueError("no agent to broadcast to")
    # The ALL row is written here, before any per-agent ingress runs its own
    # authorization: check the same submission right first.
    from core.conversation_access import authorize_message_submission
    authorize_message_submission(conversation_id, user_id)
    from core.conversation_writer import ConversationWriter
    from core.llm_client import stamp_message
    msg_id = _new_msg_id()
    row = stamp_message({
        "role": "user",
        "content": text,
        "msg_id": msg_id,
        "source": {"type": "user", "name": user_id, "target_agent": "ALL"},
        "channel": channel,
    }, conversation_id)
    ConversationWriter.for_conversation(conversation_id).enqueue_message(
        dict(row), agent_name="", user_id=user_id,
        sse_events=[{"type": "new_message", "data": {
            "role": "user", "content": text, "msg_id": msg_id,
            "ts": row.get("ts"), "source": dict(row["source"]),
            "channel": channel, "attachments": [],
        }}])
    for agent_name in agents:
        ff = _ingress_flowfile(conversation_id, agent_name, text, user_id,
                               msg_id, channel)
        ff.set_attribute("broadcast_pre_persisted", "1")
        _enqueue(executor, conversation_id, agent_name, ff)
    return msg_id
