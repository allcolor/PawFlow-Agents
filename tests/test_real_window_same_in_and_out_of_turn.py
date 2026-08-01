"""The gauge denominator must not depend on whether a turn is running.

`_service_config` resolved the provider's real context window from two
different places. With an active turn it read the per-stream map that the
Claude Code stream fills from CC's own `modelUsage[model].contextWindow`.
Without one it read `client._real_context_size` / `client._context_window` --
two attributes PawFlow assigns NOWHERE, so that branch always resolved to 0.

So the denominator was min(configured, real) during a turn and plain
`configured` between turns. Whenever the two differ, the gauge jumped at the
turn boundary with nothing behind the move, in either direction.
"""

from tasks.ai.context_usage import _client_real_window, _service_config

CONV = "c1"
AGENT = "claude"
REAL_WINDOW = 200_000
CONFIGURED = 1_000_000


class _Client:
    provider = "claude-code"

    def __init__(self, window=REAL_WINDOW):
        # Exactly what _cc_stream_result writes after a turn reports usage.
        self._cc_context_window_by_stream = (
            {(CONV, AGENT): window} if window else {})


class _Svc:
    def __init__(self, client):
        self.config = {"max_context_size": CONFIGURED, "provider": "claude-code"}
        self._client = client

    def get_client(self):
        return self._client


def test_the_real_window_is_found_without_an_active_turn(monkeypatch):
    client = _Client()
    svc = _Svc(client)

    import core.service_registry as sr
    import core.conv_agent_config as cac

    class _Registry:
        def resolve(self, *_a, **_k):
            return svc

    monkeypatch.setattr(sr.ServiceRegistry, "get_instance",
                        staticmethod(lambda: _Registry()))
    monkeypatch.setattr(cac, "get_agent_config",
                        lambda *_a, **_k: {"llm_service": "svc1"})

    _cfg, real, provider = _service_config(CONV, AGENT, "u1", None)
    assert real == REAL_WINDOW, (
        "between turns the provider window was lost, so the gauge divided by "
        "the configured budget instead")
    assert provider == "claude-code"


def test_the_active_turn_resolves_the_same_window():
    client = _Client()
    active_ctx = {
        "resolved_svc": _Svc(client),
        "real_context_size": 0,
        "client": client,
        "active_llm_provider": "claude-code",
    }
    _cfg, real, _provider = _service_config(CONV, AGENT, "u1", active_ctx)
    assert real == REAL_WINDOW


def test_the_two_paths_agree():
    """The property that matters, stated directly."""
    client = _Client()
    active_ctx = {
        "resolved_svc": _Svc(client),
        "real_context_size": 0,
        "client": client,
        "active_llm_provider": "claude-code",
    }
    _c, in_turn, _p = _service_config(CONV, AGENT, "u1", active_ctx)
    assert in_turn == _client_real_window(client, CONV, AGENT)


def test_a_provider_that_reports_no_window_still_yields_zero():
    """codex-interactive and friends report nothing; 0 means "use configured",
    and must not raise or invent a number."""
    assert _client_real_window(_Client(window=0), CONV, AGENT) == 0
    assert _client_real_window(None, CONV, AGENT) == 0
    assert _client_real_window(object(), CONV, AGENT) == 0


def test_the_window_is_per_stream_not_global():
    """Another agent's side-stream must not supply this agent's denominator:
    that is why the map is keyed by (conversation, agent) in the first place."""
    client = _Client()
    assert _client_real_window(client, CONV, "someone-else") == 0
    assert _client_real_window(client, "other-conv", AGENT) == 0
