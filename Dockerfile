ARG BUILDX_VERSION=0.36.1
ARG SQLITE_VERSION=3.53.4
ARG SQLITE_AUTOCONF_VERSION=3530400
ARG SQLITE_SOURCE_SHA3=454e45f61c6bd75b7420e7190732dea03ce6639c63ada47bbc592f67fc340338

FROM rust:1.89-bookworm AS search-cli-builder

ARG SEARCH_CLI_COMMIT=3ebd955e51035c53c7f8bf3c5b62be652ff441ff
RUN git clone https://github.com/paperfoot/search-cli.git /src/search-cli \
    && cd /src/search-cli \
    && git checkout --detach "$SEARCH_CLI_COMMIT" \
    && test "$(git rev-parse HEAD)" = "$SEARCH_CLI_COMMIT" \
    && cargo build --release --locked --no-default-features

FROM python:3.12-slim AS sqlite-builder

ARG SQLITE_VERSION
ARG SQLITE_AUTOCONF_VERSION
ARG SQLITE_SOURCE_SHA3
COPY scripts/check-sqlite-runtime.py /usr/local/bin/check-sqlite-runtime.py
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL \
      "https://www.sqlite.org/2026/sqlite-autoconf-${SQLITE_AUTOCONF_VERSION}.tar.gz" \
      -o /sqlite.tar.gz \
    && python /usr/local/bin/check-sqlite-runtime.py archive /sqlite.tar.gz \
      --sha3 "${SQLITE_SOURCE_SHA3}" \
    && tar -xzf /sqlite.tar.gz -C / \
    && cd "/sqlite-autoconf-${SQLITE_AUTOCONF_VERSION}" \
    && CFLAGS="-O2 -DSQLITE_ENABLE_DBSTAT_VTAB -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_MATH_FUNCTIONS -DSQLITE_ENABLE_RTREE" \
      ./configure --prefix=/usr/local --disable-static \
    && make -j"$(nproc)" \
    && make install \
    && test "$(/usr/local/bin/sqlite3 --version | cut -d' ' -f1)" = "${SQLITE_VERSION}"

FROM docker/buildx-bin:${BUILDX_VERSION} AS buildx

FROM python:3.12-slim

ARG SQLITE_VERSION

WORKDIR /app

COPY --from=sqlite-builder /usr/local/lib/libsqlite3.so* /usr/local/lib/
RUN ldconfig

COPY --from=search-cli-builder /src/search-cli/target/release/search /usr/local/bin/search
COPY --from=search-cli-builder /src/search-cli/LICENSE /usr/share/licenses/search-cli/LICENSE

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# System deps. openssl is required for bootstrap TLS. Playwright/Scrapling
# need browser dependencies and Chromium inside the server image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash build-essential ca-certificates curl git openssl ffmpeg procps gosu tini novnc \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI used through the mounted host socket. The installer-update helper
# also builds the local-only agent CLI image, so bundle Buildx: without it
# `docker build` falls back to the deprecated legacy builder and commits every
# Dockerfile instruction as a separate intermediate container.
ARG DOCKER_CLI_VERSION=27.5.1
COPY --from=buildx /buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) docker_arch="x86_64" ;; \
      arm64) docker_arch="aarch64" ;; \
      *) echo "unsupported Docker CLI architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://download.docker.com/linux/static/stable/${docker_arch}/docker-${DOCKER_CLI_VERSION}.tgz" \
      | tar -xz --strip-components=1 -C /usr/local/bin docker/docker; \
    chmod +x /usr/local/bin/docker /usr/local/libexec/docker/cli-plugins/docker-buildx; \
    docker --version; \
    docker buildx version

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

# Same reasoning for the local embedding model (all-MiniLM-L6-v2). With no
# HF_HOME the cache lands in the container's own ~/.cache, which a recreate
# throws away: every restart then re-fetched the model on the FIRST message,
# holding that message through some forty HEAD/GET round trips to
# huggingface.co and logging an unauthenticated rate-limit warning on the way.
# The weights never change, so the fetch is pure latency. /app/data is the
# persistent bind mount, so the model is downloaded once and found offline
# from then on -- core.embeddings already tries local_files_only first.
ENV HF_HOME=/app/data/hf_cache \
    HUGGINGFACE_HUB_CACHE=/app/data/hf_cache/hub \
    SENTENCE_TRANSFORMERS_HOME=/app/data/hf_cache/sentence-transformers

# Browser automation / Scrapling support.
RUN python -m playwright install --with-deps chromium \
    && python -m patchright install chromium

# App code
COPY . .
RUN python scripts/check-sqlite-runtime.py runtime --exact "${SQLITE_VERSION}"

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
