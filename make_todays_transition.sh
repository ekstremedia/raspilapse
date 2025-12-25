#!/bin/bash

# Handle globs that don't match any files
shopt -s nullglob

# Get today's date components
YEAR=$(date +%Y)
MONTH=$(date +%m)
DAY=$(date +%d)

# Define paths
INPUT_DIR="/var/www/html/images/${YEAR}/${MONTH}/${DAY}"
OUTPUT_DIR="/var/www/html/videos/${YEAR}/${MONTH}"
OUTPUT_FILE="${OUTPUT_DIR}/timelapse_${YEAR}${MONTH}${DAY}.mp4"

# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"

# Get current hour and minute for filtering
CURRENT_TIME=$(date +%H%M)
START_TIME="0600"

# Create a temporary file list with images from 06:00 to now
TEMP_LIST=$(mktemp)

for img in "$INPUT_DIR"/*.jpg "$INPUT_DIR"/*.JPG; do
    [ -f "$img" ] || continue

    # Extract timestamp from filename or use file modification time
    # Adjust this based on your filename format
    FILENAME=$(basename "$img")

    # Try to extract time from filename (assumes format like image_HHMMSS.jpg or YYYYMMDD_HHMMSS.jpg)
    # Modify the pattern based on your actual filename format
    FILE_TIME=$(echo "$FILENAME" | grep -oE '[0-9]{2}[0-9]{2}[0-9]{2}' | head -1 | cut -c1-4)

    # If no time found in filename, use file modification time
    if [ -z "$FILE_TIME" ]; then
        FILE_TIME=$(date -r "$img" +%H%M)
    fi

    # Check if file time is between 06:00 and current time
    if [ "$FILE_TIME" -ge "$START_TIME" ] && [ "$FILE_TIME" -le "$CURRENT_TIME" ]; then
        echo "file '$img'" >> "$TEMP_LIST"
    fi
done

# Sort the file list
sort -o "$TEMP_LIST" "$TEMP_LIST"

# Check if we have any files
if [ ! -s "$TEMP_LIST" ]; then
    echo "No images found between 06:00 and now in $INPUT_DIR"
    rm "$TEMP_LIST"
    exit 1
fi

echo "Creating timelapse from $(wc -l < "$TEMP_LIST") images..."

# Create timelapse with ffmpeg
ffmpeg -y -f concat -safe 0 -i "$TEMP_LIST" \
    -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2" \
    -c:v libx264 -preset medium -crf 23 \
    -pix_fmt yuv420p \
    -r 30 \
    "$OUTPUT_FILE"

# Cleanup
rm "$TEMP_LIST"

echo "Timelapse saved to: $OUTPUT_FILE"
