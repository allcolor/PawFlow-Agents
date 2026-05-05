#!/usr/bin/env bash
# Install and start PawFlow server in Docker.
#
# Preferred mode pulls a published image. Source mode checks out GitHub and
# builds the image locally.
#
# Usage:
#   bash scripts/install-pawflow.sh
#   bash scripts/install-pawflow.sh --source
#   bash scripts/install-pawflow.sh --image ghcr.io/allcolor/pawflow:latest --port 9090

set -euo pipefail

IMAGE="$(printenv PAWFLOW_IMAGE || true)"
REPO_URL="$(printenv PAWFLOW_REPO_URL || true)"
INSTALL_DIR="$(printenv PAWFLOW_INSTALL_DIR || true)"
PORT="$(printenv PAWFLOW_PORT || true)"
if [[ -z "$IMAGE" ]]; then IMAGE="ghcr.io/allcolor/pawflow:latest"; fi
if [[ -z "$REPO_URL" ]]; then REPO_URL="https://github.com/allcolor/PawFlow-Agents.git"; fi
if [[ -z "$INSTALL_DIR" ]]; then INSTALL_DIR="$HOME/pawflow-src"; fi
if [[ -z "$PORT" ]]; then PORT="9090"; fi
MODE="image"
RUN_DOCTOR=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) MODE="source"; shift ;;
    --image) IMAGE="$2"; shift 2 ;;
    --repo) REPO_URL="$2"; shift 2 ;;
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --skip-doctor) RUN_DOCTOR=0; shift ;;
    --help|-h)
      sed -n '1,18p' "$0"
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

need_cmd docker
if [[ "$RUN_DOCTOR" == "1" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  DOCTOR_ARGS=("--port" "$PORT")
  if [[ "$MODE" == "source" ]]; then
    DOCTOR_ARGS+=("--source")
  fi
  bash "$SCRIPT_DIR/doctor-pawflow.sh" "${DOCTOR_ARGS[@]}"
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but the daemon is not reachable." >&2
  echo "Start Docker, or add your user to the docker group on Linux." >&2
  exit 1
fi

if [[ "$MODE" == "source" ]]; then
  need_cmd git
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    echo "Updating existing checkout: $INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only
  elif [[ -e "$INSTALL_DIR" ]]; then
    echo "Install directory exists but is not a git checkout: $INSTALL_DIR" >&2
    exit 1
  else
    echo "Cloning PawFlow: $REPO_URL -> $INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi
  PAWFLOW_IMAGE="$IMAGE" bash "$INSTALL_DIR/scripts/build-pawflow-docker.sh"
  PAWFLOW_IMAGE="$IMAGE" PAWFLOW_PORT="$PORT" bash "$INSTALL_DIR/scripts/run-pawflow-docker.sh"
else
  echo "Pulling PawFlow image: $IMAGE"
  docker pull "$IMAGE"
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  PAWFLOW_IMAGE="$IMAGE" PAWFLOW_PORT="$PORT" bash "$SCRIPT_DIR/run-pawflow-docker.sh"
fi
