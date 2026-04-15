"""Tests for agent definition/instance architecture.

Verifies the separation between agent definitions (repository templates)
and agent instances (runtime, in conv_agents).
"""
import pytest
from unittest.mock import patch, MagicMock

from core.conv_agent_config import (
    AGENT_CONFIG_DEFAULTS,
    add_agent_to_conv,
    get_agent_config,
    get_all_agent_configs,
    get_definition_name,
    flatten_agent_params,
)


# --------------- conv_agent_config ---------------

class TestAgentConfigDefaults:
    def test_defaults_have_definition_and_params(self):
        assert "definition" in AGENT_CONFIG_DEFAULTS
        assert "params" in AGENT_CONFIG_DEFAULTS
        assert AGENT_CONFIG_DEFAULTS["definition"] == ""
        assert AGENT_CONFIG_DEFAULTS["params"] == {}


class TestAddAgentToConv:
    def _make_store_mock(self):
        _stored = {}
        mock = MagicMock()
        mock.get_extra.side_effect = lambda cid, key: _stored.get(key)
        mock.set_extra.side_effect = lambda cid, key, val: _stored.__setitem__(key, val)
        return mock, _stored

    def test_add_with_definition_and_params(self):
        """add_agent_to_conv stores definition + params in config."""
        mock_store, _stored = self._make_store_mock()
        with patch("core.conversation_store.ConversationStore.instance",
                   return_value=mock_store):
            cfg = add_agent_to_conv(
                "conv1", "Alice",
                llm_service="claude_svc",
                definition="researcher",
                params={"name": "Alice", "specialty": "biology"},
            )
            assert cfg["definition"] == "researcher"
            assert cfg["params"] == {"name": "Alice", "specialty": "biology"}
            assert cfg["llm_service"] == "claude_svc"
            stored = _stored["conv_agents"]["Alice"]
            assert stored["definition"] == "researcher"
            assert stored["params"]["name"] == "Alice"

    def test_add_legacy_no_definition(self):
        """When definition is omitted, instance_name is used."""
        mock_store, _stored = self._make_store_mock()
        with patch("core.conversation_store.ConversationStore.instance",
                   return_value=mock_store):
            cfg = add_agent_to_conv("conv1", "claude", llm_service="svc")
            assert cfg["definition"] == "claude"
            assert cfg["params"] == {}

    def test_add_requires_llm_service(self):
        with pytest.raises(ValueError, match="llm_service is required"):
            add_agent_to_conv("conv1", "Alice", llm_service="")


class TestGetAgentConfig:
    def test_backward_compat_no_definition_field(self):
        """Legacy conv_agents entries without 'definition' get it from instance name."""
        with patch("core.conversation_store.ConversationStore.instance") as cs:
            cs.return_value.get_extra.return_value = {
                "claude": {"llm_service": "claude_svc"},  # no definition field
            }
            cfg = get_agent_config("conv1", "claude")
            assert cfg["definition"] == "claude"  # backward compat
            assert cfg["params"] == {}  # default
            assert cfg["llm_service"] == "claude_svc"

    def test_new_format_with_definition(self):
        """New entries with definition + params are returned correctly."""
        with patch("core.conversation_store.ConversationStore.instance") as cs:
            cs.return_value.get_extra.return_value = {
                "Alice": {
                    "definition": "researcher",
                    "params": {"name": "Alice"},
                    "llm_service": "svc",
                },
            }
            cfg = get_agent_config("conv1", "Alice")
            assert cfg["definition"] == "researcher"
            assert cfg["params"] == {"name": "Alice"}


class TestGetDefinitionName:
    def test_returns_definition(self):
        with patch("core.conversation_store.ConversationStore.instance") as cs:
            cs.return_value.get_extra.return_value = {
                "Alice": {"definition": "researcher", "llm_service": "svc"},
            }
            assert get_definition_name("conv1", "Alice") == "researcher"

    def test_returns_instance_name_for_legacy(self):
        with patch("core.conversation_store.ConversationStore.instance") as cs:
            cs.return_value.get_extra.return_value = {
                "claude": {"llm_service": "svc"},
            }
            assert get_definition_name("conv1", "claude") == "claude"


# --------------- flatten_agent_params ---------------

class TestFlattenAgentParams:
    def test_basic(self):
        flat = flatten_agent_params("Alice", {"name": "Alice", "specialty": "bio"})
        assert flat == {
            "agent.instance_name": "Alice",
            "agent.name": "Alice",
            "agent.specialty": "bio",
        }

    def test_empty_params(self):
        flat = flatten_agent_params("claude", {})
        assert flat == {"agent.instance_name": "claude"}

    def test_none_value(self):
        flat = flatten_agent_params("X", {"k": None})
        assert flat["agent.k"] == ""


# --------------- Expression resolution in prompts ---------------

class TestPromptExpressionResolution:
    def test_resolve_agent_params_in_prompt(self):
        """${agent.name} in a definition prompt resolves to instance param."""
        from core.expression import resolve_expression
        template = "You are ${agent.name}, a ${agent.specialty} specialist."
        flat = flatten_agent_params("Alice", {
            "name": "Alice",
            "specialty": "biology",
        })
        result = resolve_expression(template, parameters=flat)
        assert result == "You are Alice, a biology specialist."

    def test_unset_params_preserved(self):
        """Unresolved ${agent.x} is preserved (not crashed)."""
        from core.expression import resolve_expression
        template = "Hello ${agent.name}, your role is ${agent.role}"
        flat = flatten_agent_params("Alice", {"name": "Alice"})
        result = resolve_expression(template, parameters=flat)
        assert "Alice" in result
        assert "${agent.role}" in result  # unresolved, preserved


# --------------- resolve_agent_task with definition ---------------

class TestResolveAgentTaskWithDefinition:
    def test_resolves_definition_and_params(self):
        """resolve_agent_task loads definition from repo and resolves params."""
        from core.agent_executor import resolve_agent_task
        with patch("core.conv_agent_config.get_all_agent_configs",
                   return_value={"Alice": {"llm_service": "svc", "definition": "researcher"}}), \
             patch("core.conv_agent_config.get_agent_config",
                   return_value={
                       "llm_service": "svc", "definition": "researcher",
                       "params": {"name": "Alice", "specialty": "biology"},
                   }), \
             patch("core.resource_store.ResourceStore.instance") as mock_rs:
            mock_rs.return_value.get_any.return_value = {
                "prompt": "You are ${agent.name}, expert in ${agent.specialty}.",
            }
            task = resolve_agent_task(
                "Alice", "Research this", "user1",
                conversation_id="conv1",
            )
            assert task.agent_name == "Alice"
            assert "You are Alice, expert in biology." in task.system_prompt
            assert task.llm_service == "svc"

    def test_two_instances_same_definition(self):
        """Two instances from the same definition produce different prompts."""
        from core.agent_executor import resolve_agent_task
        _conv_agents = {
            "Alice": {"llm_service": "svc_a", "definition": "researcher",
                      "params": {"name": "Alice", "specialty": "biology"}},
            "Bob": {"llm_service": "svc_b", "definition": "researcher",
                    "params": {"name": "Bob", "specialty": "physics"}},
        }
        def fake_gac(cid, name):
            raw = _conv_agents.get(name, {})
            result = dict(AGENT_CONFIG_DEFAULTS)
            result.update(raw)
            if not result["definition"]:
                result["definition"] = name
            return result

        with patch("core.conv_agent_config.get_all_agent_configs",
                   return_value=_conv_agents), \
             patch("core.conv_agent_config.get_agent_config",
                   side_effect=fake_gac), \
             patch("core.resource_store.ResourceStore.instance") as mock_rs:
            mock_rs.return_value.get_any.return_value = {
                "prompt": "You are ${agent.name}, expert in ${agent.specialty}.",
            }
            task_a = resolve_agent_task(
                "Alice", "Go", "user1", conversation_id="conv1")
            task_b = resolve_agent_task(
                "Bob", "Go", "user1", conversation_id="conv1")

            assert "You are Alice, expert in biology." in task_a.system_prompt
            assert "You are Bob, expert in physics." in task_b.system_prompt
            assert task_a.llm_service == "svc_a"
            assert task_b.llm_service == "svc_b"
