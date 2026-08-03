"""run_tests must be able to report EVERY failure in one call.

The handler hardcoded `-x`, so a batch of known-red tests cost one call per
test: the runner stopped at the first failure and the caller had to re-run
with a `-k` pattern for each remaining one. `maxfail` exposes pytest's own
knob, with the previous behaviour (stop at the first failure) as the default
so existing callers are unaffected.
"""

from core.handlers.devops import RunTestsHandler


class _Relay:
    def __init__(self):
        self.calls = []

    def exec(self, path, command, *args, **kwargs):
        self.calls.append(command)
        return {"stdout": "1 passed\n", "stderr": "", "returncode": 0}


def _run(monkeypatch, arguments):
    relay = _Relay()
    monkeypatch.delenv("PAWFLOW_USE_RTK", raising=False)
    monkeypatch.setattr(
        "core.handlers._fs_base.find_fs_service",
        lambda user_id, service_name="", conversation_id="": relay,
    )
    handler = RunTestsHandler()
    handler.set_user_id("user-1")
    handler.set_conversation_id("conv-1")
    result = handler.execute(arguments)
    return relay, result


def test_default_still_stops_at_the_first_failure(monkeypatch):
    relay, _ = _run(monkeypatch, {"test_files": ["tests/test_example.py"]})
    assert " --maxfail=1 " in relay.calls[0]


def test_zero_removes_the_limit_so_every_failure_is_reported(monkeypatch):
    relay, _ = _run(
        monkeypatch, {"test_files": ["tests/test_example.py"], "maxfail": 0})
    assert "maxfail" not in relay.calls[0]
    assert relay.calls[0] == (
        'python -m pytest "tests/test_example.py" -q --tb=short --no-header')


def test_explicit_limit_is_passed_through(monkeypatch):
    relay, _ = _run(
        monkeypatch, {"test_files": ["tests/test_example.py"], "maxfail": 5})
    assert " --maxfail=5 " in relay.calls[0]


def test_maxfail_combines_with_a_test_pattern(monkeypatch):
    relay, _ = _run(monkeypatch, {
        "test_files": ["tests/test_example.py"],
        "maxfail": 0,
        "test_pattern": "test_foo",
    })
    assert relay.calls[0].endswith('-k "test_foo"')
    assert "maxfail" not in relay.calls[0]


def test_a_string_integer_is_accepted(monkeypatch):
    # Providers routinely send integers as strings; int("3") is unambiguous.
    relay, _ = _run(
        monkeypatch, {"test_files": ["tests/test_example.py"], "maxfail": "3"})
    assert " --maxfail=3 " in relay.calls[0]


def test_a_non_integer_is_refused_with_a_usable_message(monkeypatch):
    relay, result = _run(
        monkeypatch,
        {"test_files": ["tests/test_example.py"], "maxfail": "lots"})
    assert result.startswith("Error: 'maxfail' must be an integer")
    assert "Use 0 to report every failure" in result
    assert relay.calls == [], "nothing may run on a rejected argument"


def test_a_negative_limit_is_refused(monkeypatch):
    relay, result = _run(
        monkeypatch, {"test_files": ["tests/test_example.py"], "maxfail": -1})
    assert result.startswith("Error: 'maxfail' must be >= 0")
    assert relay.calls == []


def test_null_falls_back_to_the_default(monkeypatch):
    # An optional field sent as null must behave as if it were omitted.
    relay, _ = _run(
        monkeypatch,
        {"test_files": ["tests/test_example.py"], "maxfail": None})
    assert " --maxfail=1 " in relay.calls[0]


def test_the_parameter_is_discoverable_from_the_schema_and_description():
    handler = RunTestsHandler()
    assert "maxfail" in handler.parameters_schema["properties"]
    assert handler.parameters_schema["properties"]["maxfail"]["type"] == "integer"
    # The agent must learn the 0 case from the tool itself, not from a plan.
    assert "0" in handler.description
    assert "maxfail" in handler.description
