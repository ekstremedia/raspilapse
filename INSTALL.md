# Raspilapse Installation Guide

This guide will walk you through installing and setting up Raspilapse on your Raspberry Pi.

## Prerequisites

### Hardware Requirements
- Raspberry Pi (any model with CSI camera port)
- Raspberry Pi Camera Module V3 (or V2, HQ Camera)
- microSD card with Raspberry Pi OS installed (Bullseye or later)
- Power supply for your Raspberry Pi

### Software Requirements
- Raspberry Pi OS Bullseye or later (32-bit or 64-bit)
- Python 3.7 or higher
- Internet connection for initial setup

---

## Installation Steps

### 1. Enable the Camera Interface

First, ensure the camera interface is enabled on your Raspberry Pi:

```bash
sudo raspi-config
```

Navigate to:
- **Interface Options** → **Camera** → **Enable**

Reboot your Raspberry Pi:

```bash
sudo reboot
```

### 2. Connect the Camera Module

1. Power off your Raspberry Pi
2. Locate the CSI camera port (between HDMI and audio jack on most models)
3. Gently lift the plastic clip
4. Insert the camera ribbon cable with contacts facing toward the HDMI port
5. Press the clip back down
6. Power on your Raspberry Pi

### 3. Update System Packages

```bash
sudo apt update && sudo apt upgrade -y
```

### 4. Install Picamera2 Library

Raspilapse uses the official Picamera2 library for camera control:

```bash
# Standard installation (includes GUI preview support)
sudo apt install -y python3-picamera2

# OR for headless systems (minimal installation):
sudo apt install -y python3-picamera2 --no-install-recommends
```

**Important:** Always install via `apt`, never via `pip`, to avoid compilation issues.

### 5. Install Additional Dependencies

Install PyYAML for configuration file parsing:

```bash
sudo apt install -y python3-yaml
```

### 6. Test Camera Hardware

Verify the camera is working:

```bash
rpicam-still -o test.jpg
```

If successful, you should see a `test.jpg` file. View it to confirm the camera works.

### 7. Clone or Download Raspilapse

#### Option A: Clone from GitHub

```bash
cd ~
git clone https://github.com/YOUR_USERNAME/raspilapse.git
cd raspilapse
```

#### Option B: Download ZIP

Download the repository and extract it:

```bash
cd ~
# Download and extract (example)
unzip raspilapse-main.zip
cd raspilapse-main
```

### 8. Verify Installation

Test the installation by capturing a single image:

```bash
cd ~/raspilapse
python3 src/capture_image.py
```

You should see output like:
```
Image captured: test_photos/raspilapse_0000.jpg
Metadata saved: test_photos/raspilapse_0000_metadata.json
```

Check the logs directory for detailed logging:

```bash
cat logs/capture_image.log
```

---

## Optional: Set Up Python Virtual Environment

For advanced users who want to isolate dependencies (though Raspilapse works with system packages):

```bash
cd ~/raspilapse
python3 -m venv venv
source venv/bin/activate
pip install pyyaml
```

Note: You still need to install `python3-picamera2` via apt, not pip.

---

## Troubleshooting

### Camera Not Detected

**Check cable connection:**
```bash
rpicam-still -o test.jpg
```

If this fails, check:
- Camera cable is properly inserted
- Camera interface is enabled in raspi-config
- Camera is compatible (V2, V3, HQ Camera)

### Permission Errors

Add your user to the `video` group:

```bash
sudo usermod -aG video $USER
# Log out and log back in
```

### Import Errors

If you get `ModuleNotFoundError: No module named 'picamera2'`:

```bash
# Remove any pip installations
pip3 uninstall picamera2

# Install via apt
sudo apt install -y python3-picamera2
```

### Configuration File Not Found

Ensure you're running commands from the raspilapse directory:

```bash
cd ~/raspilapse
python3 src/capture_image.py
```

Or specify the config path:

```bash
python3 src/capture_image.py -c /full/path/to/config.yml
```

---

## Next Steps

Once installed, proceed to [USAGE.md](USAGE.md) to learn how to:
- Configure camera settings
- Capture images
- Create timelapses
- Adjust logging levels
- Customize output locations

---

## Uninstallation

To remove Raspilapse:

```bash
cd ~
rm -rf raspilapse

# Optionally remove picamera2 (if not used by other apps)
sudo apt remove python3-picamera2
```

---

## Getting Help

- Check [USAGE.md](USAGE.md) for usage instructions
- Review logs in `logs/capture_image.log` for error details
- Visit the GitHub repository for issues and discussions
- Consult [Picamera2 Manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)
