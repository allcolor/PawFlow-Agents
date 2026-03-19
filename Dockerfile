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
RUN groupadd -r openpaw && useradd -r -g openpaw -d /app -s /sbin/nologin openpaw \
    && mkdir -p /app/flows /app/config /app/plugins /app/logs \
    && chown -R openpaw:openpaw /app

USER openpaw

# Expose ports: API (8000) + GUI (8501)
EXPOSE 8000 8501

# Default: run API server
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
