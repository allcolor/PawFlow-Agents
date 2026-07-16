# CUA Screen Mode — Analysis and Plan

*2026-07-16. Design analysis for `PAWFLOW_SCREEN_MODE=pawflow|cua`: routing local screen actions through [cua-driver](https://github.com/trycua/cua) (background computer-use, no cursor/focus steal) instead of pyautogui/xdotool. Analysis only — not implemented.*

## 1. Problem

PawFlow has two screen-action paths, both of which inject input at the **system** level, i.e. through the shared pointer/keyboard:

| Path | File | Mechanism | Cursor-steal exposure |
|---|---|---|---|
| Host (user's real machine, Windows/macOS) | `tools/screen_actions.py` | pyautogui via relay host helper, per-action subprocess | **Yes** — real cursor moves, focus changes |
| Container / relay desktop (Linux) | `tools/fs_screen.py` | xdotool on Xvfb (`:99`), openbox | Yes **within the shared display**: a user watching/interacting over VNC (x11vnc/noVNC) shares the one X pointer with the agent |

So the steal problem exists on both paths: on the host it disturbs the user's session; on the relay desktop it collides with the user connected over VNC.

## 2. What cua-driver is

- **Project**: `trycua/cua` — MIT, ~20k stars, Rust driver + docs at cua.ai. The same component Hermes Agent wraps for its `computer_use` toolset.
- **Interfaces**: one stdio MCP server (`cua-driver mcp`, 38 tools) — and, decisively for us, **every tool is also callable as a plain CLI**: `cua-driver <tool> '<JSON-args>'`. No MCP client plumbing is required in the relay; the subprocess-per-action pattern we already use for xdotool/pyautogui carries over unchanged.
- **Model**: two addressing rungs, chosen per action:
  - **AX rung** — `get_window_state(pid, window_id)` snapshots a window's accessibility tree (structured `elements` array + markdown + grounding screenshot); actions then target `element_index`. Works on backgrounded/minimized/off-Space windows; no cursor move, no focus steal.
  - **PX rung** — pixel coordinates, either window-local (against a window screenshot) or **desktop scope**: `get_desktop_state` (full-display native-pixel screenshot + screen size + scale factor) then `click(x, y, scope="desktop")` with no pid/window_id.
- **Sessions**: each run can declare a `session` id → its own tinted overlay cursor; concurrent sessions don't clobber each other; `launch_app(creates_new_application_instance: true)` gives per-session isolated app instances. Built for multi-agent — a natural fit for PawFlow.
- **Honesty contract**: unsupported/unsafe routes return structured refusals (`background_unavailable`, `background_occluded`) instead of silent failure. X11 delivery is toolkit-dependent; "X11 accepting an event does not prove the application handled it" is acknowledged and tested around.
- **Platform state** (per their support matrix): Windows and macOS *Supported* (UIA/`SendInput`+`PostMessage`; AX/Quartz + private SkyLight SPIs — the macOS route can break on OS updates and needs Accessibility + Screen Recording TCC grants, with `doctor`-style health reporting). Linux X11 *Supported with toolkit limits* (AT-SPI semantic background actions, XTest foreground). Wayland *compositor-specific* (GNOME/Mutter with a Shell helper, Sway proven, KWin experimental; **arbitrary raw background pixel input is not available on stock Wayland** — delivered through the seat, an occluding surface would receive it).

### Corrections to earlier internal statements

- "cua on Linux X11 = XTest = same steal problem" was **too pessimistic**: XTest is only the foreground path. Semantic background actions go through AT-SPI without touching the pointer, and unsafe routes refuse rather than steal.
- The desktop-scope PX loop (`get_desktop_state` → `click(x,y,scope="desktop")`) means our **coordinate-based vocabulary maps 1:1** — delegated vision keeps working unmodified on top of cua screenshots.

## 3. What CUA mode buys PawFlow

1. **No cursor/focus steal on the user's machine** (macOS/Windows strong; Linux honest-best-effort). The user and the agent co-work on the same desktop.
2. **Multi-agent visual identity**: per-session overlay cursors — several PawFlow agents each visibly driving with their own cursor is both genuinely useful and a demo/video asset.
3. **AX element addressing** (phase 2): more reliable than pixels on standard toolkits, works on hidden windows, and returns element trees a *text-only* model can consume directly — it composes with delegated vision instead of replacing it (vision covers canvas/WebGL/custom-drawn surfaces where AX trees are empty; the pre-click screen guard keeps verifying pixels either way).
4. **Structured refusals** instead of clicks that silently land nowhere.

## 4. Design

### 4.1 Mode switch

- `PAWFLOW_SCREEN_MODE` = `pawflow` (default, current behavior) | `cua`. Read by the relay host helper / container runtime at action-dispatch time; overridable per relay in relay config. No agent-facing change: same `screen_*` tools, same prompts.

### 4.2 Dispatch and mapping (phase 1 — drop-in, desktop scope)

Branch in `tools/screen_actions.py::handle_screen_action` (and optionally `fs_screen.py`) to a new `tools/screen_actions_cua.py` that shells out `cua-driver <tool> '<json>'` per action:

| PawFlow action | cua-driver call | Notes |
|---|---|---|
| `screen_screenshot` | `get_desktop_state --screenshot-out-file <tmp>` | Native pixels + `scale_factor` returned — matches the physical-pixel convention of screenshots and the screen guard |
| `screen_click` / `screen_double_click` | `click {x, y, scope:"desktop", button, click_count}` | Session id passed; refusal → tool error (see 4.4) |
| `screen_move` | agent-cursor move (visual) — no real pointer move exists by design | semantics change: document it |
| `screen_type` | `type_text` desktop/foreground scope | **to validate**: exact scope contract for typing without pid (docs describe pid/element paths; desktop-scope typing needs a spike) |
| `screen_key` | `press_key` | same validation as above; combos map cleanly |
| `screen_scroll` | `scroll {x, y, scope:"desktop", ...}` | background scroll has platform limits (structured refusals on some shapes) |
| `screen_mouse_position` | `get_cursor_position` | returns the *real* cursor (unused by agent flows today) |

- **Session id**: `pawflow-{agent}-{conversation}` per action batch → distinct overlay cursor per agent, isolation across concurrent conversations.
- **Screen guard unchanged**: the pre-click guard capture (`_capture_guard_region_png`, mss) is injection-path-independent and MUST keep running in cua mode.
- **Health**: a `screen_status` action wrapping `cua-driver health_report --json`; on missing binary, a clear error: install cua-driver (`curl -fsSL https://cua.ai/driver/install.sh | bash`) or unset `PAWFLOW_SCREEN_MODE`.

### 4.3 Phase 2 (optional) — AX-first tools

Expose `list_windows`, `get_window_state`, element-indexed `click`/`type_text` as new relay actions (`screen_windows`, `screen_window_state`, element params on `screen_click`). Composes with delegated vision: `get_window_state` returns both the AX tree (text — free for text-only models) and a grounding screenshot (described + content-hash-cached by the existing vision fallback). Container case: AT-SPI works inside our Xvfb desktop too, so the relay-desktop path gains element trees without any cursor contention with the VNC user.

### 4.4 Refusal policy

Structured refusals (`background_unavailable`, `background_occluded`) are surfaced to the agent as tool errors **with the reason**, never silently downgraded to foreground injection — silent fallback would defeat the mode's promise and surprise the user mid-session. A later opt-in `cua_foreground_fallback=true` relay setting can allow the foreground route explicitly.

## 5. Risks and costs

| Risk | Assessment |
|---|---|
| External dependency (Rust binary, `curl \| bash` installer, own release cadence) | MIT, healthy project (~20k stars, 3.8k commits); pin a minimum version, surface `cua-driver --version` in health |
| macOS private SkyLight SPIs | Can break on macOS updates (upstream acknowledges); their daemon/TCC model (`CuaDriver.app`, auto-delegation) adds setup friction — `doctor` output must be surfaced verbatim |
| Windows UIPI | Elevated windows can't be driven from a normal-integrity agent (OS constraint, affects every stack) |
| Wayland | No arbitrary raw background input on stock compositors; GNOME needs a Shell helper + session restart; KWin experimental. Expectation-setting in docs is mandatory |
| Security | cua-driver runs with user privileges on the user's machine; PawFlow's approval flow and screen guard remain the policy layer — cua adds no new privilege beyond what pyautogui already had |
| Latency | Accessibility-routed events ~5–20 ms per action + subprocess spawn — negligible at agent speed |
| Semantics drift | `screen_move` becomes visual-only; document in tool descriptions when mode=cua |
| Strategic | cua is the component Hermes wraps; depending on it is fine (MIT, independent project) but our differentiation stays the delegated-vision + runtime layer, not the driver |

## 6. Effort estimate

| Piece | Effort |
|---|---|
| Phase 1: mode switch + `screen_actions_cua.py` (screenshot/click/scroll mapping, session ids, health, refusal policy) | **Done 2026-07-16** — `tools/screen_actions_cua.py`, dispatch in `tools/screen_actions.py`, 14 tests (`tests/test_screen_actions_cua.py`, mocked binary). Env: `PAWFLOW_SCREEN_MODE=cua`, `PAWFLOW_CUA_BIN`, `PAWFLOW_CUA_SESSION`. New `screen_status` action wraps `health_report` |
| Spike: desktop-scope `type_text`/`press_key` contract validation on the three OSes | 1 day, gates phase 1 completion |
| Phase 2: AX-first relay actions + container AT-SPI | ~1 week, independent |
| Docs/website (desktop how-to, expectation matrix per OS) | 0.5 day |

## 7. Recommendation

Ship phase 1 behind the env var, default `pawflow`, positioned as: *“PawFlow agents get their own desktop by design (relay desktop) — and when you point them at your real machine, CUA mode drives it in the background without stealing your cursor.”* Phase 2 (AX trees) is where the real robustness gain is, and it is additive to delegated vision rather than competing with it.
