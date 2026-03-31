FROM node:22-slim

# System deps for Claude Code + Python for MCP bridge + display for auth login
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip git ca-certificates curl \
    xvfb x11vnc novnc websockify chromium \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# MCP bridge + PawFlow SDK (copied from host at build time)
COPY mcp_bridge.py /opt/pawflow/mcp_bridge.py
COPY pawflow.py /opt/pawflow/pawflow.py
ENV PYTHONPATH="/opt/pawflow:${PYTHONPATH}"

# Create non-root user (Claude Code refuses --dangerously-skip-permissions as root)
# GID/UID 1000 may already exist (node image) — use existing or create
RUN (getent group 1000 || groupadd -g 1000 pawflow) && \
    (id -u 1000 2>/dev/null || useradd -u 1000 -g 1000 -m -s /bin/bash pawflow)

# Working directory for agent sessions
RUN mkdir -p /workspace && chown 1000:1000 /workspace
WORKDIR /workspace

# Claude Code expects HOME for config
ENV HOME=/home/pawflow
ENV CLAUDE_CONFIG_DIR=/workspace

# No interactive prompts
ENV CI=true

# Auth login entrypoint (used for server-side OAuth login)
COPY auth_login.sh /opt/pawflow/auth_login.sh
RUN chmod +x /opt/pawflow/auth_login.sh

USER pawflow
ENTRYPOINT ["claude"]
