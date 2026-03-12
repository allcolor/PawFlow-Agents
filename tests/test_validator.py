"""Tests for FlowValidator."""

import pytest

from tasks import register_all_tasks
register_all_tasks()

from engine.validator import FlowValidator, ValidationResult


class TestFlowValidator:

    def setup_method(self):
        self.validator = FlowValidator()

    def _make_flow(self, **overrides):
        flow = {
            "id": "test-flow",
            "name": "Test Flow",
            "version": "1.0.0",
            "tasks": {
                "log_1": {"type": "log", "parameters": {"message": "hi"}},
                "log_2": {"type": "log", "parameters": {"message": "bye"}},
            },
            "relations": [
                {"from": "log_1", "to": "log_2", "type": "success"},
            ],
        }
        flow.update(overrides)
        return flow

    def test_valid_flow(self):
        result = self.validator.validate(self._make_flow())
        assert result.valid
        assert len(result.errors) == 0

    def test_missing_id(self):
        flow = self._make_flow()
        del flow["id"]
        result = self.validator.validate(flow)
        assert not result.valid
        assert any("id" in e for e in result.errors)

    def test_missing_name(self):
        flow = self._make_flow()
        del flow["name"]
        result = self.validator.validate(flow)
        assert not result.valid

    def test_missing_tasks(self):
        flow = self._make_flow()
        del flow["tasks"]
        result = self.validator.validate(flow)
        assert not result.valid

    def test_empty_tasks(self):
        result = self.validator.validate(self._make_flow(tasks={}))
        assert not result.valid
        assert any("no tasks" in e for e in result.errors)

    def test_task_missing_type(self):
        flow = self._make_flow(tasks={"bad": {}})
        result = self.validator.validate(flow)
        assert not result.valid
        assert any("type" in e for e in result.errors)

    def test_relation_invalid_source(self):
        flow = self._make_flow()
        flow["relations"] = [{"from": "nonexistent", "to": "log_1"}]
        result = self.validator.validate(flow)
        assert not result.valid
        assert any("nonexistent" in e for e in result.errors)

    def test_relation_invalid_target(self):
        flow = self._make_flow()
        flow["relations"] = [{"from": "log_1", "to": "ghost"}]
        result = self.validator.validate(flow)
        assert not result.valid

    def test_duplicate_relation(self):
        flow = self._make_flow()
        flow["relations"] = [
            {"from": "log_1", "to": "log_2", "type": "success"},
            {"from": "log_1", "to": "log_2", "type": "success"},
        ]
        result = self.validator.validate(flow)
        assert not result.valid
        assert any("Duplicate" in e for e in result.errors)

    def test_cycle_detection(self):
        flow = self._make_flow()
        flow["tasks"]["log_3"] = {"type": "log"}
        flow["relations"] = [
            {"from": "log_1", "to": "log_2"},
            {"from": "log_2", "to": "log_3"},
            {"from": "log_3", "to": "log_1"},
        ]
        result = self.validator.validate(flow)
        assert not result.valid
        assert any("Cycle" in e for e in result.errors)

    def test_no_cycle_in_linear(self):
        flow = self._make_flow()
        flow["tasks"]["log_3"] = {"type": "log"}
        flow["relations"] = [
            {"from": "log_1", "to": "log_2"},
            {"from": "log_2", "to": "log_3"},
        ]
        result = self.validator.validate(flow)
        assert result.valid

    def test_disconnected_task_warning(self):
        flow = self._make_flow()
        flow["tasks"]["isolated"] = {"type": "log"}
        result = self.validator.validate(flow)
        assert result.valid  # warnings don't fail
        assert any("disconnected" in w for w in result.warnings)

    def test_unknown_task_type_warning(self):
        flow = self._make_flow(tasks={
            "a": {"type": "nonExistentTaskType123"},
        }, relations=[])
        result = self.validator.validate(flow)
        assert any("not registered" in w for w in result.warnings)

    def test_validation_result_bool(self):
        r1 = ValidationResult(valid=True, errors=[], warnings=[])
        r2 = ValidationResult(valid=False, errors=["err"], warnings=[])
        assert bool(r1) is True
        assert bool(r2) is False

    def test_self_loop_cycle(self):
        flow = self._make_flow()
        flow["relations"] = [{"from": "log_1", "to": "log_1"}]
        result = self.validator.validate(flow)
        assert not result.valid
        assert any("Cycle" in e for e in result.errors)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
