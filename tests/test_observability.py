"""Tracing is optional, and being optional is the property under test.

PawFlow is self-hosted: most instances have one operator and no collector, so
an SDK dependency and its exporter thread would be paid by everyone for a
feature almost nobody turns on. Every test here is a way that could stop being
true -- an import at module scope, a raise during setup, a span that costs
something when tracing is off.
"""

import io
import logging
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core import observability
from core.log_context import NO_SESSION, SessionFormatter, bind_session
from core.server_logging import LOG_FORMAT


class OffByDefault(unittest.TestCase):

    def setUp(self):
        observability._TRACER = None
        observability._CONFIGURED = False

    def tearDown(self):
        observability._TRACER = None
        observability._CONFIGURED = False

    def test_no_endpoint_means_no_tracing(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch("core.expression._load_global_parameters", return_value={}):
            self.assertFalse(observability.configure_tracing(force=True))
        self.assertFalse(observability.tracing_enabled())

    def test_a_span_is_free_and_yields_none_when_off(self):
        with observability.span("anything", some="attr") as s:
            self.assertIsNone(s)

    def test_the_body_still_runs_and_still_raises_when_off(self):
        ran = []
        with self.assertRaises(ValueError):
            with observability.span("x"):
                ran.append(1)
                raise ValueError("from the body")
        self.assertEqual(ran, [1])

    def test_the_trace_id_is_empty_when_off(self):
        self.assertEqual(observability.current_trace_id(), "")

    def test_a_configured_endpoint_without_the_sdk_does_not_raise(self):
        """Configured but not installed is an operator mistake, not a crash."""
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
            else __builtins__.__import__

        def _no_otel(name, *args, **kwargs):
            if name.startswith("opentelemetry"):
                raise ImportError("no opentelemetry here")
            return real_import(name, *args, **kwargs)

        with patch.dict("os.environ",
                        {observability.ENDPOINT_ENV: "http://collector:4318"}), \
                patch("builtins.__import__", side_effect=_no_otel):
            self.assertFalse(observability.configure_tracing(force=True))

    def test_setup_failure_never_stops_the_boot(self):
        """An observability feature that can fail a boot is worse than none."""
        with patch.dict("os.environ",
                        {observability.ENDPOINT_ENV: "http://collector:4318"}), \
                patch.object(observability, "_configured_service_name",
                             side_effect=RuntimeError("boom")):
            self.assertFalse(observability.configure_tracing(force=True))

    def test_an_unreadable_parameter_store_is_not_an_endpoint(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch("core.expression._load_global_parameters",
                      side_effect=OSError("no file")):
            self.assertEqual(observability._configured_endpoint(), "")


class TheConfiguration(unittest.TestCase):

    def test_the_standard_otel_variable_is_read_first(self):
        """An operator already exporting it for other services gets PawFlow free."""
        with patch.dict("os.environ",
                        {observability.ENDPOINT_ENV: "http://from-env:4318"}), \
                patch("core.expression._load_global_parameters",
                      return_value={observability.ENDPOINT_PARAM: "http://from-param"}):
            self.assertEqual(observability._configured_endpoint(),
                             "http://from-env:4318")

    def test_the_pawflow_parameter_is_the_fallback(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch("core.expression._load_global_parameters",
                      return_value={observability.ENDPOINT_PARAM: "http://from-param"}):
            self.assertEqual(observability._configured_endpoint(), "http://from-param")


class TheJoinWithTheLogs(unittest.TestCase):
    """Traces and logs must name each other, or they are two stories."""

    def _line(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(SessionFormatter(LOG_FORMAT, datefmt="%H:%M:%S"))
        log = logging.getLogger("test.otel.join")
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
        try:
            log.info("hello")
        finally:
            log.removeHandler(handler)
        return stream.getvalue()

    def test_a_trace_id_labels_a_line_that_has_no_session(self):
        with patch("core.observability.current_trace_id",
                   return_value="abcdef0123456789abcdef0123456789"):
            self.assertIn("[abcdef0123456789]", self._line())

    def test_a_bound_session_still_wins_over_the_trace_id(self):
        """The session is the more specific answer, and the reason it exists."""
        with patch("core.observability.current_trace_id",
                   return_value="abcdef0123456789abcdef0123456789"), \
                bind_session("cc:alice/conv1"):
            self.assertIn("[cc:alice/conv1]", self._line())

    def test_neither_leaves_the_placeholder(self):
        with patch("core.observability.current_trace_id", return_value=""):
            self.assertIn(f"[{NO_SESSION}]", self._line())

    def test_a_broken_trace_lookup_does_not_break_logging(self):
        with patch("core.observability.current_trace_id",
                   side_effect=RuntimeError("otel exploded")):
            self.assertIn(f"[{NO_SESSION}]", self._line())


class _FakeOtelContext:
    """Enough of ``opentelemetry.context`` to observe attach/detach."""

    def __init__(self):
        self.current = object()
        self.attached = []
        self.detached = []

    def get_current(self):
        return self.current

    def attach(self, ctx):
        self.attached.append(ctx)
        return f"token-{len(self.attached)}"

    def detach(self, token):
        self.detached.append(token)


class CrossingTheThreadBoundary(unittest.TestCase):
    """Tools run in a pool, and a ContextVar does not follow them there.

    Without an explicit re-attach every tool span is a root span: the trace
    shows the tools as unrelated top-level rows instead of the turn that ran
    them. These tests are that re-attach.
    """

    def setUp(self):
        self.fake = _FakeOtelContext()
        module = types.ModuleType("opentelemetry")
        module.context = self.fake
        self._modules = patch.dict(
            "sys.modules",
            {"opentelemetry": module, "opentelemetry.context": self.fake})
        self._modules.start()
        observability._TRACER = object()

    def tearDown(self):
        self._modules.stop()
        observability._TRACER = None
        observability._CONFIGURED = False

    def test_a_captured_context_is_attached_then_detached(self):
        ctx = observability.current_context()
        self.assertIs(ctx, self.fake.current)
        with observability.attached(ctx):
            self.assertEqual(self.fake.attached, [ctx])
            self.assertEqual(self.fake.detached, [])
        self.assertEqual(self.fake.detached, ["token-1"])

    def test_the_context_is_detached_even_when_the_body_raises(self):
        """A leaked token would make every later span a child of a dead one."""
        with self.assertRaises(ValueError):
            with observability.attached(observability.current_context()):
                raise ValueError("from the tool")
        self.assertEqual(self.fake.detached, ["token-1"])

    def test_nothing_captured_means_nothing_attached(self):
        with observability.attached(None):
            pass
        self.assertEqual(self.fake.attached, [])

    def test_a_broken_attach_costs_the_parent_and_not_the_work(self):
        ran = []
        with patch.object(self.fake, "attach",
                          side_effect=RuntimeError("otel exploded")):
            with observability.attached(observability.current_context()):
                ran.append(1)
        self.assertEqual(ran, [1])


class TheHelpersAreFreeWhenOff(unittest.TestCase):

    def setUp(self):
        observability._TRACER = None

    def test_nothing_is_captured_when_tracing_is_off(self):
        self.assertIsNone(observability.current_context())

    def test_attaching_when_off_still_runs_the_body(self):
        ran = []
        with observability.attached(object()):
            ran.append(1)
        self.assertEqual(ran, [1])


class WhatIsInstrumented(unittest.TestCase):

    def test_the_server_turns_tracing_on_at_startup(self):
        src = Path("cli.py").read_text(encoding="utf-8")
        self.assertIn("configure_tracing()", src)

    def test_the_agent_iteration_carries_a_span(self):
        """The one boundary everything else hangs under."""
        src = Path("tasks/ai/_alc_iteration.py").read_text(encoding="utf-8")
        self.assertIn('span("agent.iteration"', src)

    def test_the_provider_call_carries_a_span(self):
        """The only step of a turn that leaves the process."""
        src = Path("tasks/ai/_alc_closures2.py").read_text(encoding="utf-8")
        self.assertIn('span("agent.llm_call"', src)

    def test_the_tool_execution_carries_a_span(self):
        src = Path("tasks/ai/agent_tool_exec.py").read_text(encoding="utf-8")
        self.assertIn('"agent.tool"', src)

    def test_the_tool_context_is_captured_before_the_pool_not_inside_it(self):
        """Captured in the worker it would be empty, and the span an orphan."""
        src = Path("tasks/ai/agent_tool_exec.py").read_text(encoding="utf-8")
        self.assertLess(src.index("current_context()"),
                        src.index("pool.submit("),
                        "the trace context must be captured in the submitting "
                        "thread")

    def test_the_sdk_is_not_a_hard_dependency(self):
        """Imported inside the functions, never at module scope."""
        src = Path("core/observability.py").read_text(encoding="utf-8")
        header = src.split("def _configured_endpoint")[0]
        self.assertNotIn("import opentelemetry", header)
        self.assertNotIn("from opentelemetry", header)

        deps = Path("pyproject.toml").read_text(encoding="utf-8")
        install_section = deps.split("[project.optional-dependencies]")[0]
        self.assertNotIn("opentelemetry", install_section,
                         "the SDK became a mandatory dependency")


if __name__ == "__main__":
    unittest.main()
