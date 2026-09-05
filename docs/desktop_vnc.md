# Desktop, VNC, Screen, and Audio

PawFlow can expose and control desktops through relay-backed screen automation and noVNC. This gives agents a visual interface for GUI testing, browser workflows, desktop apps, and provider login flows.

## Desktop Modes

| Mode | Description | Typical use |
|---|---|---|
| Docker virtual desktop | A relay-owned Xvfb/desktop container opened through noVNC | Safe GUI automation, browser tests, provider login in an isolated desktop. |
| Local desktop | The relay forwards screen actions to the user's real machine | Assistive workflows where the agent must see or act on the user's actual desktop. |

Use local desktop mode only when you trust the active agent and permissions.

## Slash Command

```text
/desktop [relay_name]
/desktop local [relay]
/desktop docker [relay]
/desktop close
/desktop list
/desktop status <relay>
/desktop attach <relay>
/desktop stop [relay]
```

Examples:

```text
/desktop docker
/desktop local my_relay
/desktop close
/desktop list
/desktop stop my_relay
```

`close` detaches the local viewer tab only; the backend desktop keeps
running. `list` and `status` report the server's canonical inventory, never
the state of open browser tabs. `attach` reattaches a viewer to a running
desktop and refuses (rather than starts) when none is running. `stop` is the
only lifecycle-ending command and always goes through an explicit
confirmation against the exact current session (see below).

The browser opens a noVNC session connected through PawFlow's VNC proxy. For a
remote relay, the proxy carries VNC WebSocket frames over the relay's existing
outbound authenticated connection; the PawFlow server never needs direct TCP
reachability to the relay host or its dynamic noVNC port.
The server Docker image serves its packaged noVNC HTTP assets directly with a
private browser cache; only VNC WebSocket frames and framed Opus desktop audio
use the relay's outbound connection. If a non-Docker server installation has no
local noVNC tree, PawFlow falls back to the relay or backend asset path. For a
remote local-screen session, that fallback reads the relay runtime's bundled
noVNC tree; the host helper only runs the VNC server and WebSocket bridge.
Windows therefore does not need a separate noVNC installation. Server-managed
desktops remain on their direct Docker host/port path; the relay tunnel is
selected only for remote relay sessions.
The HTTP proxy normalizes response-header names case-insensitively so
websockify's `Content-type` value is preserved as the single canonical
`Content-Type`; HTML and JavaScript assets therefore render instead of being
treated as downloads.

## Runtime Supervision

Docker desktop sessions are supervised by the relay worker. After `start_desktop`,
a lightweight healthcheck thread watches the essential processes: Xvfb, x11vnc,
and websockify. It also performs an HTTP probe against `vnc.html`, so a live
websockify process that no longer serves noVNC is treated as unhealthy. If one
process or the noVNC HTTP probe fails, the relay clears the desktop state and
terminates remaining child processes so `desktop_status` cannot keep reporting
a stale `running=true` session. `open_desktop` repeats the server-side noVNC
probe before reusing an existing session and restarts the desktop when the
backend is not reachable from PawFlow.

## Session Identity and Inventory

Every desktop start mints a random `desktop_session_id` that lives for the
session's lifetime and is returned by `start_desktop`, `desktop_status`, and
`start_local_desktop` (host desktops carry `local_screen_session_id`).

The server keeps a canonical inventory (`services/desktop_inventory.py`)
keyed by `(relay_id, kind)` and populated only from authoritative sources:
start/stop action results and `desktop_status` probes. Open browser tabs are
never a source. An unreachable relay marks its rows `unknown` — visibly
distinct from `stopped`, because confirmation cannot currently reach the
relay.

Typed actions over the inventory (all visibility-filtered by the requesting
principal's relay visibility):

- `desktop_list_active` — list rows; `probe: true` performs a full
  status-probe reconciliation (used by the dock's open and manual refresh);
- `desktop_attach` — viewer URL for a running desktop; refuses with
  `not_running` instead of starting one;
- `desktop_stop_request` — returns the exact session a stop would target,
  with `confirm_required: true`;
- `desktop_stop_confirm` — compare-and-stop carrying the observed
  `desktop_session_id`. A stale ID returns `session_conflict` (HTTP 409) at
  the server AND at the relay (`stop_desktop` rejects a mismatched
  `session_id`), so a stale confirmation can never stop a newer session.
  Retries after a lost acknowledgement are idempotent.

State changes emit a `desktop_inventory_changed` SSE event; the Webchat
action dock shows an "Active Desktops" button with a count badge, per-row
Open/Stop, and an explicit confirmation dialog. There is no bulk stop.
Every start/attach/stop request, confirmation, conflict, and failure writes
a structured `[desktop-audit]` log line without credentials or host paths.

Relays predating the session-identity contract keep working: their desktops
simply stay out of the inventory (no `session_id` in status), and exact-
session stop is unavailable for them until the relay is updated.

Host desktops carry the same atomic compare-and-stop as Docker desktops:
`stop_local_desktop` accepts a `session_id` and answers a data conflict on
a stale one, both in the relay runtime and in the host helper, so a
restart racing the confirmation is caught at the process that owns the
session. Authorization for these actions maps the plan's
`desktop.view`/`desktop.control` onto conversation roles: list and
stop-request require `read`; attach and stop-confirm require `write`
(`_RELAY_ACTION_ROLES` in `tasks/ai/actions/service_flow.py`).

The four inventory/control actions require an explicit `conversation_id`.
Missing identity is an HTTP 400 response, never a fallback to user/global relay
visibility, so an authenticated caller cannot bypass the conversation role
check by omitting the field. At the relay, Docker and host Desktop start,
status, cleanup, and compare-and-stop operations share one re-entrant lifecycle
lock per worker. Forwarded Relay Desktop host mode has the same lock on its
host-helper instance, whose request connections otherwise run concurrently.
This makes the session-ID comparison and the resulting stop atomic with respect
to a concurrent restart while leaving non-Desktop relay commands parallel.

### Events that never stop a desktop

Closing a noVNC tab, viewer count reaching zero, browser or SSE disconnect,
conversation inactivity, the agent turn ending, or a Webchat reload never
stop a healthy desktop. The relay watchdog cleans up only sessions whose
essential processes or noVNC probe FAIL — that is failure handling, not an
idle policy — and a failed desktop is not restarted without a new explicit
start.

## `screen` Tool

The `screen` tool routes through the relay. It can operate on the Docker virtual screen or the user's local screen depending on the `local` flag.

Actions:

| Action | Parameters | Purpose |
|---|---|---|
| `screenshot` | `local`, `relay` | Capture current screen to FileStore and return an opaque screen revision. |
| `click` | `x`, `y`, `button`, `expected_screen_revision`, optional `target_bbox` | Click only if the target region still matches the referenced screenshot. |
| `double_click` | `x`, `y`, `button`, `expected_screen_revision`, optional `target_bbox` | Double click only if the target region is unchanged. |
| `type` | `text` | Type text. |
| `key` | `key` | Press a key or chord such as `Enter`, `Tab`, `ctrl+c`. |
| `move` | `x`, `y` | Move the pointer. |
| `scroll` | `x`, `y`, `amount` | Scroll at a coordinate. |
| `mouse_position` | - | Read current pointer location. |
| `status` | - | Screen backend health report (`pawflow` or `cua` mode, driver health). |
| `windows` | - | List windows with `pid`/`window_id`/title (CUA backend only). |
| `window_state` | `pid` and/or `window_id` | Accessibility-tree snapshot of one window: element list with indexes plus a grounding screenshot (CUA backend only). |

Always take a screenshot first. The result includes the screen resolution and an opaque `screen revision`; all coordinates are physical pixels in that screenshot coordinate space. Do not derive click positions from the resized screenshot preview rendered inside the chat; use the returned resolution, for example `2560x1440`, as the coordinate space for `x` and `y`.

`click` and `double_click` require the exact revision associated with the image used to select the coordinates. PawFlow resolves that revision server-side, extracts a small reference crop around `target_bbox` (or around `x,y` when no box is supplied), and sends the crop privately to the relay. Immediately before any mouse movement, the relay captures the same region and compares the two images locally. A changed region returns `STALE_SCREEN` and performs no input; an unchanged region proceeds. This optimistic check does not call the primary or vision LLM and adds no second image-token charge; only the small opaque revision travels in the normal tool exchange. Only a rejected stale action requires the agent to inspect a new screenshot.

Use `see(path="screen", local=true)` or `see(path="screenshot", local=true)` when the agent needs to inspect the real desktop through the multimodal vision path. The shortcut accepts the relay screenshot format `{image, width, height}` and includes both the screen revision and the same physical-pixel coordinate hint before the image payload. The original full-resolution capture is retained for the guard even when the image sent to the model is resized.

### CUA screen mode (background computer use)

Set `PAWFLOW_SCREEN_MODE=cua` on the relay (host helper and/or container) to route screen actions through [cua-driver](https://github.com/trycua/cua) instead of pyautogui/xdotool. Desktop-capable relay images bundle the binary; on a host desktop install it with `curl -fsSL https://cua.ai/driver/install.sh | bash`. `PAWFLOW_CUA_BIN` overrides the binary path, `PAWFLOW_CUA_SESSION` names the overlay-cursor session (one tinted agent cursor per session — several agents can drive the same desktop visibly and independently).

Relay Desktop packages, generated relay-image runtimes, and development mounts
ship `screen_actions.py` together with `screen_actions_cua.py`. The default
`pawflow` mode does not import the CUA backend; selecting `cua` requires the
packaged backend module and never falls back silently to foreground input.

What changes in CUA mode:

- The real OS cursor never moves and focus is never stolen; `move` becomes an honest no-op.
- Coordinate actions (`click`, `scroll`, `type`, `key`) go through cua-driver desktop scope; the pre-click screen guard keeps running unchanged.
- AX addressing becomes available: `windows` lists windows, `window_state` returns one window's accessibility-element tree plus a grounding screenshot, and `click`/`double_click`/`type` accept `element_index` (with `pid`/`window_id`) to act on an element by identity — including backgrounded or minimized windows. Element actions need no coordinates and no `expected_screen_revision`; a stale `element_index` returns a structured driver error instead of clicking blind.
- Unsupported routes surface structured refusals (`background_unavailable`, `background_occluded`) verbatim — there is no silent fallback to foreground input injection.
- `window_state` grounding screenshots flow through the normal vision path, so delegated vision (text-only models with a `vision_llm_service`) keeps working; the AX element tree itself is plain text and free for any model.

Platform expectations: Windows and macOS are supported by cua-driver (macOS needs Accessibility + Screen Recording permissions and can break on OS updates — check `status`); Linux X11 works with toolkit limits (AT-SPI for background semantic actions); stock Wayland cannot deliver arbitrary background pixel input. Inside the relay container the Xvfb desktop supports AT-SPI, so `windows`/`window_state` work there without any pointer contention with a user connected over VNC. See `docs/CUA_MODE_PLAN.md` for the full design.

## VNC Proxy

PawFlow's VNC proxy relays WebSocket frames between the browser and a
noVNC/websockify backend. Directly reachable server-managed desktops use the
backend host/port path. Remote Docker and local-host desktops use the relay
WebSocket tunnel (`desktop_ws_open`, `desktop_ws_send`, and
`desktop_ws_close`), so NAT and host firewalls do not need to expose noVNC.
When the desktop mirrors the relay host screen, the relay worker connects to
the host address already advertised by `PAWFLOW_HOST_HELPER`; containerized
desktops continue to use the worker's loopback interface.
The proxy checks session auth before either transport is opened.

Desktop, service-login, and installer viewers pass an origin-rooted noVNC
`path` setting (`/vnc/{session_id}/{token}/websockify`). noVNC resolves that
setting against the viewer page URL; omitting the leading slash repeats the
session directory and sends the WebSocket handshake to an invalid route.

The proxied noVNC page also injects a small PawFlow bridge for native desktop ergonomics. Browser clipboard reads and writes are connected to noVNC clipboard events so ordinary OS copy/paste shortcuts work in the remote desktop without a separate PawFlow clipboard panel. Docker virtual desktops start `autocutsel` when available to keep X11 `CLIPBOARD` and `PRIMARY` selections synchronized with desktop applications. The same bridge handles repeated keydown events for repeatable keys such as Backspace so holding the key behaves like a local desktop session. Chromium's benign ResizeObserver loop notifications are intercepted before noVNC's fatal error handler, preventing a permanent red status overlay while the live desktop continues normally.

Related implementation:

- `services/vnc_proxy.py`
- Debian's `novnc` package (`/usr/share/novnc`)
- `/desktop` slash command
- `core/handlers/screen.py`
- `core/handlers/_screen_guard.py`
- `tools/fs_screen.py`
- `tools/screen_actions.py`

## Audio Notes

Remote desktop audio is packetized at the relay and forwarded over the
authenticated relay WebSocket. A server-managed relay instead keeps the direct
TCP audio path to its published Docker port.

Docker desktop audio can be affected by host clock drift, especially on WSL2. If audio plays too fast or too slow relative to video, install and run `chrony` in the WSL2 distro so the Linux clock stays synced with the Windows host.

```bash
sudo apt install -y chrony
chronyc tracking
```

See [Docker](docker.md) for the WSL2 audio sync details.

## Security Guidance

- Prefer Docker desktop for untrusted tasks.
- Treat local desktop control as high privilege.
- Use approval modes for click/type/key actions where possible.
- Avoid exposing noVNC endpoints without PawFlow auth and TLS.
- Do not run sensitive interactive sessions in a shared conversation.
