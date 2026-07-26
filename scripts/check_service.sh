#!/bin/bash
#
# Raspilapse capture watchdog.
#
# raspilapse.service already has Restart=always, so a crashed process recovers
# on its own. What that cannot catch is the process staying alive while the
# camera stops producing frames -- a wedged libcamera pipeline, a full disk, a
# USB reset. That is the only thing this script looks for.
#
# Installed as raspilapse-watchdog.{service,timer} by:
#     ./scripts/install.sh --with-watchdog
#
# It runs as root because it calls `systemctl restart` and, after repeated
# failures, `reboot`. The older cron-based version ran as the login user, where
# polkit denied the restart and reboot needed root, so it never recovered
# anything.
#
set -uo pipefail

PROJECT_DIR="${RASPILAPSE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
STATE_FILE="${RASPILAPSE_WATCHDOG_STATE:-/var/lib/raspilapse/watchdog_state}"
CONFIG_FILE="${RASPILAPSE_CONFIG:-$PROJECT_DIR/config/config.yml}"

SERVICE_NAME="raspilapse.service"
LOG_TAG="raspilapse-watchdog"
MAX_RESTART_ATTEMPTS=2
STALL_THRESHOLD_SECONDS=600 # 10 minutes without a capture means stalled

log() { logger -t "$LOG_TAG" -- "$@"; echo "$*"; }

# Read output.directory from the config rather than hardcoding a path that
# only matches one install.
image_dir() {
    local dir
    dir=$(python3 -c "
import sys, yaml
try:
    with open('$CONFIG_FILE') as f:
        print((yaml.safe_load(f) or {}).get('output', {}).get('directory', ''))
except Exception:
    sys.exit(1)
" 2>/dev/null)
    echo "${dir:-/var/www/html/images}"
}

# Age in seconds of the newest JPEG, or a large number if there are none.
last_capture_age() {
    local dir newest
    dir=$(image_dir)
    newest=$(find "$dir" -name '*.jpg' -type f -printf '%T@\n' 2>/dev/null | sort -rn | head -1)
    if [ -n "$newest" ]; then
        echo $(( $(date +%s) - ${newest%.*} ))
    else
        echo 999999
    fi
}

restart_count() { [ -f "$STATE_FILE" ] && cat "$STATE_FILE" || echo 0; }
reset_state() { rm -f "$STATE_FILE"; }

bump_restart_count() {
    mkdir -p "$(dirname "$STATE_FILE")"
    echo $(($(restart_count) + 1)) >"$STATE_FILE"
}

main() {
    # A dead service is Restart=always's job, not ours. Only report it.
    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
        log "$SERVICE_NAME is not active; leaving recovery to Restart=always"
        exit 0
    fi

    local age
    age=$(last_capture_age)

    if [ "$age" -lt "$STALL_THRESHOLD_SECONDS" ]; then
        reset_state
        exit 0
    fi

    local count
    count=$(restart_count)
    log "WARNING: no captures in $((age / 60)) minutes (previous restarts: $count)"

    if [ "$count" -ge "$MAX_RESTART_ATTEMPTS" ]; then
        log "CRITICAL: $count restarts did not help, rebooting"
        reset_state
        systemctl reboot
    else
        log "Restarting $SERVICE_NAME (attempt $((count + 1)))"
        bump_restart_count
        systemctl restart "$SERVICE_NAME"
    fi
}

main
exit 0
