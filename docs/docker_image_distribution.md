# Docker Image Distribution

PawFlow publishes only Docker images whose bundled software is redistributable under open-source or otherwise redistribution-compatible terms.

## Published Images

GitHub Actions publishes these public GHCR images from `.github/workflows/docker-publish.yml`:

- `ghcr.io/allcolor/pawflow` — PawFlow server image
- `ghcr.io/allcolor/pawflow-relay-minimal` — protected minimal server relay
- `ghcr.io/allcolor/pawflow-relay-dev` — full server relay workspace image

The workflow publishes `latest` from `main`, version tags from Git tags, and `main-<sha>` traceability tags. It enables BuildKit SBOM and provenance metadata for each image.

The published images currently target `linux/amd64`. Several upstream language/tool downloads remain architecture-specific, so multi-arch publishing must wait until those installers are made architecture-aware.

## Images Not Published

PawFlow does not publish the CLI LLM image:

- `pawflow-claude-code:latest`

That image is built locally by `scripts/install-pawflow.sh` because it installs Claude Code and Antigravity binaries. Claude Code is not licensed as open source, and Antigravity redistribution terms are not clear enough for PawFlow to redistribute it in a public image. Users build that image on their own machine from the Dockerfile.

## Install Behavior

`scripts/install-pawflow.sh` defaults to `auto` mode:

- Pull the prebuilt server and relay images first.
- Build any missing server or relay image from the checkout.
- Always build the CLI LLM image locally.

Use `--pull-images` to require all three public images to be available. Use `--build-images` to force source builds for the server and relay images.

The server container receives `PAWFLOW_SERVER_RELAY_IMAGE` and `PAWFLOW_SERVER_RELAY_MINIMAL_IMAGE` so server-spawned relays use the same GHCR or locally built tags that the installer prepared.

## Redistribution Notes

Published images must not include proprietary Google Chrome or Microsoft Visual Studio Code desktop builds. The full relay image uses Playwright-managed Chromium and `code-server` instead.

Before making releases public, review SBOM/provenance output and keep third-party notices available for bundled MIT, Apache-2.0, BSD, LGPL, Ubuntu/Debian, Python, npm, Go, Rust, and system packages.
