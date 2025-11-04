# Raspilapse

![Tests](https://github.com/ekstremedia/raspilapse/workflows/Tests/badge.svg)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi-red)

A simple, user-friendly Python library for creating timelapses with Raspberry Pi and Camera Module V3.

## Features

- **Easy to Use** - Simple configuration and command-line interface
- **Flexible Configuration** - YAML-based config for all camera and output settings
- **Professional Logging** - Comprehensive logging with automatic rotation
- **Metadata Capture** - Saves detailed metadata with each image
- **Camera Controls** - Full control over exposure, white balance, focus, and more
- **Multiple Resolutions** - Support for all Camera V3 resolutions (up to 11.9MP)
- **Image Transforms** - Horizontal and vertical flipping
- **Open Source** - Free to use and modify

## Hardware Requirements

- Raspberry Pi (any model with CSI camera port)
- Raspberry Pi Camera Module V3 (or V2, HQ Camera)
- Raspberry Pi OS Bullseye or later

## Quick Start

### Installation

```bash
# Enable camera interface
sudo raspi-config
# Interface Options → Camera → Enable → Reboot

# Install dependencies
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-picamera2 python3-yaml

# Clone repository
git clone https://github.com/ekstremedia/raspilapse.git
cd raspilapse

# Test installation
python3 src/capture_image.py
```

### Capture an Image

```bash
python3 src/capture_image.py
```

Images are saved to `test_photos/` by default with metadata and logs.

## Documentation

- **[INSTALL.md](INSTALL.md)** - Complete installation guide
- **[USAGE.md](USAGE.md)** - Usage guide and configuration reference
- **[CLAUDE.md](CLAUDE.md)** - Technical reference for Picamera2

## Configuration

Edit `config/config.yml` to customize:

- **Camera settings** - Resolution, exposure, white balance, focus
- **Output settings** - Directory, filename patterns, quality
- **Logging** - Log levels, file paths, rotation settings
- **Metadata** - Enable/disable metadata capture

### Example Configuration

```yaml
camera:
  resolution:
    width: 1920
    height: 1080

output:
  directory: "captured_images"
  filename_pattern: "{name}_{counter}.jpg"
  project_name: "my_timelapse"
  quality: 95

logging:
  enabled: true
  level: "INFO"
  log_file: "logs/{script}.log"
```

## Basic Usage Examples

### Capture with Default Settings

```bash
python3 src/capture_image.py
```

### Use Custom Config

```bash
python3 src/capture_image.py -c config/custom.yml
```

### Specify Output Path

```bash
python3 src/capture_image.py -o photos/sunset.jpg
```

### Create a Timelapse (Simple Loop)

```bash
#!/bin/bash
cd ~/raspilapse
while true; do
    python3 src/capture_image.py
    sleep 5  # Wait 5 seconds between captures
done
```

### Convert Images to Video

```bash
# Install ffmpeg
sudo apt install -y ffmpeg

# Create timelapse video (30 FPS)
cd captured_images
ffmpeg -framerate 30 -pattern_type glob -i "*.jpg" \
    -c:v libx264 -pix_fmt yuv420p \
    timelapse.mp4
```

## Logging

Raspilapse includes comprehensive logging:

- Automatic log file creation in `logs/` directory
- Configurable log levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- Automatic log rotation when files reach size limit
- Console and file output
- Detailed timestamps and error tracking

**View logs:**

```bash
cat logs/capture_image.log
```

**Monitor in real-time:**

```bash
tail -f logs/capture_image.log
```

## Metadata

Each captured image can have an associated metadata JSON file containing:

- Capture timestamp
- Camera settings (exposure, gains, etc.)
- Image resolution and quality
- File path

**Example metadata:**

```json
{
  "ExposureTime": 13968,
  "AnalogueGain": 1.2,
  "capture_timestamp": "2025-11-04T19:00:12.345678",
  "resolution": [1920, 1080],
  "quality": 95
}
```

## Project Structure

```
raspilapse/
├── config/
│   └── config.yml           # Main configuration file
├── src/
│   ├── capture_image.py     # Image capture module
│   └── logging_config.py    # Logging configuration
├── logs/                    # Log files (auto-created)
├── test_photos/             # Default output directory
├── tests/                   # Unit tests
├── INSTALL.md               # Installation guide
├── USAGE.md                 # Usage guide
├── CLAUDE.md                # Technical reference
└── README.md                # This file
```

## Advanced Features

### Camera Controls

Fine-tune camera behavior in `config.yml`:

```yaml
camera:
  controls:
    exposure_time: 20000      # Microseconds
    analogue_gain: 1.5        # Brightness multiplier
    awb_enable: true          # Auto white balance
    brightness: 0.0           # -1.0 to 1.0
    contrast: 1.0             # 0.0 to 2.0
    af_mode: 2                # Autofocus mode
```

### Custom Filename Patterns

Use placeholders and strftime formatting:

```yaml
output:
  # Sequential: project_0000.jpg, project_0001.jpg
  filename_pattern: "{name}_{counter}.jpg"

  # With timestamp: timelapse_2025-11-04T18:30:00.jpg
  filename_pattern: "{name}_{timestamp}.jpg"

  # Date-based: sunset_20251104_183000.jpg
  filename_pattern: "{name}_%Y%m%d_%H%M%S.jpg"
```

## Troubleshooting

### Camera Not Detected

```bash
# Test camera hardware
rpicam-still -o test.jpg

# Check camera interface is enabled
sudo raspi-config
```

### Import Errors

```bash
# Always install via apt, not pip
sudo apt install -y python3-picamera2
```

### Permission Issues

```bash
# Add user to video group
sudo usermod -aG video $USER
# Log out and back in
```

**For more troubleshooting, see [INSTALL.md](INSTALL.md) and check `logs/capture_image.log`**

## Use Cases

- **Construction timelapses** - Monitor building progress
- **Nature photography** - Capture plant growth, weather changes
- **Astronomy** - Long-exposure night sky timelapses
- **Art projects** - Stop-motion animation
- **Security monitoring** - Periodic image capture
- **Scientific research** - Document experiments

## Development & Testing

### Running Tests

The project includes comprehensive unit tests that run without requiring camera hardware (using mocks).

```bash
# Install development dependencies
sudo apt install -y python3-pytest

# Run all tests
python3 -m pytest tests/ -v

# Run with coverage
python3 -m pytest tests/ -v --cov=src --cov-report=term-missing
```

### Continuous Integration

GitHub Actions automatically runs tests on every push and pull request across multiple Python versions (3.9, 3.10, 3.11, 3.12). The pipeline includes:

- Unit tests with mocking (no hardware required)
- Code linting with flake8
- Code formatting checks with black
- Type checking with mypy
- Coverage reporting

All tests can run in CI/CD without requiring actual camera hardware.

## Contributing

Contributions are welcome! This is a free, open-source project.

### How to Contribute

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests (`python3 -m pytest tests/`)
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## License

Free to use and modify. See LICENSE file for details.

## Credits

Built using:
- [Picamera2](https://github.com/raspberrypi/picamera2) - Official Raspberry Pi camera library
- Python 3 and PyYAML
- Raspberry Pi Camera Module V3

## Support

- **Installation issues:** See [INSTALL.md](INSTALL.md)
- **Usage questions:** See [USAGE.md](USAGE.md)
- **Bug reports:** Open an issue on GitHub
- **Check logs:** `logs/capture_image.log`

---

**Happy timelapsing! 📷**
