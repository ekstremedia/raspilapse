# Running Raspilapse as a Service

This guide explains how to run Raspilapse continuously as a background service on your Raspberry Pi.

## Quick Start

```bash
# 1. Install the service
./install_service.sh

# 2. Start the service
sudo systemctl start raspilapse

# 3. Check status
sudo systemctl status raspilapse

# 4. View logs
sudo journalctl -u raspilapse -f
```

## Installation

### Automated Installation (Recommended)

The installation script will:
- Create the image directory (`/var/www/html/images/`)
- Set up proper permissions
- Install the systemd service
- Enable autostart on boot

```bash
cd /home/pi/raspilapse
./install_service.sh
```

### Manual Installation

If you prefer to install manually:

```bash
# 1. Create directories
sudo mkdir -p /var/www/html/images
sudo chown -R pi:www-data /var/www/html/images
sudo chmod -R 775 /var/www/html/images

# 2. Install service
sudo cp raspilapse.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable raspilapse
```

## Service Management

### Start Service

```bash
sudo systemctl start raspilapse
```

### Stop Service

```bash
sudo systemctl stop raspilapse
```

### Restart Service

```bash
sudo systemctl restart raspilapse
```

### Check Status

```bash
sudo systemctl status raspilapse
```

Example output:
```
● raspilapse.service - Raspilapse Continuous Timelapse Service
     Loaded: loaded (/etc/systemd/system/raspilapse.service; enabled)
     Active: active (running) since Tue 2025-11-05 14:30:00 GMT; 2h 15min ago
   Main PID: 1234 (python3)
      Tasks: 3 (limit: 4915)
     Memory: 45.2M
        CPU: 1min 23.456s
     CGroup: /system.slice/raspilapse.service
             └─1234 /usr/bin/python3 /home/pi/raspilapse/src/auto_timelapse.py
```

### View Logs

**Follow logs in real-time:**
```bash
sudo journalctl -u raspilapse -f
```

**View last 100 lines:**
```bash
sudo journalctl -u raspilapse -n 100
```

**View logs since today:**
```bash
sudo journalctl -u raspilapse --since today
```

**View logs with timestamps:**
```bash
sudo journalctl -u raspilapse --since "2025-11-05 14:00:00"
```

### Enable/Disable Autostart

**Enable (start on boot):**
```bash
sudo systemctl enable raspilapse
```

**Disable (don't start on boot):**
```bash
sudo systemctl disable raspilapse
```

## Configuration

### Edit Configuration

```bash
nano /home/pi/raspilapse/config/config.yml
```

### Apply Configuration Changes

After editing the config, restart the service:

```bash
sudo systemctl restart raspilapse
```

## Image Storage

### Directory Structure

Images are organized by date:

```
/var/www/html/images/
├── 2025/
│   ├── 11/
│   │   ├── 05/
│   │   │   ├── kringelen_2025_11_05_00_00_00.jpg
│   │   │   ├── kringelen_2025_11_05_00_30_00.jpg
│   │   │   ├── kringelen_2025_11_05_01_00_00.jpg
│   │   │   └── ...
│   │   ├── 06/
│   │   │   └── ...
│   │   └── ...
│   └── ...
└── ...
```

### Access via Web Browser

If you have a web server running (e.g., Apache or Nginx), you can view images at:

```
http://your-pi-ip/images/2025/11/05/
```

Latest image (via symlink):
```
http://your-pi-ip/status.jpg
```

## Troubleshooting

### Service Won't Start

**Check logs:**
```bash
sudo journalctl -u raspilapse -n 50
```

**Common issues:**
- Camera in use by another process
- Missing dependencies
- Configuration errors
- Permission issues

### Camera Not Detected

```bash
# Test camera
rpicam-still -o test.jpg

# Check camera interface
sudo raspi-config
# Navigate to: Interface Options → Camera → Enable
```

### Permission Issues

```bash
# Fix image directory permissions
sudo chown -R pi:www-data /var/www/html/images
sudo chmod -R 775 /var/www/html/images

# Add user to video group
sudo usermod -aG video pi
# Log out and back in
```

### Disk Space

```bash
# Check available space
df -h /var/www/html

# Find large directories
du -h --max-depth=2 /var/www/html/images | sort -hr | head -20
```

### Service Crashes/Restarts

The service automatically restarts after 10 seconds if it crashes.

**View crash logs:**
```bash
sudo journalctl -u raspilapse --since "1 hour ago" | grep -i error
```

## Monitoring

### Check If Running

```bash
systemctl is-active raspilapse
# Output: active (running) or inactive (dead)
```

### View Resource Usage

```bash
systemctl status raspilapse
```

### Count Images Captured Today

```bash
find /var/www/html/images/$(date +%Y/%m/%d) -name "*.jpg" | wc -l
```

### Latest Image

```bash
ls -lh /var/www/html/status.jpg
```

## Alternative Running Methods

### Foreground (for testing)

```bash
cd /home/pi/raspilapse
python3 src/auto_timelapse.py
# Press Ctrl+C to stop
```

### Screen/Tmux (manual background)

```bash
# Using screen
screen -S timelapse
python3 src/auto_timelapse.py
# Press Ctrl+A then D to detach

# Reattach later
screen -r timelapse

# Using tmux
tmux new -s timelapse
python3 src/auto_timelapse.py
# Press Ctrl+B then D to detach

# Reattach later
tmux attach -t timelapse
```

### Cron (not recommended)

While possible, cron is not recommended for continuous capture because:
- Service automatically restarts on failure
- Better logging with systemd
- Easier management

## Uninstallation

```bash
cd /home/pi/raspilapse
./uninstall_service.sh
```

Or manually:

```bash
sudo systemctl stop raspilapse
sudo systemctl disable raspilapse
sudo rm /etc/systemd/system/raspilapse.service
sudo systemctl daemon-reload
```

## Advanced Configuration

### Change Capture Interval

Edit `config/config.yml`:

```yaml
adaptive_timelapse:
  interval: 30  # seconds between captures
```

Then restart:
```bash
sudo systemctl restart raspilapse
```

### Multiple Instances

To run multiple timelapse instances (e.g., different cameras):

1. Create separate directories
2. Copy and modify service file with different names
3. Update paths in each service file

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

Enable and restart:
```bash
sudo ln -s /etc/nginx/sites-available/timelapse /etc/nginx/sites-enabled/
sudo systemctl restart nginx
```

## Summary

**Essential Commands:**
```bash
sudo systemctl start raspilapse       # Start
sudo systemctl stop raspilapse        # Stop
sudo systemctl restart raspilapse     # Restart
sudo systemctl status raspilapse      # Status
sudo journalctl -u raspilapse -f      # Logs
```

**Configuration:**
- Edit: `nano /home/pi/raspilapse/config/config.yml`
- Apply: `sudo systemctl restart raspilapse`

**Images:**
- Location: `/var/www/html/images/YYYY/MM/DD/`
- Latest: `/var/www/html/status.jpg`

---

**For more help, see:**
- [README.md](README.md) - Project overview
- [INSTALL.md](INSTALL.md) - Installation guide
- [USAGE.md](USAGE.md) - Usage examples
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues
