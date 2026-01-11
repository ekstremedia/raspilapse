# Long-Term Maintenance Guide

Ensure your Raspberry Pi timelapse runs reliably for months or years.

## Storage Management

### Storage Consumption (4K @ 30s interval)
```
Per hour:   228 MB
Per day:    5.5 GB
Per week:   38.5 GB
Per month:  160 GB
Per year:   1.9 TB
```

### Storage Solutions

#### Option 1: Automatic Cleanup (Recommended)
The cleanup service deletes images older than 7 days (videos are kept):
```bash
# Already installed via install_service.sh
systemctl status raspilapse-cleanup.timer
```

#### Option 2: Lower Resolution
```yaml
# config/config.yml
camera:
  resolution:
    width: 1920   # Down from 3840
    height: 1080  # Down from 2160
```
Result: ~80 days storage, 230 GB/year

#### Option 3: Increase Interval
```yaml
adaptive_timelapse:
  interval: 60  # Up from 30 seconds
```
Result: Half the frames, half the storage

#### Option 4: Lower Quality
```yaml
output:
  quality: 60  # Down from 75
```
Result: ~30% smaller files

#### Option 5: External Storage
Mount USB drive or NAS to `/var/www/html/images`

## Monthly Maintenance (5 minutes)

```bash
# Disk space
df -h /var/www/html/images

# Service health
systemctl status raspilapse.service

# Error check
journalctl -u raspilapse.service --since "1 month ago" | grep -i error

# Captures per day (expect ~2,880 for 30s interval)
find /var/www/html/images -name "*.jpg" -mtime -1 | wc -l

# Memory usage
free -h

# System updates
sudo apt update && sudo apt upgrade -y
```

## Key Metrics

### Disk Space
```bash
df -h /var/www/html/images
```
**Alert if:** < 10 GB remaining

### Service Status
```bash
systemctl is-active raspilapse
```
**Should be:** active

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

### Temperature
```bash
vcgencmd measure_temp
vcgencmd get_throttled  # 0x0 = no throttling
```

## Hardware Considerations

### SD Card Wear
- 2,880 writes/day = 1M+ writes/year
- Use high-endurance SD card (dashcam rated)
- Consider USB SSD for image storage

### Thermal Management
- Add heatsink to Raspberry Pi
- Ensure good ventilation
- Monitor for throttling in summer

### Power
- Use quality power supply
- Consider UPS for power outage protection

## Quick Fixes

### Service Won't Start
```bash
journalctl -u raspilapse.service -n 50
sudo systemctl restart raspilapse.service
```

### Disk Full
```bash
# Emergency cleanup (delete images older than 3 days)
find /var/www/html/images -name "*.jpg" -mtime +3 -delete
sudo systemctl restart raspilapse.service
```

### Camera Not Detected
```bash
vcgencmd get_camera
sudo modprobe -r bcm2835-v4l2
sudo modprobe bcm2835-v4l2
sudo systemctl restart raspilapse.service
```

### Slow Captures
```bash
# Check if night mode (20s exposures are normal)
python3 src/status.py

# Check throttling
vcgencmd get_throttled
```

## Power Failure Recovery

On power restoration:
1. Raspberry Pi boots normally
2. raspilapse.service starts automatically
3. Captures resume from current time
4. No data loss (each frame is independent)

## System Updates

```bash
# Monthly updates (service auto-restarts after reboot)
sudo apt update
sudo apt upgrade -y
sudo reboot  # If kernel updated
```

## Quarterly Tasks

- Download/backup videos to external storage
- Review timelapse quality
- Check Raspberry Pi firmware updates
- Test power failure recovery

## Yearly Tasks

- Replace SD card (preventative)
- Clean Raspberry Pi (dust, heatsink)
- Review and optimize configuration

## Quick Commands Reference

```bash
# Service
sudo systemctl status raspilapse.service
sudo systemctl restart raspilapse.service
sudo journalctl -u raspilapse.service -f

# Disk
df -h /var/www/html/images
du -sh /var/www/html/images

# Resources
free -h
top -p $(pidof python3)

# Recent captures
ls -lth /var/www/html/images/$(date +%Y/%m/%d)/ | head -10

# Capture count
find /var/www/html/images -name "*.jpg" | wc -l

# Today's captures
find /var/www/html/images -name "*.jpg" -mtime -1 | wc -l

# Errors
journalctl -u raspilapse.service --since "24 hours ago" | grep -i error

# Temperature
vcgencmd measure_temp
vcgencmd get_throttled
```
