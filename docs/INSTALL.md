# Raspilapse Installation Guide

## Prerequisites

### Hardware
- Raspberry Pi (any model with CSI camera port)
- Raspberry Pi Camera Module V2, V3, or HQ Camera
- microSD card with Raspberry Pi OS (Bullseye or later)
- Power supply

### Software
- Raspberry Pi OS Bullseye or later (32-bit or 64-bit)
- Python 3.9 or higher

## Installation

### 1. Enable Camera Interface

```bash
sudo raspi-config
```

Navigate to: **Interface Options** > **Camera** > **Enable**

Reboot:
```bash
sudo reboot
```

### 2. Connect Camera Module

1. Power off the Raspberry Pi
2. Locate the CSI camera port (between HDMI and audio jack)
3. Lift the plastic clip
4. Insert ribbon cable with contacts facing the HDMI port
5. Press clip down
6. Power on

### 3. Update System

```bash
sudo apt update && sudo apt upgrade -y
```

### 4. Install Dependencies

```bash
# Core dependencies
sudo apt install -y python3-picamera2 python3-yaml python3-pil python3-numpy

# For video generation
sudo apt install -y ffmpeg

# For analysis and graphs (optional but recommended)
sudo apt install -y python3-matplotlib python3-openpyxl

# For sun position calculations (optional, for polar locations)
pip3 install astral
```

**Note:** Always install picamera2 via apt, not pip.

### 5. Test Camera

```bash
rpicam-still -o test.jpg
```

Check that test.jpg was created and looks correct.

### 6. Clone Repository

```bash
cd ~
git clone https://github.com/ekstremedia/raspilapse.git
cd raspilapse
```

### 7. Create Configuration

```bash
cp config/config.example.yml config/config.yml
```

Edit as needed:
```bash
nano config/config.yml
```

### 8. Test Installation

```bash
python3 src/auto_timelapse.py --test
```

You should see output indicating a successful capture.

### 9. Install as Service (Optional)

For 24/7 operation:

```bash
./scripts/install.sh
sudo systemctl start raspilapse
sudo systemctl status raspilapse
```

## Directory Structure

After installation:

```
raspilapse/
├── config/
│   └── config.yml       # Your configuration
├── src/                 # Python source code
├── scripts/             # Utility scripts
├── logs/                # Log files (created automatically)
├── data/                # Database (created automatically)
└── graphs/              # Generated graphs
```

## Troubleshooting

### Camera Not Detected

```bash
# Test hardware
rpicam-still -o test.jpg

# Check interface enabled
sudo raspi-config  # Interface Options > Camera
```

### Permission Errors

```bash
sudo usermod -aG video $USER
# Log out and back in
```

### Import Errors

```bash
# Remove pip installations
pip3 uninstall picamera2

# Reinstall via apt
sudo apt install -y python3-picamera2
```

### Missing Dependencies

```bash
# Check what's installed
python3 -c "import picamera2; print('picamera2 OK')"
python3 -c "import yaml; print('yaml OK')"
python3 -c "import PIL; print('PIL OK')"
python3 -c "import numpy; print('numpy OK')"
```

## Next Steps

- [USAGE.md](USAGE.md) - Learn how to use Raspilapse
- [SERVICE.md](SERVICE.md) - Set up 24/7 operation
- [OVERLAY.md](OVERLAY.md) - Configure image overlays

## Uninstallation

```bash
# Remove service
./scripts/uninstall.sh

# Remove repository
cd ~
rm -rf raspilapse
```
