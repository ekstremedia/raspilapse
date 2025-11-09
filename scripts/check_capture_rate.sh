#!/bin/bash
#
# Raspilapse Capture Rate Monitor
#
# Verifies that images are being captured at the expected rate
#
# Usage: Run hourly via cron
#        0 * * * * /home/pi/raspilapse/scripts/check_capture_rate.sh
#

IMAGE_DIR="/var/www/html/images"
LOG_TAG="raspilapse-monitor"
INTERVAL_SECONDS=30  # From config: adaptive_timelapse.interval
EXPECTED_PER_HOUR=$((3600 / INTERVAL_SECONDS))  # 120 captures per hour

# Count images from last hour
LAST_HOUR_COUNT=$(find "$IMAGE_DIR" -name "*.jpg" -type f -mmin -60 | wc -l)

# Allow 50% variance (captures might be delayed by long exposures, etc.)
MINIMUM_EXPECTED=$((EXPECTED_PER_HOUR / 2))

if [ "$LAST_HOUR_COUNT" -lt "$MINIMUM_EXPECTED" ]; then
    logger -t "$LOG_TAG" "WARNING: Low capture rate - $LAST_HOUR_COUNT captures in last hour (expected $EXPECTED_PER_HOUR)"
    echo "WARNING: Only $LAST_HOUR_COUNT captures in last hour (expected $EXPECTED_PER_HOUR)"
else
    logger -t "$LOG_TAG" "Capture rate OK - $LAST_HOUR_COUNT captures in last hour (expected $EXPECTED_PER_HOUR)"
fi

# Also check today's total
TODAY=$(date +%Y/%m/%d)
TODAY_DIR="$IMAGE_DIR/$TODAY"
if [ -d "$TODAY_DIR" ]; then
    TODAY_COUNT=$(find "$TODAY_DIR" -name "*.jpg" -type f | wc -l)
    HOURS_ELAPSED=$(date +%H)
    # Add 1 to avoid division by zero at midnight
    EXPECTED_SO_FAR=$(( (HOURS_ELAPSED + 1) * EXPECTED_PER_HOUR ))

    logger -t "$LOG_TAG" "Today's captures: $TODAY_COUNT (expected ~$EXPECTED_SO_FAR after $HOURS_ELAPSED hours)"
fi

exit 0
