"""Regression tests for Active Agents interrupt and force-stop routing."""

import threading
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "method_name",
    (
        "cancel_claude_code",
        "cancel_claude_code_interactive",
        "cancel_codex",
        "cancel_codex_interactive",
        "cancel_gemini",
    ),
)
def test_provider_cancel_routes_to_every_supported_provider(method_name):
    from tasks.ai.actions.cancel_interrupt import _cancel_provider_client

    calls = []
    client = SimpleNamespace(**{
        method_name: lambda force=False: calls.append(force),
    })

    assert _cancel_provider_client(client, force=True)
    assert calls == [True]


def test_provider_cancel_supports_argumentless_abort():
    from tasks.ai.actions.cancel_interrupt import _cancel_provider_client

    calls = []
    client = SimpleNamespace(abort=lambda: calls.append("abort"))

    assert _cancel_provider_client(client, force=True)
    assert calls == ["abort"]


def test_provider_aware_abort_wins_over_inherited_provider_mixins():
    from tasks.ai.actions.cancel_interrupt import _cancel_provider_client

    calls = []
    client = SimpleNamespace(
        provider="codex-interactive",
        abort=lambda: calls.append("abort"),
        cancel_claude_code=lambda force=False: calls.append("wrong-claude"),
        cancel_codex_interactive=lambda force=False: calls.append("codex"),
    )

    assert _cancel_provider_client(client, force=True)
    assert calls == ["abort"]


def test_task_force_stop_removes_every_active_marker_and_returns_clients():
    from tasks.ai.actions.cancel_interrupt import _pop_task_runtime_state

    matching_client = object()
    other_client = object()
    matching_key = "conv::task::t_running:assistant"
    other_key = "conv:assistant"
    executor = SimpleNamespace(
        _active_contexts_lock=threading.RLock(),
        _active_contexts={matching_key: {"status": "thinking"}, other_key: {}},
        _active_turns={matching_key: {"status": "thinking"}, other_key: {}},
        _active_claude_client={matching_key: matching_client, other_key: other_client},
    )

    clients = _pop_task_runtime_state(executor, "t_running")

    assert clients == [matching_client]
    for mapping in (
        executor._active_contexts,
        executor._active_turns,
        executor._active_claude_client,
    ):
        assert matching_key not in mapping
        assert other_key in mapping
