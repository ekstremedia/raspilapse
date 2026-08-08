#!/bin/bash
#
# Raspilapse Automatic Image Cleanup Script
#
# Deletes captured stills (and their metadata sidecars) once they are older
# than the retention window, so the disk does not fill during long-term
# operation. Runs as the first of the three steps in raspilapse-cleanup.service
# (02:00 timer); videos and database rows have their own retention settings
# (video.retention_days, database.retention_days in config.yml).
#
# Configuration, overridable via environment:
#   RASPILAPSE_IMAGE_DIR   image tree to prune (default /var/www/html/images --
#                          keep in sync with output.directory in config.yml)
#   RASPILAPSE_KEEP_DAYS   days of images to keep; 0 disables deletion (default 7)
#
# To override without editing anything tracked by git:
#   sudo systemctl edit raspilapse-cleanup.service
#     [Service]
#     Environment=RASPILAPSE_KEEP_DAYS=14
#

set -e

IMAGE_DIR="${RASPILAPSE_IMAGE_DIR:-/var/www/html/images}"
KEEP_DAYS="${RASPILAPSE_KEEP_DAYS:-7}"
LOG_TAG="raspilapse-cleanup"

# Log function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    logger -t "$LOG_TAG" "$1"
}

# A non-numeric KEEP_DAYS would reach `find -mtime +$KEEP_DAYS` and fail there
# (or worse); refuse up front. 0 must short-circuit: `-mtime +0` means "older
# than 24 hours", which is the opposite of "keep everything".
case "$KEEP_DAYS" in
    ''|*[!0-9]*)
        log "ERROR: RASPILAPSE_KEEP_DAYS='$KEEP_DAYS' is not a whole number; refusing to delete anything"
        exit 1
        ;;
esac

if [ "$KEEP_DAYS" -eq 0 ]; then
    log "Image retention disabled (KEEP_DAYS=0); nothing deleted"
    exit 0
fi

if [ ! -d "$IMAGE_DIR" ]; then
    log "Image directory $IMAGE_DIR does not exist; nothing to do"
    exit 0
fi

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
