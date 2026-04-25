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
