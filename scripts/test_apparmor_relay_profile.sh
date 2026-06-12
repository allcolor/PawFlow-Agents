#!/bin/sh
# Validate the pawflow-relay AppArmor profile on a Docker host without
# deploying PawFlow. Exercises the FUSE mounts the relay legitimately makes
# (combined-fs root /tmp/pf_combined_fs and rclone root /remote) plus the
# escapes that must stay denied.
#
# Uses an rclone :memory: backend as a stand-in for any FUSE mount: same
# fstype family (fuse.rclone) and the same mount/umount path libfuse uses
# for the real combined-fs. Run inside the relay image which ships rclone
# + fusermount3.
#
# Usage (on the host, from the repo root):
#   sudo apparmor_parser -r -W docker/apparmor/pawflow-relay
#   sh scripts/test_apparmor_relay_profile.sh
#
# Expected: 5 lines ending in OK, exit 0. After this passes, the decisive
# check is still a REAL relay booting under the profile and logging
# "[FSRelay] combined-fs mounted" — this script cannot reproduce pyfuse3's
# direct mount() path, only the fstype/target mediation.
set -u

IMAGE="${IMAGE:-pawflow-relay-dev:latest}"
PROFILE="${PROFILE:-pawflow-relay}"
fail=0

run() {
    docker run --rm --cap-add SYS_ADMIN --device /dev/fuse \
        --security-opt "apparmor=$PROFILE" "$IMAGE" sh -c "$1"
}

# rclone mount helper: mount an ephemeral :memory: remote at $1, confirm it
# is a mountpoint, unmount. Prints MOUNT-OK on success.
rclone_at='
  t="$1"; mkdir -p "$t" || exit 3
  rclone mount :memory: "$t" --daemon --config /dev/null 2>/tmp/rc.err || exit 4
  for i in 1 2 3 4 5 6 7 8 9 10; do mountpoint -q "$t" && break; sleep 0.3; done
  if mountpoint -q "$t"; then echo MOUNT-OK; else cat /tmp/rc.err; exit 5; fi
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

# 4. Negative: arbitrary bind mount must be denied.
out=$(run 'mkdir -p /a /b && mount --bind /a /b 2>&1 && echo ESCAPED' 2>&1)
case "$out" in *ESCAPED*) echo "4. arbitrary bind denied: FAIL -> $out"; fail=1;;
    *) echo "4. arbitrary bind denied: OK";; esac

# 5. Negative: plain unshare -m (root propagation remount) must be denied.
out=$(run 'unshare -m sh -c "echo ESCAPED" 2>&1' 2>&1)
case "$out" in *ESCAPED*) echo "5. propagation change denied: FAIL -> $out"; fail=1;;
    *) echo "5. propagation change denied: OK";; esac

if [ "$fail" -eq 0 ]; then
    echo "pawflow-relay profile: all checks passed (now boot a real relay under it)"
else
    echo "pawflow-relay profile: CHECKS FAILED" >&2
fi
exit "$fail"
