#!/bin/sh
# Validate the pawflow-relay AppArmor profile on a Docker host without
# deploying PawFlow. Exercises the FUSE mounts the relay legitimately makes
# (combined-fs root /tmp/pf_combined_fs and rclone root /remote) plus the
# escapes that must stay denied.
#
# Uses an rclone :memory: backend as a stand-in for any FUSE mount. This is
# the faithful path: the relay image entrypoint drops to the unprivileged
# pawflow user, so all FUSE mounts (pyfuse3 combined-fs and rclone alike)
# go through setuid fusermount3, which the profile makes inherit (ix) so
# its mount(2) is mediated by the profile's rules.
#
# Usage (on the host, from the repo root):
#   sudo apparmor_parser -r -W docker/apparmor/pawflow-relay
#   sh scripts/test_apparmor_relay_profile.sh
#
# Expected: 5 lines ending in OK, exit 0. After this passes, the decisive
# check is still a REAL relay booting under the profile and logging
# "[FSRelay] combined-fs mounted".
set -u

IMAGE="${IMAGE:-pawflow-relay-dev:latest}"
PROFILE="${PROFILE:-pawflow-relay}"
fail=0

# Accept either a short name or a registry-qualified ref. If the given ref
# isn't present locally, retry under ghcr.io/allcolor/ (how the images are
# tagged on the deploy hosts) before giving up.
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    alt="ghcr.io/allcolor/$IMAGE"
    if docker image inspect "$alt" >/dev/null 2>&1; then IMAGE="$alt"; fi
fi

# Probes run through the image ENTRYPOINT (tini -> init.sh), which execs
# `sudo -u pawflow` — the SAME unprivileged context the real relay runs in.
# FUSE mounts therefore go through setuid fusermount3, which the profile
# forces to inherit (ix) so its mount(2) is mediated by our rules.
# Escape attempts (bind / propagation) are made via sudo so a denial comes
# from the profile, not from missing privilege.
run() {
    docker run --rm --cap-add SYS_ADMIN --device /dev/fuse \
        --security-opt "apparmor=$PROFILE" "$IMAGE" sh -c "$1"
}

# rclone mount helper: mount an ephemeral :memory: remote at $1, confirm it
# is a mountpoint, unmount. Prints MOUNT-OK on success.
# Root-owned mountpoint dirs (/remote, /mnt/...) are created via sudo +
# chown, mirroring RemoteMountManager._ensure_mountpoint.
rclone_at='
  t="$1"
  sudo -n mkdir -p "$t" && sudo -n chown "$(id -u):$(id -g)" "$t" || { echo "mkdir-failed: $t"; exit 3; }
  rclone mount :memory: "$t" --daemon --config /dev/null \
      --log-file /tmp/rc.log --log-level INFO 2>/tmp/rc.err \
      || { echo "rclone-mount-rc=$?"; cat /tmp/rc.err /tmp/rc.log 2>/dev/null; exit 4; }
  for i in $(seq 1 20); do mountpoint -q "$t" && break; sleep 0.3; done
  if mountpoint -q "$t"; then echo MOUNT-OK; else echo "mount-not-visible: $t"; cat /tmp/rc.err /tmp/rc.log 2>/dev/null; exit 5; fi
  fusermount3 -u "$t" 2>/dev/null || umount "$t" 2>/dev/null
'

if ! run 'command -v rclone >/dev/null'; then
    echo "SKIP: rclone not in $IMAGE; cannot run FUSE checks" >&2
    exit 2
fi

# 1. Positive: FUSE mount under the combined-fs root.
out=$(run "set -- /tmp/pf_combined_fs/probe; $rclone_at" 2>&1)
case "$out" in *MOUNT-OK*) echo "1. combined-fs root FUSE mount: OK";;
    *) echo "1. combined-fs root FUSE mount: FAIL -> $out"; fail=1;; esac

# 2. Positive: FUSE mount under the rclone remote root.
out=$(run "set -- /remote/probe; $rclone_at" 2>&1)
case "$out" in *MOUNT-OK*) echo "2. remote root FUSE mount: OK";;
    *) echo "2. remote root FUSE mount: FAIL -> $out"; fail=1;; esac

# 3. Negative: FUSE mount outside the allowed roots must be denied.
out=$(run "set -- /mnt/evil; $rclone_at" 2>&1)
case "$out" in *MOUNT-OK*) echo "3. FUSE mount outside roots denied: FAIL -> mounted"; fail=1;;
    *) echo "3. FUSE mount outside roots denied: OK";; esac

# 4. Negative: arbitrary bind mount must be denied. Done as root via sudo
# (the profile still applies across sudo) so the denial is the profile's,
# not a missing privilege.
out=$(run 'sudo -n sh -c "mkdir -p /a /b && mount --bind /a /b" 2>&1 && echo ESCAPED' 2>&1)
case "$out" in *ESCAPED*) echo "4. arbitrary bind denied: FAIL -> $out"; fail=1;;
    *) echo "4. arbitrary bind denied: OK";; esac

# 5. Negative: plain unshare -m (root propagation remount) must be denied,
# also as root via sudo.
out=$(run 'sudo -n unshare -m sh -c "echo ESCAPED" 2>&1' 2>&1)
case "$out" in *ESCAPED*) echo "5. propagation change denied: FAIL -> $out"; fail=1;;
    *) echo "5. propagation change denied: OK";; esac

if [ "$fail" -eq 0 ]; then
    echo "pawflow-relay profile: all checks passed (now boot a real relay under it)"
else
    echo "pawflow-relay profile: CHECKS FAILED" >&2
    echo "for AppArmor denial details run: sudo dmesg | grep -i apparmor | tail" >&2
fi
exit "$fail"
