#!/bin/bash
# Auth login entrypoint: starts display server + noVNC + claude auth login
# Used for server-side OAuth login via browser in Docker.
#
# noVNC serves on port 6080 (proxied by PawFlow to the webchat).
# Claude Code opens Chromium on the virtual display.
# After authorization, .credentials.json is written to $CLAUDE_CONFIG_DIR.

set -e

# Skip Claude Code first-run interactive setup
# (theme selection, etc.) by pre-creating config
mkdir -p "$HOME/.claude"
cat > "$HOME/.claude/settings.json" 2>/dev/null <<'SETTINGS' || true
{"theme": "dark", "hasCompletedOnboarding": true}
SETTINGS

# Start virtual display
Xvfb :99 -screen 0 1280x800x24 -ac &
export DISPLAY=:99

# Wait for display
sleep 0.5

# Start VNC server (no password, shared mode)
x11vnc -display :99 -nopw -forever -shared -rfbport 5900 &

# Start noVNC (WebSocket proxy VNC → port 6080)
websockify --web /usr/share/novnc 6080 localhost:5900 &

# Wait for services
sleep 1

echo "[auth-login] Display and noVNC ready on port 6080"

# Launch claude auth login (opens Chromium on the virtual display)
# CHROME_FLAGS needed for Chromium in Docker (no sandbox, etc.)
export CHROME_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage"
export CHROMIUM_FLAGS="$CHROME_FLAGS"

claude auth login || true

echo "[auth-login] claude auth login completed"

# Signal completion
touch /tmp/auth_done

# Keep alive until container is killed (server reads credentials then destroys)
echo "[auth-login] Waiting for server to read credentials..."
sleep infinity
