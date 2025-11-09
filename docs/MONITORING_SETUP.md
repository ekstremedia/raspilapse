# Monitoring Setup Guide

Quick guide to set up automated monitoring for year-long operation.

## 1. Install Monitoring Scripts (Already Done! ✅)

Scripts are located in `/home/pi/raspilapse/scripts/`:
- `cleanup_old_images.sh` - Deletes images older than 7 days (runs as systemd service)
- `check_disk_space.sh` - Monitors available disk space
- `check_service.sh` - Ensures service is running
- `check_capture_rate.sh` - Verifies captures are happening

## 2. Cleanup Service (Already Installed! ✅)

The cleanup script runs as a **systemd timer** (not cron):

```bash
# Check status
systemctl status raspilapse-cleanup.timer

# Next scheduled run
systemctl list-timers | grep cleanup

# View logs
journalctl -u raspilapse-cleanup.service
```

**Runs daily at 01:00 AM automatically!** No cron setup needed.

## 3. Set Up Monitoring Cron Jobs (Optional)

The disk space, service health, and capture rate monitors can be set up with cron if desired.

### Option A: Quick Install (Copy-Paste)

Run this command to install monitoring jobs:

```bash
(crontab -l 2>/dev/null; cat <<'EOF'
# Raspilapse monitoring (cleanup runs via systemd timer)
0 * * * * /home/pi/raspilapse/scripts/check_disk_space.sh
*/5 * * * * /home/pi/raspilapse/scripts/check_service.sh
0 * * * * /home/pi/raspilapse/scripts/check_capture_rate.sh
EOF
) | crontab -
```

### Option B: Manual Install

```bash
crontab -e
```

Add these lines at the end:

```cron
# Raspilapse monitoring (cleanup runs via systemd timer)
0 * * * * /home/pi/raspilapse/scripts/check_disk_space.sh
*/5 * * * * /home/pi/raspilapse/scripts/check_service.sh
0 * * * * /home/pi/raspilapse/scripts/check_capture_rate.sh
```

Save and exit.

### Verify Cron Jobs

```bash
crontab -l
```

Should show the 3 monitoring jobs (cleanup is handled by systemd).

## 3. Test Scripts

Test each script manually before relying on cron:

```bash
# Test disk space monitor
/home/pi/raspilapse/scripts/check_disk_space.sh

# Test service monitor
/home/pi/raspilapse/scripts/check_service.sh

# Test capture rate monitor
/home/pi/raspilapse/scripts/check_capture_rate.sh

# Test cleanup (DRY RUN - doesn't actually delete)
find /var/www/html/images -name "*.jpg" -type f -mtime +7
```

## 4. View Monitoring Logs

All scripts log to system journal with tag prefixes:

```bash
# Disk space warnings
journalctl -t raspilapse-disk

# Service health
journalctl -t raspilapse-monitor

# Cleanup activity
journalctl -t raspilapse-cleanup

# All monitoring logs today
journalctl -t raspilapse-disk -t raspilapse-monitor -t raspilapse-cleanup --since today

# Follow live
journalctl -t raspilapse-disk -t raspilapse-monitor -f
```

## 5. Customize Cleanup Settings

Edit `/home/pi/raspilapse/scripts/cleanup_old_images.sh`:

```bash
KEEP_DAYS=7  # Change to keep images longer (e.g., 14 days)
```

**Recommended values:**
- `3` - Minimal storage, videos only
- `7` - Default, good for most setups
- `14` - Keep 2 weeks for manual review
- `30` - Keep 1 month (requires more disk space)

## 6. Critical Disk Space Alert

If you want email alerts for low disk space, install and configure `mailutils`:

```bash
sudo apt install -y mailutils

# Configure email in /home/pi/raspilapse/scripts/check_disk_space.sh
# Add after the warning line:
echo "Low disk space: ${AVAILABLE_GB}GB" | mail -s "Raspilapse Disk Warning" your@email.com
```

## 7. Optional: Weekly Reboot

Some prefer weekly reboots to clear any system issues:

```bash
(crontab -l 2>/dev/null; echo "0 4 * * 0 sudo /sbin/shutdown -r now") | crontab -
```

Reboots every Sunday at 4 AM. Service auto-restarts after boot.

## 8. Monitoring Dashboard (Optional)

Create a simple status script:

```bash
#!/bin/bash
echo "=== Raspilapse Health ==="
echo
echo "Service Status:"
systemctl status raspilapse.service --no-pager -l | grep "Active:"
echo
echo "Disk Space:"
df -h /var/www/html/images | tail -1
echo
echo "Images Today:"
find /var/www/html/images -name "*.jpg" -mtime -1 | wc -l
echo
echo "Last Capture:"
ls -lth /var/www/html/images/$(date +%Y/%m/%d)/ 2>/dev/null | head -2 | tail -1
echo
echo "Recent Warnings:"
journalctl -t raspilapse-disk -t raspilapse-monitor --since "24 hours ago" | grep WARNING
```

Save as `/home/pi/raspilapse/scripts/health_check.sh` and run anytime.

## Quick Command Reference

```bash
# Install monitoring
(crontab -l 2>/dev/null; cat <<'EOF'
0 * * * * /home/pi/raspilapse/scripts/check_disk_space.sh
*/5 * * * * /home/pi/raspilapse/scripts/check_service.sh
0 * * * * /home/pi/raspilapse/scripts/check_capture_rate.sh
0 1 * * * /home/pi/raspilapse/scripts/cleanup_old_images.sh
EOF
) | crontab -

# View cron jobs
crontab -l

# View monitoring logs
journalctl -t raspilapse-disk -t raspilapse-monitor --since today

# Manual cleanup test (shows what would be deleted)
find /var/www/html/images -name "*.jpg" -type f -mtime +7

# Check service
systemctl status raspilapse.service
```

## What Happens After Setup

### Hourly
- Disk space checked
- Capture rate verified
- Results logged to journal

### Every 5 Minutes
- Service health checked
- Auto-restart if down

### Daily at 1 AM
- Old images deleted (7+ days)
- Empty directories cleaned
- Disk space freed

### Your Timelapse
- Runs 24/7 unattended
- Auto-recovers from failures
- Never fills disk
- Daily videos preserved forever

## Troubleshooting

### Cron not running?

```bash
# Check cron service
systemctl status cron

# Check cron logs
journalctl -u cron --since "1 hour ago"
```

### Script not executing?

```bash
# Check permissions
ls -l /home/pi/raspilapse/scripts/

# Should all be -rwxr-xr-x
chmod +x /home/pi/raspilapse/scripts/*.sh
```

### Not seeing logs?

```bash
# Run scripts manually first
/home/pi/raspilapse/scripts/check_disk_space.sh

# Then check journal
journalctl -t raspilapse-disk -n 5
```

## Success Indicators

After 24 hours of monitoring, you should see:

```bash
journalctl -t raspilapse-disk -t raspilapse-monitor -t raspilapse-cleanup --since "24 hours ago" --no-pager
```

Expected output:
- 24 disk space checks (hourly)
- ~288 service health checks (every 5 min)
- 24 capture rate checks (hourly)
- 1 cleanup run (if images >7 days old exist)
- No ERROR or WARNING messages (unless genuinely low on space)

## Done!

Your monitoring is now active. The system will:
- Alert on low disk space
- Keep service running
- Clean up old images automatically
- Log all activity for review

Sleep well knowing your year-long timelapse is protected! 🎥✅
