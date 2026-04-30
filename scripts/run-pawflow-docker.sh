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
#   PAWFLOW_PORT        Host/server port (default: 9090)
#   PAWFLOW_HOST        Bind host inside container (default: 0.0.0.0)
#   PAWFLOW_EXTRA_ARGS  Extra args appended to `python cli.py start`
#
# The first PawFlow bootstrap gateway key is RoyBetty. The installer wizard
# must force the user to replace it before finalization.

set -euo pipefail

IMAGE="${PAWFLOW_IMAGE}"
PAWFLOW_HOME="${PAWFLOW_HOME}"
CONTAINER="${PAWFLOW_CONTAINER}"
PORT="${PAWFLOW_PORT}"
HOST="${PAWFLOW_HOST}"
EXTRA_ARGS="${PAWFLOW_EXTRA_ARGS}"
DOCKER_ARGS=()

mkdir -p \
  "$PAWFLOW_HOME/data" \
  "$PAWFLOW_HOME/config" \
  "$PAWFLOW_HOME/certs" \
  "$PAWFLOW_HOME/logs"

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "Container '$CONTAINER' already exists."
  echo "Start it with: docker start $CONTAINER"
  echo "Or remove it explicitly before recreating: docker rm -f $CONTAINER"
  exit 1
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

echo "Starting $CONTAINER from $IMAGE"
docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  -p "$PORT:$PORT" \
  "${DOCKER_ARGS[@]}" \
  -v "$PAWFLOW_HOME/data:/app/data" \
  -v "$PAWFLOW_HOME/config:/app/config" \
  -v "$PAWFLOW_HOME/certs:/app/certs" \
  -v "$PAWFLOW_HOME/logs:/app/logs" \
  -e PAWFLOW_BOOTSTRAP_GATEWAY_KEY="${PAWFLOW_BOOTSTRAP_GATEWAY_KEY}" \
  "$IMAGE" \
  python cli.py start --host "$HOST" --port "$PORT" $EXTRA_ARGS

cat <<MSG

PawFlow is starting.

URL:
  https://localhost:$PORT

The first run uses a self-signed bootstrap certificate, so your browser will
warn until the installer configures final certificates.

Initial bootstrap Private Gateway key:
  RoyBetty

Follow logs:
  docker logs -f $CONTAINER

MSG
