import json
import shutil
import tempfile
from pathlib import Path


class TestGoalTaskAssignment:
    def setup_method(self):
        from core.conversation_store import ConversationStore
        from core.poll_scheduler import PollScheduler

        ConversationStore.reset()
        PollScheduler.reset()
        self._tmpdir = tempfile.mkdtemp()
        store = ConversationStore.instance()
        store._store_dir = Path(self._tmpdir)
        store._store_dir.mkdir(parents=True, exist_ok=True)

    def teardown_method(self):
        from core.conversation_store import ConversationStore
        from core.poll_scheduler import PollScheduler

        ConversationStore.reset()
        PollScheduler.reset()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run_scheduling(self, action, body):
        from core import FlowFile
        from core.conversation_store import ConversationStore
        from tasks.ai.agent_loop import AgentLoopTask
        from tasks.ai.actions.scheduling import _handle_scheduling

        task = AgentLoopTask({"conversation_store": True, "api_key": "test-key"})
        flowfile = FlowFile(content=json.dumps(body).encode())
        result = _handle_scheduling(
            task, action, body, ConversationStore.instance(), "alice", flowfile)
        return json.loads(result[0].get_content().decode("utf-8"))

    def test_goal_creates_conversation_task_and_uses_active_agent(self):
        from core.conversation_store import ConversationStore

        store = ConversationStore.instance()
        store.save("goal-conv", [{"role": "user", "content": "hi"}], user_id="alice")
        store.set_extra("goal-conv", "active_resources", {"agent": "qwen"})

        objective = "Migrate X until tests pass and final audit is done"
        data = self._run_scheduling("goal", {
            "action": "goal",
            "conversation_id": "goal-conv",
            "prompt": objective,
            "interval": "120",
            "max_budget": "$5",
            "interactive": True,
        })

        assert data["ok"] is True
        assert data["agent"] == "qwen"
        assert data["name"].startswith("goal_migrate_x_until_tests_pass_and_")
        task_def = store.get_extra("goal-conv", "conversation_task_defs")[data["name"]]
        assert task_def["prompt"] == objective
        assert task_def["criteria"] == objective
        assert task_def["kind"] == "goal"
        assert task_def["interactive"] is True

        tasks = store.get_extra("goal-conv", "agent_tasks")
        assigned = next(iter(tasks.values()))
        assert assigned["agent"] == "qwen"
        assert assigned["task_def_name"] == data["name"]
        assert assigned["completion_criteria"] == objective
        assert assigned["max_budget"] == 5.0
        assert assigned["interactive"] is True

    def test_inline_task_without_criteria_stays_open_ended(self):
        from core.conversation_store import ConversationStore

        store = ConversationStore.instance()
        store.save("task-conv", [], user_id="alice")

        data = self._run_scheduling("create_and_assign_task_def", {
            "action": "create_and_assign_task_def",
            "conversation_id": "task-conv",
            "agent_name": "qwen",
            "prompt": "Check the deployment periodically",
            "interval": "60",
        })

        assert data["ok"] is True
        task_def = store.get_extra("task-conv", "conversation_task_defs")[data["name"]]
        assert task_def["criteria"] == ""
        tasks = store.get_extra("task-conv", "agent_tasks")
        assigned = next(iter(tasks.values()))
        assert assigned["completion_criteria"] == ""

    def test_task_definition_verifier_is_used_when_assignment_omits_one(self):
        from core.conversation_store import ConversationStore

        store = ConversationStore.instance()
        store.save("verify-conv", [], user_id="alice")

        data = self._run_scheduling("create_and_assign_task_def", {
            "action": "create_and_assign_task_def",
            "conversation_id": "verify-conv",
            "agent_name": "qwen",
            "prompt": "Build the report",
            "criteria": "Report delivered",
            "verifier": "assistant",
        })

        assert data["ok"] is True
        tasks = store.get_extra("verify-conv", "agent_tasks")
        assigned = next(iter(tasks.values()))
        assert assigned["verifier"] == "assistant"
