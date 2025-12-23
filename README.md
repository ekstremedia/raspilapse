# Raspilapse

![Tests](https://github.com/ekstremedia/raspilapse/workflows/Tests/badge.svg)
[![codecov](https://codecov.io/gh/ekstremedia/raspilapse/branch/main/graph/badge.svg)](https://codecov.io/gh/ekstremedia/raspilapse)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Version](https://img.shields.io/badge/version-1.0.4-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

A Python library for creating timelapses with Raspberry Pi Camera. Supports adaptive day/night exposure, image overlays, and optimized long exposures up to 20 seconds.

## Requirements

- Raspberry Pi (any model with CSI port)
- Camera Module V2, V3, or HQ Camera
- Raspberry Pi OS Bullseye or later
- Python 3.9+

## Installation

```bash
# Enable camera interface
sudo raspi-config  # Interface Options → Camera → Enable → Reboot

# Install dependencies
sudo apt update
sudo apt install -y python3-picamera2 python3-yaml python3-pil

# Clone and configure
git clone https://github.com/ekstremedia/raspilapse.git
cd raspilapse
cp config/config.example.yml config/config.yml

# Test capture
python3 src/capture_image.py
```

## Running as a Service

For 24/7 operation:

```bash
./scripts/install.sh
sudo systemctl status raspilapse
```

Images save to `/var/www/html/images/YYYY/MM/DD/` by default.

## Configuration

Edit `config/config.yml`:

```yaml
camera:
  resolution:
    width: 1920
    height: 1080

output:
  directory: "captured_images"
  filename_pattern: "{name}_%Y_%m_%d_%H_%M_%S.jpg"
  project_name: "timelapse"
  quality: 95

timelapse:
  interval: 30  # seconds between captures

adaptive_timelapse:
  enabled: true
  light_thresholds:
    night: 10    # lux
    day: 100     # lux
  night_mode:
    max_exposure_time: 20.0  # seconds
    analogue_gain: 6.0

overlay:
  enabled: true
  position: "bottom-left"
  camera_name: "My Camera"
```

## Key Features

**Adaptive Exposure** - Automatically switches between day/night modes based on ambient light, with smooth transitions to prevent flickering.

**Long Exposure Optimization** - 20-second exposures complete in ~20 seconds (not 100+) through proper libcamera configuration.

**Image Overlays** - Configurable text overlays with timestamps, camera settings, and weather data.

**Analysis Tools** - Generate graphs and Excel reports from capture metadata:
```bash
python3 src/analyze_timelapse.py --hours 24
```

**Daily Video Generation** - Automatic timelapse compilation with systemd timer.

## Usage

```bash
# Single capture
python3 src/capture_image.py

# Adaptive timelapse (manual)
python3 src/auto_timelapse.py

# Test single frame
python3 src/auto_timelapse.py --test

# Check status
python3 src/status.py

# Generate video from images
python3 src/make_timelapse.py
```

## Project Structure

```
raspilapse/
├── src/                    # Source code
│   ├── auto_timelapse.py   # Adaptive day/night capture
│   ├── capture_image.py    # Core capture module
│   ├── make_timelapse.py   # Video generation
│   ├── analyze_timelapse.py # Graphs and analysis
│   ├── overlay.py          # Image overlays
│   └── status.py           # Status display
├── config/                 # Configuration files
├── scripts/                # Install/uninstall scripts
├── systemd/                # Service files
├── docs/                   # Documentation
└── tests/                  # Unit tests
```

## Documentation

| Document | Description |
|----------|-------------|
| [INSTALL.md](docs/INSTALL.md) | Installation guide |
| [SERVICE.md](docs/SERVICE.md) | Systemd service setup |
| [OVERLAY.md](docs/OVERLAY.md) | Overlay configuration |
| [TRANSITION_SMOOTHING.md](docs/TRANSITION_SMOOTHING.md) | Day/night transition system |
| [CLAUDE.md](docs/CLAUDE.md) | Technical reference (Picamera2) |
| [CHANGELOG.md](CHANGELOG.md) | Version history |

## Troubleshooting

**Camera not detected:**
```bash
rpicam-still -o test.jpg  # Test hardware
sudo raspi-config         # Check interface enabled
```

**Import errors:**
```bash
sudo apt install -y python3-picamera2  # Use apt, not pip
```

**Permission denied:**
```bash
sudo usermod -aG video $USER  # Add to video group, then re-login
```

## Development

```bash
# Run tests
python3 -m pytest tests/ -v

# Format code
black src/ tests/ --line-length=100

# Or use Makefile
make format && make test
```

## License

MIT License - see [LICENSE](LICENSE)

Copyright © 2024-2025 Terje Nesthus

## Author

**Terje Nesthus** - [ekstremedia.no](https://ekstremedia.no)
