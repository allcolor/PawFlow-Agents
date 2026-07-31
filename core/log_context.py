"""Ambient log correlation, keyed on the live-session.

The bugs this exists for all share a shape: a container's lifecycle crossing
threads. A lookup hands out a session, a sweeper kills it 200ms later, a
provider relaunches it; a credential is rotated inside a container and copied
back by a sweeper tick that belongs to no request at all. Three threads, three
log lines, and nothing in the text tying them together -- so the chain has to
be reconstructed by reading code rather than logs.

The correlating dimension here is deliberately the SESSION, not a request or a
turn. Those bugs live in background threads, outside any turn, so a per-turn id
would label exactly the lines that were never the problem. What one wants to
ask the log is "what happened to this container, from spawn to death", and the
answer is `grep 'session=cc:...'`.

Two ways in, because both situations occur:

  * ``bind_session(key)`` -- ambient, for a block of code operating on one
    session. Everything logged inside it, at any depth, carries the key.
  * ``logger.info(..., extra={"session": key})`` -- explicit, for a single line
    about a session the current block does not own.

The explicit form wins over the ambient one.

Threads are the sharp edge: a ``threading.Thread`` starts with an EMPTY
context, so a ContextVar does not cross ``Thread(target=...)`` the way it
crosses an asyncio task. A sweeper spawned inside a bound block would silently
log nothing -- correlation that looks complete and is not, which is worse than
none. ``session_bound`` wraps a callable so the thread re-binds explicitly.
"""

import contextvars
import functools
import logging
from contextlib import contextmanager

#: Empty means "no session in scope", rendered as `-` so every line keeps the
#: same number of fields and stays column-greppable.
_SESSION: contextvars.ContextVar = contextvars.ContextVar(
    "pawflow_log_session", default="")

NO_SESSION = "-"


def current_session() -> str:
    """The session key bound to this thread's context, or ""."""
    return _SESSION.get() or ""


@contextmanager
def bind_session(key):
    """Bind a session key for the duration of the block.

    Restores the previous value on exit, including on exception -- nesting is
    therefore safe, which matters because a teardown binds and then calls token
    recovery, which may bind again.
    """
    token = _SESSION.set(_render(key))
    try:
        yield
    finally:
        _SESSION.reset(token)


def session_bound(fn, key=None):
    """Wrap ``fn`` so it re-binds a session key inside whatever thread runs it.

    Use for anything handed to ``threading.Thread(target=...)`` or an executor:
    the new thread does not inherit the caller's context, so without this the
    work that most needs correlating is exactly the work that loses it.

    ``key`` defaults to whatever is bound at wrap time, which is the common
    case -- the thread is spawned from inside the block that owns the session.
    """
    bound = _render(key) if key is not None else current_session()

    @functools.wraps(fn)
    def _run(*args, **kwargs):
        with bind_session(bound):
            return fn(*args, **kwargs)

    return _run


def _render(key) -> str:
    """Render a session key for a log field: one token, no spaces.

    Registry keys are tuples; a raw tuple would put spaces and quotes in the
    middle of a log line and break the column. Callers that already have a
    formatted key (`_fmt_key`) pass a string and it is used as-is.
    """
    if key is None:
        return ""
    if isinstance(key, str):
        return key.replace(" ", "")
    if isinstance(key, (tuple, list)):
        return "/".join(str(part) for part in key).replace(" ", "")
    return str(key).replace(" ", "")


class SessionFormatter(logging.Formatter):
    """Formatter that fills the ``session`` field for every record.

    Reading the ContextVar here rather than in a Filter is what makes the field
    impossible to miss: a record emitted by a third-party library, or by any
    code that never heard of this module, still formats -- it just renders `-`.
    A `%(session)s` in the format string with no filter attached would raise on
    those records instead, turning a missing log field into a logging error.

    Formatting runs in the emitting thread (no QueueHandler anywhere), so the
    value read here is the one in scope at the call site.
    """

    def format(self, record: logging.LogRecord) -> str:
        explicit = getattr(record, "session", None)
        record.session = (_render(explicit) or current_session()
                          or _trace_id() or NO_SESSION)
        return super().format(record)


def _trace_id() -> str:
    """The active OTel trace id, when tracing is on and no session is bound.

    The join between the two stories: a line logged inside a span but outside
    any session still carries something a trace can be found by. Tracing off
    (the default) returns "" for the cost of one attribute lookup.
    """
    try:
        from core.observability import current_trace_id
        return current_trace_id()[:16]
    except Exception:
        return ""
