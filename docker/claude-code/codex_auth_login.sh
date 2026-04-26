#!/bin/bash
# Codex auth login entrypoint: starts display server + noVNC + codex login
# Used for server-side OAuth login via browser in Docker.
#
# noVNC serves on port 6080 (proxied by PawFlow to the webchat).
# Codex opens Chromium on the virtual display for the OAuth PKCE dance.
# After authorization, ~/.codex/auth.json is written and copied to /workspace.

# Force clean env — Docker Desktop WSL2 injects host PATH/HOME/USER
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/home/pawflow"
export USER="pawflow"
export CODEX_HOME="$HOME/.codex"
# Work from HOME, not /workspace — codex resolves config from CODEX_HOME
cd "$HOME"

# Pre-create CODEX_HOME so the OAuth PKCE writer can drop auth.json there
mkdir -p "$CODEX_HOME"

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

echo "[codex-auth-login] Display and noVNC ready on port 6080"

# Chromium flags for Docker (no sandbox for non-root, shared memory)
export CHROME_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage"
export CHROMIUM_FLAGS="$CHROME_FLAGS"
# BROWSER hint so codex's OAuth opener picks our Chromium wrapper
export BROWSER="/usr/local/bin/open-browser"

# Clear stale credentials
rm -f "$CODEX_HOME/auth.json" 2>/dev/null

# Open xterm for debugging alongside codex login
xterm -fa Monospace -fs 14 -bg black -fg white -e bash &

# Trigger OAuth flow — codex login (no subcommand) defaults to ChatGPT OAuth
# via PKCE; opens a browser tab. Use --device-auth to fall back to the
# device-flow if the browser fails to launch on the virtual display.
codex login || true

echo "[codex-auth-login] codex login completed"

# Copy credentials to /workspace so the host-side action can pick them up.
# auth.json may live in ~/.codex/ or in the OS keyring — the keyring path
# is not viable inside Docker, so we always end up with a plaintext file.
find / -name "auth.json" -path "*/.codex/*" -type f -newer /proc/1/cmdline 2>/dev/null | while read f; do
  echo "[codex-auth-login] Found: $f"
  cp "$f" /workspace/auth.json 2>/dev/null || true
done
ls -la /workspace/auth.json 2>/dev/null || echo "[codex-auth-login] WARNING: no auth.json found"

# Signal completion
touch /tmp/auth_done

# Keep alive until container is killed
echo "[codex-auth-login] Waiting for server to read credentials..."
sleep infinity
