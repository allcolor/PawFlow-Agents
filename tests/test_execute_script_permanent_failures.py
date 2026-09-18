"""A deterministic script failure must not be retried forever.

A build flow (conversation 1719a9c3) retried "Blocked by sandbox: Module 'os' is
not allowed" 509 times, every three seconds, until the flow was stopped: the
engine stops a task on `TaskError.retryable is False`
(engine/_continuous_exec_run.py), but the refusal arrived as a plain TaskError,
so it looked retryable and the loop had no reason to end.
"""

import pytest

from core import FlowFile, TaskError


def test_task_error_is_retryable_unless_it_says_otherwise():
    assert TaskError("boom").retryable is True
    assert TaskError("boom", retryable=False).retryable is False


def _run_script(source: str):
    from tasks.system.execute_script import ExecuteScriptTask

    task = ExecuteScriptTask({"script": source})
    return task._execute_local(FlowFile(content=b"hello", attributes={}))


def test_a_refused_import_is_a_permanent_failure():
    with pytest.raises(TaskError) as info:
        _run_script("import os\nresult = os.getcwd()\n")
    assert "Blocked by sandbox" in str(info.value)
    assert info.value.retryable is False


def test_a_syntax_error_is_a_permanent_failure():
    with pytest.raises(TaskError) as info:
        _run_script("result = (\n")
    assert info.value.retryable is False


def test_an_ordinary_script_error_stays_retryable():
    """A service that timed out may well succeed on the next pass."""
    with pytest.raises(TaskError) as info:
        _run_script("raise ValueError('transient')\n")
    assert info.value.retryable is True


def test_a_working_script_still_runs():
    result = _run_script("result = content.upper()\n")
    assert result[0].get_content() == b"HELLO"
