"""Tests for core.handlers.plan_mode (Enter/ExitPlanMode).

Pawflow replacement for the Claude Code built-ins of the same name.
Flips the conv-scoped ``plan_mode`` extra so agent_context appends the
plan-mode directive on the next system-prompt build.
"""

import unittest
from unittest.mock import patch, MagicMock

from core.handlers.plan_mode import EnterPlanModeHandler, ExitPlanModeHandler


class TestEnterPlanModeHandler(unittest.TestCase):

    def setUp(self):
        self.h = EnterPlanModeHandler()
        self.h.set_conversation_id("conv-abc")
        self.h.set_user_id("alice")

    def test_name_matches_cc_builtin(self):
        assert self.h.name == "EnterPlanMode"

    def test_schema_has_no_args(self):
        sch = self.h.parameters_schema
        assert sch["type"] == "object"
        assert sch["properties"] == {}
        assert sch["required"] == []

    def test_execute_missing_conversation_errors(self):
        h = EnterPlanModeHandler()
        res = h.execute({})
        assert res.startswith("Error:")
        assert "conversation" in res.lower()

    def test_execute_sets_plan_mode_true(self):
        store = MagicMock()
        with patch("core.conversation_store.ConversationStore.instance",
                   return_value=store):
            res = self.h.execute({})
        store.set_extra.assert_called_once_with(
            "conv-abc", "plan_mode", True, user_id="alice")
        assert "ENABLED" in res
        assert "create_plan" in res
        assert "ask_user" in res
        assert "request_confirmation" in res
        assert "other tools" in res

    def test_description_allows_only_questions_before_plan(self):
        assert "ask_user" in self.h.description
        assert "request_confirmation" in self.h.description
        assert "approve_plan" in self.h.description

    def test_workflow_cutover_uses_only_canonical_proposal_protocol(self):
        store = MagicMock()
        with (
            patch("core.conversation_store.ConversationStore.instance",
                  return_value=store),
            patch(
                "core.flow_feature_flags.workflow_proposals_enabled",
                return_value=True,
            ),
        ):
            description = self.h.description
            result = self.h.execute({})
        assert "propose_workflow" in description
        assert "propose_workflow" in result
        assert "workflow proposal" in result
        assert "create_plan" not in description
        assert "approve_plan" not in description


class TestExitPlanModeHandler(unittest.TestCase):

    def setUp(self):
        self.h = ExitPlanModeHandler()
        self.h.set_conversation_id("conv-abc")
        self.h.set_user_id("alice")

    def test_name_matches_cc_builtin(self):
        assert self.h.name == "ExitPlanMode"

    def test_schema_has_no_args(self):
        sch = self.h.parameters_schema
        assert sch["required"] == []
        assert sch["properties"] == {}

    def test_execute_missing_conversation_errors(self):
        h = ExitPlanModeHandler()
        res = h.execute({})
        assert res.startswith("Error:")

    def test_execute_sets_plan_mode_false(self):
        store = MagicMock()
        with patch("core.conversation_store.ConversationStore.instance",
                   return_value=store):
            res = self.h.execute({})
        store.set_extra.assert_called_once_with(
            "conv-abc", "plan_mode", False, user_id="alice")
        assert "DISABLED" in res


if __name__ == "__main__":
    unittest.main()
