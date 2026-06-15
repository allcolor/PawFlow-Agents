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
# The first PawFlow bootstrap gateway key is RoyBetty. The installer wizard
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
  BOOTSTRAP_GATEWAY_KEY="RoyBetty"
  BOOTSTRAP_GATEWAY_LABEL="RoyBetty"
else
  BOOTSTRAP_GATEWAY_LABEL="custom value from PAWFLOW_BOOTSTRAP_GATEWAY_KEY"
fi
if [[ -z "$RUN_UID" ]]; then RUN_UID="$(id -u)"; fi
if [[ -z "$RUN_GID" ]]; then RUN_GID="$(id -g)"; fi
if [[ -z "$SOURCE_DIR" ]]; then SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; fi
if [[ -z "$SERVER_RELAY_IMAGE" ]]; then SERVER_RELAY_IMAGE="pawflow-relay-dev:latest"; fi
if [[ -z "$SERVER_RELAY_MINIMAL_IMAGE" ]]; then SERVER_RELAY_MINIMAL_IMAGE="pawflow-relay-minimal:latest"; fi
if [[ -z "$RECREATE_CONTAINER" ]]; then RECREATE_CONTAINER="1"; fi
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
  docker rm -f "${names[@]}" >/dev/null
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

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  if [[ "$RECREATE_CONTAINER" == "1" || "$RECREATE_CONTAINER" == "true" || "$RECREATE_CONTAINER" == "yes" ]]; then
    echo "Container '$CONTAINER' already exists; recreating it with image $IMAGE while keeping persistent volumes."
    remove_managed_relay_containers
    docker rm -f "$CONTAINER" >/dev/null
  else
    echo "Container '$CONTAINER' already exists."
    echo "Start it with: docker start $CONTAINER"
    echo "Or allow in-place recreation with: PAWFLOW_RECREATE_CONTAINER=1"
    exit 1
  fi
fi

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

echo "Starting $CONTAINER from $IMAGE"
docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
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
