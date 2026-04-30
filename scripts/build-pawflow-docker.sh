#!/usr/bin/env bash
# Build the PawFlow server Docker image from the current checkout.
#
# Run from anywhere:
#   bash scripts/build-pawflow-docker.sh
#
# Environment:
#   PAWFLOW_IMAGE  Image tag to build (default: ghcr.io/allcolor/pawflow:latest)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE="${PAWFLOW_IMAGE}"

echo "Building PawFlow server image: $IMAGE"
docker build -t "$IMAGE" "$REPO_DIR"
echo "Built $IMAGE"
