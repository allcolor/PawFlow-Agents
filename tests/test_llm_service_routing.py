"""Tests for LLM service routing, call isolation, prompt library, and agent identity.

Covers:
- Feature 1: LLM service routing per agent
- Feature 2: LLM call isolation (no service-level provider blocking)
- Feature 3: Prompt library (ResourceStore "prompt" type)
- Feature 4: Agent identity/source tracking
"""

import json
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from core.llm_client import LLMClient, LLMMessage, LLMResponse, LLMToolCall
from core.connection import Connection
from core import FlowFile


# ── Feature 1: LLM Service Routing ──────────────────────────────────


import core.paths as _paths
class TestLLMConnectionServiceCapacity(unittest.TestCase):
    """Test LLMConnectionService get_client, complete_stream, capacity."""

    def test_get_client_returns_llm_client(self):
        from services.llm_connection import LLMConnectionService
        svc = LLMConnectionService.__new__(LLMConnectionService)
        svc._client = LLMClient(provider="openai", config={"api_key": "test"})
        svc._semaphore = None
        svc._max_concurrent = 0
        svc.config = {"api_key": "test"}
        client = svc.get_client()
        self.assertIsInstance(client, LLMClient)
        self.assertEqual(client.api_key, "test")
        self.assertIsNot(client, svc._client)

    def test_get_client_does_not_share_mutable_call_state(self):
        from services.llm_connection import LLMConnectionService
        svc = LLMConnectionService.__new__(LLMConnectionService)
        svc._client = LLMClient(provider="openai", config={"api_key": "test"})
        svc._client._conversation_id = "parent"
        svc._client._abort.set()
        svc._semaphore = None
        svc._max_concurrent = 0
        svc.config = {"api_key": "test"}

        first = svc.get_client()
        second = svc.get_client()

        self.assertIsNot(first, second)
        self.assertIsNot(first, svc._client)
        self.assertFalse(first._abort.is_set())
        self.assertFalse(hasattr(first, "_conversation_id"))

    def test_has_capacity_unlimited(self):
        from services.llm_connection import LLMConnectionService
        svc = LLMConnectionService.__new__(LLMConnectionService)
        svc._semaphore = None
        self.assertTrue(svc.has_capacity())

    def test_has_capacity_ignores_legacy_limit(self):
        from services.llm_connection import LLMConnectionService
        svc = LLMConnectionService.__new__(LLMConnectionService)
        svc._semaphore = threading.Semaphore(1)
        self.assertTrue(svc.has_capacity())

    def test_try_acquire_and_release_are_noops(self):
        from services.llm_connection import LLMConnectionService
        svc = LLMConnectionService.__new__(LLMConnectionService)
        svc._semaphore = threading.Semaphore(1)
        self.assertTrue(svc.try_acquire())
        self.assertTrue(svc.try_acquire())
        self.assertTrue(svc.has_capacity())
        svc.release()
        self.assertTrue(svc.has_capacity())

    def test_try_acquire_unlimited(self):
        from services.llm_connection import LLMConnectionService
        svc = LLMConnectionService.__new__(LLMConnectionService)
        svc._semaphore = None
        self.assertTrue(svc.try_acquire())
        # release should not error
        svc.release()

    def test_max_concurrent_in_schema(self):
        from services.llm_connection import LLMConnectionService
        svc = LLMConnectionService.__new__(LLMConnectionService)
        svc.config = {}
        schema = svc.get_parameter_schema()
        self.assertIn("max_concurrent", schema)
        self.assertEqual(schema["max_concurrent"]["default"], 0)


# ── Feature 2: Connection.remove ─────────────────────────────────────


class TestConnectionRemove(unittest.TestCase):
    """Test selective FlowFile removal from Connection."""

    def test_remove_specific_flowfile(self):
        conn = Connection("a", "b")
        ff1 = FlowFile(content=b"one")
        ff2 = FlowFile(content=b"two")
        ff3 = FlowFile(content=b"three")
        conn.enqueue(ff1)
        conn.enqueue(ff2)
        conn.enqueue(ff3)
        self.assertEqual(conn.queue_size(), 3)

        # Remove middle element
        result = conn.remove(ff2)
        self.assertTrue(result)
        self.assertEqual(conn.queue_size(), 2)

        # Remaining should be ff1 and ff3
        out1 = conn.dequeue()
        out2 = conn.dequeue()
        self.assertIs(out1, ff1)
        self.assertIs(out2, ff3)

    def test_remove_nonexistent_returns_false(self):
        conn = Connection("a", "b")
        ff1 = FlowFile(content=b"one")
        ff2 = FlowFile(content=b"two")
        conn.enqueue(ff1)
        result = conn.remove(ff2)
        self.assertFalse(result)
        self.assertEqual(conn.queue_size(), 1)


# ── Feature 3: Prompt Library ────────────────────────────────────────


class TestSkillResourceType(unittest.TestCase):
    """Test ResourceStore supports 'skill' type."""

    def setUp(self):
        from core.resource_store import ResourceStore
        ResourceStore.reset()
        self.store = ResourceStore.instance()
        for p in self.store.list("skill", user_id="user1"):
            self.store.delete("skill", p["name"], "user1")
        for p in self.store.list("skill", user_id="listuser"):
            self.store.delete("skill", p["name"], "listuser")

    def tearDown(self):
        from core.resource_store import ResourceStore
        ResourceStore.reset()

    def test_skill_in_valid_types(self):
        from core.resource_store import VALID_TYPES
        self.assertIn("skill", VALID_TYPES)

    def test_create_skill(self):
        entry = self.store.create("skill", "test-skill", "user1", {
            "instructions": "Summarize the following text...",
            "description": "Summarizer skill",
        })
        self.assertEqual(entry["name"], "test-skill")
        self.assertEqual(entry["instructions"], "Summarize the following text...")
        self.assertEqual(entry["description"], "Summarizer skill")

    def test_create_skill_requires_instructions(self):
        with self.assertRaises(ValueError):
            self.store.create("skill", "bad", "user1",
                              {"description": "No instructions"})

    def test_create_skill_rejects_invalid_name(self):
        with self.assertRaises(ValueError):
            self.store.create("skill", "Bad_Name", "user1", {
                "instructions": "body", "description": "desc"})

    def test_create_skill_rejects_overlong_name(self):
        with self.assertRaises(ValueError):
            self.store.create("skill", "a" * 65, "user1", {
                "instructions": "body", "description": "desc"})

    def test_create_skill_rejects_reserved_words_in_name(self):
        for bad in ("my-claude-skill", "anthropic-helper"):
            with self.assertRaises(ValueError):
                self.store.create("skill", bad, "user1", {
                    "instructions": "body", "description": "desc"})

    def test_create_skill_rejects_overlong_description(self):
        with self.assertRaises(ValueError):
            self.store.create("skill", "long-desc", "user1", {
                "instructions": "body", "description": "d" * 1025})

    def test_list_skills(self):
        self.store.create("skill", "s1", "listuser",
                          {"instructions": "skill 1", "description": "d1"})
        self.store.create("skill", "s2", "listuser",
                          {"instructions": "skill 2", "description": "d2"})
        skills = self.store.list("skill", user_id="listuser")
        self.assertEqual(len(skills), 2)

    def test_delete_skill(self):
        self.store.create("skill", "to-delete", "user1",
                          {"instructions": "bye", "description": "d"})
        self.assertTrue(self.store.delete("skill", "to-delete", "user1"))
        self.assertIsNone(self.store.get("skill", "to-delete", "user1"))


# ── Feature 4: Agent Identity / Source ───────────────────────────────


class TestLLMMessageSource(unittest.TestCase):
    """Test LLMMessage.source field."""

    def test_source_default_none(self):
        msg = LLMMessage(role="user", content="hello", conversation_id="test_conv")
        self.assertIsNone(msg.source)

    def test_source_set(self):
        msg = LLMMessage(
            role="assistant", content="hi",
            source={"type": "agent", "name": "researcher", "llm_service": "grok"}, conversation_id="test_conv")
        self.assertEqual(msg.source["type"], "agent")
        self.assertEqual(msg.source["name"], "researcher")
        self.assertEqual(msg.source["llm_service"], "grok")

    def test_source_user(self):
        msg = LLMMessage(
            role="user", content="question",
            source={"type": "user", "name": "alice"}, conversation_id="test_conv")
        self.assertEqual(msg.source["type"], "user")
        self.assertEqual(msg.source["name"], "alice")


class TestAgentTaskLLMService(unittest.TestCase):
    """Test AgentTask has llm_service and user_id fields."""

    def test_agent_task_fields(self):
        from core.agent_executor import AgentTask
        task = AgentTask(
            id="t1", agent_name="test", message="hi",
            llm_service="grok", user_id="alice",
            source_agent="parent",
        )
        self.assertEqual(task.llm_service, "grok")
        self.assertEqual(task.user_id, "alice")
        self.assertEqual(task.source_agent, "parent")

    def test_agent_task_defaults(self):
        from core.agent_executor import AgentTask
        task = AgentTask(id="t1", agent_name="test", message="hi")
        self.assertEqual(task.llm_service, "")
        self.assertEqual(task.user_id, "")
        self.assertEqual(task.source_agent, "")


class TestResolveAgentTask(unittest.TestCase):
    """Test resolve_agent_task populates llm_service and user_id."""

    def setUp(self):
        from core.resource_store import ResourceStore
        ResourceStore.reset()
        self.store = ResourceStore.instance()
        # Clean up test data
        for a in self.store.list("agent", user_id="alice"):
            self.store.delete("agent", a["name"], "alice")
        for a in self.store.list("agent", user_id="bob"):
            self.store.delete("agent", a["name"], "bob")

    def tearDown(self):
        from core.resource_store import ResourceStore
        ResourceStore.reset()

    def test_resolve_with_conv_llm_service(self):
        """llm_service comes from conv_agents config, not agent definition."""
        self.store.create("agent", "researcher", "alice", {
            "prompt": "Research assistant",
        })
        from unittest.mock import patch
        from core.conv_agent_config import CONV_AGENTS_KEY
        _fake_extras = {CONV_AGENTS_KEY: {"researcher": {
            "definition": "researcher", "llm_service": "grok",
        }}}
        with patch("core.conversation_store.ConversationStore.instance") as mock_cs:
            mock_cs.return_value.get_extra.side_effect = lambda cid, key: _fake_extras.get(key, {})
            from core.agent_executor import resolve_agent_task
            task = resolve_agent_task("researcher", "find info", "alice",
                                      conversation_id="test_conv")
            self.assertEqual(task.llm_service, "grok")
            self.assertEqual(task.user_id, "alice")

    def test_resolve_without_llm_service(self):
        """Without a conversation_id, delegation has no llm_service to
        resolve and must raise — llm_service lives on the conv_agents link."""
        self.store.create("agent", "basic", "bob", {
            "prompt": "Basic assistant",
        })
        from core.agent_executor import resolve_agent_task
        with self.assertRaises(KeyError):
            resolve_agent_task("basic", "hello", "bob")


class TestAgentDefaultInDefaults(unittest.TestCase):
    """Test that agent defaults are minimal (only description)."""

    def test_agent_defaults_minimal(self):
        from core.resource_store import _DEFAULTS
        self.assertIn("description", _DEFAULTS["agent"])
        # llm_service is now runtime (conv_agents), not in definition
        self.assertNotIn("llm_service", _DEFAULTS["agent"])


class TestManageResourceHandlerTypes(unittest.TestCase):
    """Test ManageResourceHandler resource type enum."""

    def test_skill_in_enum(self):
        from core.tool_registry import ManageResourceHandler
        h = ManageResourceHandler()
        schema = h.parameters_schema
        enum_values = schema["properties"]["resource_type"]["enum"]
        self.assertIn("skill", enum_values)
        self.assertNotIn("prompt", enum_values)


class TestMessageSerializationSource(unittest.TestCase):
    """Test that source is preserved through serialization/deserialization."""

    def test_serialize_with_source(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask.__new__(AgentLoopTask)
        msgs = [
            LLMMessage(role="user", content="hi",
                       source={"type": "user", "name": "alice"}, conversation_id="test_conv"),
            LLMMessage(role="assistant", content="hello",
                       source={"type": "agent", "name": "bot", "llm_service": "gpt"}, conversation_id="test_conv"),
        ]
        serialized = task._serialize_messages(msgs)
        self.assertEqual(serialized[0]["source"]["type"], "user")
        self.assertEqual(serialized[1]["source"]["name"], "bot")
        self.assertEqual(serialized[1]["source"]["llm_service"], "gpt")

    def test_deserialize_with_source(self):
        from tasks.ai.agent_loop import AgentLoopTask
        from core.llm_client import stamp_message
        task = AgentLoopTask.__new__(AgentLoopTask)
        data = [
            stamp_message({"role": "user", "content": "hi",
                           "source": {"type": "user", "name": "alice"}}, "test_cid"),
            stamp_message({"role": "assistant", "content": "hello",
                           "source": {"type": "agent", "name": "bot"}}, "test_cid"),
        ]
        msgs = task._deserialize_messages(data, conversation_id="test_cid")
        self.assertEqual(msgs[0].source["type"], "user")
        self.assertEqual(msgs[1].source["name"], "bot")

    def test_deserialize_without_source(self):
        """Messages without source deserialize cleanly (source is optional)."""
        from tasks.ai.agent_loop import AgentLoopTask
        from core.llm_client import stamp_message
        task = AgentLoopTask.__new__(AgentLoopTask)
        data = [stamp_message({"role": "user", "content": "hi"}, "test_cid")]
        msgs = task._deserialize_messages(data, conversation_id="test_cid")
        self.assertIsNone(msgs[0].source)

    def test_deserialize_unstamped_raises(self):
        """Unstamped on-disk message = corrupt state → hard error."""
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask.__new__(AgentLoopTask)
        data = [{"role": "user", "content": "hi"}]  # no ts/seq
        with self.assertRaises(ValueError):
            task._deserialize_messages(data)


class TestClassifyMessagesSource(unittest.TestCase):
    """Test _classify_messages_for_display includes source."""

    def test_source_in_classified(self):
        from tasks.ai.agent_loop import AgentLoopTask
        raw = [
            {"role": "user", "content": "hi",
             "source": {"type": "user", "name": "alice"}},
            {"role": "assistant", "content": "hello",
             "source": {"type": "agent", "name": "bot", "llm_service": "grok"}},
        ]
        classified = AgentLoopTask._classify_messages_for_display(raw)
        self.assertEqual(len(classified), 2)
        self.assertEqual(classified[0]["source"]["type"], "user")
        self.assertEqual(classified[1]["source"]["name"], "bot")
        self.assertEqual(classified[1]["source"]["llm_service"], "grok")

    def test_no_source_backward_compat(self):
        """Messages without explicit source get a default source for assistant."""
        from tasks.ai.agent_loop import AgentLoopTask
        raw = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        classified = AgentLoopTask._classify_messages_for_display(raw)
        self.assertEqual(len(classified), 2)
        self.assertNotIn("source", classified[0])  # user messages without source stay without
        # Assistant messages now always get a default source for badge display
        self.assertEqual(classified[1]["source"], {"type": "agent", "name": ""})

    def test_thinking_row_replays_before_assistant_anchor(self):
        from tasks.ai.agent_loop import AgentLoopTask
        raw = [
            {"role": "thinking", "content": "old reasoning",
             "msg_id": "t1", "parent_message_id": "m1", "ts": 1234.4,
             "source": {"type": "agent", "name": "bot"}},
            {"role": "assistant", "content": "visible answer",
             "msg_id": "m1", "ts": 1234.5,
             "source": {"type": "agent", "name": "bot"}},
        ]

        classified = AgentLoopTask._classify_messages_for_display(raw)

        self.assertEqual(len(classified), 2)
        self.assertEqual(classified[0]["type"], "thinking")
        self.assertEqual(classified[0]["content"], "old reasoning")
        self.assertEqual(classified[0]["msg_id"], "t1")
        self.assertEqual(classified[1]["type"], "assistant")
        self.assertEqual(classified[1]["timestamp"], 1234.5)
        self.assertEqual(classified[1]["msg_id"], "m1")

    def test_thinking_only_row_rehydrates_as_thinking_row(self):
        from tasks.ai.agent_loop import AgentLoopTask
        raw = [{
            "role": "thinking",
            "content": "live plan",
            "msg_id": "t1",
            "parent_message_id": "m1",
            "ts": 1234.5,
            "source": {"type": "agent", "name": "bot"},
        }]

        classified = AgentLoopTask._classify_messages_for_display(raw)

        self.assertEqual(len(classified), 1)
        self.assertEqual(classified[0]["type"], "thinking")
        self.assertEqual(classified[0]["content"], "live plan")
        self.assertEqual(classified[0]["timestamp"], 1234.5)
        self.assertEqual(classified[0]["msg_id"], "t1")
        self.assertEqual(classified[0]["source"]["name"], "bot")

    def test_tool_call_and_thinking_rows_rehydrate_in_order(self):
        from tasks.ai.agent_loop import AgentLoopTask
        raw = [
            {"role": "assistant", "content": "", "msg_id": "m1", "ts": 1234.5,
             "source": {"type": "agent", "name": "bot"}},
            {"role": "thinking", "content": "check files first", "msg_id": "t1",
             "parent_message_id": "m1", "ts": 1234.5,
             "source": {"type": "agent", "name": "bot"}},
            {"role": "tool_call", "content": "", "msg_id": "c1",
             "parent_message_id": "m1", "tool_call_id": "tc1", "tool_name": "read",
             "arguments": {"path": "a.py"}, "ts": 1234.5,
             "source": {"type": "agent", "name": "bot"}},
        ]

        classified = AgentLoopTask._classify_messages_for_display(raw)

        self.assertEqual([m["type"] for m in classified], ["thinking", "tool_call"])
        self.assertEqual(classified[0]["content"], "check files first")
        self.assertEqual(classified[0]["msg_id"], "t1")
        self.assertEqual(classified[0]["timestamp"], 1234.5)

    def test_empty_display_thinking_is_not_replayed_as_technical_row(self):
        from tasks.ai.agent_loop import AgentLoopTask
        raw = [
            {"role": "thinking", "content": "", "ts": 1234.0},
            {"role": "thinking", "content": "real reasoning", "ts": 1235.0},
        ]

        classified = AgentLoopTask._classify_messages_for_display(raw)

        self.assertEqual(len(classified), 1)
        self.assertEqual(classified[0]["type"], "thinking")
        self.assertEqual(classified[0]["content"], "real reasoning")

    def test_user_attachments_are_rehydrated_as_display_refs(self):
        from tasks.ai.agent_loop import AgentLoopTask
        raw = [{
            "role": "user",
            "content": "see this",
            "attachments": [{
                "filename": "screen.png",
                "mime_type": "image/png",
                "file_id": "fid123",
                "size": 42,
            }],
            "ts": 1234.0,
        }]

        classified = AgentLoopTask._classify_messages_for_display(raw)

        self.assertEqual(classified[0]["type"], "user")
        self.assertIsInstance(classified[0]["content"], list)
        self.assertEqual(classified[0]["content"][0], {"type": "text", "text": "see this"})
        self.assertEqual(classified[0]["content"][1]["type"], "image_ref")
        self.assertEqual(classified[0]["content"][1]["file_id"], "fid123")

    def test_user_attachments_are_rehydrated_for_context_deserialize(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask.__new__(AgentLoopTask)
        messages = task._deserialize_messages([{
            "role": "user",
            "content": "see this",
            "attachments": [{
                "filename": "screen.png",
                "mime_type": "image/png",
                "file_id": "fid123",
            }],
            "ts": 1234.0,
        }], conversation_id="conv1")

        self.assertEqual(messages[0].content[1]["type"], "image_ref")
        self.assertEqual(messages[0].content[1]["file_id"], "fid123")

    def test_tool_display_entries_keep_ts_without_synthetic_msg_ids(self):
        """Tool calls use tc_id; tool results keep their JSONL msg_id."""
        from tasks.ai.agent_loop import AgentLoopTask
        raw = [
            {
                "role": "tool_call",
                "content": "",
                "tool_call_id": "tc1",
                "tool_name": "bash",
                "arguments": {"command": "pwd"},
                "msg_id": "c1",
                "parent_message_id": "a1",
                "ts": 2000.0,
                "source": {"type": "agent", "name": "bot"},
            },
            {
                "role": "tool",
                "content": "ok",
                "tool_call_id": "tc1",
                "msg_id": "r1",
                "ts": 2001.0,
            },
        ]

        classified = AgentLoopTask._classify_messages_for_display(raw)

        self.assertEqual(classified[0]["type"], "tool_call")
        self.assertEqual(classified[0]["timestamp"], 2000.0)
        self.assertEqual(classified[0]["tc_id"], "tc1")
        self.assertEqual(classified[0]["msg_id"], "c1")
        self.assertEqual(classified[1]["type"], "tool_result")
        self.assertEqual(classified[1]["timestamp"], 2001.0)
        self.assertEqual(classified[1]["msg_id"], "r1")
        self.assertEqual(classified[1]["tc_id"], "tc1")

    def test_display_only_tool_result_gets_tc_id_for_reload_grouping(self):
        from tasks.ai.agent_loop import AgentLoopTask

        raw = [{
            "role": "tool_result",
            "content": "ok",
            "tool_call_id": "tc-display",
            "display_only": True,
            "ts": 2002.0,
        }]

        classified = AgentLoopTask._classify_messages_for_display(raw)

        self.assertEqual(classified[0]["type"], "tool_result")
        self.assertEqual(classified[0]["tool_call_id"], "tc-display")
        self.assertEqual(classified[0]["tc_id"], "tc-display")



class TestAgentLoopSchema(unittest.TestCase):
    """Test AgentLoopTask parameter schema changes."""

    def test_llm_service_in_schema(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask.__new__(AgentLoopTask)
        task.config = {}
        schema = task.get_parameter_schema()
        self.assertIn("llm_service", schema)

    def test_no_provider_in_schema(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask.__new__(AgentLoopTask)
        task.config = {}
        schema = task.get_parameter_schema()
        self.assertNotIn("provider", schema)
        self.assertNotIn("api_key", schema)
        self.assertNotIn("base_url", schema)

    def test_model_still_in_schema(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask.__new__(AgentLoopTask)
        task.config = {}
        schema = task.get_parameter_schema()
        self.assertIn("model", schema)


class TestFlowMigration(unittest.TestCase):
    """Verify existing flows have been migrated to llm_service."""

    def _load_flow(self, name):
        stem = name.replace(".json", "")
        path = str(_paths.REPOSITORY_DIR / "flows" / "global" / "default" / stem / "versions" / "1.0.0.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_pawflow_agent_no_inline_llm(self):
        flow = self._load_flow("pawflow_agent.json")
        params = flow.get("parameters", {})
        self.assertNotIn("provider", params)
        self.assertNotIn("api_key", params)
        # llm_service is per-agent (conv_agents), not in flow params
        self.assertNotIn("llm_service", params)

        agent_params = flow["tasks"]["agent"]["parameters"]
        self.assertNotIn("provider", agent_params)
        self.assertNotIn("api_key", agent_params)

    def test_all_agent_flows_migrated(self):
        for name in ["slack_agent.json", "discord_agent.json",
                      "telegram_agent.json", "whatsapp_agent.json"]:
            flow = self._load_flow(name)
            params = flow.get("parameters", {})
            self.assertNotIn("provider", params, f"{name} still has provider")
            self.assertNotIn("api_key", params, f"{name} still has api_key")
            self.assertIn("llm_service", params, f"{name} missing llm_service")
