# ── voxl remote-desktop streamer carrier image ──────────────────────────
# Published as ghcr.io/allcolor/pawflow-voxl:<voxl version> (linux/amd64 +
# linux/arm64) by .github/workflows/voxl-image.yml, once per voxl version.
# It holds only the binary at /voxl so catalog-generated relay images can
# `COPY --from=ghcr.io/allcolor/pawflow-voxl@sha256:<digest>` it without
# compiling Rust (see docs/relay_images.md). voxl-server is published on
# crates.io only (no prebuilt binaries); build the pinned release with its
# locked dependency graph. The binary links x264, so it is distributed under
# the GPL (see THIRD_PARTY_NOTICES.md). Runtime libraries the consuming image
# must install: libx264-164 libyuv0 libopus0 libpulse0 libxcb1 libx11-xcb1.
FROM ubuntu:24.04 AS voxl-build
ARG VOXL_VERSION
ENV DEBIAN_FRONTEND=noninteractive
# CVE-2026-109518: never let APT retry a download, and upgrade APT first.
RUN echo 'Acquire::Retries "0";' > /etc/apt/apt.conf.d/00pawflow-no-retries \
    && apt-get update && apt-get install -y --no-install-recommends --only-upgrade apt \
    && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl build-essential pkg-config clang libclang-dev \
    libx264-dev libyuv-dev libopus-dev libpulse-dev libxcb1-dev nodejs npm \
    && rm -rf /var/lib/apt/lists/*
# build.rs bundles the web client with esbuild, which must be on PATH.
RUN npm install -g esbuild && esbuild --version
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
    sh -s -- -y --default-toolchain stable --profile minimal
RUN test -n "${VOXL_VERSION}" \
    && . "$HOME/.cargo/env" \
    && cargo install voxl-server --version "${VOXL_VERSION}" --locked --root /opt/voxl \
    && /opt/voxl/bin/voxl --version

FROM scratch
COPY --from=voxl-build /opt/voxl/bin/voxl /voxl
