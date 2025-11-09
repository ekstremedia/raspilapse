# Year-Long Timelapse Checklist

Quick reference for ensuring your Raspberry Pi runs smoothly for an entire year.

## ✅ Current Status (2025-11-09)

**Your system is running perfectly!**

- ✅ No memory leaks detected (150 MB stable)
- ✅ CPU usage low (4.1% average)
- ✅ 4K captures working perfectly
- ✅ Service auto-restart configured
- ✅ Timing accurate (30s intervals maintained)
- ⚠️  **Disk space: Only 9 days remaining at current rate**

## 🚨 Critical Action Needed

**You MUST address disk space before it fills up!**

Choose ONE or combine several:

### Option 1: Automatic Cleanup (Recommended)
Install the cleanup script to delete images after 7 days (videos are kept):

```bash
# Install monitoring cron jobs (includes cleanup)
(crontab -l 2>/dev/null; cat <<'CRON'
0 * * * * /home/pi/raspilapse/scripts/check_disk_space.sh
*/5 * * * * /home/pi/raspilapse/scripts/check_service.sh
0 * * * * /home/pi/raspilapse/scripts/check_capture_rate.sh
0 1 * * * /home/pi/raspilapse/scripts/cleanup_old_images.sh
CRON
) | crontab -

# Verify installed
crontab -l
```

**Result:** Infinite operation! ✅

### Option 2: Reduce Resolution
Edit `config/config.yml`:

```yaml
camera:
  resolution:
    width: 1920   # Down from 3840
    height: 1080  # Down from 2160
```

Then restart: `sudo systemctl restart raspilapse.service`

**Result:** 80+ days of storage, 230 GB/year

### Option 3: Increase Interval
Edit `config/config.yml`:

```yaml
adaptive_timelapse:
  interval: 60  # Up from 30 seconds
```

**Result:** 18 days of storage

### Option 4: Lower Quality
Edit `config/config.yml`:

```yaml
output:
  quality: 60  # Down from 75
```

**Result:** 15 days of storage

## 📊 Storage Math (Current: 4K, 30s, Quality 75)

```
Per hour:   228 MB
Per day:    5.5 GB
Per week:   38.5 GB
Per month:  160 GB
Per year:   1.9 TB
```

**Available:** 50 GB = ~9 days

## 🛠️ Initial Setup Tasks

### 1. Choose Storage Strategy (Pick One)
- [ ] Install automatic cleanup (Option 1) - **Recommended**
- [ ] Lower resolution to 1080p (Option 2)
- [ ] Increase interval to 60s (Option 3)
- [ ] Reduce JPEG quality (Option 4)
- [ ] Add external storage (USB/NAS)

### 2. Install Monitoring
```bash
# Quick install all monitoring
(crontab -l 2>/dev/null; cat <<'CRON'
0 * * * * /home/pi/raspilapse/scripts/check_disk_space.sh
*/5 * * * * /home/pi/raspilapse/scripts/check_service.sh
0 * * * * /home/pi/raspilapse/scripts/check_capture_rate.sh
0 1 * * * /home/pi/raspilapse/scripts/cleanup_old_images.sh
CRON
) | crontab -
```

### 3. Verify Setup
```bash
# Check cron installed
crontab -l

# Test disk monitor
/home/pi/raspilapse/scripts/check_disk_space.sh

# Check logs
journalctl -t raspilapse-disk -n 5
```

### 4. Baseline Test (48 Hours)
- [ ] Let system run for 48 hours
- [ ] Check for errors: `journalctl -u raspilapse.service --since "48 hours ago" | grep ERROR`
- [ ] Verify captures: `find /var/www/html/images -name "*.jpg" -mtime -2 | wc -l` (should be ~5,760)
- [ ] Check daily video: `ls -lh videos/`

## 📅 Monthly Maintenance (5 minutes)

```bash
# Disk space
df -h /var/www/html/images

# Service health
systemctl status raspilapse.service

# Error check
journalctl -u raspilapse.service --since "1 month ago" | grep -i error

# Captures per day (should be 2,880 for 30s interval)
find /var/www/html/images -name "*.jpg" -mtime -1 | wc -l

# Memory usage
free -h

# System updates
sudo apt update && sudo apt upgrade -y
```

## 📈 Key Metrics to Watch

### Disk Space
```bash
df -h /var/www/html/images
```
**Alert if:** < 10 GB remaining

### Service Uptime
```bash
systemctl status raspilapse.service | grep "Active:"
```
**Should show:** active (running)

### Memory Usage
```bash
ps aux | grep auto_timelapse
```
**Should be:** ~150-200 MB RSS, stable over time

### Capture Rate
```bash
find /var/www/html/images -name "*.jpg" -mmin -60 | wc -l
```
**Should be:** ~120 per hour (for 30s interval)

### Errors
```bash
journalctl -u raspilapse.service --since "24 hours ago" | grep -i error
```
**Should be:** Empty or only occasional camera init errors (self-recovers)

## 🔧 Quick Fixes

### Service Won't Start
```bash
# Check logs
journalctl -u raspilapse.service -n 50

# Common fixes
sudo systemctl restart raspilapse.service
sudo reboot
```

### Disk Full
```bash
# Emergency cleanup (delete images older than 3 days)
find /var/www/html/images -name "*.jpg" -mtime +3 -delete

# Free up space immediately
sudo systemctl restart raspilapse.service
```

### Camera Not Detected
```bash
# Test camera
vcgencmd get_camera

# Reload camera module
sudo modprobe -r bcm2835-v4l2
sudo modprobe bcm2835-v4l2

# Restart service
sudo systemctl restart raspilapse.service
```

### Slow Captures
```bash
# Check if night mode (20s exposures are normal)
python3 src/status.py

# Check SD card speed
sudo hdparm -t /dev/mmcblk0

# Check temperature throttling
vcgencmd get_throttled
```

## 📝 Monthly Checklist

```
Month: _______  Year: _______

[ ] Disk space > 10 GB available
[ ] Service running without restarts
[ ] No errors in past month
[ ] ~86,400 captures this month (30s interval)
[ ] Daily videos generating successfully
[ ] Memory usage stable (~150-200 MB)
[ ] Temperature < 80°C
[ ] No throttling events
[ ] System updates applied
[ ] Backups current (if applicable)

Notes:
__________________________________________________
__________________________________________________
```

## 🎯 Success Criteria

After proper setup, your system should:
- ✅ Run 24/7 without intervention
- ✅ Auto-recover from crashes (rare)
- ✅ Never fill disk (with cleanup enabled)
- ✅ Generate daily videos automatically
- ✅ Capture perfectly timed frames
- ✅ Maintain stable memory usage
- ✅ Alert on disk space issues

## 📚 Documentation

- **Full details:** `LONG_TERM_STABILITY.md`
- **Monitoring setup:** `MONITORING_SETUP.md`
- **Daily usage:** `README.md`
- **Overlay config:** `OVERLAY.md`
- **Adaptive flow:** `ADAPTIVE_TIMELAPSE_FLOW.md`

## 🆘 Emergency Contact

If something goes wrong:

1. Check service: `systemctl status raspilapse.service`
2. Check logs: `journalctl -u raspilapse.service -n 100`
3. Restart: `sudo systemctl restart raspilapse.service`
4. Reboot: `sudo reboot`

---

**Bottom Line:** Install the cleanup script, verify it runs daily, and your system will operate flawlessly for a year! 🎥
