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
```

Examples:

```text
/desktop docker
/desktop local my_relay
/desktop close
```

The browser opens a noVNC session connected through PawFlow's VNC proxy.

## Runtime Supervision

Docker desktop sessions are supervised by the relay worker. After `start_desktop`,
a lightweight healthcheck thread watches the essential processes: Xvfb, x11vnc,
and websockify. If one dies, the relay clears the desktop state and terminates
remaining child processes so `desktop_status` cannot keep reporting a stale
`running=true` session.

## `screen` Tool

The `screen` tool routes through the relay. It can operate on the Docker virtual screen or the user's local screen depending on the `local` flag.

Actions:

| Action | Parameters | Purpose |
|---|---|---|
| `screenshot` | `local`, `relay` | Capture current screen to FileStore. |
| `click` | `x`, `y`, `button` | Click at physical pixel coordinates. |
| `double_click` | `x`, `y`, `button` | Double click. |
| `type` | `text` | Type text. |
| `key` | `key` | Press a key or chord such as `Enter`, `Tab`, `ctrl+c`. |
| `move` | `x`, `y` | Move the pointer. |
| `scroll` | `x`, `y`, `amount` | Scroll at a coordinate. |
| `mouse_position` | - | Read current pointer location. |

Always take a screenshot first. The result includes the screen resolution, and all coordinates are physical pixels in that screenshot coordinate space.

## VNC Proxy

PawFlow's VNC proxy relays WebSocket frames between the browser and a noVNC/websockify backend. It is used for Docker desktop sessions and server-side login containers. The proxy checks session auth and maps a session id to the backend host/port.

Related implementation:

- `services/vnc_proxy.py`
- `static/novnc/`
- `/desktop` slash command
- `core/handlers/screen.py`

## Audio Notes

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
