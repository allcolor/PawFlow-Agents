#!/bin/bash
# Antigravity auth login entrypoint: starts display server + noVNC + agy OAuth.
# Antigravity and Gemini CLI both use the Gemini OAuth credential files, so the
# status endpoint reads the same ~/.gemini/oauth_creds.json output.

# Force clean env — Docker Desktop WSL2 injects host PATH/HOME/USER.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/home/pawflow"
export USER="pawflow"
export LANG="C.UTF-8"
export LC_ALL="C.UTF-8"
export TERM="xterm-256color"
unset NO_BROWSER CI GITHUB_ACTIONS
unset GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_GENAI_USE_VERTEXAI
unset GOOGLE_CLOUD_PROJECT GOOGLE_CLOUD_PROJECT_ID GOOGLE_CLOUD_LOCATION
cd "$HOME"

mkdir -p "$HOME/.gemini/antigravity-cli" "$HOME/.agents"
cat > "$HOME/.gemini/settings.json" 2>/dev/null <<'SETTINGS' || true
{
  "theme": "Default",
  "selectedAuthType": "oauth-personal",
  "security": {
    "auth": {
      "selectedType": "oauth-personal"
    }
  }
}
SETTINGS
cat > "$HOME/.gemini/antigravity-cli/settings.json" 2>/dev/null <<'SETTINGS' || true
{
  "enableTelemetry": false,
  "trustedWorkspaces": ["/home/pawflow"]
}
SETTINGS
export GOOGLE_GENAI_USE_GCA="true"

Xvfb :99 -screen 0 1280x800x24 -ac &
export DISPLAY=:99
sleep 0.5
x11vnc -display :99 -nopw -forever -shared -rfbport 5900 &
websockify --web /usr/share/novnc --timeout=0 6080 localhost:5900 &
sleep 1

echo "[agy-auth-login] Display and noVNC ready on port 6080"
export CHROME_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage"
export CHROMIUM_FLAGS="$CHROME_FLAGS"
export BROWSER="/usr/local/bin/open-browser"

rm -f "$HOME/.gemini/oauth_creds.json" "$HOME/.gemini/google_accounts.json" 2>/dev/null

AGY_LOG="/tmp/agy-auth.log"
: > "$AGY_LOG"
cat > /tmp/agy-login-inner.sh <<'INNER'
#!/bin/bash
set +e
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/home/pawflow"
export USER="pawflow"
export LANG="C.UTF-8"
export LC_ALL="C.UTF-8"
export TERM="xterm-256color"
export DISPLAY=":99"
export BROWSER="/usr/local/bin/open-browser"
unset NO_BROWSER CI GITHUB_ACTIONS
unset GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_GENAI_USE_VERTEXAI
unset GOOGLE_CLOUD_PROJECT GOOGLE_CLOUD_PROJECT_ID GOOGLE_CLOUD_LOCATION
export GOOGLE_GENAI_USE_GCA="true"
mkdir -p "$HOME/.gemini/antigravity-cli" "$HOME/.agents"
cat > "$HOME/.gemini/antigravity-cli/settings.json" <<'SETTINGS'
{
  "enableTelemetry": false,
  "trustedWorkspaces": ["/home/pawflow"]
}
SETTINGS

echo "Starting Antigravity OAuth login..."
echo "TTY check: stdin=$(test -t 0 && echo yes || echo no) stdout=$(test -t 1 && echo yes || echo no) NO_BROWSER=${NO_BROWSER} GEMINI_API_KEY=$(test -n "${GEMINI_API_KEY}" && echo set || echo unset) GOOGLE_GENAI_USE_GCA=${GOOGLE_GENAI_USE_GCA}"
agy --dangerously-skip-permissions
echo "Antigravity process exited. Waiting for PawFlow to read credentials..."
sleep infinity
INNER
chmod +x /tmp/agy-login-inner.sh
xterm -fa Monospace -fs 14 -bg black -fg white -e /tmp/agy-login-inner.sh &
AGY_PID=$!

for _ in $(seq 1 300); do
  for f in "$HOME/.gemini/oauth_creds.json" "$HOME/.gemini/google_accounts.json"; do
    if [ -f "$f" ]; then
      echo "[agy-auth-login] Found: $f"
      cp "$f" "/workspace/$(basename "$f")" 2>/dev/null || true
    fi
  done
  if [ -f "/workspace/oauth_creds.json" ]; then
    break
  fi
  sleep 1
done

kill "$AGY_PID" 2>/dev/null || true
wait "$AGY_PID" 2>/dev/null || true
ls -la /workspace/oauth_creds.json 2>/dev/null || echo "[agy-auth-login] WARNING: no oauth_creds.json found"

touch /tmp/auth_done
echo "[agy-auth-login] Waiting for server to read credentials..."
sleep infinity

