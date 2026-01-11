# Running Raspilapse as a Service

This guide explains how to run Raspilapse continuously as a background service.

## Quick Start

```bash
# Install and start the service
./install_service.sh
sudo systemctl start raspilapse
sudo systemctl status raspilapse
sudo journalctl -u raspilapse -f
```

## All Raspilapse Services

Your system includes 3 systemd services:

| Service | Purpose | Schedule |
|---------|---------|----------|
| `raspilapse.service` | Main timelapse capture | 24/7 continuous |
| `raspilapse-daily-video.timer` | Generate daily videos | 00:04 AM |
| `raspilapse-cleanup.timer` | Delete old images | 01:00 AM |

### Service Timeline (Daily)
```
00:04 AM  - Daily video generation (yesterday's images)
01:00 AM  - Cleanup old images (>7 days)
24/7      - Continuous capture every 30s
```

### Quick Status Check
```bash
# View all Raspilapse services
systemctl list-units --type=service,timer | grep raspilapse

# View timer schedules
systemctl list-timers | grep raspilapse
```

## Installation

### Automated (Recommended)

```bash
cd /home/pi/raspilapse
./install_service.sh
```

This will:
- Create image directory (`/var/www/html/images/`)
- Set up proper permissions
- Install systemd services
- Enable autostart on boot

### Manual Installation

```bash
# Create directories
sudo mkdir -p /var/www/html/images
sudo chown -R pi:www-data /var/www/html/images
sudo chmod -R 775 /var/www/html/images

# Install services
sudo cp raspilapse.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable raspilapse
```

## Service Management

### Basic Commands

```bash
# Start/Stop/Restart
sudo systemctl start raspilapse
sudo systemctl stop raspilapse
sudo systemctl restart raspilapse

# Check status
sudo systemctl status raspilapse

# Enable/Disable autostart
sudo systemctl enable raspilapse
sudo systemctl disable raspilapse
```

### Managing All Services

```bash
# Enable all
sudo systemctl enable raspilapse.service
sudo systemctl enable raspilapse-daily-video.timer
sudo systemctl enable raspilapse-cleanup.timer

# Restart all
sudo systemctl restart raspilapse.service
sudo systemctl restart raspilapse-daily-video.timer
sudo systemctl restart raspilapse-cleanup.timer

# Stop everything
sudo systemctl stop raspilapse.service
sudo systemctl stop raspilapse-daily-video.timer
sudo systemctl stop raspilapse-cleanup.timer
```

### Manual Trigger

```bash
# Trigger daily video now
sudo systemctl start raspilapse-daily-video.service

# Trigger cleanup now
sudo systemctl start raspilapse-cleanup.service
```

## Viewing Logs

```bash
# Follow live
sudo journalctl -u raspilapse -f

# Last 100 lines
sudo journalctl -u raspilapse -n 100

# Since today
sudo journalctl -u raspilapse --since today

# Errors only
sudo journalctl -u raspilapse -p err

# All services
journalctl -u raspilapse.service -u raspilapse-daily-video.service -u raspilapse-cleanup.service -f

# Check for errors (last 24h)
journalctl -u raspilapse.service --since "24 hours ago" | grep -i error
```

## Configuration

Edit config and restart:

```bash
nano /home/pi/raspilapse/config/config.yml
sudo systemctl restart raspilapse
```

### Cleanup Configuration

Edit `/home/pi/raspilapse/scripts/cleanup_old_images.sh`:
```bash
KEEP_DAYS=7  # Change to keep images longer
```

## Image Storage

### Directory Structure

```
/var/www/html/images/
├── 2025/
│   ├── 11/
│   │   ├── 05/
│   │   │   ├── kringelen_2025_11_05_00_00_00.jpg
│   │   │   └── ...
│   │   └── ...
│   └── ...
└── ...
```

### Web Access

With nginx installed:
```
http://your-pi-ip/images/2025/11/05/
http://your-pi-ip/status.jpg  # Latest image
```

## Monitoring

### Check If Running
```bash
systemctl is-active raspilapse
```

### Resource Usage
```bash
systemctl status raspilapse
ps aux | grep auto_timelapse
```

### Disk Space
```bash
df -h /var/www/html/images
du -sh /var/www/html/images
```

### Capture Rate
```bash
# Images in last hour (should be ~120 for 30s interval)
find /var/www/html/images -name "*.jpg" -mmin -60 | wc -l

# Today's captures
find /var/www/html/images -name "*.jpg" -mtime -1 | wc -l
```

## Troubleshooting

### Service Won't Start

```bash
# Check logs
sudo journalctl -u raspilapse -n 50

# Common causes:
# - Camera in use by another process
# - Configuration errors
# - Permission issues
```

### Camera Not Detected

```bash
rpicam-still -o test.jpg
sudo raspi-config  # Interface Options > Camera > Enable
```

### Permission Issues

```bash
sudo chown -R pi:www-data /var/www/html/images
sudo chmod -R 775 /var/www/html/images
sudo usermod -aG video pi  # Re-login required
```

### Cleanup Not Running

```bash
# Check timer enabled
systemctl is-enabled raspilapse-cleanup.timer

# Check next run
systemctl list-timers | grep cleanup

# Test manually
sudo systemctl start raspilapse-cleanup.service
journalctl -u raspilapse-cleanup.service -n 50
```

## Alternative Running Methods

### Foreground (Testing)
```bash
python3 src/auto_timelapse.py
# Ctrl+C to stop
```

### Screen/Tmux
```bash
screen -S timelapse
python3 src/auto_timelapse.py
# Ctrl+A then D to detach
screen -r timelapse  # Reattach
```

## Web Server Setup (Optional)

### Install Nginx
```bash
sudo apt install nginx
```

### Configure Directory Listing

Create `/etc/nginx/sites-available/timelapse`:
```nginx
server {
    listen 80;
    server_name _;
    root /var/www/html;
    index index.html;

    location /images/ {
        autoindex on;
        autoindex_exact_size off;
        autoindex_localtime on;
    }

    location /status.jpg {
        alias /var/www/html/status.jpg;
    }
}
```

Enable:
```bash
sudo ln -s /etc/nginx/sites-available/timelapse /etc/nginx/sites-enabled/
sudo systemctl restart nginx
```

## Uninstallation

```bash
./uninstall_service.sh

# Or manually:
sudo systemctl stop raspilapse
sudo systemctl disable raspilapse
sudo rm /etc/systemd/system/raspilapse.service
sudo systemctl daemon-reload
```

## Configuration Files

### Systemd Services
- `/etc/systemd/system/raspilapse.service`
- `/etc/systemd/system/raspilapse-daily-video.service`
- `/etc/systemd/system/raspilapse-daily-video.timer`
- `/etc/systemd/system/raspilapse-cleanup.service`
- `/etc/systemd/system/raspilapse-cleanup.timer`

### Application
- `/home/pi/raspilapse/config/config.yml`

### Scripts
- `/home/pi/raspilapse/src/auto_timelapse.py`
- `/home/pi/raspilapse/src/make_timelapse_daily.py`
- `/home/pi/raspilapse/scripts/cleanup_old_images.sh`

## Quick Reference

```bash
# Essential commands
sudo systemctl start raspilapse
sudo systemctl stop raspilapse
sudo systemctl restart raspilapse
sudo systemctl status raspilapse
sudo journalctl -u raspilapse -f

# Configuration
nano /home/pi/raspilapse/config/config.yml
sudo systemctl restart raspilapse

# Images
ls /var/www/html/images/$(date +%Y/%m/%d)/
ls -lh /var/www/html/status.jpg
```
