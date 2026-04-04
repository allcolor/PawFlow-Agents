FROM node:22-slim

# System deps for Claude Code + Python for MCP bridge + display for auth login
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip git ca-certificates curl \
    xvfb x11vnc novnc websockify chromium \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Create non-root user (Claude Code refuses --dangerously-skip-permissions as root)
# node:22-slim has UID 1000 as 'node' — rename to 'pawflow' for consistency
RUN if id -u node 2>/dev/null; then usermod -l pawflow -d /home/pawflow -m node && groupmod -n pawflow node; \
    elif ! id -u pawflow 2>/dev/null; then groupadd -g 1000 pawflow && useradd -u 1000 -g 1000 -m -s /bin/bash pawflow; fi

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

# MCP bridge + PawFlow SDK (LAST — changes often, don't bust cache)
COPY mcp_bridge.py /opt/pawflow/mcp_bridge.py
COPY pawflow.py /opt/pawflow/pawflow.py
ENV PYTHONPATH="/opt/pawflow"

USER pawflow
ENTRYPOINT ["claude"]
