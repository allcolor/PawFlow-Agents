FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# Create non-root user and set ownership
RUN groupadd -r pawflow && useradd -r -g pawflow -d /app -s /sbin/nologin pawflow \
    && mkdir -p /app/flows /app/config /app/plugins /app/logs \
    && chown -R pawflow:pawflow /app

USER pawflow

EXPOSE 9090

# Default: run the PawFlow listener/chat runtime.
CMD ["python", "cli.py", "start", "--host", "0.0.0.0", "--port", "9090"]
