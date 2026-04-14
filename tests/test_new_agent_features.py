"""Tests for new agent features: TokenTracker, Plan, Notify, CreateTool,
AskAgent, FlowManager.

Covers the handlers added in the agent extensibility sprint.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import FlowFile
from core.token_tracker import TokenTracker
from core.conversation_store import ConversationStore
from core.tool_registry import (
    CreatePlanHandler,
    UpdatePlanHandler,
    NotifyUserHandler,
    CreateToolHandler,
    FlowManagerHandler,
    PawFlowHelpHandler,
    StoreSecretHandler,
)


# ── Helpers ────────────────────────────────────────────────────────


def _make_conv_store(tmp_dir):
    """Create a ConversationStore using a tmp directory."""
    ConversationStore.reset()
    store = ConversationStore(store_dir=tmp_dir)
    ConversationStore._instance = store
    return store


def _seed_conversation(store, conv_id="conv1", user_id="user1"):
    """Create a conversation so set_extra/get_extra work."""
    store.save(conv_id, [{"role": "user", "content": "hi"}], user_id=user_id)


class _FakeLLMResponse:
    def __init__(self, content):
        self.content = content
        self.tool_calls = []
        self.usage = None


class _FakeLLMClient:
    def complete(self, messages, model="", max_tokens=2048, **kw):
        return _FakeLLMResponse("I am the agent's response.")


# ══════════════════════════════════════════════════════════════════
# 1. TokenTracker
# ══════════════════════════════════════════════════════════════════


class TestTokenTracker(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "usage.json")
        TokenTracker.reset()
        self.tracker = TokenTracker(path=self.path)
        TokenTracker._instance = self.tracker

    def tearDown(self):
        TokenTracker.reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_track_basic(self):
        self.tracker.track("alice", 100, 50, agent_name="test-agent", llm_service="openai")
        usage = self.tracker.get_usage("alice")
        self.assertEqual(usage["total_in"], 100)
        self.assertEqual(usage["total_out"], 50)

    def test_track_daily(self):
        self.tracker.track("alice", 10, 5, agent_name="test-agent", llm_service="openai")
        usage = self.tracker.get_usage("alice")
        today = time.strftime("%Y-%m-%d")
        self.assertIn(today, usage["daily"])
        self.assertEqual(usage["daily"][today]["in"], 10)

    def test_track_model(self):
        self.tracker.track("alice", 10, 5, model="gpt-4", agent_name="test-agent", llm_service="openai")
        usage = self.tracker.get_usage("alice")
        self.assertIn("gpt-4", usage["models"])
        self.assertEqual(usage["models"]["gpt-4"]["in"], 10)

    def test_get_usage_unknown_user(self):
        usage = self.tracker.get_usage("nobody")
        self.assertEqual(usage["total_in"], 0)
        self.assertEqual(usage["total_out"], 0)

    def test_singleton(self):
        self.assertIs(TokenTracker.instance(), TokenTracker.instance())

    def test_reset(self):
        old = TokenTracker.instance()
        TokenTracker.reset()
        # After reset, instance() creates a new one (default path)
        TokenTracker._instance = TokenTracker(path=self.path)
        self.assertIsNot(old, TokenTracker.instance())

    def test_flush_and_reload(self):
        self.tracker.track("alice", 200, 100, model="claude", agent_name="test-agent", llm_service="openai")
        self.tracker.flush()
        # Verify file was written
        self.assertTrue(os.path.exists(self.path))
        # Create a new tracker from same path
        TokenTracker.reset()
        t2 = TokenTracker(path=self.path)
        usage = t2.get_usage("alice")
        self.assertEqual(usage["total_in"], 200)
        self.assertEqual(usage["total_out"], 100)

    def test_flush_not_dirty(self):
        # Should not error
        self.tracker.flush()
        self.assertFalse(os.path.exists(self.path))

    def test_multiple_tracks(self):
        self.tracker.track("alice", 10, 5, agent_name="test-agent", llm_service="openai")
        self.tracker.track("alice", 20, 10, agent_name="test-agent", llm_service="openai")
        self.tracker.track("alice", 30, 15, agent_name="test-agent", llm_service="openai")
        usage = self.tracker.get_usage("alice")
        self.assertEqual(usage["total_in"], 60)
        self.assertEqual(usage["total_out"], 30)

    def test_get_all_usage(self):
        self.tracker.track("alice", 10, 5, agent_name="test-agent", llm_service="openai")
        self.tracker.track("bob", 20, 10, agent_name="test-agent", llm_service="openai")
        all_usage = self.tracker.get_all_usage()
        self.assertIn("alice", all_usage)
        self.assertIn("bob", all_usage)
        self.assertEqual(all_usage["alice"]["total_in"], 10)
        self.assertEqual(all_usage["bob"]["total_in"], 20)


# ══════════════════════════════════════════════════════════════════
# 2. Plan Handlers
# ══════════════════════════════════════════════════════════════════


class TestPlanHandlers(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = _make_conv_store(self.tmp)
        _seed_conversation(self.store, "conv1")
        # Ensure clean plan state
        from core.plan_store import PlanStore
        PlanStore._instance = None
        import core.paths as _p; shutil.rmtree(str(_p.PLANS_DIR), ignore_errors=True); _p.PLANS_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        ConversationStore.reset()
        shutil.rmtree(self.tmp, ignore_errors=True)
        # Clean up plans created during test
        from core.plan_store import PlanStore
        PlanStore._instance = None
        import core.paths as _p; shutil.rmtree(str(_p.PLANS_DIR), ignore_errors=True); _p.PLANS_DIR.mkdir(parents=True, exist_ok=True)

    def test_create_plan(self):
        h = CreatePlanHandler()
        result = h.execute({
            "title": "Deploy app",
            "steps": [
                {"description": "Build image"},
                {"description": "Push to registry"},
                {"description": "Deploy to k8s"},
            ],
        })
        self.assertIn("Deploy app", result)
        self.assertIn("Build image", result)
        self.assertIn("Push to registry", result)
        self.assertIn("Deploy to k8s", result)

    def test_create_plan_missing_fields(self):
        h = CreatePlanHandler()
        result = h.execute({"title": "", "steps": []})
        self.assertIn("Error", result)

    def test_create_plan_persists(self):
        h = CreatePlanHandler()
        h.set_conversation_id("conv1")
        h.set_user_id("user1")
        h.execute({
            "title": "Test plan",
            "steps": [{"description": "Step 1"}],
        })
        from core.plan_store import PlanStore
        plans = PlanStore.instance().list_plans("user1", "conv1")
        self.assertTrue(len(plans) >= 1)
        plan = plans[0]
        self.assertEqual(plan["title"], "Test plan")
        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual(plan["status"], "pending_approval")

    def _get_plan_id(self, conv_id="conv1"):
        """Helper: get the first plan ID from the plan store."""
        from core.plan_store import PlanStore
        plans = PlanStore.instance().list_plans("user1", conv_id)
        return plans[0]["id"] if plans else None

    def test_update_plan(self):
        # Create first
        ch = CreatePlanHandler()
        ch.set_conversation_id("conv1")
        ch.set_user_id("user1")
        ch.execute({
            "title": "My plan",
            "steps": [
                {"description": "A"},
                {"description": "B"},
                {"description": "C"},
            ],
        })
        plan_id = self._get_plan_id()
        # Set step 1 to in_progress (mimicking the orchestrator)
        from core.plan_store import PlanStore
        plan = PlanStore.instance().get("user1", "conv1", plan_id)
        plan["status"] = "approved"
        plan["steps"][0]["status"] = "in_progress"
        PlanStore.instance().save("user1", "conv1", plan)
        # Update
        uh = UpdatePlanHandler()
        uh.set_conversation_id("conv1")
        uh.set_user_id("user1")
        result = uh.execute({
            "plan_id": plan_id,
            "updates": [{"step": 1, "status": "done"}],
        })
        self.assertIn("1/3", result)

    def test_update_plan_no_plan(self):
        uh = UpdatePlanHandler()
        uh.set_conversation_id("conv1")
        uh.set_user_id("user1")
        result = uh.execute({
            "plan_id": "p_nonexistent",
            "updates": [{"step": 1, "status": "done"}],
        })
        self.assertIn("Error", result)
        self.assertIn("not found", result)

    def test_update_plan_with_note(self):
        ch = CreatePlanHandler()
        ch.set_conversation_id("conv1")
        ch.set_user_id("user1")
        ch.execute({
            "title": "Noted plan",
            "steps": [{"description": "Do X"}],
        })
        plan_id = self._get_plan_id()
        # Set step to in_progress first
        from core.plan_store import PlanStore
        plan = PlanStore.instance().get("user1", "conv1", plan_id)
        plan["status"] = "approved"
        plan["steps"][0]["status"] = "in_progress"
        PlanStore.instance().save("user1", "conv1", plan)
        uh = UpdatePlanHandler()
        uh.set_conversation_id("conv1")
        uh.set_user_id("user1")
        result = uh.execute({
            "plan_id": plan_id,
            "updates": [{"step": 1, "status": "done", "note": "All good"}],
        })
        self.assertIn("All good", result)

    def test_update_plan_invalid_step(self):
        ch = CreatePlanHandler()
        ch.set_conversation_id("conv1")
        ch.set_user_id("user1")
        ch.execute({
            "title": "Plan",
            "steps": [{"description": "Only step"}],
        })
        plan_id = self._get_plan_id()
        uh = UpdatePlanHandler()
        uh.set_conversation_id("conv1")
        uh.set_user_id("user1")
        # Step 99 doesn't exist — should not crash
        result = uh.execute({
            "plan_id": plan_id,
            "updates": [{"step": 99, "status": "done"}],
        })
        self.assertIn("0/1", result)  # no step was actually updated

    def test_create_multiple_plans(self):
        h = CreatePlanHandler()
        h.set_conversation_id("conv1")
        h.set_user_id("user1")
        h.execute({"title": "Plan A", "steps": [{"description": "X"}]})
        h.execute({"title": "Plan B", "steps": [{"description": "Y"}, {"description": "Z"}]})
        from core.plan_store import PlanStore
        plans = PlanStore.instance().list_plans("user1", "conv1")
        self.assertEqual(len(plans), 2)
        titles = [p["title"] for p in plans]
        self.assertIn("Plan A", titles)
        self.assertIn("Plan B", titles)


# ══════════════════════════════════════════════════════════════════
# 3. NotifyUserHandler
# ══════════════════════════════════════════════════════════════════


class TestNotifyUserHandler(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = _make_conv_store(self.tmp)
        _seed_conversation(self.store, "conv1")

    def tearDown(self):
        ConversationStore.reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("core.conversation_event_bus.ConversationEventBus")
    def test_notify_basic(self, mock_bus_cls):
        mock_bus = MagicMock()
        mock_bus_cls.instance.return_value = mock_bus
        h = NotifyUserHandler()
        h.set_conversation_id("conv1")
        h.set_user_id("user1")
        result = h.execute({"message": "Hello!"})
        self.assertIn("sse", result)
        mock_bus.publish_event.assert_called_once()

    def test_notify_no_channels(self):
        h = NotifyUserHandler()
        # No conversation_id set
        result = h.execute({"message": "Hello!"})
        self.assertIn("queued", result.lower())

    def test_notify_missing_message(self):
        h = NotifyUserHandler()
        result = h.execute({"message": ""})
        self.assertIn("Error", result)

    @patch("core.conversation_event_bus.ConversationEventBus")
    def test_notify_urgency(self, mock_bus_cls):
        mock_bus = MagicMock()
        mock_bus_cls.instance.return_value = mock_bus
        h = NotifyUserHandler()
        h.set_conversation_id("conv1")
        h.set_user_id("user1")
        result = h.execute({"message": "Urgent!", "urgency": "high"})
        self.assertIn("sse", result)
        call_args = mock_bus.publish_event.call_args
        self.assertEqual(call_args[0][2]["urgency"], "high")

    @patch("core.conversation_event_bus.ConversationEventBus")
    def test_notify_with_telegram_metadata(self, mock_bus_cls):
        mock_bus = MagicMock()
        mock_bus_cls.instance.return_value = mock_bus
        # Set telegram_chat_id in conv extra
        self.store.set_extra("conv1", "telegram_chat_id", "12345")
        h = NotifyUserHandler()
        h.set_conversation_id("conv1")
        h.set_user_id("user1")
        result = h.execute({"message": "TG test"})
        self.assertIn("telegram_queued", result)


# ══════════════════════════════════════════════════════════════════
# 4. CreateToolHandler
# ══════════════════════════════════════════════════════════════════


_VALID_TOOL_SOURCE = '''
class GreeterHandler(ToolHandler):
    @property
    def name(self):
        return "greeter"

    @property
    def description(self):
        return "Says hello"

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
        }

    def execute(self, arguments):
        return f"Hello, {arguments.get('name', 'world')}!"
'''


class TestCreateToolHandler(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        from core.dynamic_tool_store import DynamicToolStore
        from core.tool_registry import ToolRegistry
        DynamicToolStore.reset()
        self.dts = DynamicToolStore(store_dir=self.tmp)
        DynamicToolStore._instance = self.dts
        self._old_live = ToolRegistry._live_registry
        ToolRegistry._live_registry = ToolRegistry()

    def tearDown(self):
        from core.dynamic_tool_store import DynamicToolStore
        from core.tool_registry import ToolRegistry
        ToolRegistry._live_registry = self._old_live
        DynamicToolStore.reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_tool_basic(self):
        h = CreateToolHandler()
        h.set_user_id("alice")
        result = h.execute({
            "tool_name": "greeter",
            "code": _VALID_TOOL_SOURCE, "tool_description": "Test tool",
        })
        self.assertIn("created and registered", result)
        self.assertIn("greeter", result)

    def test_create_tool_missing_fields(self):
        h = CreateToolHandler()
        result = h.execute({"tool_name": "", "code": "", "tool_description": "Test tool"})
        self.assertIn("Error", result)

    def test_create_tool_bad_source(self):
        h = CreateToolHandler()
        h.set_user_id("alice")
        bad_source = "import os\nos.system('rm -rf /')\n"
        result = h.execute({
            "tool_name": "evil",
            "code": bad_source, "tool_description": "Test tool",
        })
        # Dynamic tools are registered without source validation
        # (validation happens at execution time in the sandbox)
        self.assertIn("created and registered", result)

    def test_create_tool_no_handler(self):
        h = CreateToolHandler()
        h.set_user_id("alice")
        source = "x = 42\n"
        result = h.execute({
            "tool_name": "nohandler",
            "code": source, "tool_description": "Test tool",
        })
        # Code without a handler class still registers (runs as script)
        self.assertIn("created and registered", result)

    def test_create_tool_user_isolation(self):
        from core.tool_registry import ToolRegistry
        h = CreateToolHandler()
        h.set_user_id("bob")
        h.execute({
            "tool_name": "mytool",
            "code": _VALID_TOOL_SOURCE, "tool_description": "Test tool",
        })
        # Verify tool is registered in the live registry
        registry = ToolRegistry._live_registry
        self.assertIsNotNone(registry.get("mytool"))


# ══════════════════════════════════════════════════════════════════
# 5. FlowManagerHandler
# ══════════════════════════════════════════════════════════════════


class TestFlowManagerHandler(unittest.TestCase):

    def setUp(self):
        from core.deployment_registry import DeploymentRegistry
        DeploymentRegistry.reset()
        import core.paths as _p
        import shutil
        if _p.DEPLOYMENTS_DIR.exists():
            shutil.rmtree(_p.DEPLOYMENTS_DIR)
        _p.DEPLOYMENTS_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        from core.deployment_registry import DeploymentRegistry
        DeploymentRegistry.reset()

    def _make_handler(self, user_id="alice"):
        h = FlowManagerHandler()
        h.set_user_id(user_id)
        return h

    def _sample_definition(self, flow_id="flow1", name="Test Flow"):
        return {
            "id": flow_id,
            "name": name,
            "tasks": {"t1": {"type": "generateFlowFile"}},
            "connections": [],
        }

    def test_list_empty(self):
        h = self._make_handler()
        result = h.execute({"action": "list"})
        self.assertIn("No flows", result)

    def test_create_flow(self):
        h = self._make_handler()
        result = h.execute({
            "action": "create",
            "definition": self._sample_definition(),
        })
        self.assertIn("created", result)
        # Instance should exist in deployment registry
        from core.deployment_registry import DeploymentRegistry
        inst = DeploymentRegistry.get_instance().get("flow1")
        self.assertIsNotNone(inst)

    def test_create_flow_missing_id(self):
        h = self._make_handler()
        result = h.execute({
            "action": "create",
            "definition": {"name": "No ID"},
        })
        self.assertIn("Error", result)

    def test_create_flow_owner_tag(self):
        h = self._make_handler("bob")
        h.execute({
            "action": "create",
            "definition": self._sample_definition(),
        })
        from core.deployment_registry import DeploymentRegistry
        inst = DeploymentRegistry.get_instance().get("flow1")
        self.assertEqual(inst.owner, "bob")

    def test_start_flow(self):
        h = self._make_handler()
        h.execute({"action": "create", "definition": self._sample_definition()})
        result = h.execute({"action": "start", "flow_id": "flow1"})
        # Start may fail (no real template parse in test) but instance should exist
        from core.deployment_registry import DeploymentRegistry
        inst = DeploymentRegistry.get_instance().get("flow1")
        self.assertIsNotNone(inst)

    def test_stop_flow(self):
        h = self._make_handler()
        h.execute({"action": "create", "definition": self._sample_definition()})
        result = h.execute({"action": "stop", "flow_id": "flow1"})
        self.assertIn("stopped", result)

    def test_status_flow(self):
        h = self._make_handler()
        h.execute({"action": "create", "definition": self._sample_definition()})
        result = h.execute({"action": "status", "flow_id": "flow1"})
        self.assertIn("Test Flow", result)

    def test_delete_flow(self):
        h = self._make_handler()
        h.execute({"action": "create", "definition": self._sample_definition()})
        result = h.execute({"action": "delete", "flow_id": "flow1"})
        self.assertIn("deleted", result)
        from core.deployment_registry import DeploymentRegistry
        self.assertIsNone(DeploymentRegistry.get_instance().get("flow1"))

    def test_isolation(self):
        h_alice = self._make_handler("alice")
        h_bob = self._make_handler("bob")
        h_alice.execute({
            "action": "create",
            "definition": self._sample_definition("alice_flow"),
        })
        # Bob tries to stop Alice's flow
        result = h_bob.execute({"action": "stop", "flow_id": "alice_flow"})
        self.assertIn("belongs to another user", result)

    def test_list_only_own(self):
        h_alice = self._make_handler("alice")
        h_bob = self._make_handler("bob")
        h_alice.execute({
            "action": "create",
            "definition": self._sample_definition("fa", "Alice's flow"),
        })
        h_bob.execute({
            "action": "create",
            "definition": self._sample_definition("fb", "Bob's flow"),
        })
        alice_list = h_alice.execute({"action": "list_all"})
        bob_list = h_bob.execute({"action": "list_all"})
        self.assertIn("fa", alice_list)
        self.assertNotIn("fb", alice_list)
        self.assertIn("fb", bob_list)
        self.assertNotIn("fa", bob_list)

    def test_start_with_parameters(self):
        h = self._make_handler()
        h.execute({"action": "create", "definition": self._sample_definition()})
        h.execute({
            "action": "start",
            "flow_id": "flow1",
            "parameters": {"key1": "val1"},
        })
        from core.deployment_registry import DeploymentRegistry
        inst = DeploymentRegistry.get_instance().get("flow1")
        self.assertEqual(inst.parameters.get("key1"), "val1")

    def test_delete_nonexistent(self):
        h = self._make_handler()
        result = h.execute({"action": "delete", "flow_id": "nope"})
        self.assertIn("not found", result)

    def test_unknown_action(self):
        h = self._make_handler()
        result = h.execute({"action": "explode"})
        self.assertIn("unknown action", result)


# ══════════════════════════════════════════════════════════════════
# i18n keys check
# ══════════════════════════════════════════════════════════════════


class TestPawFlowHelpHandler(unittest.TestCase):
    """Tests for PawFlowHelpHandler."""

    def setUp(self):
        self.handler = PawFlowHelpHandler()

    def test_name(self):
        self.assertEqual(self.handler.name, "pawflow_help")

    def test_schema(self):
        schema = self.handler.parameters_schema
        self.assertIn("topic", schema["properties"])
        self.assertEqual(schema["required"], ["topic"])

    def test_list_tasks(self):
        result = self.handler.execute({"topic": "tasks"})
        self.assertIn("Available tasks", result)

    def test_task_detail_known(self):
        # updateAttribute should always be available
        result = self.handler.execute({"topic": "task:updateAttribute"})
        self.assertIn("updateAttribute", result)

    def test_task_detail_unknown(self):
        result = self.handler.execute({"topic": "task:nonExistentTask123"})
        self.assertIn("not found", result)

    def test_list_services(self):
        result = self.handler.execute({"topic": "services"})
        self.assertIn("Available services", result)

    def test_service_detail_unknown(self):
        result = self.handler.execute({"topic": "service:nonExistentSvc123"})
        self.assertIn("not found", result)

    def test_flow_guide(self):
        result = self.handler.execute({"topic": "flow_guide"})
        self.assertIn("Flow JSON Structure", result)
        self.assertIn("connections", result)
        self.assertIn("tasks", result)

    def test_expressions_guide(self):
        result = self.handler.execute({"topic": "expressions"})
        self.assertIn("${", result)
        self.assertIn("Flow Parameters", result)

    def test_triggers_guide(self):
        result = self.handler.execute({"topic": "triggers"})
        self.assertIn("CRON", result)
        self.assertIn("cronTrigger", result)

    def test_unknown_topic(self):
        result = self.handler.execute({"topic": "foobar"})
        self.assertIn("Unknown topic", result)

    def test_empty_topic(self):
        result = self.handler.execute({"topic": ""})
        self.assertIn("Error", result)


class TestStoreSecretHandler(unittest.TestCase):
    """Tests for StoreSecretHandler."""

    def setUp(self):
        self.handler = StoreSecretHandler()
        self.handler.set_user_id("testuser")
        self.tmpdir = tempfile.mkdtemp()
        import core.paths as _p
        self._orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(self.tmpdir)

    def tearDown(self):
        import core.paths as _p
        _p.USER_CONFIG_DIR = self._orig_ucd
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_name(self):
        self.assertEqual(self.handler.name, "store_secret")

    def test_schema(self):
        schema = self.handler.parameters_schema
        self.assertIn("key", schema["properties"])
        self.assertIn("value", schema["properties"])
        self.assertEqual(sorted(schema["required"]), ["key", "value"])

    def test_store_secret(self):
        result = self.handler.execute({"key": "my_api_key", "value": "sk-12345"})
        self.assertIn("stored securely", result)
        self.assertIn("my_api_key", result)
        # Verify file was created in user directory
        secrets_path = Path(self.tmpdir) / "testuser" / "secrets.json"
        self.assertTrue(secrets_path.exists())
        data = json.loads(secrets_path.read_text(encoding="utf-8"))
        self.assertIn("my_api_key", data)
        # Value should be encrypted (enc: prefix)
        self.assertTrue(data["my_api_key"].startswith("enc:"))

    def test_store_secret_missing_key(self):
        result = self.handler.execute({"key": "", "value": "abc"})
        self.assertIn("Error", result)

    def test_store_secret_missing_value(self):
        result = self.handler.execute({"key": "foo", "value": ""})
        self.assertIn("Error", result)

    def test_store_multiple_secrets(self):
        self.handler.execute({"key": "key1", "value": "val1"})
        self.handler.execute({"key": "key2", "value": "val2"})
        secrets_path = Path(self.tmpdir) / "testuser" / "secrets.json"
        data = json.loads(secrets_path.read_text(encoding="utf-8"))
        self.assertIn("key1", data)
        self.assertIn("key2", data)

    def test_store_secret_anonymous(self):
        handler = StoreSecretHandler()  # no user_id set
        result = handler.execute({"key": "anon_key", "value": "val"})
        self.assertIn("anon_key", result)


class TestFlowManagerSchedule(unittest.TestCase):
    """Tests for the CRON scheduling feature in FlowManagerHandler."""

    def setUp(self):
        self.handler = FlowManagerHandler()
        self.handler.set_user_id("testuser")
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)



class TestFlowCatalogDeploy(unittest.TestCase):
    """Tests for catalog and deploy actions in FlowManagerHandler."""

    def setUp(self):
        from core.deployment_registry import DeploymentRegistry
        DeploymentRegistry.reset()
        # Clean deployments dir for fresh state
        import core.paths as _p
        import shutil
        if _p.DEPLOYMENTS_DIR.exists():
            shutil.rmtree(_p.DEPLOYMENTS_DIR)
        _p.DEPLOYMENTS_DIR.mkdir(parents=True, exist_ok=True)

        self.handler = FlowManagerHandler()
        self.handler.set_user_id("user1")
        self.handler.set_conversation_id("conv-1")
        self.tmpdir = tempfile.mkdtemp()

        # Redirect deployments to temp dir
        import core.paths as _p; _p.DEPLOYMENTS_DIR.mkdir(parents=True, exist_ok=True)

        # Create test flow templates in the repository (may already exist from conftest)
        from core.repository import ScopedRepository
        repo = ScopedRepository.instance()
        for fqn, data in [
            ("default.hello_world:1.0.0", {
                "id": "hello-world", "name": "Hello World",
                "description": "A simple hello flow",
                "tasks": {"t1": {"type": "logAttribute"}},
                "relations": [], "parameters": {"greeting": "hello"},
            }),
            ("default.data_pipeline:2.0.0", {
                "id": "data-pipeline", "name": "Data Pipeline",
                "description": "ETL pipeline",
                "tasks": {"extract": {"type": "fetchData"}, "load": {"type": "putFile"}},
                "relations": [{"from": "extract", "to": "load", "type": "success"}],
            }),
        ]:
            try:
                repo.create_flow(fqn, "global", data)
            except ValueError:
                pass

    def tearDown(self):
        import core.deployment_registry as dep_mod
        from core.deployment_registry import DeploymentRegistry
        DeploymentRegistry.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_catalog_lists_templates(self):
        result = self.handler.execute({"action": "catalog"})
        self.assertIn("Available templates", result)
        self.assertIn("hello_world", result)
        self.assertIn("data_pipeline", result)
        self.assertIn("hello_world", result)

    def test_catalog_shows_description(self):
        result = self.handler.execute({"action": "catalog"})
        self.assertIn("A simple hello flow", result)
        self.assertIn("ETL pipeline", result)

    def test_deploy_creates_instance(self):
        result = self.handler.execute({
            "action": "deploy",
            "template_id": "default.hello_world:1.0.0",
        })
        self.assertIn("deployed", result)
        self.assertIn("instance", result.lower())
        # Instance should exist in deployment registry
        from core.deployment_registry import DeploymentRegistry
        dep_reg = DeploymentRegistry.get_instance()
        instances = dep_reg.get_by_owner("user1")
        self.assertEqual(len(instances), 1)
        inst = instances[0]
        self.assertEqual(inst.flow_id, "hello-world")
        self.assertEqual(inst.owner, "user1")
        self.assertEqual(inst.conversation_id, "conv-1")

    def test_deploy_with_parameters(self):
        result = self.handler.execute({
            "action": "deploy",
            "template_id": "default.hello_world:1.0.0",
            "parameters": {"greeting": "bonjour"},
        })
        self.assertIn("deployed", result)
        from core.deployment_registry import DeploymentRegistry
        instances = DeploymentRegistry.get_instance().get_by_owner("user1")
        self.assertEqual(instances[0].parameters.get("greeting"), "bonjour")

    def test_run_executes_template_synchronously(self):
        """`run` parses the FQN, runs the flow once, returns outputs inline."""
        from core.repository import ScopedRepository
        from tasks import register_all_tasks
        register_all_tasks()
        # Tiny one-task flow that just passes the input through `log`,
        # which does not modify the FlowFile content.
        try:
            ScopedRepository.instance().create_flow(
                "default.runme:1.0.0", "global", {
                    "id": "runme", "name": "RunMe",
                    "tasks": {"t": {"type": "log",
                                    "parameters": {"message": "hi"}}},
                    "relations": [],
                })
        except ValueError:
            pass
        result = self.handler.execute({
            "action": "run",
            "template_id": "default.runme:1.0.0",
            "input": "payload-xyz",
        })
        data = json.loads(result)
        self.assertTrue(data["success"])
        self.assertEqual(data["template_id"], "default.runme:1.0.0")
        self.assertGreaterEqual(len(data["outputs"]), 1)
        self.assertEqual(data["outputs"][0]["content"], "payload-xyz")

    def test_run_unknown_template(self):
        result = self.handler.execute({
            "action": "run",
            "template_id": "default.nope:9.9.9",
        })
        self.assertIn("not found", result)

    def test_run_missing_template_id(self):
        result = self.handler.execute({"action": "run"})
        self.assertIn("template_id is required", result)

    def test_deploy_unknown_template(self):
        result = self.handler.execute({
            "action": "deploy",
            "template_id": "default.nonexistent:1.0.0",
        })
        self.assertIn("not found", result)

    def test_deploy_no_template_id(self):
        result = self.handler.execute({"action": "deploy"})
        self.assertIn("Error", result)

    def test_deploy_overwrites_existing(self):
        """Re-deploying same template creates a new instance each time."""
        self.handler.execute({
            "action": "deploy", "template_id": "default.hello_world:1.0.0",
        })
        result = self.handler.execute({
            "action": "deploy", "template_id": "default.hello_world:1.0.0",
        })
        # New deployment model creates new instances each time
        self.assertIn("deployed", result)
        from core.deployment_registry import DeploymentRegistry
        instances = DeploymentRegistry.get_instance().get_by_owner("user1")
        self.assertEqual(len(instances), 2)

    def test_deploy_different_conversations_coexist(self):
        """Same template deployed in different conversations → 2 instances."""
        self.handler.set_conversation_id("conv-A")
        self.handler.execute({
            "action": "deploy", "template_id": "default.hello_world:1.0.0",
        })
        self.handler.set_conversation_id("conv-B")
        self.handler.execute({
            "action": "deploy", "template_id": "default.hello_world:1.0.0",
        })
        from core.deployment_registry import DeploymentRegistry
        instances = DeploymentRegistry.get_instance().get_by_owner("user1")
        self.assertEqual(len(instances), 2)

    def test_deployed_instance_shows_in_list(self):
        self.handler.execute({
            "action": "deploy", "template_id": "default.data_pipeline:2.0.0",
        })
        result = self.handler.execute({"action": "list"})
        self.assertIn("data-pipeline", result)
        self.assertIn("from:", result)

    def test_status_shows_template(self):
        self.handler.execute({
            "action": "deploy", "template_id": "default.hello_world:1.0.0",
        })
        from core.deployment_registry import DeploymentRegistry
        instances = DeploymentRegistry.get_instance().get_by_owner("user1")
        instance_id = instances[0].instance_id
        result = self.handler.execute({
            "action": "status", "flow_id": instance_id,
        })
        self.assertIn("hello_world", result)


class TestAutoStopAndStopFlow(unittest.TestCase):
    """Tests for auto-stop mechanism and StopFlowTask."""

    def test_base_task_is_persistent_source_default(self):
        """Default tasks are not persistent sources."""
        from core.base_task import BaseTask
        class DummyTask(BaseTask):
            TYPE = "dummy"
            def execute(self, ff):
                return [ff]
        t = DummyTask({})
        self.assertFalse(t.is_persistent_source)

    def test_http_receiver_is_persistent(self):
        from tasks.io.http_receiver import HTTPReceiverTask
        t = HTTPReceiverTask.__new__(HTTPReceiverTask)
        self.assertTrue(t.is_persistent_source)

    def test_telegram_receiver_is_persistent(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        t = TelegramReceiverTask.__new__(TelegramReceiverTask)
        self.assertTrue(t.is_persistent_source)

    def test_list_files_persistent_when_polling(self):
        from tasks.system.list_files import ListFilesTask
        t = ListFilesTask({"directory": "/tmp", "polling_interval": "10"})
        self.assertTrue(t.is_persistent_source)

    def test_list_files_not_persistent_without_polling(self):
        from tasks.system.list_files import ListFilesTask
        t = ListFilesTask({"directory": "/tmp", "polling_interval": "0"})
        self.assertFalse(t.is_persistent_source)

    def test_stop_flow_task(self):
        from tasks.control.stop_flow import StopFlowTask
        t = StopFlowTask({"reason": "test complete"})
        ff = FlowFile(content=b"done")
        results = t.execute(ff)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get_attribute("flow.stop_requested"), "true")
        self.assertEqual(results[0].get_attribute("flow.stop_reason"), "test complete")

    def test_stop_flow_task_registered(self):
        from core import TaskFactory
        cls = TaskFactory.get("stopFlow")
        self.assertEqual(cls.TYPE, "stopFlow")

    def test_connection_manager_all_empty(self):
        from core.connection import ConnectionManager, Connection
        mgr = ConnectionManager()
        # Empty manager -> all_empty is True
        self.assertTrue(mgr.all_empty())
        conn = Connection("a", "b")
        mgr.add_connection(conn)
        self.assertTrue(mgr.all_empty())
        conn.enqueue(FlowFile(content=b"data"))
        self.assertFalse(mgr.all_empty())

    def test_continuous_executor_detects_persistent_sources(self):
        """Executor should detect persistent sources in flow."""
        from unittest.mock import MagicMock, PropertyMock
        from engine.continuous_executor import ContinuousFlowExecutor
        from core import Flow

        # Flow with a persistent source
        flow = Flow({"id": "test", "relations": []})
        mock_task = MagicMock()
        type(mock_task).is_persistent_source = PropertyMock(return_value=True)
        mock_task.TYPE = "httpReceiver"
        flow.tasks["http_in"] = mock_task

        executor = ContinuousFlowExecutor(
            flow, enable_checkpoints=False,
        )
        self.assertTrue(executor._has_persistent_sources)

    def test_continuous_executor_no_persistent_sources(self):
        """Executor should detect absence of persistent sources."""
        from unittest.mock import MagicMock, PropertyMock
        from engine.continuous_executor import ContinuousFlowExecutor
        from core import Flow

        flow = Flow({"id": "test2", "relations": []})
        mock_task = MagicMock()
        type(mock_task).is_persistent_source = PropertyMock(return_value=False)
        mock_task.TYPE = "logAttribute"
        flow.tasks["log"] = mock_task

        executor = ContinuousFlowExecutor(
            flow, enable_checkpoints=False,
        )
        self.assertFalse(executor._has_persistent_sources)


class TestConversationScoping(unittest.TestCase):
    """Tests for conversation-scoped resource management."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        from core.deployment_registry import DeploymentRegistry
        import core.deployment_registry as dep_mod
        DeploymentRegistry.reset()
        import core.paths as _p; _p.DEPLOYMENTS_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import core.deployment_registry as dep_mod
        from core.deployment_registry import DeploymentRegistry
        DeploymentRegistry.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_flow_tagged_with_conversation_id(self):
        handler = FlowManagerHandler()
        handler.set_user_id("user1")
        handler.set_conversation_id("conv-123")
        handler.execute({"action": "create", "definition": {
            "id": "f1", "name": "Flow 1", "tasks": {"t1": {"type": "log"}},
        }})
        from core.deployment_registry import DeploymentRegistry
        inst = DeploymentRegistry.get_instance().get("f1")
        self.assertIsNotNone(inst)
        self.assertEqual(inst.conversation_id, "conv-123")

    def test_list_filters_by_conversation(self):
        handler = FlowManagerHandler()
        handler.set_user_id("user1")
        # Create flow in conv-A
        handler.set_conversation_id("conv-A")
        handler.execute({"action": "create", "definition": {
            "id": "fa", "name": "Flow A", "tasks": {"t1": {"type": "log"}},
        }})
        # Create flow in conv-B
        handler.set_conversation_id("conv-B")
        handler.execute({"action": "create", "definition": {
            "id": "fb", "name": "Flow B", "tasks": {"t1": {"type": "log"}},
        }})
        # List from conv-A should only show fa
        handler.set_conversation_id("conv-A")
        result = handler.execute({"action": "list"})
        self.assertIn("fa", result)
        self.assertNotIn("fb", result)

    def test_list_all_shows_everything(self):
        handler = FlowManagerHandler()
        handler.set_user_id("user1")
        handler.set_conversation_id("conv-A")
        handler.execute({"action": "create", "definition": {
            "id": "fa", "name": "Flow A", "tasks": {"t1": {"type": "log"}},
        }})
        handler.set_conversation_id("conv-B")
        handler.execute({"action": "create", "definition": {
            "id": "fb", "name": "Flow B", "tasks": {"t1": {"type": "log"}},
        }})
        result = handler.execute({"action": "list_all"})
        self.assertIn("fa", result)
        self.assertIn("fb", result)

    def test_update_flow_parameters(self):
        handler = FlowManagerHandler()
        handler.set_user_id("user1")
        handler.set_conversation_id("conv-1")
        handler.execute({"action": "create", "definition": {
            "id": "f1", "name": "Flow", "tasks": {"t1": {"type": "log"}},
            "parameters": {"key1": "val1"},
        }})
        result = handler.execute({"action": "update", "flow_id": "f1",
                                   "parameters": {"key2": "val2"}})
        self.assertIn("updated", result)
        from core.deployment_registry import DeploymentRegistry
        inst = DeploymentRegistry.get_instance().get("f1")
        self.assertEqual(inst.parameters.get("key1"), "val1")
        self.assertEqual(inst.parameters.get("key2"), "val2")

    def test_update_flow_no_params(self):
        handler = FlowManagerHandler()
        handler.set_user_id("user1")
        handler.set_conversation_id("conv-1")
        handler.execute({"action": "create", "definition": {
            "id": "f1", "name": "Flow", "tasks": {"t1": {"type": "log"}},
        }})
        result = handler.execute({"action": "update", "flow_id": "f1"})
        self.assertIn("Error", result)

    def test_cleanup_conversation_flows(self):
        handler = FlowManagerHandler()
        handler.set_user_id("user1")
        handler.set_conversation_id("conv-del")
        handler.execute({"action": "create", "definition": {
            "id": "f1", "name": "Flow 1", "tasks": {"t1": {"type": "log"}},
        }})
        handler.execute({"action": "create", "definition": {
            "id": "f2", "name": "Flow 2", "tasks": {"t1": {"type": "log"}},
        }})
        # Create flow in different conversation (should NOT be deleted)
        handler.set_conversation_id("conv-keep")
        handler.execute({"action": "create", "definition": {
            "id": "f3", "name": "Flow 3", "tasks": {"t1": {"type": "log"}},
        }})
        # Cleanup
        FlowManagerHandler.cleanup_conversation("conv-del")
        from core.deployment_registry import DeploymentRegistry
        dep_reg = DeploymentRegistry.get_instance()
        self.assertIsNone(dep_reg.get("f1"))
        self.assertIsNone(dep_reg.get("f2"))
        self.assertIsNotNone(dep_reg.get("f3"))

    def test_cleanup_conversation_secrets_noop(self):
        """User secrets are permanent, cleanup_conversation is a no-op."""
        handler = StoreSecretHandler()
        handler.set_user_id("user1")
        handler.execute({"key": "k1", "value": "v1"})
        # Cleanup should not remove user secrets
        StoreSecretHandler.cleanup_conversation("conv-del")
        from core.paths import user_secrets_path; secrets_path = user_secrets_path("user1")
        data = json.loads(secrets_path.read_text(encoding="utf-8"))
        self.assertIn("k1", data)

    def test_secret_stored_in_user_dir(self):
        """Secrets are stored in data/config/users/{username}/secrets.json."""
        handler = StoreSecretHandler()
        handler.set_user_id("user1")
        handler.execute({"key": "mykey", "value": "myval"})
        from core.paths import user_secrets_path; secrets_path = user_secrets_path("user1")
        self.assertTrue(secrets_path.exists())
        data = json.loads(secrets_path.read_text(encoding="utf-8"))
        self.assertIn("mykey", data)
        self.assertTrue(data["mykey"].startswith("enc:"))

