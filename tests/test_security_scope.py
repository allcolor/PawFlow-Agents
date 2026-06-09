"""Tests for security scope enforcement across resource scopes."""
import json
import pytest
from unittest.mock import patch, MagicMock
from core import FlowFile


_SAFE_SKILL_REVIEW = {
    "hash": "test",
    "risk": "low",
    "allowed": True,
    "requires_human_review": False,
}


class TestGlobalScopePermissions:
    """Verify global writes are admin-only while user/conv scopes stay writable."""

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

    def test_create_resource_global_allowed_for_admin(self):
        """create_resource with scope=global should write as global for admin."""
        from core.resource_store import GLOBAL_USER_ID
        from tasks.ai.actions.agent_resource import _handle_agent_resource

        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "create_resource", "resource_type": "skill",
            "name": "admin_global_skill", "scope": "global",
            "data": {"prompt": "test"},
        })
        ff.set_attribute("http.auth.roles", "admin")
        rs = MagicMock()
        with patch("core.resource_store.ResourceStore.instance", return_value=rs), \
                patch("core.review_bindings.review_for_write", return_value=_SAFE_SKILL_REVIEW):
            result = _handle_agent_resource(
                task, "create_resource", json.loads(ff.get_content()), None,
                "test_user", ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert body == {"ok": True, "scope": "global"}
        rs.create.assert_called_once_with(
            "skill", "admin_global_skill", GLOBAL_USER_ID, {
                "prompt": "test",
                "review": _SAFE_SKILL_REVIEW,
            })

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

    def test_update_resource_global_allowed_for_admin(self):
        """update_resource with scope=global should update global for admin."""
        from core.resource_store import GLOBAL_USER_ID
        from tasks.ai.actions.agent_resource import _handle_agent_resource

        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "update_resource", "resource_type": "agent",
            "name": "admin_global_agent", "scope": "global",
            "data": {"prompt": "updated", "description": "desc"},
        })
        ff.set_attribute("http.auth.roles", "admin")
        rs = MagicMock()
        with patch("core.resource_store.ResourceStore.instance", return_value=rs):
            result = _handle_agent_resource(
                task, "update_resource", json.loads(ff.get_content()), None,
                "test_user", ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert body == {"ok": True}
        rs.update.assert_called_once_with(
            "agent", "admin_global_agent", GLOBAL_USER_ID,
            {"prompt": "updated", "description": "desc"})

    def test_import_skill_global_requires_admin(self):
        """Skill marketplace import must not allow non-admin global writes."""
        from tasks.ai.actions.agent_resource import _handle_agent_resource

        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "import_skill_marketplace", "resource_type": "skill",
            "ref": "owner/repo", "scope": "global",
        })
        with patch("core.skill_marketplace.import_marketplace_skill") as importer:
            result = _handle_agent_resource(
                task, "import_skill_marketplace", json.loads(ff.get_content()), None,
                "test_user", ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert "admin" in body["error"].lower()
        assert result[0].get_attribute("http.response.status") == "403"
        importer.assert_not_called()

    def test_import_skill_global_allowed_for_admin(self):
        """Admin can import marketplace skills to global scope."""
        from tasks.ai.actions.agent_resource import _handle_agent_resource

        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "import_skill_marketplace", "resource_type": "skill",
            "ref": "owner/repo", "scope": "global",
        })
        ff.set_attribute("http.auth.roles", "admin")
        with patch("core.skill_marketplace.import_marketplace_skill", return_value={"ok": True}) as importer:
            result = _handle_agent_resource(
                task, "import_skill_marketplace", json.loads(ff.get_content()), None,
                "test_user", ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert body == {"ok": True}
        importer.assert_called_once()
        assert importer.call_args.kwargs["scope"] == "global"

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

    def test_set_param_with_conversation_respects_user_scope(self):
        """set_param must respect explicit user scope even when conversation_id is present."""
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
        with patch("core.config_store.ConfigStore.load_params", return_value={}), \
                patch("core.config_store.ConfigStore.save_params") as save_params:
            result = _handle_secrets_variables(
                task, "set_param", json.loads(ff.get_content()), store,
                "test_user", ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert body.get("ok") is True
        assert body.get("scope") == "user"
        assert store.get_extra(conv_id, "conv_parameters") in (None, {})
        save_params.assert_called_once()
        store.delete(conv_id)

    def test_create_resource_with_conversation_respects_user_scope(self):
        """create_resource must respect explicit user scope even when conversation_id is present."""
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
        with patch("core.resource_store.ResourceStore.instance", return_value=rs), \
                patch("core.review_bindings.review_for_write", return_value=_SAFE_SKILL_REVIEW):
            result = _handle_agent_resource(
                task, "create_resource", json.loads(ff.get_content()), store,
                "test_user", ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert body == {"ok": True, "scope": "user"}
        rs.create.assert_called_once_with(
            "skill", "conv_skill", "test_user", {
                "prompt": "test",
                "review": _SAFE_SKILL_REVIEW,
            })
        store.delete(conv_id)

    def test_create_agent_global_writes_global_scope_for_admin(self):
        """create_agent has its own path and must honor explicit global scope."""
        from core.resource_store import GLOBAL_USER_ID
        from tasks.ai.actions.agent_resource import _handle_agent_resource

        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "create_agent",
            "name": "global_agent",
            "prompt": "system prompt",
            "scope": "global",
        })
        ff.set_attribute("http.auth.roles", "admin")
        rs = MagicMock()
        with patch("core.resource_store.ResourceStore.instance", return_value=rs):
            result = _handle_agent_resource(
                task, "create_agent", json.loads(ff.get_content()), None,
                "test_user", ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert "scope: global" in body["result"]
        rs.create.assert_called_once_with(
            "agent", "global_agent", GLOBAL_USER_ID, {"prompt": "system prompt"})

    def test_agent_promote_global_requires_admin(self):
        """agent_promote must require admin for global target scope."""
        from tasks.ai.actions.agent_resource import _handle_agent_resource

        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "agent_promote",
            "agent_name": "agent1",
            "target_scope": "global",
        })
        rs = MagicMock()
        rs.get_any.return_value = {"name": "agent1", "prompt": "p", "_scope": "user"}
        with patch("core.resource_store.ResourceStore.instance", return_value=rs):
            result = _handle_agent_resource(
                task, "agent_promote", json.loads(ff.get_content()), None,
                "test_user", ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert "admin" in body["error"].lower()
        assert result[0].get_attribute("http.response.status") == "403"
        rs.create.assert_not_called()

    def test_copy_resource_scope_uses_explicit_source_scope(self):
        """Resource scope moves must read from the displayed source scope, not cascade."""
        from core.conversation_store import ConversationStore
        from tasks.ai.actions.agent_resource import _handle_agent_resource

        store = ConversationStore.instance()
        conv_id = "test_conv_resource_move"
        store.save(conv_id, [{"role": "user", "content": "hi"}], user_id="test_user")
        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "copy_resource_scope", "resource_type": "skill",
            "name": "same_name", "from_scope": "user", "target_scope": "conversation",
            "conversation_id": conv_id,
        })
        rs = MagicMock()
        rs.get.return_value = {"name": "same_name", "prompt": "user copy"}
        with patch("core.resource_store.ResourceStore.instance", return_value=rs):
            result = _handle_agent_resource(
                task, "copy_resource_scope", json.loads(ff.get_content()), store,
                "test_user", ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert body.get("ok") is True
        assert body.get("from_scope") == "user"
        rs.get.assert_called_once_with("skill", "same_name", "test_user")
        rs.get_any.assert_not_called()
        rs.create.assert_called_once_with(
            "skill", "same_name", "test_user", {"prompt": "user copy"},
            conversation_id=conv_id)
        rs.delete.assert_called_once_with("skill", "same_name", "test_user")
        store.delete(conv_id)

    def test_promote_flow_respects_target_global_scope(self):
        """Flow promote/demote must use target_scope, with admin required for global."""
        from types import SimpleNamespace
        from tasks.ai.actions.service_flow import _handle_service_flow

        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "promote_flow",
            "instance_id": "flow1",
            "target_scope": "global",
            "conversation_id": "conv1",
        })
        ff.set_attribute("http.auth.roles", "admin")
        inst = SimpleNamespace(owner="test_user", conversation_id="conv1")
        dr = MagicMock()
        dr.get.return_value = inst
        with patch("core.deployment_registry.DeploymentRegistry.get_instance", return_value=dr):
            result = _handle_service_flow(
                task, "promote_flow", json.loads(ff.get_content()), None,
                "test_user", ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert body == {"ok": True, "scope": "global"}
        dr.set_owner.assert_called_once_with("flow1", None)
        assert inst.conversation_id is None

    def test_agent_deploy_flow_without_scope_defaults_to_conversation(self):
        """Agent flow deployment keeps conversation identity when scope is omitted."""
        from pathlib import Path
        from tasks.ai.actions.service_flow import _handle_service_flow

        task = self._get_agent_loop()
        task._agent_name = "agentA"
        ff = self._make_flowfile({
            "action": "deploy_flow",
            "template_id": "pkg.flow:1.0.0",
            "conversation_id": "conv1",
        })
        dr = MagicMock()
        dr.deploy.return_value = "flow1"
        dr.get.return_value = None
        with patch("tasks.ai.actions.service_flow._resolve_flow_template_path", return_value=Path("/tmp/flow.json")), \
                patch("pathlib.Path.read_text", return_value=json.dumps({
                    "id": "flow", "name": "Flow", "scope": "conversation",
                })), \
                patch("core.deployment_registry.DeploymentRegistry.get_instance", return_value=dr):
            result = _handle_service_flow(
                task, "deploy_flow", json.loads(ff.get_content()), None,
                "test_user", ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert body["scope"] == "conversation"
        assert dr.deploy.call_args.kwargs["conversation_id"] == "conv1"
        assert dr.deploy.call_args.kwargs["agent_name"] == "agentA"

    def test_admin_deploy_flow_global_uses_global_owner(self):
        """Direct global deployment must create a global instance, not a user one."""
        from pathlib import Path
        from tasks.ai.actions.service_flow import _handle_service_flow

        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "deploy_flow",
            "template_id": "pkg.flow:1.0.0",
            "scope": "global",
        })
        ff.set_attribute("http.auth.roles", "admin")
        dr = MagicMock()
        dr.deploy.return_value = "flow1"
        dr.get.return_value = None
        with patch("tasks.ai.actions.service_flow._resolve_flow_template_path", return_value=Path("/tmp/flow.json")), \
                patch("pathlib.Path.read_text", return_value=json.dumps({
                    "id": "flow", "name": "Flow", "scope": "independent",
                })), \
                patch("core.deployment_registry.DeploymentRegistry.get_instance", return_value=dr):
            result = _handle_service_flow(
                task, "deploy_flow", json.loads(ff.get_content()), None,
                "test_user", ff)

        assert result is not None
        body = json.loads(result[0].get_content())
        assert body["scope"] == "global"
        assert dr.deploy.call_args.kwargs["owner"] is None
        assert dr.deploy.call_args.kwargs["conversation_id"] is None

    def test_demote_global_flow_requires_admin(self):
        """Moving a global flow down to user scope also modifies global state."""
        from types import SimpleNamespace
        from tasks.ai.actions.service_flow import _handle_service_flow

        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "promote_flow",
            "instance_id": "flow1",
            "target_scope": "user",
        })
        inst = SimpleNamespace(owner=None, conversation_id=None)
        dr = MagicMock()
        dr.get.return_value = inst
        with patch("core.deployment_registry.DeploymentRegistry.get_instance", return_value=dr):
            result = _handle_service_flow(
                task, "promote_flow", json.loads(ff.get_content()), None,
                "test_user", ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert "admin" in body["error"].lower()
        assert result[0].get_attribute("http.response.status") == "403"
        dr.set_owner.assert_not_called()

    def test_promote_task_def_global_requires_admin(self):
        """Task definition promotion to global must require admin."""
        from core.conversation_store import ConversationStore
        from tasks.ai.actions.scheduling import _handle_scheduling

        store = ConversationStore.instance()
        conv_id = "test_conv_task_def_promote"
        store.save(conv_id, [{"role": "user", "content": "hi"}], user_id="test_user")
        store.set_extra(conv_id, "conversation_task_defs", {
            "td1": {"prompt": "do it", "criteria": "done"},
        })
        task = self._get_agent_loop()
        ff = self._make_flowfile({
            "action": "promote_task_def",
            "name": "td1",
            "target_scope": "global",
            "conversation_id": conv_id,
        })
        result = _handle_scheduling(
            task, "promote_task_def", json.loads(ff.get_content()), store,
            "test_user", ff)
        assert result is not None
        body = json.loads(result[0].get_content())
        assert "admin" in body["error"].lower()
        assert result[0].get_attribute("http.response.status") == "403"
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
        with patch("core.resource_store.ResourceStore.instance", return_value=rs), \
                patch("core.review_bindings.review_for_write", return_value=_SAFE_SKILL_REVIEW):
            result = handler.execute({
                "action": "create", "resource_type": "skill", "name": "user_skill",
                "data": {"prompt": "test", "scope": "user"},
            })
        assert "(scope: user)" in result
        rs.create.assert_called_once_with(
            "skill", "user_skill", "test_user", {
                "prompt": "test",
                "review": _SAFE_SKILL_REVIEW,
            })

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
        with patch("core.resource_store.ResourceStore.instance", return_value=rs), \
                patch("core.review_bindings.review_for_write", return_value=_SAFE_SKILL_REVIEW):
            result = handler.execute({
                "action": "create", "resource_type": "skill", "name": "conv_skill",
                "data": {"prompt": "test", "scope": "global"},
            })
        assert "(scope: conversation)" in result
        rs.create.assert_called_once_with(
            "skill", "conv_skill", "test_user",
            {"prompt": "test", "_created_by": "assistant", "review": _SAFE_SKILL_REVIEW},
            conversation_id="test_conv")

    def test_manage_resource_agent_update_user_scope_is_read_only(self):
        from core.tool_registry import create_default_registry

        registry = create_default_registry()
        handler = registry.get("manage_resource")
        if handler is None:
            pytest.skip("manage_resource handler not found")
        handler._user_id = "test_user"
        handler._conversation_id = "test_conv"
        handler._agent_name = "assistant"
        rs = MagicMock()
        rs.get_any.return_value = {"name": "user_skill", "_scope": "user", "prompt": "old"}
        with patch("core.resource_store.ResourceStore.instance", return_value=rs):
            result = handler.execute({
                "action": "update", "resource_type": "skill", "name": "user_skill",
                "data": {"prompt": "new"},
            })
        assert "read-only" in result
        rs.update.assert_not_called()

    def test_manage_resource_agent_import_forces_conversation_scope(self):
        from core.tool_registry import create_default_registry

        registry = create_default_registry()
        handler = registry.get("manage_resource")
        if handler is None:
            pytest.skip("manage_resource handler not found")
        handler._user_id = "test_user"
        handler._conversation_id = "test_conv"
        handler._agent_name = "assistant"
        with patch("core.skill_marketplace.import_marketplace_skill", return_value={"ok": True}) as importer:
            result = handler.execute({
                "action": "import_marketplace", "resource_type": "skill",
                "ref": "owner/repo", "scope": "global",
            })
        assert json.loads(result) == {"ok": True}
        importer.assert_called_once()
        assert importer.call_args.kwargs["scope"] == "conversation"
