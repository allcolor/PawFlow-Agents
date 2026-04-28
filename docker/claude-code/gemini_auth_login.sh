#!/bin/bash
# Gemini auth login entrypoint: starts display server + noVNC + gemini OAuth.
# Used for server-side OAuth login via browser in Docker.
#
# noVNC serves on port 6080 (proxied by PawFlow to the webchat).
# Gemini has no dedicated `gemini login` subcommand — the first interactive
# launch with `selectedAuthType=oauth-personal` triggers the browser flow.
# After authorization, ~/.gemini/oauth_creds.json is written and copied to
# /workspace.

# Force clean env — Docker Desktop WSL2 injects host PATH/HOME/USER
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/home/pawflow"
export USER="pawflow"
# Work from HOME, not /workspace — gemini reloads its OAuth creds only when
# launched from a directory where the session can resolve ~/.gemini
# (see google-gemini/gemini-cli#5474).
cd "$HOME"

# Pre-create ~/.gemini and seed settings.json with selectedAuthType so the
# first launch picks the OAuth-personal path immediately (no auth-type
# selection menu). Without this seed the CLI would prompt interactively
# for the auth type, which we can't drive headlessly.
mkdir -p "$HOME/.gemini"
cat > "$HOME/.gemini/settings.json" 2>/dev/null <<'SETTINGS' || true
{
  "theme": "Default",
  "selectedAuthType": "oauth-personal"
}
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

echo "[gemini-auth-login] Display and noVNC ready on port 6080"

# Chromium flags for Docker (no sandbox for non-root, shared memory)
export CHROME_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage"
export CHROMIUM_FLAGS="$CHROME_FLAGS"
# BROWSER hint so gemini's OAuth opener picks our Chromium wrapper
export BROWSER="/usr/local/bin/open-browser"

# Clear stale credentials
rm -f "$HOME/.gemini/oauth_creds.json" "$HOME/.gemini/google_accounts.json" 2>/dev/null

# Trigger OAuth flow. Gemini prints the Google OAuth URL to stdout; capture it
# and open that exact URL in Chromium on the noVNC display. Relying only on
# BROWSER is not enough across Gemini CLI versions: some builds leave the user
# at the terminal prompt instead of launching the browser.
GEMINI_LOG="/tmp/gemini-auth.log"
: > "$GEMINI_LOG"
(
  printf '/exit\n' | gemini 2>&1 | tee -a "$GEMINI_LOG"
) &
GEMINI_PID=$!

AUTH_URL=""
for _ in $(seq 1 120); do
  AUTH_URL=$(grep -Eo 'https://accounts\.google\.com/o/oauth2/[^[:space:]]+' "$GEMINI_LOG" | head -n 1 || true)
  if [ -n "$AUTH_URL" ]; then
    echo "[gemini-auth-login] Opening OAuth URL in Chromium"
    /usr/local/bin/open-browser "$AUTH_URL" >/tmp/gemini-browser.log 2>&1 &
    break
  fi
  if [ -f "$HOME/.gemini/oauth_creds.json" ]; then
    break
  fi
  sleep 1
done

# Keep waiting while the browser flow writes credentials. The status endpoint
# polls /workspace/oauth_creds.json, so copy as soon as files appear.
for _ in $(seq 1 300); do
  for f in "$HOME/.gemini/oauth_creds.json" "$HOME/.gemini/google_accounts.json"; do
    if [ -f "$f" ]; then
      echo "[gemini-auth-login] Found: $f"
      cp "$f" "/workspace/$(basename "$f")" 2>/dev/null || true
    fi
  done
  if [ -f "/workspace/oauth_creds.json" ]; then
    break
  fi
  sleep 1
done

kill "$GEMINI_PID" 2>/dev/null || true
wait "$GEMINI_PID" 2>/dev/null || true
ls -la /workspace/oauth_creds.json 2>/dev/null || echo "[gemini-auth-login] WARNING: no oauth_creds.json found"

# Signal completion
touch /tmp/auth_done

# Keep alive until container is killed
echo "[gemini-auth-login] Waiting for server to read credentials..."
sleep infinity
