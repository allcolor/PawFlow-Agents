#!/usr/bin/env python3
import sys as _boot_sys
_boot_sys.stderr.write(
    f"[FSRelay] BOOT: script=__file__-will-be-set, argv[0]={_boot_sys.argv[0]!r}, "
    f"python={_boot_sys.version.split()[0]}\n")
_boot_sys.stderr.flush()
"""PawFlow Relay — Connects to the PawFlow server to provide filesystem access.

Runs on the user's machine and connects TO the server (reverse WebSocket).
Works behind firewalls/NAT. Zero external dependencies (stdlib only).

Usage (auto — default, opens browser for OAuth login):
    python pawflow_relay.py --dir /path/to/share
    python pawflow_relay.py --dir /path/to/share --allow-exec --port 9091
    python pawflow_relay.py --dir /path/to/share --login-url http://host:9090

Usage (manual — legacy):
    python pawflow_relay.py --server ws://host:port/ws/relay \\
        --relay-id localFS --token abc123 --dir /path/to/share

The relay ID is auto-generated as fs_{username}_{hash8} from username + directory,
consistent with PawCode CLI and VSCode extension.

Security:
- OAuth browser login — no plaintext passwords
- Shared secret validated via hmac.compare_digest on every request
- Path traversal prevention (resolve + startswith check)
- --readonly flag rejects write/delete operations (defense-in-depth)
- --bind 127.0.0.1 by default (local only, HTTP mode)
- All operations logged to stderr
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from fs_common import (
    _docker_cmd, _get_host_ip, _translate_path, _to_host_path,
)

# Worker body (WS reverse-tunnel protocol, HTTP handler, action dispatch)
# now lives in pawflow_relay/worker.py. The script is a thin launcher: it
# parses argv, acquires the session/gateway cookie, then calls _ws_connect
# via the two argparse↔package bridges below (_api_call, _auto_register).
from pawflow_relay.worker import _ws_connect


# HTTP + OAuth auto-registration helpers live in the package.
from pawflow_relay.register import (
    acquire_gateway_cookie as _acquire_gateway_cookie,
    agent_api_call as _agent_api_call,
    create_service as _create_service,
    delete_service as _delete_service,
    auto_register as _auto_register_impl,
)
from pawflow_relay.utils import api_call as _api_call_impl

# Module-level gateway cookie + session token, set once in main() and
# threaded through _api_call / _ws_connect.
_gateway_cookie = ""
_session_token = ""


def _api_call(api_url, method, path, body=None, session_id=""):
    """Thin wrapper that threads the module-level gateway cookie.

    Keeps the legacy (api_url, method, path, body=, session_id=) call shape
    used throughout this script while delegating to pawflow_relay.utils.
    """
    return _api_call_impl(api_url, method, path, body=body,
                          session_token=session_id,
                          gateway_cookie=_gateway_cookie)


def _auto_register(args):
    """Argparse-bridge for pawflow_relay.register.auto_register."""
    ws_url, ws_token, session_token, resolved_id, login_url = _auto_register_impl(
        login_url=args.login_url,
        directory=args.dir,
        relay_id=args.relay_id,
        relay_path=args.relay_path,
    )
    args.relay_id = resolved_id
    return ws_url, ws_token, session_token, login_url



def main():
    # Env var fallback — used when running as a server-spawned container
    _env_server = os.environ.get("PAWFLOW_RELAY_SERVER", "")
    _env_token = os.environ.get("PAWFLOW_RELAY_TOKEN", "")
    _env_relay_id = os.environ.get("PAWFLOW_RELAY_ID", "")
    _env_dir = os.environ.get("PAWFLOW_RELAY_DIR", "")
    _env_allow_exec = os.environ.get("PAWFLOW_RELAY_ALLOW_EXEC", "").lower() in ("1", "true", "yes")

    parser = argparse.ArgumentParser(
        description="PawFlow Relay — Connects to PawFlow server for filesystem access",
    )
    parser.add_argument("--server", default=_env_server,
                        help="PawFlow server WS URL (manual mode)")
    parser.add_argument("--relay-id", default=_env_relay_id,
                        help="Service ID (auto-generated from username+dir if omitted)")
    parser.add_argument("--token", default=_env_token,
                        help="Token for manual WS auth")
    parser.add_argument("--dir", required=not bool(_env_dir), default=_env_dir,
                        help="Root directory for filesystem access")
    parser.add_argument("--readonly", action="store_true",
                        help="Reject write/delete operations")
    parser.add_argument("--allow-exec", action="store_true",
                        help="Allow shell command execution (disabled by default)")
    parser.add_argument("--allow-automation", action="store_true",
                        help="Allow screen automation (screenshot, click, type — disabled by default)")
    parser.add_argument("--allow-local-screen", action="store_true",
                        help="Allow local screen access — actions execute on this machine's display (disabled by default)")
    parser.add_argument("--allow-local", action="store_true",
                        help="Allow local exec — commands run on the host, not in Docker (disabled by default)")
    # Auto-registration params
    parser.add_argument("--login-url", default="http://localhost:9090",
                        help="PawFlow chat UI URL for OAuth login (default: http://localhost:9090)")
    parser.add_argument("--host", default="localhost",
                        help="Host the WS listener binds to (default: localhost)")
    parser.add_argument("--port", type=int, default=0,
                        help="Port for WS listener (0 = auto-select free port)")
    parser.add_argument("--relay-path", default="/ws/relay",
                        help="WS endpoint path (default: /ws/relay)")
    parser.add_argument("--no-tls", action="store_true",
                        help="Use ws:// instead of wss:// (default is wss with self-signed cert)")
    parser.add_argument("--docker-image", default="",
                        help="Run exec/git commands inside this Docker image (mounts --dir as /workspace)")
    parser.add_argument("--docker-cpus", default=os.environ.get("PAWFLOW_RELAY_CPUS", "2"),
                        help="CPU limit for Docker containers (default: 2, env: PAWFLOW_RELAY_CPUS)")
    parser.add_argument("--docker-memory", default=os.environ.get("PAWFLOW_RELAY_MEMORY", "4g"),
                        help="Memory limit for Docker containers (default: 4g, env: PAWFLOW_RELAY_MEMORY)")
    parser.add_argument("--gateway-key", default=os.environ.get("PAWFLOW_GATEWAY_KEY", ""),
                        help="Private gateway access key (env: PAWFLOW_GATEWAY_KEY)")
    parser.add_argument("--gateway-cookie", default=os.environ.get("PAWFLOW_GATEWAY_COOKIE", ""),
                        help="Pre-acquired _pf_gw cookie value (env: PAWFLOW_GATEWAY_COOKIE)")
    parser.add_argument("--session-token", default=os.environ.get("PAWFLOW_SESSION_TOKEN", ""),
                        help="User session token / pawflow_token cookie (env: PAWFLOW_SESSION_TOKEN)")
    args = parser.parse_args()
    sys.stderr.write(
        f"[FSRelay] args parsed: server={bool(args.server)}, "
        f"token={bool(args.token)}, relay_id={args.relay_id!r}, "
        f"docker_image={args.docker_image!r}, "
        f"gateway_cookie={'set' if args.gateway_cookie else 'EMPTY'}, "
        f"gateway_key={'set' if args.gateway_key else 'EMPTY'}, "
        f"session_token={'set' if args.session_token else 'EMPTY'}\n")
    sys.stderr.flush()
    # Apply env var defaults that argparse store_true can't handle natively
    if _env_allow_exec:
        args.allow_exec = True

    root_dir = str(Path(args.dir).resolve())
    if not Path(root_dir).is_dir():
        sys.stderr.write(f"[Relay] Error: not a directory: {root_dir}\n")
        sys.exit(1)

    mode = "readonly" if args.readonly else "readwrite"
    session_id = ""
    login_url = ""
    _cleaned_up = False

    if args.server and args.token:
        # Manual mode (legacy) — relay_id required
        if not args.relay_id:
            sys.stderr.write("[Relay] Error: --relay-id is required in manual mode\n")
            sys.exit(1)
        ws_url = args.server
        token = args.token
        masked = token[:2] + "*" * max(0, len(token) - 2)
    else:
        # Auto-registration mode (default — opens browser for OAuth login)
        ws_url, token, session_id, login_url = _auto_register(args)
        masked = token[:4] + "****"

    sys.stderr.write(
        f"\n  PawFlow Relay\n"
        f"  ─────────────\n"
        f"  Server:    {ws_url}\n"
        f"  Relay ID:  {args.relay_id}\n"
        f"  Directory: {root_dir}\n"
        f"  Mode:      {mode}\n"
        f"  Exec:      {'enabled' if args.allow_exec else 'disabled'}\n"
        f"  Automation:{'enabled' if args.allow_automation else 'disabled'}\n"
        f"  Local scr: {'enabled' if args.allow_local_screen else 'disabled'}\n"
        f"  Local exec:{'enabled' if args.allow_local else 'disabled'}\n"
        f"  Token:     {masked}\n"
        f"  Auto-reg:  {'no (manual)' if args.server else 'yes'}\n"
        f"  Gateway:   {'cookie provided' if args.gateway_cookie else ('key provided' if args.gateway_key else 'none')}\n\n"
    )

    # Acquire / set gateway cookie and session token
    global _gateway_cookie, _session_token
    if args.gateway_cookie:
        _gateway_cookie = args.gateway_cookie
    elif args.gateway_key:
        # In auto-register mode, login_url is populated from the OAuth
        # flow. In manual mode (--server + --token), login_url is empty
        # and args.login_url defaults to http://localhost:9090 — which
        # would be the container itself. Derive from ws_url so the
        # gateway POST reaches the actual server instead.
        if login_url:
            _gw_url = login_url
        else:
            from urllib.parse import urlparse as _gw_parse
            _gw_parsed = _gw_parse(ws_url)
            _gw_scheme = "https" if _gw_parsed.scheme in ("wss", "https") else "http"
            _gw_url = f"{_gw_scheme}://{_gw_parsed.hostname}:{_gw_parsed.port or 80}"
        sys.stderr.write(
            f"[FSRelay] Acquiring gateway cookie at {_gw_url!r} "
            f"(login_url={login_url!r}, ws_url={ws_url!r})\n")
        sys.stderr.flush()
        _gateway_cookie = _acquire_gateway_cookie(_gw_url, args.gateway_key)
    if args.session_token:
        _session_token = args.session_token
    elif session_id:
        _session_token = session_id

    # Cleanup on exit (auto-registration only)
    def _cleanup():
        nonlocal _cleaned_up
        if _cleaned_up:
            return
        if session_id and login_url:
            _cleaned_up = True
            sys.stderr.write(f"[FSRelay] Cleaning up service '{args.relay_id}' ...\n")
            _delete_service(login_url, session_id, args.relay_id)
            sys.stderr.write(f"[FSRelay] Service deleted.\n")

    import atexit
    import signal
    atexit.register(_cleanup)

    def _signal_handler(sig, frame):
        sys.stderr.write("\n[FSRelay] Shutting down (signal).\n")
        _cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    if args.docker_image:
        # Docker mode: launch the relay INSIDE the container.
        # The container relay connects directly to the PawFlow server.
        # The host process just manages the container lifecycle.
        import uuid as _uuid_docker
        _docker_container = f"pawflow-relay-{_uuid_docker.uuid4().hex[:8]}"
        sys.stderr.write(f"[FSRelay] Starting Docker relay: {_docker_container}\n")

        # The container runs the relay Python script connecting to the server
        docker_run_args = [
            "--rm",
            "--name", _docker_container,
            "-v", f"{_translate_path(_to_host_path(root_dir))}:/workspace",
        ]
        # Dev mount: bind relay scripts from host so changes take effect without rebuild
        _tools_dir = os.path.dirname(os.path.abspath(__file__))
        for _relay_file in ["pawflow_relay.py", "fs_actions.py", "fs_exec.py", "fs_screen.py", "fs_mcp.py"]:
            _src = os.path.join(_tools_dir, _relay_file)
            if os.path.exists(_src):
                docker_run_args.extend(["-v", f"{_translate_path(_to_host_path(_src))}:/opt/pawflow/{_relay_file}:ro"])
        # Propagate auth cookies to the container via env (not argv, to stay out of `ps`)
        if _gateway_cookie:
            docker_run_args += ["-e", f"PAWFLOW_GATEWAY_COOKIE={_gateway_cookie}"]
        if _session_token:
            docker_run_args += ["-e", f"PAWFLOW_SESSION_TOKEN={_session_token}"]
        if os.environ.get('PAWFLOW_RELAY_INSECURE') == '1':
            docker_run_args += ["-e", "PAWFLOW_RELAY_INSECURE=1"]
        docker_run_args += [
            "--add-host", "host.docker.internal:host-gateway",
            "--cpus", args.docker_cpus,
            "--memory", args.docker_memory,
            "--security-opt", "no-new-privileges",
            args.docker_image,
            "python3", "/opt/pawflow/pawflow_relay.py",
            "--server", ws_url.replace("localhost", _get_host_ip())
                               .replace("127.0.0.1", _get_host_ip()),
            "--token", token,
            "--relay-id", args.relay_id,
            "--dir", "/workspace",
        ]
        docker_cmd = _docker_cmd() + ["run"] + docker_run_args
        if args.allow_exec:
            docker_cmd.append("--allow-exec")
        if args.allow_automation:
            docker_cmd.append("--allow-automation")
        if args.readonly:
            docker_cmd.append("--readonly")

        sys.stderr.write(f"[FSRelay] Container relay connecting to server...\n")
        try:
            # Run in foreground — blocks until container exits
            proc = subprocess.Popen(docker_cmd, stdout=sys.stdout, stderr=sys.stderr)

            def _cleanup_docker():
                try:
                    subprocess.run(_docker_cmd() + ["rm", "-f", _docker_container],
                                   capture_output=True, timeout=10)
                except Exception:
                    pass
            atexit.register(_cleanup_docker)

            proc.wait()
        except KeyboardInterrupt:
            sys.stderr.write(f"\n[FSRelay] Stopping container: {_docker_container}\n")
            subprocess.run(_docker_cmd() + ["rm", "-f", _docker_container],
                           capture_output=True, timeout=10)
        finally:
            _cleanup()
    else:
        # Direct mode: connect to server from this process
        sys.stderr.write(
            f"[FSRelay] Entering direct mode: gateway_cookie="
            f"{'set' if _gateway_cookie else 'EMPTY'}, "
            f"session_token={'set' if _session_token else 'EMPTY'}, "
            f"server={ws_url}\n")
        sys.stderr.flush()
        try:
            _ws_connect(ws_url, token, token, args.relay_id,
                         root_dir, args.readonly, allow_exec=args.allow_exec,
                         allow_automation=args.allow_automation,
                         allow_local_screen=args.allow_local_screen,
                         allow_local=args.allow_local)
        finally:
            _cleanup()


if __name__ == "__main__":
    main()
