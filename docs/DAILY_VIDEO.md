# Daily Video Generation

Automatic daily timelapse video generation for Raspilapse.

## Overview

The daily video feature automatically creates a timelapse video, keogram, and slitscan from the last 24 hours of captured images, then optionally uploads them to your server.

## Features

- Automatic daily generation via systemd timer
- 24-hour window (05:00 yesterday to 05:00 today)
- Keogram generation (time-slice image)
- Slitscan generation (full-width time progression)
- Server upload with API authentication
- Date-organized output directories
- Memory-optimized for 4K encoding
- Deflicker to smooth exposure transitions

## Quick Start

The daily timelapse runs automatically via systemd timer at 05:00. To test manually:

```bash
# Run for yesterday (same as timer)
python3 src/daily_timelapse.py

# Run for a specific date
python3 src/daily_timelapse.py --date 2025-12-23

# Skip upload (just create video + keogram)
python3 src/daily_timelapse.py --no-upload

# Dry run (see what would happen)
python3 src/daily_timelapse.py --dry-run
```

## Installation

```bash
./scripts/install_daily_video.sh
```

This installs the systemd timer that runs at 05:00 daily.

Check status:
```bash
systemctl status raspilapse-daily-video.timer
systemctl list-timers | grep daily-video
```

## Configuration

### Video Upload Settings

Add to `config/config.yml`:

```yaml
video_upload:
  enabled: true
  url: "https://your-server.com/api/video/upload"
  api_key: "your-api-key-here"
  camera_id: "camera_01"
```

### Video Output Settings

```yaml
video:
  directory: "/var/www/html/videos"
  organize_by_date: true
  date_format: "%Y/%m"
  codec:
    name: "libx264"
    pixel_format: "yuv420p"
    crf: 20
    preset: "ultrafast"
    threads: 2
  fps: 25
  deflicker: true
  deflicker_size: 10
```

## Command Line Options

```
python3 src/daily_timelapse.py [OPTIONS]

  --date DATE      Date for timelapse in YYYY-MM-DD format (default: yesterday)
  -c, --config     Path to configuration file
  --no-upload      Skip upload step
  --only-upload    Skip video creation, just upload existing files
  --dry-run        Show what would be done
```

## Output Files

### Video
`/var/www/html/videos/2025/12/kringelen_2025-12-24_0500-0500.mp4`

### Keogram
`/var/www/html/videos/2025/12/keogram_kringelen_2025-12-24_0500-0500.jpg`

### Slitscan
`/var/www/html/videos/2025/12/slitscan_kringelen_2025-12-24_0500-0500.jpg`

## Process Flow

1. **Create Video** - Runs make_timelapse.py with 05:00 to 05:00 window
2. **Create Keogram** - Center vertical slice from each image
3. **Create Slitscan** - Full-width time progression
4. **Upload** - POST video, keogram, slitscan to server

## Logs

```bash
# View timer status
systemctl status raspilapse-daily-video.timer

# View service logs
journalctl -u raspilapse-daily-video.service -n 50

# Follow live
journalctl -u raspilapse-daily-video.service -f
```

## Troubleshooting

### No Video Generated

Check images exist for the date:
```bash
ls /var/www/html/images/2025/12/24/
```

Check logs:
```bash
journalctl -u raspilapse-daily-video.service -n 100
```

### Upload Failed

Verify server endpoint and API key in config. Test with dry-run:
```bash
python3 src/daily_timelapse.py --dry-run
```

### Timer Not Running

```bash
# Check if enabled
systemctl is-enabled raspilapse-daily-video.timer

# Enable if needed
sudo systemctl enable raspilapse-daily-video.timer
sudo systemctl start raspilapse-daily-video.timer

# Check next run time
systemctl list-timers | grep daily-video
```

## Manual Trigger

```bash
sudo systemctl start raspilapse-daily-video.service
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
- slitscan: (file) JPEG slitscan image
- camera_id: (string) Camera identifier
- date: (string) Date in YYYY-MM-DD format
```

## Related Documentation

- [TIMELAPSE_VIDEO.md](TIMELAPSE_VIDEO.md) - Video creation details
- [SERVICE.md](SERVICE.md) - Service management
