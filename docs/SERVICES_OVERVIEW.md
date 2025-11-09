# Raspilapse Services Overview

Complete reference for all systemd services and timers managing your timelapse.

## Active Services

### 1. raspilapse.service
**Purpose:** Main timelapse capture service (runs 24/7)

**Status Check:**
```bash
systemctl status raspilapse.service
```

**Logs:**
```bash
journalctl -u raspilapse.service -f
```

**Control:**
```bash
sudo systemctl start raspilapse.service
sudo systemctl stop raspilapse.service
sudo systemctl restart raspilapse.service
```

**What it does:**
- Captures images every 30 seconds (configurable)
- Automatically adjusts exposure for day/night
- Saves to `/var/www/html/images/YYYY/MM/DD/`
- Auto-restarts on failure
- Runs continuously

---

### 2. raspilapse-daily-video.timer + raspilapse-daily-video.service
**Purpose:** Generates daily timelapse video from yesterday's images

**Timer Status:**
```bash
systemctl status raspilapse-daily-video.timer
```

**Service Status:**
```bash
systemctl status raspilapse-daily-video.service
```

**Logs:**
```bash
journalctl -u raspilapse-daily-video.service
```

**When it runs:** Daily at 00:04 AM

**What it does:**
- Creates video from previous day's images
- Saves to `videos/` directory
- Uses ffmpeg with H.264 encoding
- Runs once per day

**Manual trigger:**
```bash
sudo systemctl start raspilapse-daily-video.service
```

---

### 3. raspilapse-cleanup.timer + raspilapse-cleanup.service ⭐ NEW!
**Purpose:** Automatically deletes old images to prevent disk from filling

**Timer Status:**
```bash
systemctl status raspilapse-cleanup.timer
```

**Service Status:**
```bash
systemctl status raspilapse-cleanup.service
```

**Logs:**
```bash
journalctl -u raspilapse-cleanup.service
```

**When it runs:** Daily at 01:00 AM (after video generation)

**What it does:**
- Deletes images older than 7 days
- Deletes associated metadata files
- Removes empty directories
- Logs cleanup statistics
- Prevents disk from filling up

**Manual trigger:**
```bash
sudo systemctl start raspilapse-cleanup.service
```

**Configuration:**
Edit `/home/pi/raspilapse/scripts/cleanup_old_images.sh`:
```bash
KEEP_DAYS=7  # Change to keep images longer
```

---

## Service Timeline (Daily)

```
00:04 AM  → raspilapse-daily-video.service runs
            Creates yesterday's timelapse video
            
01:00 AM  → raspilapse-cleanup.service runs
            Deletes images older than 7 days
            
24/7      → raspilapse.service captures every 30s
```

---

## Quick Status Check

### View All Raspilapse Services
```bash
systemctl list-units --type=service,timer | grep raspilapse
```

Expected output:
```
raspilapse-cleanup.service       loaded inactive dead    Raspilapse Image Cleanup
raspilapse-daily-video.service   loaded inactive dead    Raspilapse Daily Video
raspilapse.service               loaded active  running  Raspilapse Continuous Timelapse
raspilapse-cleanup.timer         loaded active  waiting  Daily Cleanup Timer
raspilapse-daily-video.timer     loaded active  waiting  Daily Video Timer
```

### View Timer Schedule
```bash
systemctl list-timers | grep raspilapse
```

Shows next scheduled run time for each timer.

---

## Service Management

### Enable/Disable Services

**Enable (start on boot):**
```bash
sudo systemctl enable raspilapse.service
sudo systemctl enable raspilapse-daily-video.timer
sudo systemctl enable raspilapse-cleanup.timer
```

**Disable (don't start on boot):**
```bash
sudo systemctl disable raspilapse.service
sudo systemctl disable raspilapse-daily-video.timer
sudo systemctl disable raspilapse-cleanup.timer
```

### Restart All Services
```bash
sudo systemctl restart raspilapse.service
sudo systemctl restart raspilapse-daily-video.timer
sudo systemctl restart raspilapse-cleanup.timer
```

### Stop Everything
```bash
sudo systemctl stop raspilapse.service
sudo systemctl stop raspilapse-daily-video.timer
sudo systemctl stop raspilapse-cleanup.timer
```

---

## Log Management

### View Recent Logs
```bash
# Main timelapse (last hour)
journalctl -u raspilapse.service --since "1 hour ago"

# Daily video (last run)
journalctl -u raspilapse-daily-video.service -n 100

# Cleanup (last run)
journalctl -u raspilapse-cleanup.service -n 50
```

### Follow Live Logs
```bash
# Main timelapse
journalctl -u raspilapse.service -f

# All raspilapse services
journalctl -u raspilapse.service -u raspilapse-daily-video.service -u raspilapse-cleanup.service -f
```

### Check for Errors
```bash
# Errors in last 24 hours
journalctl -u raspilapse.service --since "24 hours ago" | grep -i error

# All services, errors only
journalctl -u raspilapse.service -u raspilapse-daily-video.service -u raspilapse-cleanup.service -p err
```

---

## Monitoring Commands

### Disk Space Tracking
```bash
# Current usage
df -h /var/www/html/images

# Images stored
find /var/www/html/images -name "*.jpg" | wc -l

# Total size
du -sh /var/www/html/images
```

### Capture Rate Verification
```bash
# Images in last hour (should be ~120 for 30s interval)
find /var/www/html/images -name "*.jpg" -mmin -60 | wc -l

# Today's captures
find /var/www/html/images -name "*.jpg" -mtime -1 | wc -l
```

### Service Health
```bash
# Check if running
systemctl is-active raspilapse.service

# Uptime
systemctl status raspilapse.service | grep "Active:"

# Memory usage
ps aux | grep auto_timelapse
```

---

## Troubleshooting

### Service Won't Start
```bash
# Check status
systemctl status raspilapse.service

# View detailed logs
journalctl -u raspilapse.service -n 100 --no-pager

# Common fixes
sudo systemctl daemon-reload
sudo systemctl restart raspilapse.service
```

### Cleanup Not Running
```bash
# Check timer is enabled
systemctl is-enabled raspilapse-cleanup.timer

# Check next run time
systemctl list-timers | grep cleanup

# Test manually
sudo systemctl start raspilapse-cleanup.service
journalctl -u raspilapse-cleanup.service -n 50
```

### Daily Video Failing
```bash
# Check last run
systemctl status raspilapse-daily-video.service

# View errors
journalctl -u raspilapse-daily-video.service -n 100

# Test manually
sudo systemctl start raspilapse-daily-video.service
```

---

## Configuration Files

### Service Files (systemd)
- `/etc/systemd/system/raspilapse.service`
- `/etc/systemd/system/raspilapse-daily-video.service`
- `/etc/systemd/system/raspilapse-daily-video.timer`
- `/etc/systemd/system/raspilapse-cleanup.service` ⭐ NEW!
- `/etc/systemd/system/raspilapse-cleanup.timer` ⭐ NEW!

### Application Config
- `/home/pi/raspilapse/config/config.yml` - Main configuration

### Scripts
- `/home/pi/raspilapse/src/auto_timelapse.py` - Main capture
- `/home/pi/raspilapse/src/make_timelapse_daily.py` - Video generation
- `/home/pi/raspilapse/scripts/cleanup_old_images.sh` - Cleanup script ⭐

---

## After Reboot

All services start automatically:

1. **raspilapse.service** starts capturing immediately
2. **raspilapse-daily-video.timer** schedules next video at 00:04
3. **raspilapse-cleanup.timer** schedules next cleanup at 01:00

No manual intervention needed! ✅

---

## Complete Status Dashboard

Create this script for a quick overview:

```bash
#!/bin/bash
echo "=== Raspilapse Services Status ==="
echo
echo "Main Timelapse:"
systemctl is-active raspilapse.service && echo "  ✅ Running" || echo "  ❌ Stopped"

echo
echo "Daily Video Timer:"
systemctl is-active raspilapse-daily-video.timer && echo "  ✅ Active" || echo "  ❌ Inactive"
echo "  Next run: $(systemctl list-timers | grep daily-video | awk '{print $3, $4}')"

echo
echo "Cleanup Timer:"
systemctl is-active raspilapse-cleanup.timer && echo "  ✅ Active" || echo "  ❌ Inactive"
echo "  Next run: $(systemctl list-timers | grep cleanup | awk '{print $3, $4}')"

echo
echo "Disk Space:"
df -h /var/www/html/images | tail -1

echo
echo "Images Stored:"
find /var/www/html/images -name "*.jpg" | wc -l

echo
echo "Recent Errors:"
journalctl -u raspilapse.service --since "24 hours ago" | grep -i error | wc -l
```

Save as `/home/pi/raspilapse/scripts/status_dashboard.sh` and run anytime!

---

## Summary

You now have **3 systemd services** managing your timelapse:

1. ✅ **raspilapse.service** - Captures images 24/7
2. ✅ **raspilapse-daily-video.timer** - Creates daily videos at 00:04
3. ⭐ **raspilapse-cleanup.timer** - Deletes old images at 01:00 (NEW!)

All services:
- Start automatically on boot
- Log to journald
- Can be monitored with systemctl
- Run reliably without manual intervention

**Your system is now fully automated for year-long operation!** 🎥✅
