#!/bin/bash
#
# Raspilapse Automatic Image Cleanup Script
#
# This script automatically deletes old images after they have been processed
# into daily videos. This prevents disk from filling up during long-term operation.
#
# Usage: Run daily via cron after video generation
#        0 1 * * * /home/pi/raspilapse/scripts/cleanup_old_images.sh
#

set -e

# Configuration
IMAGE_DIR="/var/www/html/images"
KEEP_DAYS=7  # Keep images for 7 days (daily videos are created, so originals not needed)
LOG_TAG="raspilapse-cleanup"

# Log function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    logger -t "$LOG_TAG" "$1"
}

log "Starting automatic cleanup of images older than $KEEP_DAYS days..."

# Count files before cleanup
BEFORE_COUNT=$(find "$IMAGE_DIR" -name "*.jpg" -type f | wc -l)
BEFORE_SIZE=$(du -sh "$IMAGE_DIR" 2>/dev/null | cut -f1)

log "Before cleanup: $BEFORE_COUNT images, total size: $BEFORE_SIZE"

# Delete old JPG images
DELETED_IMAGES=$(find "$IMAGE_DIR" -name "*.jpg" -type f -mtime +$KEEP_DAYS -delete -print | wc -l)
log "Deleted $DELETED_IMAGES old images"

# Delete old metadata JSON files
DELETED_METADATA=$(find "$IMAGE_DIR" -name "*_metadata.json" -type f -mtime +$KEEP_DAYS -delete -print | wc -l)
log "Deleted $DELETED_METADATA old metadata files"

# Clean up empty date directories
DELETED_DIRS=$(find "$IMAGE_DIR" -type d -empty -delete -print 2>/dev/null | wc -l)
log "Deleted $DELETED_DIRS empty directories"

# Count files after cleanup
AFTER_COUNT=$(find "$IMAGE_DIR" -name "*.jpg" -type f | wc -l)
AFTER_SIZE=$(du -sh "$IMAGE_DIR" 2>/dev/null | cut -f1)

log "After cleanup: $AFTER_COUNT images, total size: $AFTER_SIZE"
log "Cleanup complete!"

# Check disk space and warn if low
AVAILABLE_MB=$(df --output=avail -BM "$IMAGE_DIR" | tail -1 | tr -d 'M')
if [ "$AVAILABLE_MB" -lt 10000 ]; then
    log "WARNING: Low disk space! Only ${AVAILABLE_MB}MB available"
fi

exit 0
