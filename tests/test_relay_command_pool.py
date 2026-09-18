"""How many relay commands run at once is the operator's number.

It used to be a bare ``max_workers=4``: with four agents running long tools the
pool was saturated, an ``open_terminal`` waited 188s for a worker, and nothing
said why. The value is now read from ``PAWFLOW_RELAY_COMMAND_WORKERS``, the
worker prints the concurrency it uses, and a command that waits for a worker
reports the wait -- no silent cap.
"""

from pawflow_relay import worker


def test_the_silent_socket_timeout_is_the_operators_number(monkeypatch, capsys):
    """The one deadline in the relay loop was a bare 90."""
    monkeypatch.delenv("PAWFLOW_RELAY_DEAD_TIMEOUT", raising=False)
    assert worker._env_seconds(
        "PAWFLOW_RELAY_DEAD_TIMEOUT", worker._DEFAULT_DEAD_TIMEOUT) == 90.0

    monkeypatch.setenv("PAWFLOW_RELAY_DEAD_TIMEOUT", "300")
    assert worker._env_seconds("PAWFLOW_RELAY_DEAD_TIMEOUT", 90.0) == 300.0

    monkeypatch.setenv("PAWFLOW_RELAY_DEAD_TIMEOUT", "soon")
    assert worker._env_seconds("PAWFLOW_RELAY_DEAD_TIMEOUT", 90.0) == 90.0
    assert "not a duration" in capsys.readouterr().err


def test_the_default_concurrency_is_stated_not_hidden(monkeypatch):
    monkeypatch.delenv("PAWFLOW_RELAY_COMMAND_WORKERS", raising=False)
    assert worker._command_pool_workers() == worker._DEFAULT_COMMAND_WORKERS


def test_the_operator_can_raise_it(monkeypatch):
    monkeypatch.setenv("PAWFLOW_RELAY_COMMAND_WORKERS", "16")
    assert worker._command_pool_workers() == 16


def test_an_unusable_value_is_reported_and_replaced(monkeypatch, capfd):
    for bad in ("abc", "0", "-3"):
        monkeypatch.setenv("PAWFLOW_RELAY_COMMAND_WORKERS", bad)
        assert worker._command_pool_workers() == worker._DEFAULT_COMMAND_WORKERS
    err = capfd.readouterr().err
    assert "is not a worker count" in err
