"""Optional OpenTelemetry tracing, and the bridge to the log correlation.

Off unless an operator turns it on. PawFlow is self-hosted and most instances
have one operator and no collector, so a hard dependency on the OTel SDK -- and
the exporter thread that comes with it -- would be paid by everyone for a
feature almost nobody enables. Absent package or absent endpoint, everything
here is a no-op that costs one attribute lookup.

What it is FOR, concretely: the questions that span threads and outlive a
request. A turn crosses the HTTP handler, the agent loop, a provider, a
container and a sweeper; the logs already carry `session=` (see
``core.log_context``), which answers "what happened to this container". Tracing
answers the other half -- where the wall-clock went, and in what order -- and it
only earns its keep once there is more than one instance, or once somebody
other than the author operates it.

The two are deliberately joined: when a span is active its trace id becomes the
ambient log correlation, so a trace found in a UI names the exact log lines,
and a log line names the trace. Without that, tracing is a second, parallel
story nobody can line up with the first.

Instrument few things. A span per boundary that can be slow or can fail on its
own -- the turn, the provider call, the tool, the session lifecycle -- and
nothing else: a trace where everything is a span costs money to store and hides
the four lines that mattered.
"""

import logging
import os
from contextlib import contextmanager
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Set once by configure_tracing(). None means "tracing is off", which is the
#: default and the state every no-op path checks.
_TRACER: Optional[Any] = None
_CONFIGURED = False

#: Standard OTel variable first, so an operator who already exports it for
#: other services gets PawFlow for free. The PawFlow parameter is the fallback
#: for an instance configured entirely through global_parameters.json.
ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
ENDPOINT_PARAM = "otel.endpoint"
SERVICE_NAME_PARAM = "otel.service_name"
DEFAULT_SERVICE_NAME = "pawflow"


def _configured_endpoint() -> str:
    endpoint = str(os.getenv(ENDPOINT_ENV, "") or "").strip()
    if endpoint:
        return endpoint
    try:
        from core.expression import _load_global_parameters
        params = _load_global_parameters()
    except Exception:
        logger.debug("otel: global parameter lookup failed", exc_info=True)
        return ""
    return str(params.get(ENDPOINT_PARAM, "") or "").strip()


def _configured_service_name() -> str:
    name = str(os.getenv("OTEL_SERVICE_NAME", "") or "").strip()
    if name:
        return name
    try:
        from core.expression import _load_global_parameters
        return (str(_load_global_parameters().get(SERVICE_NAME_PARAM, "") or "").strip()
                or DEFAULT_SERVICE_NAME)
    except Exception:
        return DEFAULT_SERVICE_NAME


def configure_tracing(force: bool = False) -> bool:
    """Turn tracing on if an endpoint is configured AND the SDK is installed.

    Returns whether tracing ended up enabled. Never raises: an observability
    feature that can stop a server from booting is worse than no observability.
    Called once at startup; ``force`` re-reads the configuration (tests).
    """
    global _TRACER, _CONFIGURED
    if _CONFIGURED and not force:
        return _TRACER is not None
    _CONFIGURED = True
    _TRACER = None

    endpoint = _configured_endpoint()
    if not endpoint:
        logger.debug("otel: no endpoint configured; tracing stays off")
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter)
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        # Configured but not installed is an operator mistake worth a line:
        # silence here looks exactly like "tracing is on and nothing happens".
        logger.warning(
            "otel: %s is set but the OpenTelemetry SDK is not installed; "
            "tracing stays off (pip install opentelemetry-sdk "
            "opentelemetry-exporter-otlp-proto-http)", ENDPOINT_ENV)
        return False
    try:
        provider = TracerProvider(resource=Resource.create(
            {"service.name": _configured_service_name()}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        _TRACER = trace.get_tracer("pawflow")
        logger.info("otel: tracing enabled, exporting to %s", endpoint)
        return True
    except Exception:
        logger.warning("otel: tracing setup failed; continuing without it",
                       exc_info=True)
        _TRACER = None
        return False


def tracing_enabled() -> bool:
    return _TRACER is not None


@contextmanager
def span(name: str, **attributes):
    """A span, or nothing at all when tracing is off.

    Attributes are passed as keyword arguments and any value that is not a
    scalar is stringified: an exporter that rejects one attribute drops the
    whole span, and losing a span to a stray dict is not a trade worth making.

    A failure inside never escapes as a tracing error -- the exception the body
    raised is what the caller must see. It is recorded on the span first, so
    the trace shows the failure rather than an unexplained gap.
    """
    tracer = _TRACER
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name) as otel_span:
        for key, value in attributes.items():
            try:
                if not isinstance(value, (str, bool, int, float)):
                    value = str(value)
                otel_span.set_attribute(key, value)
            except Exception:
                logger.debug("otel: attribute %s rejected", key, exc_info=True)
        try:
            yield otel_span
        except Exception as exc:
            try:
                otel_span.record_exception(exc)
            except Exception:
                logger.debug("otel: record_exception failed", exc_info=True)
            raise


def current_context() -> Optional[Any]:
    """An opaque handle to the active trace context, or None when tracing is off.

    Only useful together with ``attached``: a context captured here in one
    thread and re-attached there in another is what keeps a span made in a
    worker under the span that submitted the work.
    """
    if _TRACER is None:
        return None
    try:
        from opentelemetry import context as otel_context
        return otel_context.get_current()
    except Exception:
        logger.debug("otel: context capture failed", exc_info=True)
        return None


@contextmanager
def attached(ctx: Optional[Any]):
    """Re-attach a context captured by ``current_context`` in another thread.

    OpenTelemetry keeps the active span in a ContextVar, and a ContextVar does
    not cross a ThreadPoolExecutor boundary: work submitted to a pool starts
    with an empty context. Without this, a span opened inside a worker is a
    root span rather than a child, and a trace shows the tools as unrelated
    top-level rows instead of the turn that ran them.

    A no-op when tracing is off or nothing was captured.
    """
    if _TRACER is None or ctx is None:
        yield
        return
    try:
        from opentelemetry import context as otel_context
        token = otel_context.attach(ctx)
    except Exception:
        # Failing to attach costs a correct parent, never the work itself.
        logger.debug("otel: context attach failed", exc_info=True)
        yield
        return
    try:
        yield
    finally:
        try:
            otel_context.detach(token)
        except Exception:
            logger.debug("otel: context detach failed", exc_info=True)


def current_trace_id() -> str:
    """The active trace id as hex, or "" when there is no span.

    This is the join between traces and logs: bound as the ambient log
    correlation, it makes a trace name its own log lines.
    """
    if _TRACER is None:
        return ""
    try:
        from opentelemetry import trace
        ctx = trace.get_current_span().get_span_context()
        if not ctx.is_valid:
            return ""
        return format(ctx.trace_id, "032x")
    except Exception:
        logger.debug("otel: trace id lookup failed", exc_info=True)
        return ""
