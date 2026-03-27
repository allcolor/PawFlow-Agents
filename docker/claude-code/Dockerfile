FROM node:22-slim

# System deps for Claude Code + Python for MCP bridge
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# MCP bridge + PawFlow SDK (copied from host at build time)
COPY mcp_bridge.py /opt/pawflow/mcp_bridge.py
COPY pawflow.py /opt/pawflow/pawflow.py
ENV PYTHONPATH="/opt/pawflow:${PYTHONPATH}"

# Working directory for agent sessions
RUN mkdir -p /workspace && chmod 777 /workspace
WORKDIR /workspace

# Claude Code expects HOME for config
ENV HOME=/workspace
ENV CLAUDE_CONFIG_DIR=/workspace

# No interactive prompts
ENV CI=true

ENTRYPOINT ["claude"]
