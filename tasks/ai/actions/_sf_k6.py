"""AgentLoopTask actions  - service flow"""

import json
import logging
import time
import threading

from tasks.ai.actions._sf_base import _UNHANDLED
from tasks.ai.actions._sf_base import (
    _set_instance_config,
    _restart_running_flow_instance,
)
from tasks.ai.actions._sf_routes import (
    _ensure_terminal_routes,
    _ensure_code_server_routes,
    _publish_command_result,
)

logger = logging.getLogger(__name__)


def _handle_sf_k6(self, action, body, store, user_id, flowfile, _helpers):
    """service_flow cluster _sf_k6. Returns result or _UNHANDLED."""
    (_find_relay_svc, _audio_lookup_token, _get_server_relay_container_ip,
     _get_relay_published_port, _server_relay_proxy_target, _private_gateway_for_body) = _helpers
    if action == "update_flow_params":
        iid = body.get("instance_id", "")
        params = body.get("parameters", {})
        service_overrides = body.get("service_overrides")
        service_configs = body.get("service_configs")
        replace_parameters = bool(body.get("replace_parameters"))
        if not iid:
            flowfile.set_content(json.dumps({"error": "Missing instance_id"}).encode())
            return [flowfile]
        try:
            from core.deployment_registry import DeploymentRegistry
            dr = DeploymentRegistry.get_instance()
            inst = dr.get(iid)
            if not inst:
                flowfile.set_content(json.dumps({"error": "Instance not found"}).encode())
                return [flowfile]
            if user_id and inst.owner and inst.owner != user_id:
                flowfile.set_content(json.dumps({"error": "Permission denied"}).encode())
                return [flowfile]
            if replace_parameters:
                _set_instance_config(
                    inst, parameters=params,
                    service_overrides=service_overrides,
                    service_configs=service_configs)
            else:
                inst.parameters.update(params)
                _set_instance_config(
                    inst, service_overrides=service_overrides,
                    service_configs=service_configs)
            dr._save_instance(inst)
            restarted = _restart_running_flow_instance(iid, inst)
            flowfile.set_content(json.dumps({"ok": True, "restarted": restarted}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    # ── Terminal / code-server on relay ──────────────────────────


    if action == "open_terminal":
        relay_id = body.get("relay_id", "")
        local = body.get("local", False)
        cols = body.get("cols", 80)
        rows = body.get("rows", 24)
        shell = body.get("shell")  # None = relay default
        if not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing relay_id"}).encode())
            return [flowfile]
        try:
            svc = _find_relay_svc(relay_id)
            if not svc:
                flowfile.set_content(json.dumps({"error": f"Relay '{relay_id}' not found"}).encode())
                return [flowfile]
            _term_action = "open_local_terminal" if local else "open_terminal"
            terminal_kwargs = {"shell": shell} if shell else {}
            result = svc._request(_term_action, cols=cols, rows=rows,
                                  **terminal_kwargs)
            session_id = result.get("session_id", "") if isinstance(result, dict) else str(result)

            # Register terminal session for WS proxy
            # Both Docker and local terminals use the same relay WS path
            # (local terminal data arrives via host helper → relay → progress → dispatch)
            from services.terminal_proxy import register_terminal
            _term_token = register_terminal(
                session_id, relay_id, relay_service=svc,
                owner_user_id=user_id,
                login_session_id=flowfile.get_attribute("auth.session_id") or "")

            _ensure_terminal_routes(flowfile)

            flowfile.set_content(json.dumps({
                "ok": True,
                "session_id": session_id,
                "token": _term_token,
                "relay_id": relay_id,
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "list_cc_interactive_terminals":
        conversation_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        service_id = body.get("service_id", "") or ""
        if not conversation_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            from core.claude_code_interactive_pool import InteractiveClaudeCodePool
            sessions = InteractiveClaudeCodePool.instance().list_sessions(
                user_id, conversation_id, service_id=service_id)
            flowfile.set_content(json.dumps({"sessions": sessions}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "open_cc_interactive_terminal":
        agent_name = body.get("agent_name", "") or body.get("agent", "")
        conversation_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        service_id = body.get("service_id", "") or ""
        if not agent_name:
            flowfile.set_content(json.dumps({"error": "Missing agent_name"}).encode())
            return [flowfile]
        if not conversation_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            import uuid
            from core.claude_code_interactive_pool import InteractiveClaudeCodePool
            from core.docker_utils import docker_cmd
            from services.terminal_proxy import register_terminal

            pool = InteractiveClaudeCodePool.instance()
            # The terminal viewer must attach tmux as the SAME uid the pool
            # used to start the session (PAWFLOW_RUN_UID, not a hardcoded
            # 1000) — otherwise tmux looks in /tmp/tmux-<other-uid>/ and
            # reports "no sessions".
            user_spec = pool._user_spec()
            state = pool.find_session(
                user_id, conversation_id, agent_name, service_id=service_id)
            if not state:
                flowfile.set_content(json.dumps({
                    "error": f"No live Claude Code interactive tmux session for agent '{agent_name}'"
                }).encode())
                return [flowfile]

            session_id = f"cci_term_{uuid.uuid4().hex[:12]}"
            cols = int(body.get("cols", 120) or 120)
            rows = int(body.get("rows", 30) or 30)
            bridge_script = r'''
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time

rows = int(os.environ.get("PAWFLOW_TERM_ROWS", "30") or "30")
cols = int(os.environ.get("PAWFLOW_TERM_COLS", "120") or "120")
master, slave = pty.openpty()
try:
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
except Exception:
    pass
env = dict(os.environ)
env.setdefault("TERM", "xterm-256color")
for option in (("mouse", "on"), ("history-limit", "50000")):
    try:
        subprocess.run(["tmux", "set-option", "-g", *option],
                       capture_output=True, timeout=2)
    except Exception:
        pass
proc = subprocess.Popen(
    ["tmux", "attach-session", "-t", "pawflow"],
    stdin=slave,
    stdout=slave,
    stderr=slave,
    close_fds=True,
    start_new_session=True,
    env=env,
)
os.close(slave)
time.sleep(0.1)
try:
    os.write(master, b"\x0c")
except Exception:
    pass
stdin_fd = sys.stdin.fileno()
stdout = sys.stdout.buffer
try:
    while True:
        if proc.poll() is not None:
            try:
                data = os.read(master, 65536)
                if data:
                    stdout.write(data)
                    stdout.flush()
            except OSError:
                pass
            break
        readable, _, _ = select.select([stdin_fd, master], [], [], 0.2)
        if master in readable:
            data = os.read(master, 65536)
            if not data:
                break
            stdout.write(data)
            stdout.flush()
        if stdin_fd in readable:
            data = os.read(stdin_fd, 65536)
            if not data:
                break
            os.write(master, data)
finally:
    try:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        pass
    try:
        os.close(master)
    except Exception:
        pass
'''
            cmd = docker_cmd() + [
                "exec", "-i", "--user", user_spec,
                "-e", f"PAWFLOW_TERM_COLS={cols}",
                "-e", f"PAWFLOW_TERM_ROWS={rows}",
                "-e", "TERM=xterm-256color",
                state.name,
                "python3", "-c", bridge_script,
            ]
            _term_token = register_terminal(
                session_id, "__server__", relay_service=None,
                owner_user_id=user_id,
                conversation_id=conversation_id,
                login_session_id=flowfile.get_attribute("auth.session_id") or "",
                server_pipe_command=cmd,
                # NO resize propagation. The pawflow window is pinned to a
                # fixed size with window-size manual (see
                # claude_code_interactive_pool._start_claude_tmux), so the
                # viewer must never resize the agent's terminal: a browser
                # resize that ran `tmux resize-window -t pawflow` SIGWINCHed
                # Claude Code's Ink TUI mid-turn and corrupted the in-flight
                # capture (garbled/spliced text, phantom empty rows,
                # stuck-active). Browser resize is now a no-op; the client
                # letterboxes the fixed pane.
                server_pipe_resize_command=None)

            _ensure_terminal_routes(flowfile)

            flowfile.set_content(json.dumps({
                "ok": True,
                "session_id": session_id,
                "token": _term_token,
                "relay_id": f"cc:{agent_name}",
                "container": state.name,
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action in {"open_antigravity_interactive_terminal", "start_antigravity_observer"}:
        agent_name = body.get("agent_name", "") or body.get("agent", "")
        conversation_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        service_id = body.get("service_id", "") or ""
        if not agent_name:
            flowfile.set_content(json.dumps({"error": "Missing agent_name"}).encode())
            return [flowfile]
        if not conversation_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            import uuid
            from core.antigravity_observer_pool import AntigravityObserverPool
            from core.docker_utils import docker_cmd
            from services.terminal_proxy import register_terminal

            if not service_id:
                try:
                    from core.conv_agent_config import get_agent_config
                    service_id = (get_agent_config(conversation_id, agent_name).get("llm_service") or "")
                except Exception:
                    service_id = ""
            pool = AntigravityObserverPool.instance()
            # Attach/exec as the pool's run_uid (PAWFLOW_RUN_UID-derived), not a
            # hardcoded 1000 — otherwise the viewer lands in the wrong
            # /tmp/tmux-<uid>/ and reports 'no sessions' on deployments launched
            # under a different uid. Same contract as the CCI viewer.
            user_spec = pool._user_spec()
            state = pool.find_session(
                user_id=user_id,
                conversation_id=conversation_id,
                agent_name=agent_name,
                service_id=service_id,
            )
            if not state and service_id:
                state = pool.find_session(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    agent_name=agent_name,
                    service_id="",
                )
            if not state:
                flowfile.set_content(json.dumps({
                    "error": f"No live Antigravity tmux session for agent '{agent_name}'"
                }).encode())
                return [flowfile]

            session_id = f"agy_term_{uuid.uuid4().hex[:12]}"
            cols = int(body.get("cols", 120) or 120)
            rows = int(body.get("rows", 30) or 30)
            bridge_script = r'''
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time

rows = int(os.environ.get("PAWFLOW_TERM_ROWS", "30") or "30")
cols = int(os.environ.get("PAWFLOW_TERM_COLS", "120") or "120")
tmux_session = os.environ.get("PAWFLOW_TMUX_SESSION", "pawflow-agy")
master, slave = pty.openpty()
try:
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
except Exception:
    pass
env = dict(os.environ)
env.setdefault("TERM", "xterm-256color")
for option in (("mouse", "on"), ("history-limit", "50000")):
    try:
        subprocess.run(["tmux", "set-option", "-g", *option],
                       capture_output=True, timeout=2)
    except Exception:
        pass
proc = subprocess.Popen(
    ["tmux", "attach-session", "-t", tmux_session],
    stdin=slave,
    stdout=slave,
    stderr=slave,
    close_fds=True,
    start_new_session=True,
    env=env,
)
os.close(slave)
time.sleep(0.1)
try:
    os.write(master, b"\x0c")
except Exception:
    pass
stdin_fd = sys.stdin.fileno()
stdout = sys.stdout.buffer
try:
    while True:
        if proc.poll() is not None:
            try:
                data = os.read(master, 65536)
                if data:
                    stdout.write(data)
                    stdout.flush()
            except OSError:
                pass
            break
        readable, _, _ = select.select([stdin_fd, master], [], [], 0.2)
        if master in readable:
            data = os.read(master, 65536)
            if not data:
                break
            stdout.write(data)
            stdout.flush()
        if stdin_fd in readable:
            data = os.read(stdin_fd, 65536)
            if not data:
                break
            os.write(master, data)
finally:
    try:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        pass
    try:
        os.close(master)
    except Exception:
        pass
'''
            cmd = docker_cmd() + [
                "exec", "-i", "--user", user_spec,
                "-e", f"PAWFLOW_TERM_COLS={cols}",
                "-e", f"PAWFLOW_TERM_ROWS={rows}",
                "-e", "PAWFLOW_TMUX_SESSION=pawflow-agy",
                "-e", "TERM=xterm-256color",
                state.name,
                "python3", "-c", bridge_script,
            ]
            _term_token = register_terminal(
                session_id, "__server__", relay_service=None,
                owner_user_id=user_id,
                conversation_id=conversation_id,
                login_session_id=flowfile.get_attribute("auth.session_id") or "",
                server_pipe_command=cmd,
                # NO resize propagation: the pawflow-agy window is pinned to a
                # fixed size with window-size manual (see
                # antigravity_observer_pool), so the viewer must never resize
                # the agent's terminal. Browser resize is a no-op; the client
                # letterboxes the fixed pane. Same fix as the CCI viewer.
                server_pipe_resize_command=None)

            _ensure_terminal_routes(flowfile)

            flowfile.set_content(json.dumps({
                "ok": True,
                "session_id": session_id,
                "token": _term_token,
                "relay_id": f"agy:{agent_name}",
                "container": state.name,
                "log_path": state.log_path,
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "close_terminal":
        session_id = body.get("session_id", "")
        relay_id = body.get("relay_id", "")
        if not session_id:
            flowfile.set_content(json.dumps({"error": "Missing session_id"}).encode())
            return [flowfile]
        # Look up relay_id from terminal session if not provided
        if not relay_id:
            try:
                from services.terminal_proxy import get_terminal
                tsess = get_terminal(session_id)
                if tsess:
                    relay_id = tsess.get("relay_service_id", "")
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        if not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing relay_id"}).encode())
            return [flowfile]
        try:
            svc = _find_relay_svc(relay_id)
            if svc:
                svc._request("close_terminal", session_id=session_id)
            from services.terminal_proxy import unregister_terminal
            unregister_terminal(session_id)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "open_code_server":
        relay_id = body.get("relay_id", "")
        local = body.get("local", False)
        if not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing relay_id"}).encode())
            return [flowfile]
        try:
            svc = _find_relay_svc(relay_id)
            if not svc:
                flowfile.set_content(json.dumps({"error": f"Relay '{relay_id}' not found"}).encode())
                return [flowfile]
            # Register HTTP/WS proxy session before launch so code-server can
            # be started with the exact public base path it will be served at.
            from services.code_server_proxy import (
                register_code_server, update_code_server_port,
            )
            _cs_session_id, _cs_token = register_code_server(
                relay_id, 0, svc,
                owner_user_id=user_id,
                login_session_id=flowfile.get_attribute("auth.session_id") or "")

            _cs_action = "start_local_code_server" if local else "start_code_server"
            _base_path = f"/code/{_cs_session_id}/{_cs_token}/"
            logger.info("[open_code_server] Starting %s on relay %s", _cs_action, relay_id)
            result = svc._request(_cs_action, base_path=_base_path)
            logger.debug("[open_code_server] start_code_server result: %s", result)
            result_data = result.get("data") if isinstance(result, dict) else None
            if not isinstance(result_data, dict):
                result_data = result if isinstance(result, dict) else {}
            port = result_data.get("port")
            if not port:
                detail = result_data.get("error") or result_data.get("detail") or str(result)
                try:
                    from services.code_server_proxy import unregister_code_server
                    unregister_code_server(relay_id)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                flowfile.set_content(json.dumps({"error": f"Failed to start code-server: {detail}", "detail": str(result)}).encode())
                return [flowfile]
            update_code_server_port(
                _cs_session_id, port,
                upstream_base_path=result_data.get("upstream_base_path"))

            _ensure_code_server_routes(flowfile)

            conv_id = body.get("conversation_id", "")
            _rl = relay_id
            _pt = port
            _csid = _cs_session_id
            _ctok = _cs_token

            def _bg_wait_code():
                time.sleep(2)
                logger.info("[code-server] Ready on relay %s port %s", _rl, _pt)
                if conv_id:
                    _url = f"/code/{_csid}/{_ctok}/"
                    _publish_command_result(conv_id, {
                        "ok": True, "port": _pt, "relay_id": _rl,
                        "session_id": _csid, "token": _ctok,
                        "url": _url,
                        "message": f"Code server ready at {_url}",
                    })

            threading.Thread(target=_bg_wait_code, daemon=True).start()
            flowfile.set_content(json.dumps({
                "ok": True, "message": "Starting code server...",
                "port": port, "relay_id": relay_id,
                "session_id": _cs_session_id, "token": _cs_token,
                "url": f"/code/{_cs_session_id}/{_cs_token}/",
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    return _UNHANDLED
