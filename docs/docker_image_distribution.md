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

`scripts/install-pawflow.sh` defaults to `auto` mode. `scripts/install-pawflow.ps1`
provides the Windows PowerShell image-install/update path for Docker Desktop
Linux containers.

- Pull the prebuilt server image first.
- When the server image is available, extract installer/runtime artifacts from `/app` in that image and pull the matching relay images.
- When the server image is unavailable, use a source checkout and build the server and relay images from source.
- Always build the CLI LLM image locally.

Use `--pull-images` to require all three public images to be available. Use `--build-images` to force source builds for the server and relay images.

Image installs do not require the installer zip to carry PawFlow runtime files. The installer copies the run script, doctor script, PowerShell installer, CLI image Docker context, MCP bridge, PawFlow SDK, and relay Python package out of the pulled `ghcr.io/allcolor/pawflow:<tag>` image into `PAWFLOW_RUNTIME_DIR` or `~/.pawflow/runtime/<tag>`. Source installs keep using the checkout selected by `--dir` / `PAWFLOW_INSTALL_DIR`.

Use `--check-updates` to query the latest GitHub release and print the
recommended server update command. Use `--self-update` to refresh the installer
scripts from the latest `pawflow-install-VERSION.zip`. A versioned image update
such as `bash scripts/install-pawflow.sh --version 1.0.0.prealpha.2 --port PORT
--pull-images` recreates the server container on the new image while preserving
persistent data, then removes older PawFlow server/relay image tags unless
`--keep-old-images` is set.

Build the release zip with:

```bash
bash scripts/build-pawflow-install-zip.sh --version VERSION
```

The resulting `dist/pawflow-installers/pawflow-install-VERSION.zip` contains only `scripts/install-pawflow.sh`, `scripts/install-pawflow.ps1`, `README.md`, and `LICENSE`. After unzip, users can run `bash scripts/install-pawflow.sh --version VERSION --port PORT` or `powershell -ExecutionPolicy Bypass -File scripts/install-pawflow.ps1 -Version VERSION -Port PORT`; the remaining installer scripts and runtime bridge files are copied from the pulled server image.

## GitHub Release Assets

`.github/workflows/release-assets.yml` publishes user-facing installers to the GitHub Release attached to a pushed tag or manual `workflow_dispatch` version. It builds:

- the minimal PawFlow install zip on Linux;
- PawCode archives and native packages on Linux and Windows;
- standalone Relay CLI archives on Linux and Windows;
- Relay Desktop installers on Linux (`.AppImage`, `.deb`) and Windows (`.exe`).

To publish a release, push a version tag after the Docker image workflow can publish matching GHCR tags:

```bash
VERSION=1.0.0.prealpha.N
git tag "$VERSION"
git push origin "$VERSION"
```

The release asset workflow uses the tag name without a leading `v` for file names and installer versions. If a manual run is needed, start **Release Assets** from GitHub Actions and pass the exact version.

The server container receives `PAWFLOW_SERVER_RELAY_IMAGE` and `PAWFLOW_SERVER_RELAY_MINIMAL_IMAGE` so server-spawned relays use the same GHCR or locally built tags that the installer prepared.

## Redistribution Notes

Published images must not include proprietary Google Chrome or Microsoft Visual Studio Code desktop builds. The full relay image uses Playwright-managed Chromium and `code-server` instead.

Before making releases public, review SBOM/provenance output and keep third-party notices available for bundled MIT, Apache-2.0, BSD, LGPL, Ubuntu/Debian, Python, npm, Go, Rust, and system packages.
