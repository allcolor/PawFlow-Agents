#!/bin/bash
# Auth login entrypoint: starts display server + noVNC + claude auth login
# Used for server-side OAuth login via browser in Docker.
#
# noVNC serves on port 6080 (proxied by PawFlow to the webchat).
# Claude Code opens Chromium on the virtual display.
# After authorization, .credentials.json is written to $CLAUDE_CONFIG_DIR.

# Don't use set -e — keep container alive even if commands fail
# (the user needs to see the error in the VNC display)

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

# Start noVNC (WebSocket proxy VNC → port 6080, no idle timeout)
websockify --web /usr/share/novnc --timeout=0 6080 localhost:5900 &

# Wait for services
sleep 1

echo "[auth-login] Display and noVNC ready on port 6080"

# Browser wrapper is pre-installed in the image (Dockerfile)
export BROWSER=/usr/local/bin/open-browser
export XDG_UTILS_DEBUG_LEVEL=0

# Unset CI — Claude Code skips interactive auth login when CI=true
unset CI

# Remove ALL possible stale credentials so claude auth login does a fresh OAuth flow
echo "[auth-login] Clearing all credential files..."
find / -name ".credentials.json" -type f 2>/dev/null | while read f; do
  echo "[auth-login] Removing: $f"
  rm -f "$f"
done
# Also clear any cached auth/session state
rm -rf "$HOME/.claude/auth" "$HOME/.claude/statsig" "$HOME/.claude/projects" 2>/dev/null
echo "[auth-login] Done clearing"

# Launch claude auth login (it will use $BROWSER to open the auth URL)
claude auth login || true

# Find where claude wrote the new credentials
echo "[auth-login] Searching for new credentials..."
find / -name ".credentials.json" -type f -newer /proc/1/cmdline 2>/dev/null | while read f; do
  echo "[auth-login] Found: $f"
  # Copy to /workspace so the server can read it
  cp "$f" /workspace/.credentials.json 2>/dev/null || true
done
ls -la /workspace/.credentials.json 2>/dev/null || echo "[auth-login] WARNING: no .credentials.json found anywhere!"

echo "[auth-login] claude auth login completed"

# Signal completion
touch /tmp/auth_done

# Keep alive until container is killed (server reads credentials then destroys)
echo "[auth-login] Waiting for server to read credentials..."
sleep infinity
