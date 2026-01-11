# Raspilapse Usage Guide

This guide covers daily operation of Raspilapse for capturing timelapses.

## Quick Start

### Test a Single Capture

```bash
cd ~/raspilapse
python3 src/auto_timelapse.py --test
```

This takes one test shot with adaptive exposure and exits. Check the output in your configured image directory.

### Run Continuously (Foreground)

```bash
python3 src/auto_timelapse.py
```

Press Ctrl+C to stop. For 24/7 operation, use the systemd service instead.

### Run as Service (Recommended)

```bash
# Install and start
./scripts/install.sh
sudo systemctl start raspilapse

# Check status
sudo systemctl status raspilapse

# View logs
sudo journalctl -u raspilapse -f
```

See [SERVICE.md](SERVICE.md) for complete service management.

## Configuration

All settings are in `config/config.yml`. Copy the example if needed:

```bash
cp config/config.example.yml config/config.yml
```

### Key Settings

```yaml
# Camera resolution
camera:
  resolution:
    width: 1920
    height: 1080

# Capture interval (seconds)
adaptive_timelapse:
  interval: 30

# Light thresholds for day/night switching
adaptive_timelapse:
  light_thresholds:
    night: 3      # Below this lux = night mode
    day: 80       # Above this lux = day mode

# Output location
output:
  directory: "/var/www/html/images"
  project_name: "timelapse"
```

After editing config, restart the service:

```bash
sudo systemctl restart raspilapse
```

## Monitoring and Analysis

### Check System Status

```bash
python3 src/status.py
```

Shows service status, configuration summary, recent captures, and timing info.

### View Database Statistics

```bash
python3 scripts/db_stats.py           # Last 1 hour (default)
python3 scripts/db_stats.py 5m        # Last 5 minutes
python3 scripts/db_stats.py 24h       # Last 24 hours
python3 scripts/db_stats.py 7d        # Last 7 days
python3 scripts/db_stats.py --all     # All captures
python3 scripts/db_stats.py -n 10     # Last 10 captures
```

Shows capture counts, averages (lux, brightness, exposure), mode distribution, and weather data.

### Generate Graphs from Database

```bash
python3 scripts/db_graphs.py          # Last 24 hours (default)
python3 scripts/db_graphs.py 6h       # Last 6 hours
python3 scripts/db_graphs.py 7d       # Last 7 days
python3 scripts/db_graphs.py --all    # All data
```

Creates PNG graphs in the `graphs/` directory:
- `lux_levels.png` - Light levels with day/night zones
- `exposure_gain.png` - Exposure time and gain
- `brightness.png` - Image brightness metrics
- `weather.png` - Temperature, humidity, wind
- `system.png` - CPU temperature and load
- `overview.png` - Summary of key metrics

### Analyze from Metadata Files

```bash
python3 src/analyze_timelapse.py              # Last 24 hours
python3 src/analyze_timelapse.py --hours 48   # Last 48 hours
```

Generates detailed graphs from JSON metadata files and exports to Excel.

## Video Generation

### Create Timelapse Video

```bash
# Default: 05:00 yesterday to 05:00 today
python3 src/make_timelapse.py

# Custom time range
python3 src/make_timelapse.py --start 07:00 --end 19:00 --today

# Specific dates
python3 src/make_timelapse.py --start 05:00 --end 05:00 --start-date 2025-01-10 --end-date 2025-01-11

# Test with limited frames
python3 src/make_timelapse.py --limit 100
```

See [TIMELAPSE_VIDEO.md](TIMELAPSE_VIDEO.md) for complete options.

### Automatic Daily Videos

The `raspilapse-daily-video.timer` creates videos automatically at 05:00 each day. Install with:

```bash
./scripts/install_daily_video.sh
```

See [DAILY_VIDEO.md](DAILY_VIDEO.md) for configuration.

## Common Operations

### View Recent Captures

```bash
ls -lht /var/www/html/images/$(date +%Y/%m/%d)/ | head -10
```

### Check Capture Rate

```bash
# Images in last hour (expect ~120 for 30s interval)
find /var/www/html/images -name "*.jpg" -mmin -60 | wc -l
```

### Check Disk Space

```bash
df -h /var/www/html/images
du -sh /var/www/html/images
```

### View Service Logs

```bash
# Follow live
sudo journalctl -u raspilapse -f

# Last 100 lines
sudo journalctl -u raspilapse -n 100

# Errors only
sudo journalctl -u raspilapse -p err
```

## Image Overlays

Text overlays can be added to images showing timestamp, camera settings, and weather data. Enable in config:

```yaml
overlay:
  enabled: true
  position: "bottom-left"
  camera_name: "My Camera"
```

See [OVERLAY.md](OVERLAY.md) for complete configuration.

## Weather Integration

Display weather data from a Netatmo station:

```yaml
weather:
  enabled: true
  endpoint: "http://your-server/api/netatmo/stations/your-station-id"
```

See [WEATHER.md](WEATHER.md) for setup.

## Troubleshooting

### Service Not Running

```bash
sudo systemctl status raspilapse
sudo journalctl -u raspilapse -n 50
```

### Camera Not Detected

```bash
rpicam-still -o test.jpg
sudo raspi-config  # Interface Options > Camera > Enable
```

### Disk Full

```bash
df -h /var/www/html/images

# Emergency cleanup (delete images older than 3 days)
find /var/www/html/images -name "*.jpg" -mtime +3 -delete
```

### Images Too Dark/Bright

Check adaptive timelapse settings:

```yaml
adaptive_timelapse:
  light_thresholds:
    night: 3    # Adjust these
    day: 80
  reference_lux: 3.8  # Target brightness control
```

## File Locations

| Item | Location |
|------|----------|
| Configuration | `config/config.yml` |
| Images | `/var/www/html/images/YYYY/MM/DD/` |
| Videos | `/var/www/html/videos/YYYY/MM/` |
| Database | `data/timelapse.db` |
| Logs | `logs/` |
| Graphs | `graphs/` |

## Related Documentation

- [INSTALL.md](INSTALL.md) - Installation guide
- [SERVICE.md](SERVICE.md) - Systemd service management
- [OVERLAY.md](OVERLAY.md) - Image overlay configuration
- [TIMELAPSE_VIDEO.md](TIMELAPSE_VIDEO.md) - Video generation
- [MAINTENANCE.md](MAINTENANCE.md) - Long-term operation
