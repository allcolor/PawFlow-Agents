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
#   PAWFLOW_PUBLISH_HOST Host interface for Docker port publishing (default: 127.0.0.1)
#   PAWFLOW_EXTRA_ARGS  Extra args appended to `python cli.py start`
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
EXTRA_ARGS="$(printenv PAWFLOW_EXTRA_ARGS || true)"
BOOTSTRAP_GATEWAY_KEY="$(printenv PAWFLOW_BOOTSTRAP_GATEWAY_KEY || true)"
if [[ -z "$IMAGE" ]]; then IMAGE="ghcr.io/allcolor/pawflow:latest"; fi
if [[ -z "$PAWFLOW_HOME" ]]; then PAWFLOW_HOME="$HOME/pawflow"; fi
if [[ -z "$CONTAINER" ]]; then CONTAINER="pawflow-server"; fi
if [[ -z "$PORT" ]]; then PORT="9090"; fi
if [[ -z "$HOST" ]]; then HOST="0.0.0.0"; fi
if [[ -z "$PUBLISH_HOST" ]]; then PUBLISH_HOST="127.0.0.1"; fi
if [[ -z "$BOOTSTRAP_GATEWAY_KEY" ]]; then
  BOOTSTRAP_GATEWAY_KEY="RoyBetty"
  BOOTSTRAP_GATEWAY_LABEL="RoyBetty"
else
  BOOTSTRAP_GATEWAY_LABEL="custom value from PAWFLOW_BOOTSTRAP_GATEWAY_KEY"
fi
DOCKER_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      sed -n '1,16p' "$0"
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

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
  -p "$PUBLISH_HOST:$PORT:$PORT" \
  "${DOCKER_ARGS[@]}" \
  -v "$PAWFLOW_HOME/data:/app/data" \
  -v "$PAWFLOW_HOME/config:/app/config" \
  -v "$PAWFLOW_HOME/certs:/app/certs" \
  -v "$PAWFLOW_HOME/logs:/app/logs" \
  -e PAWFLOW_BOOTSTRAP_GATEWAY_KEY="$BOOTSTRAP_GATEWAY_KEY" \
  "$IMAGE" \
  python cli.py start --host "$HOST" --port "$PORT" $EXTRA_ARGS

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
