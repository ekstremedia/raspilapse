# Daily Video Generation

This document describes the automatic daily timelapse video generation feature for Raspilapse.

## Overview

The daily video feature automatically creates a timelapse video from the last 24 hours of captured images every day at 04:00. This is perfect for creating daily summary videos of your timelapse project.

## Features

- **Automatic daily generation**: Creates a video every day at 04:00
- **Last 24 hours**: Always captures exactly 24 hours of footage
- **Smart naming**: Videos are named `{project_name}_daily_YYYY-MM-DD.mp4`
- **Date-organized**: Saves to `/var/www/html/videos/YYYY/MM/` for easy browsing
- **Memory-optimized**: Uses ultrafast preset and limited threads for 4K encoding
- **No timeout**: Service runs until completion (no arbitrary time limits)
- **Persistent timer**: Will catch up if the system was offline during scheduled time

## Installation

To enable automatic daily video generation, run:

```bash
cd /home/pi/raspilapse
./install_daily_video.sh
```

This will:
1. Create the video output directory (`/var/www/html/videos/`)
2. Install systemd service and timer files
3. Enable and start the timer
4. Show you when the next video will be generated

## Manual Usage

### Generate Last 24 Hours (Default)

```bash
python3 src/make_timelapse.py
```

This creates a video from the last 24 hours with automatic naming.

### Generate Custom Time Range

```bash
# From 04:00 yesterday to 04:00 today
python3 src/make_timelapse.py --start 04:00 --end 04:00

# From 20:00 yesterday to 08:00 today
python3 src/make_timelapse.py --start 20:00 --end 08:00
```

### Test Mode (Limited Frames)

```bash
# Process only first 100 images for quick testing
python3 src/make_timelapse.py --limit 100
```

### Custom Output Directory

```bash
# Save to specific directory
python3 src/make_timelapse.py --output-dir /path/to/videos
```

## Service Management

### Check Status

```bash
# Check timer status (when next video will be generated)
sudo systemctl status raspilapse-daily-video.timer

# Check service logs (see video generation output)
sudo journalctl -u raspilapse-daily-video.service -f

# See next scheduled runs
sudo systemctl list-timers raspilapse-daily-video.timer
```

### Manual Trigger

```bash
# Generate daily video right now (doesn't affect schedule)
sudo systemctl start raspilapse-daily-video.service
```

### Disable/Enable

```bash
# Temporarily stop daily videos
sudo systemctl stop raspilapse-daily-video.timer

# Disable daily videos completely
sudo systemctl disable --now raspilapse-daily-video.timer

# Re-enable daily videos
sudo systemctl enable --now raspilapse-daily-video.timer
```

### Change Schedule

To change when videos are generated (default is 04:00):

```bash
sudo systemctl edit raspilapse-daily-video.timer
```

Add these lines to override the schedule:
```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 06:00:00
```

This example changes it to 06:00. The first `OnCalendar=` line clears the default.

## Uninstallation

To remove the daily video service:

```bash
cd /home/pi/raspilapse
./uninstall_daily_video.sh
```

This removes the service but keeps your existing videos.

## Video Output

### Naming Convention

- **Daily videos**: `{project_name}_daily_YYYY-MM-DD.mp4`
  - Example: `kringelen_daily_2025-11-09.mp4`
- **Custom ranges**: `{project_name}_{start_date}_to_{end_date}.mp4`
  - Example: `kringelen_2025-11-08_to_2025-11-09.mp4`

### Location

Videos are saved to date-organized directories by default:
- `/var/www/html/videos/2025/12/kringelen_nord_daily_2025-12-23.mp4`

Accessible via:
- Local web server: `http://raspberrypi.local/videos/2025/12/`
- Direct file access: `/var/www/html/videos/YYYY/MM/`

### Video Settings

From `config/config.yml`:
- **Frame rate**: 25 fps (smooth European standard)
- **Codec**: H.264 (libx264)
- **Preset**: ultrafast (memory-optimized for 4K)
- **Threads**: 2 (prevents OOM on 4GB Pi)
- **Quality**: CRF 23 (good quality)
- **Format**: MP4 with YUV420p (maximum compatibility)

## Troubleshooting

### No Video Generated

Check if the service ran:
```bash
sudo journalctl -u raspilapse-daily-video.service --since today
```

### Videos Too Large

Adjust quality in `config/config.yml`:
```yaml
video:
  codec:
    crf: 23  # Higher = smaller files (20-23 recommended)
```

### Video Not Playable (moov atom not found)

**Problem:** Video file exists but won't play, ffprobe shows "moov atom not found"

**Cause:** ffmpeg was killed before it could finalize the file (usually OOM killer)

**Solutions:**
1. Check if OOM killed ffmpeg:
   ```bash
   dmesg | grep -i "oom\|killed"
   ```

2. Use memory-optimized settings in `config/config.yml`:
   ```yaml
   video:
     codec:
       preset: "ultrafast"
       threads: 2
   ```

3. Delete the corrupt video and regenerate:
   ```bash
   rm /var/www/html/videos/2025/12/broken_video.mp4
   sudo systemctl start --no-block raspilapse-daily-video.service
   ```

### Wrong Time

Check system time:
```bash
timedatectl
```

Set correct timezone:
```bash
sudo timedatectl set-timezone Europe/Oslo
```

### Missing Images

Verify images exist for the time range:
```bash
ls -la /var/www/html/images/$(date +%Y/%m/%d)/
```

## Web Integration

To display videos on a web page, create an index.html in `/var/www/html/videos/`:

```html
<!DOCTYPE html>
<html>
<head>
    <title>Daily Timelapse Videos</title>
    <style>
        body { font-family: Arial; padding: 20px; }
        video { width: 100%; max-width: 1920px; }
        .video-item { margin: 20px 0; }
    </style>
</head>
<body>
    <h1>Daily Timelapse Videos</h1>
    <div class="video-list">
        <!-- Videos will be listed here by a script -->
    </div>
</body>
</html>
```

## Performance Notes

### 1080p (1920x1080)
- Processing 2,880 images takes approximately 5-10 minutes
- Video size is typically 150-200 MB for 24 hours

### 4K (3840x2160)
- Processing 2,880 images takes approximately 60-90 minutes
- Video size is typically 400-600 MB for 24 hours
- Uses ultrafast preset and 2 threads to prevent OOM

### Service Timeout
- The service has **no timeout** (`TimeoutStartSec=infinity`)
- Encoding runs until completion, regardless of how long it takes
- Monitor progress with: `journalctl -u raspilapse-daily-video -f`

## Advanced Configuration

### Custom Filename Pattern

Edit `config/config.yml`:
```yaml
video:
  filename_pattern: "{name}_{start_date}_to_{end_date}.mp4"
```

### Different Frame Rates

Override in the service file:
```bash
sudo systemctl edit raspilapse-daily-video.service
```

Add:
```ini
[Service]
ExecStart=
ExecStart=/usr/bin/python3 /home/pi/raspilapse/src/make_timelapse.py --output-dir /var/www/html/videos --fps 30
```

## Integration with Adaptive Timelapse

The daily video generation works seamlessly with the adaptive timelapse system:
- Uses all images captured by `auto_timelapse.py`
- Includes day, night, and transition images
- Maintains chronological order
- Preserves overlay information if enabled

## Tips

1. **Storage Management**: Set up a cron job to delete videos older than 30 days:
   ```bash
   0 5 * * * find /var/www/html/videos -name "*.mp4" -mtime +30 -delete
   ```

2. **Notification**: Add a webhook to notify when videos are ready:
   ```bash
   sudo systemctl edit raspilapse-daily-video.service
   ```
   Add `ExecStartPost=/usr/bin/curl -X POST https://your-webhook-url`

3. **Multiple Cameras**: Use different project names and separate services for each camera

## Related Documentation

- [README.md](README.md) - Main project documentation
- [ADAPTIVE_TIMELAPSE_FLOW.md](ADAPTIVE_TIMELAPSE_FLOW.md) - How images are captured
- [OVERLAY.md](OVERLAY.md) - Adding text overlays to videos