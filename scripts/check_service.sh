#!/bin/bash
#
# Raspilapse Service Health Monitor (Watchdog)
#
# Checks if the timelapse service is running and capturing images.
# Restarts service if captures stall, reboots if restart doesn't help.
#
# Usage: Run every 5 minutes via cron
#        */5 * * * * /home/pi/raspilapse/scripts/check_service.sh
#

SERVICE_NAME="raspilapse.service"
LOG_TAG="raspilapse-watchdog"
STATE_FILE="/tmp/raspilapse_watchdog_state"
MAX_RESTART_ATTEMPTS=2
STALL_THRESHOLD_SECONDS=600  # 10 minutes without captures = stalled

# Get age of most recent image in seconds
get_last_capture_age() {
    local last_image
    last_image=$(find /var/www/html/images -name "*.jpg" -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)
    if [ -n "$last_image" ] && [ -f "$last_image" ]; then
        local last_modified now
        last_modified=$(stat -c %Y "$last_image")
        now=$(date +%s)
        echo $((now - last_modified))
    else
        echo "9999"  # No images found
    fi
}

# Read restart attempt count from state file
get_restart_count() {
    if [ -f "$STATE_FILE" ]; then
        cat "$STATE_FILE"
    else
        echo "0"
    fi
}

# Reset state (called when captures are working)
reset_state() {
    rm -f "$STATE_FILE"
}

# Increment restart count
increment_restart_count() {
    local count
    count=$(get_restart_count)
    echo $((count + 1)) > "$STATE_FILE"
}

# Main logic
main() {
    local age restart_count

    # Check if service is running
    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
        logger -t "$LOG_TAG" "Service is down, attempting restart"
        systemctl restart "$SERVICE_NAME"
        sleep 5
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            logger -t "$LOG_TAG" "Service restarted successfully"
        else
            logger -t "$LOG_TAG" "CRITICAL: Failed to restart service"
        fi
        exit 0
    fi

    # Service is running - check capture age
    age=$(get_last_capture_age)

    if [ "$age" -lt "$STALL_THRESHOLD_SECONDS" ]; then
        # Captures are working - reset any escalation state
        reset_state
        exit 0
    fi

    # Captures have stalled
    restart_count=$(get_restart_count)
    logger -t "$LOG_TAG" "WARNING: No captures in $((age/60)) minutes (restart_count=$restart_count)"

    if [ "$restart_count" -ge "$MAX_RESTART_ATTEMPTS" ]; then
        # Multiple restarts haven't helped - escalate to reboot
        logger -t "$LOG_TAG" "CRITICAL: $restart_count restart attempts failed, rebooting system"
        reset_state
        /sbin/reboot
    else
        # Try restarting the service
        logger -t "$LOG_TAG" "Restarting service (attempt $((restart_count + 1)))"
        increment_restart_count
        systemctl restart "$SERVICE_NAME"
    fi
}

main
exit 0
