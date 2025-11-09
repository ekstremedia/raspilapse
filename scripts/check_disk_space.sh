#!/bin/bash
#
# Raspilapse Disk Space Monitor
#
# Checks available disk space and alerts if running low
#
# Usage: Run hourly via cron
#        0 * * * * /home/pi/raspilapse/scripts/check_disk_space.sh
#

IMAGE_DIR="/var/www/html/images"
THRESHOLD_MB=10000  # Alert if less than 10 GB free
LOG_TAG="raspilapse-disk"

# Get available space in MB
AVAILABLE_MB=$(df --output=avail -BM "$IMAGE_DIR" | tail -1 | tr -d 'M')
AVAILABLE_GB=$((AVAILABLE_MB / 1024))

if [ "$AVAILABLE_MB" -lt "$THRESHOLD_MB" ]; then
    echo "WARNING: Only ${AVAILABLE_GB} GB free on $IMAGE_DIR"
    logger -t "$LOG_TAG" "WARNING: Low disk space - ${AVAILABLE_GB}GB remaining"

    # Calculate days remaining at current usage rate
    # Assuming 5.5 GB/day for 4K @ 30s interval
    USAGE_PER_DAY_MB=5632  # 5.5 GB in MB
    DAYS_REMAINING=$((AVAILABLE_MB / USAGE_PER_DAY_MB))

    echo "Estimated days remaining: ~$DAYS_REMAINING days"
    logger -t "$LOG_TAG" "Estimated storage remaining: ~$DAYS_REMAINING days"
else
    logger -t "$LOG_TAG" "Disk space OK - ${AVAILABLE_GB}GB available"
fi

exit 0
