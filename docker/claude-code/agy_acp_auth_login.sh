#!/bin/bash
# Antigravity ACP server login entrypoint: display server + noVNC + one
# `authenticate` round trip driven by agy_acp_login.py. The server opens the
# OAuth page in the in-container Chromium and completes the redirect on its
# own loopback listener; the token lands under GEMINI_HOME/antigravity-acp/,
# which the PawFlow status action copies into the service home.

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

# The login home is the tmpfs /workspace so nothing survives the container;
# only the token files are read back by the server.
export GEMINI_HOME="/workspace/.gemini"
export AGY_ACP_AUTH_METHOD="${AGY_ACP_AUTH_METHOD:-oauth-personal}"
export AGY_ACP_LOGIN_RESULT="/tmp/agy-acp-login.result.json"
export AGY_ACP_LOGIN_STDERR="/tmp/agy-acp-login.stderr.log"
mkdir -p "$GEMINI_HOME/antigravity-acp"
rm -f "$AGY_ACP_LOGIN_RESULT"

Xvfb :99 -screen 0 1280x800x24 -ac &
export DISPLAY=:99
sleep 0.5
x11vnc -display :99 -nopw -forever -shared -rfbport 5900 &
websockify --web /usr/share/novnc --timeout=0 6080 localhost:5900 &
sleep 1

echo "[agy-acp-login] Display and noVNC ready on port 6080"
export CHROME_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage"
export CHROMIUM_FLAGS="$CHROME_FLAGS"
export BROWSER="/usr/local/bin/open-browser"

cat > /tmp/agy-acp-login-inner.sh <<'INNER'
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
export GEMINI_HOME="/workspace/.gemini"
unset NO_BROWSER CI GITHUB_ACTIONS
unset GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_GENAI_USE_VERTEXAI
unset GOOGLE_CLOUD_PROJECT GOOGLE_CLOUD_PROJECT_ID GOOGLE_CLOUD_LOCATION
echo "Starting Antigravity ACP server login (${AGY_ACP_AUTH_METHOD})..."
python3 /opt/pawflow/agy_acp_login.py
echo "Login driver exited with status $?. Waiting for PawFlow to read the token..."
sleep infinity
INNER
chmod +x /tmp/agy-acp-login-inner.sh
xterm -fa Monospace -fs 14 -bg black -fg white -e /tmp/agy-acp-login-inner.sh &
LOGIN_PID=$!

for _ in $(seq 1 300); do
  if [ -f "$AGY_ACP_LOGIN_RESULT" ]; then
    echo "[agy-acp-login] Result: $(cat "$AGY_ACP_LOGIN_RESULT")"
    break
  fi
  sleep 1
done

ls -la "$GEMINI_HOME/antigravity-acp/" 2>/dev/null || echo "[agy-acp-login] WARNING: no antigravity-acp home"

touch /tmp/auth_done
echo "[agy-acp-login] Waiting for server to read the token..."
sleep infinity
