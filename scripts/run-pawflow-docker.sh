#!/usr/bin/env bash
# Run PawFlow server from a Docker image with persistent volumes.
#
# Defaults are safe for a first local install:
#   bash scripts/run-pawflow-docker.sh
#
# Environment:
#   PAWFLOW_IMAGE       Image to run (default: ghcr.io/allcolor/pawflow:latest)
#   PAWFLOW_HOME        Persistent data directory (default: $HOME/pawflow)
#   PAWFLOW_CONTAINER   Container name (default: pawflow-server)
#   PAWFLOW_PORT        Host/server port selected during install (required)
#   PAWFLOW_HOST        Host interface for Docker port publishing (default: 0.0.0.0)
#   PAWFLOW_PUBLISH_HOST Host interface for Docker port publishing (default: PAWFLOW_HOST)
#   PAWFLOW_CONTAINER_HOST Bind host inside container (default: 0.0.0.0)
#   PAWFLOW_EXTRA_ARGS  Extra args appended to `python cli.py start`
#   PAWFLOW_BOOTSTRAP_RESET Reset first-run installer state before startup
#   PAWFLOW_RUN_UID/GID Host uid/gid used by the container process (default: current user)
#   PAWFLOW_SOURCE_DIR   Host checkout path used for CLI bridge bind mounts (default: script parent)
#   PAWFLOW_SERVER_RELAY_IMAGE Full server relay image used by PawFlow (default: pawflow-relay-dev:latest)
#   PAWFLOW_SERVER_RELAY_MINIMAL_IMAGE Minimal server relay image used by PawFlow (default: pawflow-relay-minimal:latest)
#   PAWFLOW_RECREATE_CONTAINER Recreate an existing PawFlow container in place (default: 1)
#
# The first PawFlow bootstrap gateway key is RoyBatty. The installer wizard
# must force the user to replace it before finalization.

set -euo pipefail

IMAGE="$(printenv PAWFLOW_IMAGE || true)"
PAWFLOW_HOME="$(printenv PAWFLOW_HOME || true)"
CONTAINER="$(printenv PAWFLOW_CONTAINER || true)"
PORT="$(printenv PAWFLOW_PORT || true)"
HOST="$(printenv PAWFLOW_HOST || true)"
PUBLISH_HOST="$(printenv PAWFLOW_PUBLISH_HOST || true)"
CONTAINER_HOST="$(printenv PAWFLOW_CONTAINER_HOST || true)"
NETWORK_MODE="$(printenv PAWFLOW_NETWORK_MODE || true)"
EXTRA_ARGS="$(printenv PAWFLOW_EXTRA_ARGS || true)"
BOOTSTRAP_GATEWAY_KEY="$(printenv PAWFLOW_BOOTSTRAP_GATEWAY_KEY || true)"
BOOTSTRAP_RESET="$(printenv PAWFLOW_BOOTSTRAP_RESET || true)"
RUN_UID="$(printenv PAWFLOW_RUN_UID || true)"
RUN_GID="$(printenv PAWFLOW_RUN_GID || true)"
SOURCE_DIR="$(printenv PAWFLOW_SOURCE_DIR || true)"
SERVER_RELAY_IMAGE="$(printenv PAWFLOW_SERVER_RELAY_IMAGE || true)"
SERVER_RELAY_MINIMAL_IMAGE="$(printenv PAWFLOW_SERVER_RELAY_MINIMAL_IMAGE || true)"
RECREATE_CONTAINER="$(printenv PAWFLOW_RECREATE_CONTAINER || true)"
STARTUP_HEALTH_RETRIES="$(printenv PAWFLOW_STARTUP_HEALTH_RETRIES || true)"
STARTUP_HEALTH_INTERVAL="$(printenv PAWFLOW_STARTUP_HEALTH_INTERVAL || true)"
if [[ -z "$IMAGE" ]]; then IMAGE="ghcr.io/allcolor/pawflow:latest"; fi
if [[ -z "$PAWFLOW_HOME" ]]; then PAWFLOW_HOME="$HOME/pawflow"; fi
if [[ -z "$CONTAINER" ]]; then CONTAINER="pawflow-server"; fi
if [[ -z "$HOST" ]]; then HOST="0.0.0.0"; fi
if [[ -z "$PUBLISH_HOST" ]]; then PUBLISH_HOST="$HOST"; fi
# Network mode. "host" shares the host network namespace so EVERY port the
# container opens — including the dynamic ports of deployed httpListener flows,
# which are not known in advance — is reachable on the host without explicit
# -p publishing. The in-container bind stays 0.0.0.0 so those ports are also
# reachable from sibling bridge containers (the managed relay containers connect
# back to the main listener via the host-gateway IP, which only resolves to a
# 0.0.0.0 bind). Keeping ports off the public internet is the host firewall's
# job in this mode. Host networking is the default (the installer resolves it
# per-OS — host on Linux, bridge on macOS/Windows where host networking only
# binds the Docker VM). "bridge" publishes just the main port via -p. Override
# the bind with PAWFLOW_CONTAINER_HOST (e.g. 127.0.0.1) if a front proxy is the
# only ingress.
if [[ -z "$NETWORK_MODE" ]]; then NETWORK_MODE="host"; fi
if [[ -z "$CONTAINER_HOST" ]]; then CONTAINER_HOST="0.0.0.0"; fi
if [[ -z "$BOOTSTRAP_GATEWAY_KEY" ]]; then
  BOOTSTRAP_GATEWAY_KEY="RoyBatty"
  BOOTSTRAP_GATEWAY_LABEL="RoyBatty"
else
  BOOTSTRAP_GATEWAY_LABEL="custom value from PAWFLOW_BOOTSTRAP_GATEWAY_KEY"
fi
if [[ -z "$RUN_UID" ]]; then RUN_UID="$(id -u)"; fi
if [[ -z "$RUN_GID" ]]; then RUN_GID="$(id -g)"; fi
if [[ -z "$SOURCE_DIR" ]]; then SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; fi
if [[ -z "$SERVER_RELAY_IMAGE" ]]; then SERVER_RELAY_IMAGE="pawflow-relay-dev:latest"; fi
if [[ -z "$SERVER_RELAY_MINIMAL_IMAGE" ]]; then SERVER_RELAY_MINIMAL_IMAGE="pawflow-relay-minimal:latest"; fi
if [[ -z "$RECREATE_CONTAINER" ]]; then RECREATE_CONTAINER="1"; fi
if [[ -z "$STARTUP_HEALTH_RETRIES" ]]; then STARTUP_HEALTH_RETRIES="30"; fi
if [[ -z "$STARTUP_HEALTH_INTERVAL" ]]; then STARTUP_HEALTH_INTERVAL="2"; fi
DOCKER_ARGS=()
if [[ "$NETWORK_MODE" == "host" ]]; then
  # Host networking: no -p (the app binds host interfaces directly). Every
  # listener the container opens is reachable on the host; CONTAINER_HOST
  # (default 0.0.0.0) so sibling bridge containers (managed relays) reach the
  # listener via the host-gateway IP. The host firewall gates public exposure.
  DOCKER_ARGS+=("--network" "host")
else
  DOCKER_ARGS+=("-p" "$PUBLISH_HOST:$PORT:$PORT")
fi

remove_managed_relay_containers() {
  local names=()
  mapfile -t names < <(docker ps -a --format '{{.Names}}' | grep -E '^(pawflow-relay-srv|pawflow-relay-min)' || true)
  if [[ ${#names[@]} -eq 0 ]]; then
    return 0
  fi
  echo "Removing managed PawFlow relay containers so they restart with current runtime code: ${names[*]}"
  echo "Relay home volumes and workspace directories are preserved."
  # Best-effort, never fatal. A relay wedged in the daemon ("could not kill
  # container: tried to kill container, but did not receive an exit event")
  # used to abort the whole script under `set -e` — after the new image was
  # pulled and just before the server itself was recreated, leaving the server
  # on its old image with its relays half-killed. Relays are accessory: they
  # are recreated on demand, so a failure here is reported loudly and the
  # update carries on.
  local name
  for name in "${names[@]}"; do
    if ! docker rm -f "$name" >/dev/null 2>&1; then
      echo "WARNING could not remove relay container '$name'; it keeps running old runtime code. Remove it by hand: docker rm -f $name" >&2
    fi
  done
  return 0
}

OLD_CONTAINER_BACKUP=""

restore_previous_server() {
  local reason="$1"
  echo "ERROR replacement server failed $reason; restoring the previous container configuration." >&2
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  if [[ -n "$OLD_CONTAINER_BACKUP" ]]; then
    if docker rename "$OLD_CONTAINER_BACKUP" "$CONTAINER" \
        && docker start "$CONTAINER" >/dev/null; then
      echo "Previous PawFlow server restarted as '$CONTAINER'." >&2
    else
      echo "CRITICAL could not restart previous PawFlow server '$OLD_CONTAINER_BACKUP'." >&2
      return 1
    fi
  fi
}

replacement_is_healthy() {
  local attempt=1
  while [[ "$attempt" -le "$STARTUP_HEALTH_RETRIES" ]]; do
    if docker exec -i "$CONTAINER" python - "$PORT" <<'PY'
import ssl
import sys
import urllib.request

port = sys.argv[1]
checks = (
    (f"https://127.0.0.1:{port}/health", ssl._create_unverified_context()),
    (f"http://127.0.0.1:{port}/health", None),
)
for url, context in checks:
    try:
        with urllib.request.urlopen(url, timeout=2, context=context) as response:
            if response.status == 200:
                raise SystemExit(0)
    except Exception:
        pass
raise SystemExit(1)
PY
    then
      return 0
    fi
    attempt=$((attempt + 1))
    if [[ "$attempt" -le "$STARTUP_HEALTH_RETRIES" ]]; then
      sleep "$STARTUP_HEALTH_INTERVAL"
    fi
  done
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      sed -n '1,16p' "$0"
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$PORT" ]]; then
  echo "ERROR PAWFLOW_PORT is required; pass the port selected during install." >&2
  exit 2
fi

mkdir -p \
  "$PAWFLOW_HOME/data" \
  "$PAWFLOW_HOME/config" \
  "$PAWFLOW_HOME/certs" \
  "$PAWFLOW_HOME/logs"

if [[ -S /var/run/docker.sock ]]; then
  DOCKER_ARGS+=("-v" "/var/run/docker.sock:/var/run/docker.sock")
  if command -v stat >/dev/null 2>&1; then
    DOCKER_GID="$(stat -c '%g' /var/run/docker.sock 2>/dev/null || true)"
    if [[ -n "${DOCKER_GID}" ]]; then
      DOCKER_ARGS+=("--group-add" "$DOCKER_GID")
    fi
  fi
else
  echo "Warning: /var/run/docker.sock not found; first-run bootstrap cannot build CLI/relay images from inside the PawFlow container." >&2
fi

DOCKER_CLI_CHECK="$(docker run --rm --entrypoint sh "$IMAGE" -lc 'command -v docker && docker --version' 2>&1 || true)"
if [[ "$DOCKER_CLI_CHECK" != *"Docker version"* ]]; then
  cat >&2 <<MSG
Server image '$IMAGE' does not contain the Docker CLI.

Server-side login needs the Docker client inside the PawFlow server container
to use the mounted host Docker socket and start the noVNC login desktop.

Rebuild the server image from the current checkout, then recreate the server:
  PAWFLOW_IMAGE="$IMAGE" bash scripts/build-pawflow-docker.sh
  docker rm -f "$CONTAINER"
  PAWFLOW_IMAGE="$IMAGE" PAWFLOW_PORT="$PORT" PAWFLOW_HOST="$HOST" PAWFLOW_HOME="$PAWFLOW_HOME" bash scripts/run-pawflow-docker.sh

Docker CLI check output:
$DOCKER_CLI_CHECK
MSG
  exit 1
fi

if [[ -S /var/run/docker.sock ]]; then
  DOCKER_SOCKET_CHECK="$(docker run --rm "${DOCKER_ARGS[@]}" --entrypoint sh "$IMAGE" -lc 'docker version >/dev/null' 2>&1 || true)"
  if [[ -n "$DOCKER_SOCKET_CHECK" ]]; then
    cat >&2 <<MSG
Server image '$IMAGE' contains the Docker CLI, but the PawFlow server container
cannot reach the mounted host Docker daemon.

Server-side login needs both:
  - /var/run/docker.sock mounted into the PawFlow container
  - permission for the container user to use that socket

Docker daemon check output:
$DOCKER_SOCKET_CHECK
MSG
    exit 1
  fi
fi

# Only now, with the image proved usable, is the running server destroyed.
# Everything above is read-only: it pulls nothing down and starts nothing. The
# two probes above used to run *after* this block, so an image without the
# Docker CLI -- or a socket the container cannot reach -- left the operator
# with no server at all and a message about how to rebuild one. An update that
# fails must leave the old server running.
if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  if [[ "$RECREATE_CONTAINER" == "1" || "$RECREATE_CONTAINER" == "true" || "$RECREATE_CONTAINER" == "yes" ]]; then
    echo "Container '$CONTAINER' already exists; recreating it with image $IMAGE while keeping persistent volumes."
    OLD_CONTAINER_BACKUP="${CONTAINER}-pawflow-rollback-$$"
    docker stop "$CONTAINER" >/dev/null
    if ! docker rename "$CONTAINER" "$OLD_CONTAINER_BACKUP"; then
      docker start "$CONTAINER" >/dev/null 2>&1 || true
      echo "ERROR could not preserve the existing container for rollback." >&2
      exit 1
    fi
  else
    echo "Container '$CONTAINER' already exists."
    echo "Start it with: docker start $CONTAINER"
    echo "Or allow in-place recreation with: PAWFLOW_RECREATE_CONTAINER=1"
    exit 1
  fi
fi

echo "Starting $CONTAINER from $IMAGE"
# Labels the server reads back to update itself. A `docker run` records nothing
# on its own (compose stamps its project path on every container it creates), so
# without these the server cannot tell how it was started. core/installer_
# deployment.py falls back to PAWFLOW_HOST_APP_DIR and `docker inspect` for
# containers created before these labels existed.
if docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  --label org.pawflow.deployment=installer \
  --label org.pawflow.host-app-dir="$SOURCE_DIR" \
  --label org.pawflow.home="$PAWFLOW_HOME" \
  --label org.pawflow.port="$PORT" \
  --label org.pawflow.network-mode="$NETWORK_MODE" \
  "${DOCKER_ARGS[@]}" \
  -v "$PAWFLOW_HOME/data:/app/data" \
  -v "$PAWFLOW_HOME/config:/app/config" \
  -v "$PAWFLOW_HOME/certs:/app/certs" \
  -v "$PAWFLOW_HOME/logs:/app/logs" \
  -e PAWFLOW_APP_DIR="/app" \
  -e PAWFLOW_HOST_APP_DIR="$SOURCE_DIR" \
  -e PAWFLOW_DATA_DIR="/app/data" \
  -e PAWFLOW_HOST_DATA_DIR="$PAWFLOW_HOME/data" \
  -e PAWFLOW_SERVER_RELAY_IMAGE="$SERVER_RELAY_IMAGE" \
  -e PAWFLOW_SERVER_RELAY_MINIMAL_IMAGE="$SERVER_RELAY_MINIMAL_IMAGE" \
  -e PAWFLOW_RUN_UID="$RUN_UID" \
  -e PAWFLOW_RUN_GID="$RUN_GID" \
  -e PAWFLOW_BOOTSTRAP_GATEWAY_KEY="$BOOTSTRAP_GATEWAY_KEY" \
  -e PAWFLOW_BOOTSTRAP_RESET="$BOOTSTRAP_RESET" \
  "$IMAGE" \
  python cli.py start --host "$CONTAINER_HOST" --port "$PORT" $EXTRA_ARGS
then
  :
else
  RUN_RC=$?
  restore_previous_server "during docker run" || true
  exit "$RUN_RC"
fi

if ! replacement_is_healthy; then
  restore_previous_server "its post-start health check" || true
  exit 1
fi

# The replacement has answered /health. Only now is the old configuration
# discarded and accessory relays recycled onto the new runtime.
if [[ -n "$OLD_CONTAINER_BACKUP" ]]; then
  if ! docker rm -f "$OLD_CONTAINER_BACKUP" >/dev/null 2>&1; then
    echo "WARNING could not remove rollback container '$OLD_CONTAINER_BACKUP'." >&2
  fi
fi
remove_managed_relay_containers

cat <<MSG

PawFlow is starting.

URL:
  https://localhost:$PORT

The first run uses a self-signed bootstrap certificate, so your browser will
warn until the installer configures final certificates.

Initial bootstrap Private Gateway key:
  $BOOTSTRAP_GATEWAY_LABEL

Follow logs:
  docker logs -f $CONTAINER

MSG
