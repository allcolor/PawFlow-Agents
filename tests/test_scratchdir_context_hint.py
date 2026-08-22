"""The ScratchDir steer must reach both provider families.

Without it an agent has no way to learn that `fs://scratchdir/` exists: the
`scratchdir` tool describes itself well, but only to whoever already thought to
look it up. The observed failure is an agent writing to /tmp on the relay or
the server container, which works right up until the container restarts.
"""

from core.scratchdir_models import context_hint


class TestHintContent:

    def test_names_the_uri_scheme(self):
        assert "fs://scratchdir/" in context_hint()

    def test_names_the_lifecycle_tool(self):
        assert "scratchdir" in context_hint()

    def test_rules_out_tmp_explicitly(self):
        """Naming the wrong path is the whole point: /tmp is the actual habit."""
        hint = context_hint()
        assert "/tmp" in hint
        assert "never" in hint.lower()

    def test_needs_no_arguments_and_no_relay(self):
        """Built on every turn, so it must not do a relay round trip."""
        assert context_hint() == context_hint()
        assert context_hint().strip()


class TestHintIsWiredIn:
    """Both prompt-assembly paths must carry it."""

    def test_cli_bootstrap_includes_the_hint(self):
        import inspect

        from core.llm_providers import cli_shared

        src = inspect.getsource(cli_shared)
        assert "ScratchDir Hint" in src
        assert "scratchdir_models import context_hint" in src

    def test_api_context_includes_the_hint(self):
        import inspect

        from tasks.ai import _agentctx_p3

        src = inspect.getsource(_agentctx_p3)
        assert "ScratchDir Hint" in src
        assert "scratchdir_models import context_hint" in src

    def test_cli_hint_is_not_gated_on_existing_state(self):
        """The Scratchpad hint is conditional; this one must not be.

        A cold start has no ScratchDir yet -- gating the steer on one existing
        would hide it exactly when it is needed.
        """
        import inspect

        from core.llm_providers import cli_shared

        src = inspect.getsource(cli_shared)
        marker = 'body.extend(["## ScratchDir Hint"'
        assert marker in src
        line_start = src.rindex("\n", 0, src.index(marker))
        preceding = src[:line_start].rstrip().rsplit("\n", 1)[-1].strip()
        assert not preceding.startswith("if ")
