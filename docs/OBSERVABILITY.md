# Observability

Two mechanisms, deliberately different in cost and in what they answer.

| | Answers | Cost | Default |
|---|---|---|---|
| Session correlation in logs | *what happened to this container, from spawn to death* | none | always on |
| OpenTelemetry tracing | *where the wall-clock went, and in what order* | an SDK and a collector | off |

## Session correlation

Every log line carries a correlation field:

```
09:08:07 [INFO] core.codex_live_registry [u1/conv2/assistant/svc/0]: sweeper evict ...
09:08:07 [INFO] core.llm_providers._cc_credentials [u1/conv2/assistant/svc/0]: credential updated in pool
09:08:07 [INFO] some.library [-]: nothing in scope
```

`-` means no session is in scope. The field is always present, so the column
stays greppable:

```bash
grep 'u1/conv2/assistant/svc/0' server.log
```

**Why the session and not the request or the turn.** The bugs this exists for --
an idle TTL not refreshed, a credential slot taken by the wrong path, a rotated
token lost between two sweepers, a leaked heartbeat -- are container-lifecycle
bugs spanning threads, and every one of them happened in a background thread
that belongs to no turn at all. A per-turn id would have labelled exactly the
lines that were never the problem.

### Using it

```python
from core.log_context import bind_session, session_bound

with bind_session(key):        # everything logged inside, at any depth
    teardown(session)

logger.info("...", extra={"session": key})   # one line about another session
```

**The sharp edge: threads.** A `threading.Thread` starts with an EMPTY context,
so a `ContextVar` does not cross `Thread(target=...)` the way it crosses an
asyncio task. Anything handed to a thread or an executor must be wrapped:

```python
threading.Thread(target=session_bound(work)).start()
```

Without it, the work that most needs correlating is exactly the work that loses
it -- correlation that looks complete and is not, which is worse than none.

### Where it is bound

The three live registries bind around the whole teardown, not just their own
log line: the kill, the pool release and the OAuth token copy-back all log from
in there, and those are the lines that cost an account when they go wrong.

## Tracing (OpenTelemetry)

**Off unless configured.** No endpoint, or no SDK installed, and every call is
a no-op costing one attribute lookup. The SDK is not a dependency of PawFlow.

### Turning it on

```bash
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
export OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318
```

The standard variable is read first, so an operator already exporting it for
other services gets PawFlow for free. Failing that, `global_parameters.json`:

```json
{ "otel.endpoint": "http://collector:4318", "otel.service_name": "pawflow" }
```

Configured but not installed logs a warning and stays off -- silence there looks
exactly like "tracing is on and nothing happens". Setup failure never stops the
boot: an observability feature that can fail a boot is worse than no
observability.

### What is instrumented

`agent.iteration` -- the outermost boundary. Provider call, tools and restarts
all hang under it.

Instrument few things. A trace where everything is a span costs money to store
and hides the four rows that mattered. Add one only for a boundary that can be
slow or fail on its own.

```python
from core.observability import span

with span("thing.that.can.be.slow", **{"pawflow.conversation_id": cid}):
    ...
```

### The join

When a span is active and no session is bound, the trace id becomes the log
correlation field. A trace found in a UI names its log lines, and a log line
names its trace. A bound session still wins -- it is the more specific answer.

### When it earns its keep

Honestly: not on a single-operator instance. The correlation field above
answers the questions that have actually cost time here. Tracing pays once
there is more than one node, once latency needs to be compared *over time*
rather than case by case, or once somebody other than the author operates the
server.
