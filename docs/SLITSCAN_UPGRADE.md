# Slitscan Feature Upgrade Guide

Upgrade an existing raspilapse camera to generate and upload slitscan images.

## Prerequisites

- Camera already running raspilapse with daily timelapse generation
- `video_upload` configured in `config/config.yml`

## Upgrade Steps

```bash
# 1. Pull latest code
cd ~/raspilapse
git pull

# 2. Update the systemd service
sudo cp systemd/raspilapse-daily-video.service /etc/systemd/system/
sudo systemctl daemon-reload

# 3. Verify the service is updated
cat /etc/systemd/system/raspilapse-daily-video.service | grep ExecStart
# Should show: daily_timelapse.py (not make_timelapse.py)
```

## What Changed

The daily timelapse service now:
- Runs `daily_timelapse.py` instead of `make_timelapse.py`
- Generates slitscan image alongside keogram
- Uploads three files to server: `video`, `keogram`, `slitscan`

## Verify It Works

After the next 04:00 run, check the logs:

```bash
# Check service status
sudo systemctl status raspilapse-daily-video

# Check recent logs
journalctl -u raspilapse-daily-video -n 50

# Look for slitscan generation
journalctl -u raspilapse-daily-video | grep -i slitscan
```

## Manual Test (Optional)

Run a dry-run to verify configuration:

```bash
python3 src/daily_timelapse.py --dry-run --date $(date -d "yesterday" +%Y-%m-%d)
```

This shows what would be created/uploaded without actually doing it.
