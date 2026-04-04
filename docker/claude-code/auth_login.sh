#!/bin/bash
# Auth login entrypoint: starts display server + noVNC + claude auth login
# Used for server-side OAuth login via browser in Docker.
#
# noVNC serves on port 6080 (proxied by PawFlow to the webchat).
# Claude Code opens Chromium on the virtual display.
# After authorization, .credentials.json is written to $CLAUDE_CONFIG_DIR.

# Force clean env — Docker Desktop WSL2 injects host PATH/HOME/USER
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/home/pawflow"
export USER="pawflow"
export CLAUDE_CONFIG_DIR="$HOME"
# Work from HOME, not /workspace — Node.js resolves modules from cwd
cd "$HOME"

# Skip Claude Code first-run setup
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

# Start noVNC (WebSocket proxy VNC → port 6080, no idle timeout)
websockify --web /usr/share/novnc --timeout=0 6080 localhost:5900 &

# Wait for services
sleep 1

echo "[auth-login] Display and noVNC ready on port 6080"

# Chromium flags for Docker (no sandbox for non-root, shared memory)
export CHROME_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage"
export CHROMIUM_FLAGS="$CHROME_FLAGS"

# Clear stale credentials
rm -f "$CLAUDE_CONFIG_DIR/.credentials.json" "$HOME/.claude/.credentials.json" 2>/dev/null

# Open xterm for debugging alongside claude auth login
xterm -fa Monospace -fs 14 -bg black -fg white -e bash &

claude auth login || true

echo "[auth-login] claude auth login completed"

# Copy credentials to /workspace
find / -name ".credentials.json" -type f -newer /proc/1/cmdline 2>/dev/null | while read f; do
  echo "[auth-login] Found: $f"
  cp "$f" /workspace/.credentials.json 2>/dev/null || true
done
ls -la /workspace/.credentials.json 2>/dev/null || echo "[auth-login] WARNING: no .credentials.json found"

# Signal completion
touch /tmp/auth_done

# Keep alive until container is killed
echo "[auth-login] Waiting for server to read credentials..."
sleep infinity
