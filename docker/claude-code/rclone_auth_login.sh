#!/bin/bash
# Rclone auth login entrypoint: starts display server + noVNC + rclone OAuth.
# Used for server-side OAuth login via browser in Docker.
#
# noVNC serves on port 6080 (proxied by PawFlow to the webchat). Rclone opens
# Chromium on the virtual display for OAuth backends such as Google Drive and
# OneDrive, then writes the resulting remote config body to a writable temp dir.

set +e

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/home/pawflow"
export USER="pawflow"
export LANG="C.UTF-8"
export LC_ALL="C.UTF-8"
export TERM="xterm-256color"
unset NO_BROWSER CI GITHUB_ACTIONS
cd "$HOME"

BACKEND="$PAWFLOW_RCLONE_TYPE"
if [ -z "$BACKEND" ]; then
  BACKEND="drive"
fi
REMOTE_NAME="pawflow_remote"
OUTPUT_DIR="/tmp/pawflow-rclone-login"
CONFIG_FILE="$OUTPUT_DIR/rclone.conf"
BODY_FILE="$OUTPUT_DIR/rclone_config_body.txt"
ERROR_FILE="$OUTPUT_DIR/rclone_error.txt"

mkdir -p "$OUTPUT_DIR"
chmod 700 "$OUTPUT_DIR" 2>/dev/null || true
rm -f "$CONFIG_FILE" "$BODY_FILE" "$ERROR_FILE" 2>/dev/null || true

Xvfb :99 -screen 0 1280x800x24 -ac &
export DISPLAY=:99
sleep 0.5

x11vnc -display :99 -nopw -forever -shared -rfbport 5900 &
websockify --web /usr/share/novnc --timeout=0 6080 localhost:5900 &
sleep 1

echo "[rclone-auth-login] Display and noVNC ready on port 6080"

export CHROME_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage"
export CHROMIUM_FLAGS="$CHROME_FLAGS"
export BROWSER="/usr/local/bin/open-browser"

cat > /tmp/rclone-login-inner.sh <<'INNER'
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

BACKEND="$PAWFLOW_RCLONE_TYPE"
if [ -z "$BACKEND" ]; then
  BACKEND="drive"
fi
REMOTE_NAME="pawflow_remote"
OUTPUT_DIR="/tmp/pawflow-rclone-login"
CONFIG_FILE="$OUTPUT_DIR/rclone.conf"
BODY_FILE="$OUTPUT_DIR/rclone_config_body.txt"
ERROR_FILE="$OUTPUT_DIR/rclone_error.txt"

mkdir -p "$OUTPUT_DIR"
chmod 700 "$OUTPUT_DIR" 2>/dev/null || true
rm -f "$CONFIG_FILE" "$BODY_FILE" "$ERROR_FILE" 2>/dev/null || true

echo "Starting rclone OAuth login for backend: $BACKEND"
echo "A browser window should open in this noVNC session. Complete the provider authorization."
echo "PawFlow will save the generated remote config into the service."
echo

if ! command -v rclone >/dev/null 2>&1; then
  echo "rclone binary is not installed in this image" | tee "$ERROR_FILE"
  sleep infinity
fi

rclone config create "$REMOTE_NAME" "$BACKEND" config_is_local true --config "$CONFIG_FILE"
RC=$?
if [ "$RC" -ne 0 ]; then
  echo "rclone config create failed with exit code $RC" | tee "$ERROR_FILE"
  sleep infinity
fi

awk -v remote="$REMOTE_NAME" '
  $0 == "[" remote "]" { found = 1; next }
  found && $0 ~ /^\[/ { exit }
  found { print }
' "$CONFIG_FILE" > "$BODY_FILE"

if [ ! -s "$BODY_FILE" ]; then
  echo "rclone config was created but no remote body was found" | tee "$ERROR_FILE"
  sleep infinity
fi

echo
echo "Rclone login completed. PawFlow can now read the config."
sleep infinity
INNER

chmod +x /tmp/rclone-login-inner.sh
xterm -fa Monospace -fs 14 -bg black -fg white -e /tmp/rclone-login-inner.sh &

touch /tmp/auth_done

echo "[rclone-auth-login] Waiting for server to read generated config..."
sleep infinity
