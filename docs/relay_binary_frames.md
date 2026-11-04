# Relay binary stream frames

The relay WebSocket (`/ws/relay/<service_id>`) carries two kinds of frames:

- **Text frames (opcode `0x01`)**: JSON control messages, as before:
  commands, results, progress, `exec_output`, `http_response` start/end,
  stream open/close, FUSE requests.
- **Binary frames (opcode `0x02`)**: the bytes of a stream, untouched.

The stream traffic below used to travel as base64 strings inside JSON text
frames. Each packet grew by a third, and was base64-encoded, serialized,
parsed and decoded again, on the relay and under the server's GIL. On top of
that, the relay masked every outgoing frame with a per-byte Python loop. For
one 100 kB desktop video frame, the round trip cost about 17 ms of CPU, which
capped a core at about 59 frames per second. With binary frames and a
whole-buffer XOR mask it costs about 0.95 ms, measured on the development
relay on 2026-11-04 with 60 frames of 100 kB each.

## Frame layout

Defined in `pawflow_relay/binary_channel.py` (stdlib only; imported by the
relay and the server):

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 1 | channel |
| 1 | 1 | inner WebSocket opcode for WS tunnels, `0` otherwise |
| 2 | 1 | length `N` of the stream id (1..255) |
| 3 | `N` | stream id, ASCII |
| 3 + `N` | rest | payload |

| Channel | Value | Direction | Stream id | Payload |
| --- | --- | --- | --- | --- |
| `CHANNEL_DESKTOP_WS` | 1 | both | desktop WS session id | one WS message payload; its opcode in byte 1 |
| `CHANNEL_DESKTOP_AUDIO` | 2 | relay -> server | audio session id | one Opus packet |
| `CHANNEL_CODE_SERVER_WS` | 3 | both | code-server WS session id | one complete on-wire WS frame, forwarded as is |
| `CHANNEL_HTTP_CHUNK` | 4 | relay -> server | command request id | one body chunk of a streamed `http_proxy` / `http_fetch` response |
| `CHANNEL_ATTACHMENT` | 5 | both | `<message key>.<index>` | one `bytes` value of the JSON message that follows |

A malformed frame or an unknown channel is logged and dropped; the
connection stays up. The relay refuses `CHANNEL_DESKTOP_AUDIO` and
`CHANNEL_HTTP_CHUNK` from the server.

## Attachments: bytes inside JSON messages

Commands, results, FUSE `relay_request` / `relay_response` messages may hold
`bytes` values anywhere in their structure: file contents (`read_file`
`content`, `read_file_chunked` / `read_chunk` `data`, `write_file` /
`atomic_write_file` / `append_file` `content`, `write_file_chunked` `data`),
FUSE `sfs.*` / `ffs.*` / `skfs.*` read and write `data`, HTTP request and
inline response bodies, and screenshots (`image`). None of them is base64.

`binary_channel.pack_message(msg)` turns a message into its frames: one
`CHANNEL_ATTACHMENT` frame per `bytes` value, with stream id
`<key>.<index>`, then the JSON text frame in which each value is replaced by
`{"$pawflow_bin": <index>}` and a top-level `"$pawflow_att": <key>` names the
set. The sender writes those frames back to back under one hold of the send
lock (`LEDGER.send_frames`, `_send_to_pool`, the server's `relay_response`
path, `ServerFsClient.request`). The receiver keeps one
`AttachmentAssembler` per connection: attachment frames wait there, and the
JSON message gets its bytes back before it is routed. Text values stay JSON.
The command ledger stores and replays a result as its list of frames.

The host helper's local protocol and the screen-action child process are
JSON lines over a local socket or a pipe: there, bytes travel as
`{"$pawflow_b64": "..."}` (`json_line_dumps` / `json_line_loads`) and are
turned back into bytes on the relay before they reach the relay link.

## Ordering

A stream's binary frames and its JSON control messages leave the same socket
from the same sender, so they arrive in order:

- Relay side, server-to-relay frames for one stream join that stream's FIFO
  in `ConnSession` (`_stream_io_queue`), the same queue as its
  `desktop_ws_close` / `cs_ws_close` command, so a close never overtakes
  the frames before it. Binary frames get no reply; only JSON commands do.
- HTTP responses: `start` and `end` stay JSON `http_response` messages, and
  the chunks between them are binary frames. All of them are sent by the
  command's thread through `LEDGER.send`.

## Senders and receivers

- Relay: `_relay_desktop.py` (desktop WS + audio), `_relay_codeserver.py`
  (code-server WS), `_relay_msg_loop.py` (HTTP chunks, inbound binary
  routing), `auth.forward_to_host_helper` (HTTP chunks streamed by the host
  helper). The host helper's local line protocol is still JSON and still
  carries chunks as base64; the relay decodes them and sends the raw bytes.
- Server: `_RelayConnMixin._dispatch_relay_binary` routes to
  `vnc_proxy.dispatch_vnc_ws_data`, `audio_proxy.dispatch_audio_data`,
  `code_server_proxy.dispatch_cs_ws_frame` and the pending request's
  `http_response` callback. `RelayHttpResponseStream` accepts only raw
  bytes. Browser-to-relay frames go out through `_relay_ws.send_to_relay`
  (opcode `0x02`, under the connection's send lock).

## Browser delivery

The server dispatches relay stream frames on the relay connection's asyncio
loop. It never writes to a browser socket there: each viewer has a
`services._browser_sink.BrowserSink` that owns the socket and one sender
thread, and the loop only appends to its queue. One slow viewer therefore
no longer stalls FUSE reads, tool results or the other viewers of the same
relay. The queue is bounded: a desktop or code-server viewer that falls
32 MiB behind (2 MiB for audio) is disconnected and reconnects, rather than
piling up memory and latency. A backend close is queued behind the frames
already waiting (`finish`), so the browser still receives them first.

## Masking

RFC 6455 requires client frames to be masked. `pawflow_relay.ws_frame.xor_mask`
XORs the whole payload with the repeated 4-byte key as one big integer. The
relay's sender and readers, the server's relay reader (`_relay_ws._ws_unmask`)
and the server's browser-side readers (VNC, audio, code-server, terminal,
ACP, realtime voice) all use it in place of a per-byte generator.

## Compatibility

There is none, by design: a server and its relays must run matching
versions. An older relay sends JSON stream messages the server no longer
handles, and an older server ignores binary frames.

Server code that receives one of these fields from an older relay (a base64
string) fails with an explicit "relay is older than this server" error
instead of decoding it.
