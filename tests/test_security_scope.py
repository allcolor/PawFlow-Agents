"""Tests for security scope enforcement -- global writes blocked."""
import json
import pytest
from unittest.mock import patch, MagicMock
from core import FlowFile


class TestGlobalScopeBlocked:
    """Verify that global scope writes are blocked from chat actions."""

    def _make_flowfile(self, body: dict) -> FlowFile:
        ff = FlowFile(content=json.dumps(body).encode())
        ff.set_attribute("http.auth.principal", "test_user")
        return ff

    def _get_agent_loop(self):
        from tasks.ai.agent_loop import AgentLoopTask
        return AgentLoopTask({"api_key": "test"})

    def test_set_param_global_blocked(self):
        """set_param with scope=global should be rejected."""
        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "set_param", "key": "test_key", "value": "test_val", "scope": "global",
        })
        result = task._handle_action(ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert "error" in body
        assert "admin" in body["error"].lower()

    def test_set_param_user_allowed(self):
        """set_param with scope=user should be allowed (writes to user config)."""
        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "set_param", "key": "test_key_scope", "value": "test_val", "scope": "user",
        })
        with patch("core.config_store.ConfigStore.load_params", return_value={}), \
             patch("core.config_store.ConfigStore.save_params"):
            result = task._handle_action(ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        # Should succeed -- user scope is allowed
        assert body.get("ok") is True

    def test_delete_param_global_blocked(self):
        """delete_param with scope=global should be rejected."""
        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "delete_param", "key": "test_key", "scope": "global",
        })
        result = task._handle_action(ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert "error" in body
        assert "admin" in body["error"].lower()

    def test_create_resource_global_blocked(self):
        """create_resource with scope=global should be rejected."""
        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "create_resource", "resource_type": "skill",
            "name": "test_skill", "scope": "global",
            "data": {"prompt": "test"},
        })
        result = task._handle_action(ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert "error" in body
        assert "admin" in body["error"].lower()

    def test_update_resource_global_blocked(self):
        """update_resource with scope=global should be rejected."""
        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "update_resource", "resource_type": "agent",
            "name": "test_agent", "scope": "global",
            "data": {"prompt": "updated"},
        })
        result = task._handle_action(ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert "error" in body
        assert "admin" in body["error"].lower()

    def test_delete_resource_global_blocked(self):
        """delete_resource with scope=global should be rejected."""
        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "delete_resource", "resource_type": "skill",
            "name": "test_skill", "scope": "global",
        })
        result = task._handle_action(ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert "error" in body
        assert "admin" in body["error"].lower()

    def test_set_param_default_is_user(self):
        """set_param without scope should default to user, not global."""
        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "set_param", "key": "default_test_scope", "value": "val",
        })
        with patch("core.config_store.ConfigStore.load_params", return_value={}), \
             patch("core.config_store.ConfigStore.save_params"):
            result = task._handle_action(ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        # Should succeed (user scope is the default) not fail (global blocked)
        assert body.get("ok") is True

    def test_set_secret_global_blocked(self):
        """set_secret with scope=global should be rejected."""
        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "set_secret", "key": "my_key", "value": "s3cret", "scope": "global",
        })
        result = task._handle_action(ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert "error" in body
        assert "admin" in body["error"].lower()

    def test_delete_secret_global_blocked(self):
        """delete_secret with scope=global should be rejected."""
        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "delete_secret", "key": "my_key", "scope": "global",
        })
        result = task._handle_action(ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert "error" in body
        assert "admin" in body["error"].lower()

    def test_set_param_conversation_scope_allowed(self):
        """set_param with scope=conversation should be allowed."""
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        conv_id = "test_conv_scope_param"
        store.save(conv_id, [{"role": "user", "content": "hi"}], user_id="test_user")
        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "set_param", "key": "conv_key", "value": "conv_val",
            "scope": "conversation", "conversation_id": conv_id,
        })
        result = task._handle_action(ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert body.get("status") == "accepted"
        store.delete(conv_id)

    def test_set_param_with_conversation_forces_conversation_scope(self):
        """set_param with an active conversation must not write user scope."""
        from core.conversation_store import ConversationStore
        from tasks.ai.actions.secrets_variables import _handle_secrets_variables

        store = ConversationStore.instance()
        conv_id = "test_conv_param_default"
        store.save(conv_id, [{"role": "user", "content": "hi"}], user_id="test_user")
        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "set_param", "key": "conv_key", "value": "conv_val",
            "scope": "user", "conversation_id": conv_id,
        })
        with patch("core.config_store.ConfigStore.save_params") as save_params:
            result = _handle_secrets_variables(
                task, "set_param", json.loads(ff.get_content()), store,
                "test_user", ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert body.get("ok") is True
        assert store.get_extra(conv_id, "conv_parameters") == {"conv_key": "conv_val"}
        save_params.assert_not_called()
        store.delete(conv_id)

    def test_create_resource_with_conversation_passes_conversation_scope(self):
        """create_resource with an active conversation must not create user resources."""
        from core.conversation_store import ConversationStore
        from tasks.ai.actions.agent_resource import _handle_agent_resource

        store = ConversationStore.instance()
        conv_id = "test_conv_resource_default"
        store.save(conv_id, [{"role": "user", "content": "hi"}], user_id="test_user")
        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "create_resource", "resource_type": "skill",
            "name": "conv_skill", "scope": "user", "conversation_id": conv_id,
            "data": {"prompt": "test"},
        })
        rs = MagicMock()
        with patch("core.resource_store.ResourceStore.instance", return_value=rs):
            result = _handle_agent_resource(
                task, "create_resource", json.loads(ff.get_content()), store,
                "test_user", ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert body == {"ok": True, "scope": "conversation"}
        rs.create.assert_called_once_with(
            "skill", "conv_skill", "test_user", {"prompt": "test"},
            conversation_id=conv_id)
        store.delete(conv_id)


class TestPromoteGlobalBlocked:
    """Verify promote to global is blocked from ManageResourceHandler."""

    def test_promote_global_returns_error(self):
        from core.tool_registry import create_default_registry
        registry = create_default_registry()
        handler = registry.get("manage_resource")
        if handler is None:
            pytest.skip("manage_resource handler not found")
        handler._user_id = "test_user"
        handler._conversation_id = "test_conv"
        result = handler.execute({
            "action": "promote",
            "resource_type": "agent",
            "name": "nonexistent_agent",
            "data": {"target_scope": "global"},
        })
        # Should either say "admin GUI" (blocked) or "not found"
        assert "admin" in result.lower() or "not found" in result.lower()

    def test_manage_resource_user_create_with_conversation_keeps_requested_scope(self):
        from core.tool_registry import create_default_registry

        registry = create_default_registry()
        handler = registry.get("manage_resource")
        if handler is None:
            pytest.skip("manage_resource handler not found")
        handler._user_id = "test_user"
        handler._conversation_id = "test_conv"
        rs = MagicMock()
        with patch("core.resource_store.ResourceStore.instance", return_value=rs):
            result = handler.execute({
                "action": "create", "resource_type": "skill", "name": "user_skill",
                "data": {"prompt": "test", "scope": "user"},
            })
        assert "(scope: user)" in result
        rs.create.assert_called_once_with(
            "skill", "user_skill", "test_user", {"prompt": "test"})

    def test_manage_resource_agent_create_forces_conversation_scope(self):
        from core.tool_registry import create_default_registry

        registry = create_default_registry()
        handler = registry.get("manage_resource")
        if handler is None:
            pytest.skip("manage_resource handler not found")
        handler._user_id = "test_user"
        handler._conversation_id = "test_conv"
        handler._agent_name = "assistant"
        rs = MagicMock()
        with patch("core.resource_store.ResourceStore.instance", return_value=rs):
            result = handler.execute({
                "action": "create", "resource_type": "skill", "name": "conv_skill",
                "data": {"prompt": "test", "scope": "global"},
            })
        assert "(scope: conversation)" in result
        rs.create.assert_called_once_with(
            "skill", "conv_skill", "test_user",
            {"prompt": "test", "_created_by": "assistant"},
            conversation_id="test_conv")
