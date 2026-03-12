"""Tests for ResourceStore — CRUD for agents, skills, MCP servers."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from core.resource_store import ResourceStore, _RESOURCE_FILES, VALID_TYPES


@pytest.fixture(autouse=True)
def reset_singleton(tmp_path):
    """Reset singleton and redirect files to tmp_path."""
    ResourceStore.reset()
    patched = {k: tmp_path / v.name for k, v in _RESOURCE_FILES.items()}
    with patch.dict("core.resource_store._RESOURCE_FILES", patched):
        with patch("core.resource_store._CONFIG_DIR", tmp_path):
            yield tmp_path
    ResourceStore.reset()


class TestResourceStoreSingleton:
    def test_singleton(self):
        a = ResourceStore.instance()
        b = ResourceStore.instance()
        assert a is b

    def test_reset(self):
        a = ResourceStore.instance()
        ResourceStore.reset()
        b = ResourceStore.instance()
        assert a is not b


class TestAgentCRUD:
    def test_create_agent(self):
        store = ResourceStore.instance()
        result = store.create("agent", "analyst", "user1", {
            "prompt": "You are a financial analyst",
            "model": "gpt-4",
        })
        assert result["name"] == "analyst"
        assert result["prompt"] == "You are a financial analyst"
        assert result["model"] == "gpt-4"
        assert result["max_depth"] == 1  # default
        assert result["timeout"] == 120  # default
        assert "created_at" in result

    def test_create_duplicate_raises(self):
        store = ResourceStore.instance()
        store.create("agent", "a1", "user1", {"prompt": "p"})
        with pytest.raises(ValueError, match="already exists"):
            store.create("agent", "a1", "user1", {"prompt": "p2"})

    def test_create_missing_prompt_raises(self):
        store = ResourceStore.instance()
        with pytest.raises(ValueError, match="Missing required field"):
            store.create("agent", "a1", "user1", {"model": "gpt-4"})

    def test_get_agent(self):
        store = ResourceStore.instance()
        store.create("agent", "a1", "user1", {"prompt": "p"})
        agent = store.get("agent", "a1", "user1")
        assert agent is not None
        assert agent["prompt"] == "p"

    def test_get_nonexistent(self):
        store = ResourceStore.instance()
        assert store.get("agent", "nope", "user1") is None

    def test_get_wrong_user(self):
        store = ResourceStore.instance()
        store.create("agent", "a1", "user1", {"prompt": "p"})
        assert store.get("agent", "a1", "user2") is None

    def test_update_agent(self):
        store = ResourceStore.instance()
        store.create("agent", "a1", "user1", {"prompt": "old"})
        updated = store.update("agent", "a1", "user1", {"prompt": "new", "model": "claude"})
        assert updated["prompt"] == "new"
        assert updated["model"] == "claude"

    def test_update_nonexistent_raises(self):
        store = ResourceStore.instance()
        with pytest.raises(KeyError):
            store.update("agent", "nope", "user1", {"prompt": "p"})

    def test_delete_agent(self):
        store = ResourceStore.instance()
        store.create("agent", "a1", "user1", {"prompt": "p"})
        assert store.delete("agent", "a1", "user1") is True
        assert store.get("agent", "a1", "user1") is None

    def test_delete_nonexistent(self):
        store = ResourceStore.instance()
        assert store.delete("agent", "nope", "user1") is False

    def test_list_agents(self):
        store = ResourceStore.instance()
        store.create("agent", "a1", "user1", {"prompt": "p1"})
        store.create("agent", "a2", "user1", {"prompt": "p2"})
        store.create("agent", "a3", "user2", {"prompt": "p3"})
        # All for user1
        agents = store.list("agent", "user1")
        assert len(agents) == 2
        names = {a["name"] for a in agents}
        assert names == {"a1", "a2"}
        # All users
        all_agents = store.list("agent")
        assert len(all_agents) == 3

    def test_exists(self):
        store = ResourceStore.instance()
        store.create("agent", "a1", "user1", {"prompt": "p"})
        assert store.exists("agent", "a1", "user1") is True
        assert store.exists("agent", "a1", "user2") is False
        assert store.exists("agent", "nope", "user1") is False


class TestSkillCRUD:
    def test_create_skill(self):
        store = ResourceStore.instance()
        result = store.create("skill", "summarizer", "user1", {
            "prompt": "Summarize the following text concisely",
        })
        assert result["name"] == "summarizer"
        assert result["prompt"] == "Summarize the following text concisely"
        assert result["description"] == ""  # default

    def test_skill_full_lifecycle(self):
        store = ResourceStore.instance()
        store.create("skill", "s1", "u1", {"prompt": "p1"})
        store.create("skill", "s2", "u1", {"prompt": "p2"})
        assert len(store.list("skill", "u1")) == 2
        store.update("skill", "s1", "u1", {"prompt": "updated"})
        assert store.get("skill", "s1", "u1")["prompt"] == "updated"
        store.delete("skill", "s1", "u1")
        assert len(store.list("skill", "u1")) == 1


class TestMCPCRUD:
    def test_create_mcp(self):
        store = ResourceStore.instance()
        result = store.create("mcp", "db-server", "user1", {
            "url": "http://localhost:3000",
        })
        assert result["name"] == "db-server"
        assert result["url"] == "http://localhost:3000"
        assert result["auth"] == {}  # default
        assert result["discovered_tools"] == []  # default

    def test_create_mcp_missing_url(self):
        store = ResourceStore.instance()
        with pytest.raises(ValueError, match="Missing required field"):
            store.create("mcp", "m1", "u1", {"auth": {}})


class TestInvalidType:
    def test_create_invalid_type(self):
        store = ResourceStore.instance()
        with pytest.raises(ValueError, match="Invalid resource type"):
            store.create("invalid", "x", "u1", {})

    def test_list_invalid_type(self):
        store = ResourceStore.instance()
        assert store.list("invalid") == []

    def test_get_invalid_type(self):
        store = ResourceStore.instance()
        assert store.get("invalid", "x", "u1") is None

    def test_delete_invalid_type(self):
        store = ResourceStore.instance()
        assert store.delete("invalid", "x", "u1") is False


class TestPersistence:
    def test_save_and_reload(self, reset_singleton):
        store = ResourceStore.instance()
        store.create("agent", "a1", "u1", {"prompt": "hello"})
        store.create("skill", "s1", "u1", {"prompt": "summarize"})

        # Verify files exist on disk
        agent_file = reset_singleton / "agents.json"
        assert agent_file.exists()
        data = json.loads(agent_file.read_text(encoding="utf-8"))
        assert "u1.a1" in data

        # Reset and reload
        ResourceStore.reset()
        store2 = ResourceStore.instance()
        agent = store2.get("agent", "a1", "u1")
        assert agent is not None
        assert agent["prompt"] == "hello"

        skill = store2.get("skill", "s1", "u1")
        assert skill is not None
        assert skill["prompt"] == "summarize"

    def test_delete_persists(self, reset_singleton):
        store = ResourceStore.instance()
        store.create("agent", "a1", "u1", {"prompt": "p"})
        store.delete("agent", "a1", "u1")

        ResourceStore.reset()
        store2 = ResourceStore.instance()
        assert store2.get("agent", "a1", "u1") is None


class TestUserIsolation:
    def test_same_name_different_users(self):
        store = ResourceStore.instance()
        store.create("agent", "helper", "alice", {"prompt": "Alice's helper"})
        store.create("agent", "helper", "bob", {"prompt": "Bob's helper"})

        alice_agent = store.get("agent", "helper", "alice")
        bob_agent = store.get("agent", "helper", "bob")
        assert alice_agent["prompt"] == "Alice's helper"
        assert bob_agent["prompt"] == "Bob's helper"

    def test_delete_only_own(self):
        store = ResourceStore.instance()
        store.create("agent", "a1", "alice", {"prompt": "p"})
        # Bob can't delete Alice's agent
        assert store.delete("agent", "a1", "bob") is False
        assert store.get("agent", "a1", "alice") is not None
