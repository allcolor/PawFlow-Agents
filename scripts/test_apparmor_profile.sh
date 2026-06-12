#!/bin/sh
# Validate the pawflow-mount AppArmor profile on a Docker host without
# deploying PawFlow. Replays the exact mount sequence the provider pools
# run, plus negative cases that must stay denied.
#
# Usage (on the host, from the repo root):
#   sudo apparmor_parser -r -W docker/apparmor/pawflow-mount
#   sh scripts/test_apparmor_profile.sh
#
# Expected output: 4 lines ending in OK, exit code 0.
set -u

IMAGE="${IMAGE:-ubuntu:24.04}"
PROFILE="${PROFILE:-pawflow-mount}"
fail=0

run() {
    docker run --rm --cap-add SYS_ADMIN \
        --security-opt "apparmor=$PROFILE" "$IMAGE" sh -c "$1"
}

# 1. Positive: the pools' exact pattern must work.
out=$(run 'mkdir -p /cc_sessions_host/u1/conv1 /cc_sessions \
  && touch /cc_sessions_host/u1/conv1/marker \
  && unshare -m --propagation unchanged -- \
     sh -c "mount --bind /cc_sessions_host/u1/conv1 /cc_sessions \
            && test -f /cc_sessions/marker && echo BIND-OK"' 2>&1)
case "$out" in *BIND-OK*) echo "1. session-slot bind: OK";;
    *) echo "1. session-slot bind: FAIL -> $out"; fail=1;; esac

# 2. Negative: bind to any other target must be denied.
out=$(run 'mkdir -p /a /b && unshare -m --propagation unchanged -- \
  sh -c "mount --bind /a /b" 2>&1 && echo ESCAPED' 2>&1)
case "$out" in *ESCAPED*) echo "2. arbitrary bind denied: FAIL -> $out"; fail=1;;
    *) echo "2. arbitrary bind denied: OK";; esac

# 3. Negative: bind FROM outside the slot tree onto /cc_sessions must be denied.
out=$(run 'mkdir -p /cc_sessions && unshare -m --propagation unchanged -- \
  sh -c "mount --bind /etc /cc_sessions" 2>&1 && echo ESCAPED' 2>&1)
case "$out" in *ESCAPED*) echo "3. foreign-source bind denied: FAIL -> $out"; fail=1;;
    *) echo "3. foreign-source bind denied: OK";; esac

# 4. Negative: plain unshare -m (root propagation remount) must be denied.
out=$(run 'unshare -m sh -c "echo ESCAPED" 2>&1' 2>&1)
case "$out" in *ESCAPED*) echo "4. propagation change denied: FAIL -> $out"; fail=1;;
    *) echo "4. propagation change denied: OK";; esac

if [ "$fail" -eq 0 ]; then
    echo "pawflow-mount profile: all checks passed"
else
    echo "pawflow-mount profile: CHECKS FAILED" >&2
fi
exit "$fail"
