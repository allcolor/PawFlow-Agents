# Native HTTP/2 Server Plan

Status: **planned, not implemented** (2026-08-09). The HTTP/1.1 persistence
hotfix is separate and must land first. This plan replaces the stdlib HTTP
transport rather than maintaining two permanent server stacks.

## Why this work exists

The current listener inherits `BaseHTTPRequestHandler` and historically replied
with its HTTP/1.0 default. Behind Caddy, a hard webchat reload therefore created
one upstream TCP/TLS lifecycle per asset. A measured reload issued 108 requests;
one 34 KB script spent 14.51 seconds waiting for response headers and only
16.55 ms downloading.

HTTP/1.1 keep-alive fixes that defect. It does not provide stream multiplexing,
header compression, per-stream flow control, or native HTTP/2 service when
PawFlow is exposed without Caddy.

## Target

One production server stack MUST:

- negotiate `h2` and `http/1.1` with TLS ALPN;
- support clear-text HTTP/2 only when explicitly enabled for a trusted internal
  hop (`h2c`), never as an accidental public downgrade;
- serve HTTP, SSE, WebSocket, uploads, byte ranges, FileStore downloads and
  streaming task responses through the same PawFlow route/auth model;
- work directly and behind Caddy;
- bound connections, concurrent streams, header bytes, body bytes and idle
  timeouts independently;
- expose protocol/connection/stream observability;
- replace the stdlib listener after parity; no indefinite compatibility server.

## Architecture

Use an ASGI HTTP/2 server (Hypercorn is the reference implementation for the
spike) and move PawFlow semantics behind a transport-neutral adapter:

```text
TLS + ALPN (h2/http1.1)
        |
ASGI server: connections, streams, flow control
        |
PawFlow ASGI adapter
        |
gateway -> session auth -> built-in fast paths -> RouteRegistry -> response
```

The adapter owns PawFlow behavior. The server owns HTTP parsing, response
framing, keep-alive, HTTP/2 multiplexing and WebSocket protocol details. The
existing raw socket pre-read, `_PrefixedSocket`, manual WebSocket handshake and
`ThreadingMixIn` dispatch plumbing are deleted after migration.

## Phase 0 — protocol spike and decision gate

Build a minimal non-production adapter that serves `/health`, one static asset,
one SSE stream and one WebSocket route over the candidate server.

The spike passes only if:

- ALPN selects `h2` for direct TLS clients and `http/1.1` for legacy clients;
- at least 100 concurrent HTTP/2 streams share one TCP connection;
- a slow stream does not block a fast stream on the same connection;
- cancellation reaches the PawFlow handler when a client resets one stream;
- Caddy can proxy normal HTTP over HTTP/2 upstream while WebSocket behavior is
  explicitly verified rather than assumed;
- shutdown drains active requests and terminates long-lived streams within a
  configured deadline.

If Hypercorn fails a gate, evaluate another maintained ASGI HTTP/2 server against
the same executable tests. Do not write a second PawFlow adapter for each server.

## Phase 1 — transport-neutral request and response boundary

Extract the behavior currently embedded in `_RequestHandler` into interfaces
that do not reference sockets or `BaseHTTPRequestHandler`:

- immutable request method/path/query/header/client/scheme data;
- bounded request-body reader with upload streaming;
- response start, body chunks and completion/cancellation;
- WebSocket accept/receive/send/close;
- connection metadata carrying negotiated protocol and, for HTTP/2, stream ID.

Move these behaviors behind that boundary without changing semantics:

- global rate limits and private gateway checks;
- session/API-key authentication and cookie renewal;
- `/health`, `/chat/js`, `/api/upload` and FileStore fast paths;
- `RouteRegistry` matching and pending-request response delivery;
- security headers, cache headers and range responses;
- short versus long-lived concurrency accounting.

HTTP/1.1 parity tests must remain green throughout this phase.

## Phase 2 — ASGI HTTP/1.1 parity

Run the full listener test suite against the ASGI adapter in HTTP/1.1 mode before
enabling HTTP/2. Add wire-level tests for:

- persistent sequential requests;
- fixed-length, chunked and connection-delimited bodies;
- HEAD, 1xx, 204 and 304 responses;
- malformed headers, oversized bodies, timeouts and disconnects;
- SSE and streamed responses;
- WebSocket authentication, private-only routes and relay routes;
- multipart uploads, FileStore range requests and client cancellation.

Once parity is reached, make the ASGI server the only implementation. Remove the
stdlib server and its raw socket adapters in the same migration series.

## Phase 3 — HTTP/2 enablement

Enable TLS ALPN for `h2` plus `http/1.1`. Add protocol-specific controls:

- maximum concurrent streams per connection;
- maximum header-list size and body size;
- connection and stream idle deadlines;
- flow-control-aware streaming and bounded outbound queues;
- GOAWAY on graceful shutdown;
- rejection of invalid pseudo-headers and connection-specific HTTP/1 headers;
- cancellation propagation on RST_STREAM.

Use an HTTP/2-capable test client plus low-level `h2` protocol tests where a
high-level client hides stream identity or reset behavior.

## Phase 4 — Caddy and direct deployment

Direct PawFlow TLS must advertise both protocols with ALPN. The Caddy deployment
must be tested in two modes:

1. browser HTTP/2 -> Caddy -> PawFlow HTTP/2 over TLS;
2. browser HTTP/2 -> Caddy -> PawFlow HTTP/1.1 fallback.

The intended Caddy upstream transport is:

```caddyfile
transport http {
    versions 2 1.1
    tls_insecure_skip_verify
}
```

The exact production configuration is accepted only after HTTP, SSE, WebSocket,
large upload and range-download tests pass through Caddy. If Caddy or the chosen
server cannot proxy WebSocket over an HTTP/2 upstream safely, keep ALPN fallback
to HTTP/1.1 for that hop until extended CONNECT is supported; this does not
remove native HTTP/2 support for ordinary PawFlow requests.

## Observability

Every request timing record must include:

- negotiated protocol (`http/1.1` or `h2`);
- stable connection ID and HTTP/2 stream ID;
- header-complete, dispatch, first-byte and completion timestamps;
- active connections, active streams, stream resets and GOAWAY counts;
- flow-control stalls and outbound queue high-water marks.

Logs must distinguish time waiting before PawFlow dispatch from time executing a
flow. This prevents a future transport queue from presenting as a slow task.

## Performance gates

On the release runner and on the production-shaped Caddy path:

- 100 cached small assets over one HTTP/2 connection: no request waits more than
  250 ms for response headers after connection establishment;
- webchat hard reload at 50 ms RTT: DOMContentLoaded under 2 seconds at p95;
- one slow SSE stream must not increase `/health` p95 by more than 25 ms;
- 100 multiplexed streams must not create 100 PawFlow threads;
- memory growth under a 10-minute mixed HTTP/SSE/WebSocket soak must return to
  within 10% of baseline after clients disconnect.

## Release sequence

1. Land the HTTP/1.1 persistence hotfix and verify it through the real Caddy
   deployment.
2. Land Phase 0 spike tests and record the server decision.
3. Extract the transport-neutral adapter with HTTP/1.1 parity.
4. Switch production to the ASGI server and delete the stdlib transport.
5. Enable HTTP/2 behind a feature gate for one release candidate.
6. Make HTTP/2 + HTTP/1.1 ALPN the default after the Caddy/direct matrices and
   soak gates pass; remove the feature gate.

## Definition of done

HTTP/2 support is complete only when direct ALPN and Caddy-upstream HTTP/2 are
both demonstrated by automated tests, every existing listener feature passes on
the new stack, the stdlib transport has been removed, protocol metrics are
visible, and the performance gates above pass in release CI.
