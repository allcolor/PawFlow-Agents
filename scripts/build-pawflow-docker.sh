#!/usr/bin/env bash
# Build the PawFlow server Docker image from the current checkout.
#
# Run from anywhere:
#   bash scripts/build-pawflow-docker.sh
#
# Environment:
#   PAWFLOW_IMAGE  Image tag to build (default: ghcr.io/allcolor/pawflow:latest)
#   PAWFLOW_DOCKER_PLATFORM  Optional docker build platform (for example linux/amd64)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE="$(printenv PAWFLOW_IMAGE || true)"
PLATFORM="$(printenv PAWFLOW_DOCKER_PLATFORM || true)"
if [[ -z "$IMAGE" ]]; then IMAGE="ghcr.io/allcolor/pawflow:latest"; fi
BUILD_ARGS=()
if [[ -n "$PLATFORM" ]]; then BUILD_ARGS+=(--platform "$PLATFORM"); fi

echo "Building PawFlow server image: $IMAGE"
docker build "${BUILD_ARGS[@]}" -t "$IMAGE" "$REPO_DIR"
echo "Built $IMAGE"
