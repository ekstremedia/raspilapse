# Raspilapse

![Tests](https://github.com/ekstremedia/raspilapse/workflows/Tests/badge.svg)
[![codecov](https://codecov.io/gh/ekstremedia/raspilapse/branch/main/graph/badge.svg)](https://codecov.io/gh/ekstremedia/raspilapse)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Version](https://img.shields.io/badge/version-0.9.0--beta-orange)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi-red)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

> 🎥 **A Python library for creating professional timelapses with Raspberry Pi Camera**
>
> Created by **Terje Nesthus** | Open Source (MIT) | Currently in Beta

A simple, user-friendly Python library for creating timelapses with Raspberry Pi and Camera Module V3. Features adaptive exposure, beautiful overlays, and optimized long exposures for day/night photography.

## Features

### Core Features
- ✨ **Easy to Use** - Simple configuration and command-line interface
- 📝 **Flexible Configuration** - YAML-based config for all camera and output settings
- 📊 **Professional Logging** - Comprehensive logging with automatic rotation
- 📷 **Metadata Capture** - Saves detailed metadata with each image
- 🎛️ **Camera Controls** - Full control over exposure, white balance, focus, and more
- 🖼️ **Multiple Resolutions** - Support for all Camera V3 resolutions (up to 11.9MP)
- 🔄 **Image Transforms** - Horizontal and vertical flipping

### Advanced Features
- 🚀 **Optimized Long Exposures** - Fast 20s exposures (~20-22s capture time) with proper libcamera configuration
- 🌅 **Adaptive Timelapse** - Automatically adjusts exposure based on ambient light (day/night/transition modes)
- 🎨 **Image Overlay System** - Beautiful, configurable overlays with camera settings, timestamps, and metadata
- 🌍 **Localized Timestamps** - Multi-language datetime formatting (Norwegian, English, etc.)
- 🔗 **Web Integration** - Automatic symlink to latest image for web servers
- 🎭 **Gradient Backgrounds** - Professional semi-transparent overlays that adapt to image brightness
- 🧪 **Fully Tested** - 64 unit tests with CI/CD integration
- 🆓 **Open Source** - MIT licensed, free to use and modify

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
sudo apt install -y python3-picamera2 python3-yaml python3-pil

# Clone repository
git clone https://github.com/ekstremedia/raspilapse.git
cd raspilapse

# Test installation
python3 src/capture_image.py
```

### Production Setup (Continuous Timelapse)

For continuous 24/7 operation as a background service:

```bash
# Install and start service
./install_service.sh

# Check status
sudo systemctl status raspilapse

# View logs
sudo journalctl -u raspilapse -f
```

Images are automatically saved to `/var/www/html/images/YYYY/MM/DD/` and organized by date.

See [SERVICE.md](SERVICE.md) for complete service documentation.

### Manual Capture

For one-off captures or testing:

```bash
python3 src/capture_image.py
```

Images are saved to the directory specified in `config/config.yml`.

## Documentation

- **[SERVICE.md](SERVICE.md)** - Running as a background service (systemd)
- **[INSTALL.md](INSTALL.md)** - Complete installation guide
- **[USAGE.md](USAGE.md)** - Usage guide and configuration reference
- **[OVERLAY.md](OVERLAY.md)** - Image overlay system documentation
- **[CLAUDE.md](CLAUDE.md)** - Technical reference for Picamera2
- **[CHANGELOG.md](CHANGELOG.md)** - Version history

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
- **Code formatting checks with Black** (must pass!)
- Type checking with mypy
- Coverage reporting

All tests can run in CI/CD without requiring actual camera hardware.

### Development Workflow

```bash
# Quick way: Use Makefile commands
make format    # Format code with Black
make test      # Run tests
make all       # Format, check, and test (recommended before commit!)

# Manual way:
black src/ tests/ --line-length=100
python3 -m pytest tests/ -v

# Pre-commit hooks (automatic formatting on git commit):
pip3 install pre-commit
pre-commit install
```

**IMPORTANT**: Always run `make format` or `black src/ tests/ --line-length=100` before committing to avoid CI failures!

## Roadmap to 1.0.0

Current version: **0.9.0-beta** 🚧

### What's Working ✅
- ✅ Core image capture
- ✅ Adaptive timelapse (day/night/transition)
- ✅ Image overlay system with localization
- ✅ Long exposure optimization
- ✅ Comprehensive logging
- ✅ Full test coverage (64 tests)
- ✅ CI/CD pipeline

### Planned for 1.0.0 Stable Release 🎯
- 🔄 Video compilation script (ffmpeg wrapper)
- 🌐 Web interface for monitoring
- 📱 Mobile app integration APIs
- ⏰ Advanced scheduling (cron-like)
- ☁️ Cloud storage integration (optional)
- 📖 Video tutorials and examples
- 🌍 Multi-language documentation

### How to Contribute 🤝

Contributions are welcome! This is a free, open-source project under the MIT license.

**Ways to contribute:**
1. 🐛 Report bugs and issues
2. 💡 Suggest new features
3. 📝 Improve documentation
4. 🧪 Add more tests
5. 💻 Submit pull requests

**Contribution process:**
1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes and add tests
4. Run the test suite (`python3 -m pytest tests/`)
5. Format your code (`black src/ tests/`)
6. Commit your changes (`git commit -m 'Add amazing feature'`)
7. Push to the branch (`git push origin feature/amazing-feature`)
8. Open a Pull Request

See [CHANGELOG.md](CHANGELOG.md) for version history.

## License

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.

Copyright © 2024-2025 Terje Nesthus

You are free to:
- ✅ Use commercially
- ✅ Modify and distribute
- ✅ Use privately
- ✅ Sublicense

## Author

**Terje Nesthus**
- 🌐 Website: [ekstremedia.no](https://ekstremedia.no)
- 💼 Company: Ekstremedia
- 📧 Email: terje@ekstremedia.no
- 🐙 GitHub: [@ekstremedia](https://github.com/ekstremedia)

## Credits & Acknowledgments

Built with:
- [Picamera2](https://github.com/raspberrypi/picamera2) - Official Raspberry Pi camera library
- [Pillow](https://python-pillow.org/) - Python Imaging Library for overlay system
- [PyYAML](https://pyyaml.org/) - YAML parser for configuration
- Python 3.9+ and the Raspberry Pi Foundation

Special thanks to the Raspberry Pi community for their excellent documentation and support.

## Support

- **Installation issues:** See [INSTALL.md](INSTALL.md)
- **Usage questions:** See [USAGE.md](USAGE.md)
- **Bug reports:** Open an issue on GitHub
- **Check logs:** `logs/capture_image.log`

---

**Happy timelapsing! 📷**
