FROM node:22-slim

# System deps for Claude Code + Python for MCP bridge + display for auth login
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip git ca-certificates curl chrony \
    xvfb x11vnc novnc websockify chromium dbus-x11 xterm \
    && rm -rf /var/lib/apt/lists/*

# Install agent CLIs (Claude Code + Codex + Gemini) — single image hosts all three;
# the pool decides which binary to exec per-spawn so they share base layers + caches.
RUN npm install -g @anthropic-ai/claude-code @openai/codex @google/gemini-cli

# Create non-root user (Claude Code refuses --dangerously-skip-permissions as root)
# node:22-slim has UID 1000 as 'node' — rename to 'pawflow' for consistency
RUN if id -u node 2>/dev/null; then usermod -l pawflow -d /home/pawflow -m node && groupmod -n pawflow node; \
    elif ! id -u pawflow 2>/dev/null; then groupadd -g 1000 pawflow && useradd -u 1000 -g 1000 -m -s /bin/bash pawflow; fi

# /cc_sessions is the host CLAUDE_SESSIONS_DIR mount (set up by the pool
# at `docker run`). The per-exec mount-namespace then binds the user's
# slot `/cc_sessions/<user>` over `/cc_sessions` so CC sees its conv
# tree at `/cc_sessions/<conv>/<agent>` — the same canonical path the
# user's relay exposes via server-fs FUSE. CLAUDE_CONFIG_DIR is set
# per-exec to that conv path; the value here is just a sane default for
# diagnostics outside an exec.
WORKDIR /home/pawflow

ENV HOME="/home/pawflow"
ENV CLAUDE_CONFIG_DIR="/home/pawflow"
ENV PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ENV USER="pawflow"

# No interactive prompts
ENV CI=true

# Auth login entrypoints (server-side OAuth login via noVNC + Chromium).
# One per CLI: claude / codex / gemini. Each spawns Xvfb + x11vnc +
# websockify and drives its CLI's interactive OAuth flow, then copies
# the resulting credentials file to /workspace for the host action to pick up.
COPY auth_login.sh /opt/pawflow/auth_login.sh
COPY codex_auth_login.sh /opt/pawflow/codex_auth_login.sh
COPY gemini_auth_login.sh /opt/pawflow/gemini_auth_login.sh
RUN chmod +x /opt/pawflow/auth_login.sh /opt/pawflow/codex_auth_login.sh /opt/pawflow/gemini_auth_login.sh

# Browser wrapper for Claude Code auth login (Chromium with Docker-safe flags)
RUN printf '#!/bin/bash\nexec chromium --no-sandbox --disable-gpu --disable-dev-shm-usage --disable-software-rasterizer --window-size=1280,800 "$@"\n' \
    > /usr/local/bin/open-browser && chmod +x /usr/local/bin/open-browser
RUN mkdir -p /usr/local/share/applications && \
    printf '[Desktop Entry]\nType=Application\nName=Chromium\nExec=/usr/local/bin/open-browser %%U\nMimeType=text/html;x-scheme-handler/http;x-scheme-handler/https;\n' \
    > /usr/local/share/applications/chromium.desktop

# Python deps for MCP bridge
RUN pip3 install --break-system-packages --no-cache-dir json-repair

# MCP bridge + PawFlow SDK are NOT baked — the CC pool spawn in
# core/claude_code_pool.py dev-mounts them onto /opt/pawflow/*.py from
# the host tree, so iterating on the bridge is rebuild-free. We just
# pre-create the directory so the binds have a target.
RUN mkdir -p /opt/pawflow
ENV PYTHONPATH="/opt/pawflow"

USER pawflow
ENTRYPOINT ["claude"]
