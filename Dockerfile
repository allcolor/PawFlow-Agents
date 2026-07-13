FROM python:3.12-slim

WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# System deps. openssl is required for bootstrap TLS. Playwright/Scrapling
# need browser dependencies and Chromium inside the server image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash build-essential ca-certificates curl git openssl ffmpeg procps gosu tini \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI used by first-run server login to start the noVNC login desktop
# through the mounted host Docker socket. Keep this independent of distro
# packages so slim images consistently expose /usr/local/bin/docker.
ARG DOCKER_CLI_VERSION=27.5.1
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) docker_arch="x86_64" ;; \
      arm64) docker_arch="aarch64" ;; \
      *) echo "unsupported Docker CLI architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://download.docker.com/linux/static/stable/${docker_arch}/docker-${DOCKER_CLI_VERSION}.tgz" \
      | tar -xz --strip-components=1 -C /usr/local/bin docker/docker; \
    chmod +x /usr/local/bin/docker; \
    docker --version

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-cache tiktoken BPE file so token counting never needs network at runtime.
# The cl100k_base encoding (1.7 MB) is downloaded once at build time and stored
# in a path that survives bind mounts. Without this, a /tmp cache wipe + network
# failure at startup permanently degrades the context gauge to an approximate
# fallback.
ENV TIKTOKEN_CACHE_DIR=/app/data/tiktoken_cache
RUN mkdir -p /app/default-tiktoken-cache && \
    TIKTOKEN_CACHE_DIR=/app/default-tiktoken-cache \
    python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" && \
    chmod -R 755 /app/default-tiktoken-cache

# Browser automation / Scrapling support.
RUN python -m playwright install --with-deps chromium \
    && python -m patchright install chromium

# App code
COPY . .

# Defaults that must survive persistent bind mounts over /app/data and
# /app/config on first Docker boot.
RUN mkdir -p /app/default-data /app/default-config \
    && cp -a /app/data/repository /app/default-data/repository \
    && cp -a /app/config/. /app/default-config/

# Create non-root user and set ownership
RUN groupadd -g 1000 pawflow && useradd -u 1000 -g 1000 -d /app -s /bin/bash pawflow \
    && mkdir -p /app/flows /app/config /app/plugins /app/logs /app/data /app/certs /ms-playwright \
    && chmod +x /app/docker/server-entrypoint.sh \
    && chown -R pawflow:pawflow /app /ms-playwright

# Default: run the PawFlow listener/chat runtime.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/server-entrypoint.sh"]
CMD ["sh", "-lc", "test -n \"$PAWFLOW_PORT\" || { echo 'PAWFLOW_PORT is required' >&2; exit 2; }; exec python cli.py start --host 0.0.0.0 --port \"$PAWFLOW_PORT\""]
