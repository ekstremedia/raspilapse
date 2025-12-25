# Daily Video Generation

This document describes the automatic daily timelapse video generation feature for Raspilapse.

## Overview

The daily video feature automatically creates a timelapse video and keogram from the last 24 hours of captured images every day at 05:00, then uploads them to your server.

## Features

- **Automatic daily generation**: Creates video + keogram every day at 05:00
- **24-hour window**: Captures from 05:00 yesterday to 05:00 today
- **Keogram generation**: Creates a time-slice image alongside the video
- **Server upload**: Automatically uploads video and keogram to your webserver
- **Smart naming**: Videos named `{project_name}_YYYY-MM-DD_0500-0500.mp4`
- **Date-organized**: Saves to `/var/www/html/videos/YYYY/MM/` for easy browsing
- **Memory-optimized**: Uses ultrafast preset and limited threads for 4K encoding
- **Deflicker**: Smooths exposure transitions (sunrise/sunset spikes)

## Quick Start

The daily timelapse runs automatically via cron at 05:00. To test manually:

```bash
# Run for yesterday (same as cron job)
cd /home/pi/raspilapse && python3 src/daily_timelapse.py

# Run for a specific date
python3 src/daily_timelapse.py --date 2025-12-23

# Skip upload (just create video + keogram)
python3 src/daily_timelapse.py --no-upload

# Dry run (see what would happen)
python3 src/daily_timelapse.py --dry-run
```

## Configuration

### Video Upload Settings

Add to `config/config.yml`:

```yaml
video_upload:
  # Enable/disable automatic upload after timelapse creation
  enabled: true

  # Server endpoint URL for video uploads
  url: "https://your-server.com/api/video/upload"

  # API key for authentication (Bearer token)
  api_key: "your-api-key-here"

  # Camera ID for the upload
  camera_id: "camera_01"
```

### Video Output Settings

```yaml
video:
  # Directory for generated timelapse videos
  directory: "/var/www/html/videos"

  # Organize videos by date subdirectories (YYYY/MM format)
  organize_by_date: true
  date_format: "%Y/%m"

  # Video codec settings
  codec:
    name: "libx264"
    pixel_format: "yuv420p"
    crf: 20
    preset: "ultrafast"
    threads: 2

  # Frame rate
  fps: 25

  # Deflicker to smooth exposure transitions
  deflicker: true
  deflicker_size: 10
```

## Cron Setup

The cron job is configured to run at 05:00 daily:

```bash
# View current crontab
crontab -l

# The entry looks like:
0 5 * * * cd /home/pi/raspilapse && /usr/bin/python3 src/daily_timelapse.py >> logs/daily_timelapse_cron.log 2>&1
```

To modify the schedule:

```bash
crontab -e
```

## Command Line Options

```
usage: daily_timelapse.py [-h] [--date DATE] [-c CONFIG] [--no-upload]
                          [--only-upload] [--dry-run]

Daily timelapse runner - creates video and uploads to server

options:
  --date DATE      Date for timelapse in YYYY-MM-DD format (default: yesterday)
  -c, --config     Path to configuration file (default: config/config.yml)
  --no-upload      Skip upload step (just create video and keogram)
  --only-upload    Skip video creation (just upload existing files)
  --dry-run        Show what would be done without actually doing it
```

## What Gets Uploaded

The script uploads the following to your server:

| Field | Description |
|-------|-------------|
| `video` | The timelapse MP4 file (required) |
| `keogram` | The keogram/time-slice image (optional) |
| `camera_id` | Camera identifier from config |
| `date` | Date in YYYY-MM-DD format |

## Process Flow

1. **Create Timelapse Video**
   - Runs `make_timelapse.py` with 05:00→05:00 time window
   - Uses ffmpeg with deflicker and configured codec settings
   - Saves to `/var/www/html/videos/YYYY/MM/`

2. **Create Keogram**
   - Generated automatically by `make_timelapse.py`
   - Takes center vertical slice from each image
   - Shows day/night transitions in single image
   - Automatically crops 7% from top to remove overlay bar

3. **Upload to Server**
   - POSTs video + keogram to configured endpoint
   - Uses Bearer token authentication
   - Logs success/failure

## Output Files

### Video Naming

- **Same-day range**: `{project}_YYYY-MM-DD_HHMM-HHMM.mp4`
  - Example: `kringelen_2025-12-24_0500-0500.mp4`
- **Multi-day range**: `{project}_YYYY-MM-DD_HHMM_to_YYYY-MM-DD_HHMM.mp4`
  - Example: `kringelen_2025-12-23_0500_to_2025-12-24_0500.mp4`

### Keogram Naming

- `keogram_{project}_YYYY-MM-DD_HHMM-HHMM.jpg`
  - Example: `keogram_kringelen_2025-12-24_0500-0500.jpg`

### Location

Videos and keograms are saved to date-organized directories:
- `/var/www/html/videos/2025/12/kringelen_2025-12-24_0500-0500.mp4`
- `/var/www/html/videos/2025/12/keogram_kringelen_2025-12-24_0500-0500.jpg`

## Logs

Logs are written to `logs/daily_timelapse_cron.log`:

```bash
# View recent logs
tail -50 logs/daily_timelapse_cron.log

# Follow logs in real-time
tail -f logs/daily_timelapse_cron.log
```

## Troubleshooting

### No Video Generated

Check the logs:
```bash
cat logs/daily_timelapse_cron.log
```

Verify images exist for the date:
```bash
ls /var/www/html/images/2025/12/24/
```

### Upload Failed

Check server endpoint and API key in config:
```yaml
video_upload:
  url: "https://your-server.com/api/video/upload"
  api_key: "your-api-key"
```

Test with dry-run:
```bash
python3 src/daily_timelapse.py --dry-run
```

### Wrong Time Window

The default is 05:00→05:00. This is configured in `daily_timelapse.py`. To change:

```python
# In daily_timelapse.py, modify the make_timelapse_cmd:
"--start", "06:00",  # Change start time
"--end", "06:00",    # Change end time
```

### Video Too Large

Adjust quality in `config/config.yml`:
```yaml
video:
  codec:
    crf: 23  # Higher = smaller files (20-28 range)
```

## Manual Video Creation

To create a video without the upload wrapper:

```bash
# Using make_timelapse.py directly
python3 src/make_timelapse.py --start 05:00 --end 05:00 --start-date 2025-12-23 --end-date 2025-12-24

# Custom time range (same day)
python3 src/make_timelapse.py --start 08:00 --end 18:00 --today

# Limit frames for testing
python3 src/make_timelapse.py --limit 100
```

## Server API Requirements

Your server endpoint should accept:

```
POST /api/video/upload
Authorization: Bearer <api_key>
Content-Type: multipart/form-data

Fields:
- video: (file) MP4 video file
- keogram: (file) JPEG keogram image
- camera_id: (string) Camera identifier
- date: (string) Date in YYYY-MM-DD format
```

## Related Documentation

- [TIMELAPSE_VIDEO.md](TIMELAPSE_VIDEO.md) - Video creation details
- [ADAPTIVE_TIMELAPSE_FLOW.md](ADAPTIVE_TIMELAPSE_FLOW.md) - How images are captured
- [OVERLAY.md](OVERLAY.md) - Adding text overlays to images
