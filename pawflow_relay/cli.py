"""PawFlow relay worker — CLI entry point.

The launcher script tools/pawflow_relay.py (run inside the relay Docker
container or natively on the host) calls worker_main(). Argument parsing,
OAuth auto-registration, gateway cookie acquisition and Docker-in-Docker
mode all live here so the thin script is just `from pawflow_relay.cli
import worker_main; worker_main()`.
"""

import argparse
import atexit
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

from pawflow_relay.utils import (
    docker_cmd, get_host_ip, translate_path, to_host_path,
)
from pawflow_relay.register import (
    acquire_gateway_cookie,
    auto_register,
    delete_service,
)
from pawflow_relay.worker import _ws_connect


def _argparse_auto_register(args, gateway_cookie):
    """argparse.Namespace bridge into pawflow_relay.register.auto_register."""
    ws_url, ws_token, session_token, resolved_id, login_url = auto_register(
        login_url=args.login_url,
        directory=args.dir,
        relay_id=args.relay_id,
        relay_path=args.relay_path,
        gateway_cookie=gateway_cookie,
    )
    args.relay_id = resolved_id
    return ws_url, ws_token, session_token, login_url


def worker_main():
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
                        help="Allow local screen access (disabled by default)")
    parser.add_argument("--allow-local", action="store_true",
                        help="Allow local exec — commands run on the host, not in Docker")
    parser.add_argument("--login-url", default="http://localhost:9090",
                        help="PawFlow chat UI URL for OAuth login")
    parser.add_argument("--host", default="localhost",
                        help="Host the WS listener binds to")
    parser.add_argument("--port", type=int, default=0,
                        help="Port for WS listener (0 = auto-select free port)")
    parser.add_argument("--relay-path", default="/ws/relay",
                        help="WS endpoint path")
    parser.add_argument("--no-tls", action="store_true",
                        help="Use ws:// instead of wss://")
    parser.add_argument("--docker-image", default="",
                        help="Run exec/git commands inside this Docker image")
    parser.add_argument("--docker-cpus", default=os.environ.get("PAWFLOW_RELAY_CPUS", "2"),
                        help="CPU limit for Docker containers")
    parser.add_argument("--docker-memory", default=os.environ.get("PAWFLOW_RELAY_MEMORY", "4g"),
                        help="Memory limit for Docker containers")
    parser.add_argument("--gateway-key", default=os.environ.get("PAWFLOW_GATEWAY_KEY", ""),
                        help="Private gateway access key")
    parser.add_argument("--gateway-cookie", default=os.environ.get("PAWFLOW_GATEWAY_COOKIE", ""),
                        help="Pre-acquired _pf_gw cookie value")
    parser.add_argument("--session-token", default=os.environ.get("PAWFLOW_SESSION_TOKEN", ""),
                        help="User session token / pawflow_token cookie")
    args = parser.parse_args()
    sys.stderr.write(
        f"[FSRelay] args parsed: server={bool(args.server)}, "
        f"token={bool(args.token)}, relay_id={args.relay_id!r}, "
        f"docker_image={args.docker_image!r}, "
        f"gateway_cookie={'set' if args.gateway_cookie else 'EMPTY'}, "
        f"gateway_key={'set' if args.gateway_key else 'EMPTY'}, "
        f"session_token={'set' if args.session_token else 'EMPTY'}\n")
    sys.stderr.flush()
    if _env_allow_exec:
        args.allow_exec = True

    root_dir = str(Path(args.dir).resolve())
    if not Path(root_dir).is_dir():
        sys.stderr.write(f"[Relay] Error: not a directory: {root_dir}\n")
        sys.exit(1)

    mode = "readonly" if args.readonly else "readwrite"
    gateway_cookie = args.gateway_cookie
    session_token = args.session_token
    session_id = ""
    login_url = ""
    _cleaned_up = [False]

    if args.server and args.token:
        if not args.relay_id:
            sys.stderr.write("[Relay] Error: --relay-id is required in manual mode\n")
            sys.exit(1)
        ws_url = args.server
        token = args.token
        masked = token[:2] + "*" * max(0, len(token) - 2)
    else:
        ws_url, token, session_id, login_url = _argparse_auto_register(args, gateway_cookie)
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

    # Resolve gateway cookie + session token
    if not gateway_cookie and args.gateway_key:
        # In auto-register mode, login_url is populated from the OAuth flow.
        # In manual mode (--server + --token), login_url is empty and
        # args.login_url defaults to http://localhost:9090 — which would
        # be the container itself. Derive from ws_url so the gateway POST
        # reaches the actual server instead.
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
        gateway_cookie = acquire_gateway_cookie(_gw_url, args.gateway_key)
    if not session_token and session_id:
        session_token = session_id

    def _cleanup():
        if _cleaned_up[0]:
            return
        if session_id and login_url:
            _cleaned_up[0] = True
            sys.stderr.write(f"[FSRelay] Cleaning up service '{args.relay_id}' ...\n")
            delete_service(login_url, session_id, args.relay_id,
                           gateway_cookie=gateway_cookie)
            sys.stderr.write("[FSRelay] Service deleted.\n")

    atexit.register(_cleanup)

    def _signal_handler(sig, frame):
        sys.stderr.write("\n[FSRelay] Shutting down (signal).\n")
        _cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    if args.docker_image:
        _docker_container = f"pawflow-relay-{uuid.uuid4().hex[:8]}"
        sys.stderr.write(f"[FSRelay] Starting Docker relay: {_docker_container}\n")

        docker_run_args = [
            "--rm",
            "--name", _docker_container,
            "-v", f"{translate_path(to_host_path(root_dir))}:/workspace",
        ]
        _tools_dir = os.path.dirname(os.path.abspath(__file__))
        _pkg_src = _tools_dir
        _tools_src = os.path.normpath(os.path.join(_tools_dir, os.pardir, "tools"))
        for _relay_file in ["pawflow_relay.py", "fs_actions.py", "fs_exec.py",
                            "fs_screen.py", "fs_mcp.py", "fs_common.py"]:
            _src = os.path.join(_tools_src, _relay_file)
            if os.path.exists(_src):
                docker_run_args += [
                    "-v",
                    f"{translate_path(to_host_path(_src))}:/opt/pawflow/{_relay_file}:ro",
                ]
        docker_run_args += [
            "-v",
            f"{translate_path(to_host_path(_pkg_src))}:/opt/pawflow/pawflow_relay:ro",
        ]
        if gateway_cookie:
            docker_run_args += ["-e", f"PAWFLOW_GATEWAY_COOKIE={gateway_cookie}"]
        if session_token:
            docker_run_args += ["-e", f"PAWFLOW_SESSION_TOKEN={session_token}"]
        if os.environ.get("PAWFLOW_RELAY_INSECURE") == "1":
            docker_run_args += ["-e", "PAWFLOW_RELAY_INSECURE=1"]
        docker_run_args += [
            "--add-host", "host.docker.internal:host-gateway",
            "--cpus", args.docker_cpus,
            "--memory", args.docker_memory,
            "--security-opt", "no-new-privileges",
            args.docker_image,
            "python3", "/opt/pawflow/pawflow_relay.py",
            "--server", ws_url.replace("localhost", get_host_ip()).replace("127.0.0.1", get_host_ip()),
            "--token", token,
            "--relay-id", args.relay_id,
            "--dir", "/workspace",
        ]
        cmd = docker_cmd() + ["run"] + docker_run_args
        if args.allow_exec:
            cmd.append("--allow-exec")
        if args.allow_automation:
            cmd.append("--allow-automation")
        if args.readonly:
            cmd.append("--readonly")

        sys.stderr.write("[FSRelay] Container relay connecting to server...\n")
        try:
            proc = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)

            def _cleanup_docker():
                try:
                    subprocess.run(docker_cmd() + ["rm", "-f", _docker_container],
                                   capture_output=True, timeout=10)
                except Exception:
                    pass
            atexit.register(_cleanup_docker)
            proc.wait()
        except KeyboardInterrupt:
            sys.stderr.write(f"\n[FSRelay] Stopping container: {_docker_container}\n")
            subprocess.run(docker_cmd() + ["rm", "-f", _docker_container],
                           capture_output=True, timeout=10)
        finally:
            _cleanup()
    else:
        sys.stderr.write(
            f"[FSRelay] Entering direct mode: gateway_cookie="
            f"{'set' if gateway_cookie else 'EMPTY'}, "
            f"session_token={'set' if session_token else 'EMPTY'}, "
            f"server={ws_url}\n")
        sys.stderr.flush()
        try:
            _ws_connect(ws_url, token, token, args.relay_id,
                        root_dir, args.readonly,
                        allow_exec=args.allow_exec,
                        allow_automation=args.allow_automation,
                        allow_local_screen=args.allow_local_screen,
                        allow_local=args.allow_local,
                        gateway_cookie=gateway_cookie,
                        session_token=session_token)
        finally:
            _cleanup()
