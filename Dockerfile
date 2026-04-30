FROM python:3.12-slim

WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# System deps. docker.io provides the Docker CLI used by the first-run
# bootstrap to build PawFlow's CLI and relay runtime images via the mounted
# host Docker socket. openssl is required for bootstrap TLS. Playwright/Scrapling
# need browser dependencies and Chromium inside the server image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl git docker.io openssl ffmpeg procps \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Browser automation / Scrapling support.
RUN python -m playwright install --with-deps chromium

# App code
COPY . .

# Create non-root user and set ownership
RUN groupadd -r pawflow && useradd -r -g pawflow -d /app -s /sbin/nologin pawflow \
    && mkdir -p /app/flows /app/config /app/plugins /app/logs /app/data /app/certs /ms-playwright \
    && chown -R pawflow:pawflow /app /ms-playwright

USER pawflow

EXPOSE 9090

# Default: run the PawFlow listener/chat runtime.
CMD ["python", "cli.py", "start", "--host", "0.0.0.0", "--port", "9090"]
