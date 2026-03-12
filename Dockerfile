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
RUN groupadd -r pyfi2 && useradd -r -g pyfi2 -d /app -s /sbin/nologin pyfi2 \
    && mkdir -p /app/flows /app/config /app/plugins /app/logs \
    && chown -R pyfi2:pyfi2 /app

USER pyfi2

# Expose ports: API (8000) + GUI (8501)
EXPOSE 8000 8501

# Default: run API server
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
