"""Log correlation on the live-session key.

The bugs this exists for -- TTL not refreshed, a credential slot taken by the
wrong path, a rotated token lost between two sweepers, a leaked heartbeat --
are all container-lifecycle bugs spanning threads, and every one of them was
found by reading code because the logs could not be joined.

So the correlating dimension is the SESSION, not a request or a turn: those
bugs happen in background threads that belong to no turn at all.
"""

import io
import logging
import threading
import unittest

from core.log_context import (
    NO_SESSION, SessionFormatter, bind_session, current_session, session_bound)
from core.server_logging import LOG_FORMAT


def _capture(fn, name="test.corr"):
    """Run fn with a handler using the real server format, return the output."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SessionFormatter(LOG_FORMAT, datefmt="%H:%M:%S"))
    logger = logging.getLogger(name)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        fn(logger)
    finally:
        logger.removeHandler(handler)
    return stream.getvalue()


class TheField(unittest.TestCase):

    def test_the_real_server_format_carries_the_field(self):
        """Correlation that only exists in tests is not correlation."""
        self.assertIn("%(session)s", LOG_FORMAT)

    def test_a_bound_block_labels_everything_inside_it(self):
        def run(log):
            with bind_session("cc:alice/conv1"):
                log.info("inside")

        self.assertIn("[cc:alice/conv1]: inside", _capture(run))

    def test_an_unbound_line_still_formats(self):
        """Every line keeps the same shape, so the column stays greppable."""
        out = _capture(lambda log: log.info("outside"))
        self.assertIn(f"[{NO_SESSION}]: outside", out)

    def test_a_record_from_code_that_never_heard_of_us_still_formats(self):
        """A `%(session)s` with no filter would raise on a foreign record.

        Reading the context inside the formatter is what makes a missing field
        impossible: a third-party library logging through the root handler must
        not turn into a logging error.
        """
        out = _capture(lambda _log: logging.getLogger("some.library").handlers
                       or None, name="test.foreign")
        # The real assertion: formatting a bare record raises nothing.
        record = logging.LogRecord("lib", logging.WARNING, "f.py", 1,
                                   "from a library", None, None)
        text = SessionFormatter(LOG_FORMAT, datefmt="%H:%M:%S").format(record)
        self.assertIn(f"[{NO_SESSION}]", text)
        self.assertIn("from a library", text)
        self.assertEqual(out, "")

    def test_an_explicit_session_beats_the_ambient_one(self):
        """For a line about a session the current block does not own."""
        def run(log):
            with bind_session("ambient"):
                log.info("other", extra={"session": "explicit"})

        self.assertIn("[explicit]: other", _capture(run))

    def test_a_tuple_key_renders_as_one_token(self):
        """Registry keys are tuples; a raw tuple would break the column."""
        def run(log):
            with bind_session(("user 1", "conv2", "agent", "svc", 3)):
                log.info("x")

        out = _capture(run)
        self.assertIn("[user1/conv2/agent/svc/3]: x", out)

    def test_binding_restores_the_previous_value(self):
        """Teardown binds, then calls recovery, which may bind again."""
        with bind_session("outer"):
            with bind_session("inner"):
                self.assertEqual(current_session(), "inner")
            self.assertEqual(current_session(), "outer")
        self.assertEqual(current_session(), "")

    def test_an_exception_does_not_leak_the_binding(self):
        try:
            with bind_session("doomed"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertEqual(current_session(), "")


class TheThreadBoundary(unittest.TestCase):
    """The sharp edge, and the reason session_bound exists."""

    def test_a_plain_thread_does_not_inherit_the_binding(self):
        """Pinned deliberately: this is the behaviour, not a bug.

        A ContextVar does NOT cross threading.Thread the way it crosses an
        asyncio task. Anyone binding a session and spawning a sweeper inside it
        gets silence -- correlation that looks complete and is not.
        """
        seen = []
        with bind_session("parent"):
            t = threading.Thread(target=lambda: seen.append(current_session()))
            t.start()
            t.join()
        self.assertEqual(seen, [""], "contextvars started crossing threads")

    def test_session_bound_carries_it_across(self):
        seen = []
        with bind_session("parent"):
            target = session_bound(lambda: seen.append(current_session()))
        # Started OUTSIDE the block on purpose: the wrap captured the key, so
        # when the thread actually runs is irrelevant.
        t = threading.Thread(target=target)
        t.start()
        t.join()
        self.assertEqual(seen, ["parent"])

    def test_session_bound_accepts_an_explicit_key(self):
        seen = []
        target = session_bound(lambda: seen.append(current_session()), key="given")
        t = threading.Thread(target=target)
        t.start()
        t.join()
        self.assertEqual(seen, ["given"])


class TheLifecyclePaths(unittest.TestCase):
    """The paths that had no correlation and needed it most."""

    def test_the_sweeper_teardown_is_bound_not_just_its_log_line(self):
        """The kill, the pool release and the token copy-back log from there.

        Binding only the `sweeper evict` line would label the one message that
        already named the key, and leave the credential writes -- the ones that
        cost an account -- anonymous.
        """
        seen = {}

        import core.cc_live_registry as reg

        class _Dead:
            last_used = 0.0
            reuse_count = 0
            workdir = ""
            service_id = "svc"
            svc_pool_idx = 0
            user_id = "u"
            conv_id = "c"

            def is_alive(self):
                return False

        registry = reg.LiveSessionRegistry()
        key = ("u1", "conv1", "agent", "svc", 0)
        registry._sessions[key] = _Dead()

        def _teardown(session, reason, killer, recover=None):
            seen["session"] = current_session()

        original = reg._teardown_session
        reg._teardown_session = _teardown
        try:
            registry.sweep_idle(1)
        finally:
            reg._teardown_session = original

        self.assertEqual(seen.get("session"), reg._fmt_key(key),
                         "the teardown ran outside the session binding")

    def test_every_registry_binds_its_own_key_format(self):
        """All three providers, or the grep only works for one of them."""
        from pathlib import Path
        for path in ("core/cc_live_registry.py",
                     "core/codex_live_registry.py",
                     "core/gemini_live_registry.py"):
            src = Path(path).read_text(encoding="utf-8")
            self.assertIn("bind_session", src, f"{path} logs uncorrelated")
            self.assertIn("_recover_live_tokens", src)


if __name__ == "__main__":
    unittest.main()
