#!/bin/bash
#
# Raspilapse Service Health Monitor
#
# Checks if the timelapse service is running and restarts if needed
#
# Usage: Run every 5 minutes via cron
#        */5 * * * * /home/pi/raspilapse/scripts/check_service.sh
#

SERVICE_NAME="raspilapse.service"
LOG_TAG="raspilapse-monitor"

# Check if service is active
if systemctl is-active --quiet "$SERVICE_NAME"; then
    # Service is running - check when last capture happened
    LAST_IMAGE=$(find /var/www/html/images -name "*.jpg" -type f -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)
    if [ -n "$LAST_IMAGE" ]; then
        LAST_MODIFIED=$(stat -c %Y "$LAST_IMAGE")
        NOW=$(date +%s)
        AGE=$((NOW - LAST_MODIFIED))

        # Alert if no capture in last 10 minutes (should capture every 30s)
        if [ "$AGE" -gt 600 ]; then
            logger -t "$LOG_TAG" "WARNING: No captures in last $((AGE/60)) minutes"
        fi
    fi
else
    # Service is down - log and attempt restart
    logger -t "$LOG_TAG" "ERROR: Service $SERVICE_NAME is down, attempting restart"
    systemctl restart "$SERVICE_NAME"

    # Wait a moment and check if restart succeeded
    sleep 5
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        logger -t "$LOG_TAG" "Service $SERVICE_NAME successfully restarted"
    else
        logger -t "$LOG_TAG" "CRITICAL: Failed to restart $SERVICE_NAME"
    fi
fi

exit 0
