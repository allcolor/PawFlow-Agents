#!/usr/bin/env bash
# Validate host prerequisites for installing PawFlow Server in Docker.

set -euo pipefail

PORT="$(printenv PAWFLOW_PORT || true)"
if [[ -z "$PORT" ]]; then PORT="9090"; fi
SOURCE_MODE=0
REQUIRE_SOCKET=0
FAILS=0
WARNS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --source) SOURCE_MODE=1; shift ;;
    --require-socket) REQUIRE_SOCKET=1; shift ;;
    --help|-h)
      cat <<'HELP'
Usage: bash scripts/doctor-pawflow.sh [--port 9090] [--source] [--require-socket]

Checks host prerequisites for running PawFlow Server in Docker.
- --source         Also require git for building from source.
- --require-socket Treat missing /var/run/docker.sock as a failure.
HELP
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

ok() { printf 'OK    %s\n' "$*"; }
warn() { printf 'WARN  %s\n' "$*"; WARNS=$((WARNS + 1)); }
fail() { printf 'FAIL  %s\n' "$*"; FAILS=$((FAILS + 1)); }
info() { printf 'INFO  %s\n' "$*"; }

has_cmd() { command -v "$1" >/dev/null 2>&1; }

OS="unknown"
KERNEL="$(uname -s 2>/dev/null || echo unknown)"
case "$KERNEL" in
  Linux*) OS="linux" ;;
  Darwin*) OS="macos" ;;
  MINGW*|MSYS*|CYGWIN*) OS="windows-shell" ;;
esac
if [[ "$OS" == "linux" ]] && grep -qi microsoft /proc/version 2>/dev/null; then
  OS="wsl"
fi

info "Detected host: $OS ($KERNEL)"

case "$OS" in
  linux)
    info "Linux install help: install Docker Engine from https://docs.docker.com/engine/install/, then add your user to the docker group: sudo usermod -aG docker \$USER && newgrp docker"
    ;;
  wsl)
    info "WSL install help: use WSL2, install Docker Desktop on Windows, enable WSL integration for this distro, then run this script inside WSL."
    ;;
  macos)
    info "macOS install help: install Docker Desktop from https://www.docker.com/products/docker-desktop/ and start it before running PawFlow."
    ;;
  windows-shell)
    info "Windows install help: prefer running this installer inside WSL2. Install WSL with 'wsl --install', install Docker Desktop, and enable WSL integration."
    ;;
  *)
    warn "Unknown OS. Docker must be installed and reachable; source mode also needs git."
    ;;
esac

if has_cmd docker; then
  ok "docker command found: $(command -v docker)"
else
  fail "docker command not found. Install Docker Desktop (Windows/macOS) or Docker Engine (Linux)."
fi

if has_cmd docker && docker info >/dev/null 2>&1; then
  SERVER_VERSION="$(docker info --format '{{.ServerVersion}}' 2>/dev/null || true)"
  ok "Docker daemon reachable${SERVER_VERSION}"
else
  fail "Docker daemon is not reachable. Start Docker Desktop/Engine; on Linux, verify permissions with 'docker ps'."
fi

if [[ "$OS" == "wsl" || "$OS" == "windows-shell" ]]; then
  if has_cmd wsl.exe; then
    ok "wsl.exe found"
    if wsl.exe --status >/dev/null 2>&1; then
      ok "WSL status command works"
    else
      warn "wsl.exe exists but 'wsl.exe --status' failed. Ensure WSL2 is installed and healthy."
    fi
  elif [[ "$OS" == "wsl" ]]; then
    warn "wsl.exe not visible from PATH. Docker can still work, but Windows-side WSL diagnostics are unavailable."
  else
    fail "WSL is not visible. Install WSL2 and rerun from the Linux distro shell."
  fi
fi

if [[ "$SOURCE_MODE" == "1" ]]; then
  if has_cmd git; then
    ok "git command found: $(command -v git)"
  else
    fail "git command not found. Required for --source installs. Install Git, then rerun."
  fi
else
  if has_cmd git; then
    ok "git command found (optional for image install)"
  else
    warn "git not found. Image install can continue; --source install will not work."
  fi
fi

if has_cmd curl; then
  ok "curl command found"
else
  warn "curl not found. Install curl for easier manual diagnostics; PawFlow image install can still use docker pull."
fi

if [[ -S /var/run/docker.sock ]]; then
  ok "Docker socket exists: /var/run/docker.sock"
  if [[ -r /var/run/docker.sock && -w /var/run/docker.sock ]]; then
    ok "Docker socket is readable/writable by current user"
  else
    warn "Docker socket exists but current user may not have direct rw access. The run script will add the socket group when possible."
  fi
else
  if [[ "$REQUIRE_SOCKET" == "1" ]]; then
    fail "Docker socket /var/run/docker.sock not found. Required when PawFlow bootstrap must build CLI/relay images from inside the server container."
  else
    warn "Docker socket /var/run/docker.sock not found. PawFlow can start, but first-run bootstrap cannot build CLI/relay images from inside the server container."
  fi
fi

if has_cmd python3; then
  ok "python3 found (optional for Docker image install)"
else
  warn "python3 not found on host. Not required for image install, but useful for source/debug workflows."
fi

if has_cmd nc; then
  if nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1; then
    fail "Port $PORT is already in use on 127.0.0.1. Choose another port with --port or stop the conflicting service."
  else
    ok "Port $PORT appears available"
  fi
elif has_cmd python3; then
  if python3 - "$PORT" <<'PY'
import socket, sys
port = int(sys.argv[1])
s = socket.socket()
try:
    s.bind(("127.0.0.1", port))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
  then
    ok "Port $PORT appears available"
  else
    fail "Port $PORT is already in use on 127.0.0.1. Choose another port with --port or stop the conflicting service."
  fi
else
  warn "Cannot check port $PORT because neither nc nor python3 is available."
fi

if has_cmd docker; then
  if docker system df >/dev/null 2>&1; then
    info "Docker disk usage:"
    docker system df 2>/dev/null | sed 's/^/INFO    /' || true
  else
    warn "Could not inspect Docker disk usage. Ensure Docker has enough space for PawFlow, CLI, relay and browser images."
  fi
fi

if [[ "$FAILS" -gt 0 ]]; then
  echo
  fail "Doctor found $FAILS blocking issue(s) and $WARNS warning(s)."
  exit 1
fi

echo
ok "Doctor passed with $WARNS warning(s)."
