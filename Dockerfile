FROM python:3.12-slim

WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# System deps. docker.io provides the Docker CLI used by the first-run
# bootstrap to build PawFlow's CLI and relay runtime images via the mounted
# host Docker socket. openssl is required for bootstrap TLS. Playwright/Scrapling
# need browser dependencies and Chromium inside the server image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl git docker.io openssl ffmpeg procps gosu \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Browser automation / Scrapling support.
RUN python -m playwright install --with-deps chromium

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

EXPOSE 9090

# Default: run the PawFlow listener/chat runtime.
ENTRYPOINT ["/app/docker/server-entrypoint.sh"]
CMD ["python", "cli.py", "start", "--host", "0.0.0.0", "--port", "9090"]
