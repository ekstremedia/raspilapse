# Raspilapse
A Python library to take pictures and create timelapses with Raspberry Pi and PiCamera V3

## Project Overview
This library provides a simple, user-friendly interface for creating timelapse videos using the Raspberry Pi Camera V3 module and the Picamera2 library.

---

## Picamera2 Library Reference

### Architecture & Background
- **Modern Stack**: Built on top of libcamera (NOT the legacy camera stack)
- **Official Support**: Maintained by Raspberry Pi Foundation
- **Python Interface**: High-level Python API for camera control
- **Pre-installed**: Comes with Raspberry Pi OS Bullseye and later (both 32-bit and 64-bit)
- **Hardware Support**: Works on all Raspberry Pi boards (Pi Zero to Pi 5)
- **Camera Compatibility**: Supports Camera V2, V3, and sensors including OV5647, IMX219, IMX477, IMX708, global shutter sensors

### Installation
```bash
# Standard installation
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-picamera2

# Minimal installation (no GUI/preview dependencies - good for headless)
sudo apt install -y python3-picamera2 --no-install-recommends

# If previously installed via pip, remove first
pip3 uninstall picamera2
```

**Important**: Use apt, NOT pip, to avoid compilation issues.

### Basic Usage Pattern
Every Picamera2 script follows this structure:

```python
from picamera2 import Picamera2, Preview
import time

# Initialize camera
picam2 = Picamera2()

# Create and apply configuration
config = picam2.create_preview_configuration()
picam2.configure(config)

# Start camera
picam2.start()

# Perform operations (capture, record, etc.)
time.sleep(2)  # Allow camera to adjust settings

# Clean up
picam2.close()
```

### Core Methods

#### Initialization & Configuration
- `Picamera2()` - Create camera object
- `create_preview_configuration(main={...})` - Standard capture configuration
- `create_video_configuration()` - Video recording configuration
- `configure(config)` - Apply configuration to camera
- `start()` - Start camera operations
- `close()` - Clean shutdown

#### Capture Methods
- `capture_file("filename.jpg")` - Capture and save JPG image
- `capture_array()` - Capture as numpy array for processing
- `capture_metadata()` - Get capture metadata

#### Video Recording
```python
from picamera2.encoders import H264Encoder

video_config = picam2.create_video_configuration()
picam2.configure(video_config)
encoder = H264Encoder(10000000)  # 10Mbps bitrate
picam2.start_recording(encoder, 'output.h264')
time.sleep(10)  # Record duration
picam2.stop_recording()
```

Or simplified:
```python
picam2.start_and_record_video("video.mp4", duration=5)
```

#### Preview System
```python
picam2.start_preview(Preview.QTGL)  # Qt-based preview window
```

Preview backends: QTGL, eglfs, linuxfb, minimal, minimalegl, offscreen, vnc

### Configuration Options

#### Resolution
```python
config = picam2.create_preview_configuration(main={"size": (1600, 1200)})
picam2.configure(config)
```

Common resolutions for Camera V3:
- 4608 × 2592 (11.9MP, full sensor)
- 2304 × 1296 (3MP, 2x2 binned)
- 1920 × 1080 (Full HD)
- 1280 × 720 (HD)

#### Image Transforms
```python
import libcamera

config = picam2.create_preview_configuration()
config["transform"] = libcamera.Transform(hflip=1, vflip=1)
picam2.configure(config)
```

- `hflip=1` - Horizontal flip
- `vflip=1` - Vertical flip

#### Camera Controls
Set controls after starting camera:

```python
picam2.start()

# Exposure
picam2.set_controls({"ExposureTime": 20000, "AnalogueGain": 1.0})

# White Balance
picam2.set_controls({"AwbEnable": 0, "ColourGains": (1.5, 1.5)})

# Brightness & Contrast
picam2.set_controls({"Brightness": 0.0, "Contrast": 1.0})

# Autofocus (for AF-enabled cameras like some V3 variants)
picam2.set_controls({"AfMode": 2, "AfTrigger": 0})
```

**Autofocus Modes**:
- `AfMode: 0` - Manual focus (use with `LensPosition: 0-15`)
- `AfMode: 1` - Single autofocus
- `AfMode: 2` - Continuous autofocus

### Timelapse Implementation

#### Basic Timelapse Loop
```python
from picamera2 import Picamera2
import time

picam2 = Picamera2()
config = picam2.create_preview_configuration()
picam2.configure(config)
picam2.start()

# Allow camera to stabilize
time.sleep(2)

# Capture frames
num_frames = 100
interval_seconds = 3

for i in range(num_frames):
    picam2.capture_file(f"frame{i:04d}.jpg")
    time.sleep(interval_seconds)

picam2.close()
```

#### Converting to Video
```bash
# Using ffmpeg
ffmpeg -r 30 -pattern_type glob -i "frame*.jpg" -vcodec libx264 -pix_fmt yuv420p timelapse.mp4

# Or with frame rate control
ffmpeg -framerate 30 -i frame%04d.jpg -c:v libx264 -pix_fmt yuv420p output.mp4
```

### Testing Camera

#### Hardware Test
```bash
# Verify camera detection and basic functionality
rpicam-still -o test.jpg

# With preview (5 second delay)
rpicam-still -o test.jpg -t 5000
```

#### Python Test
```python
from picamera2 import Picamera2

picam2 = Picamera2()
print(picam2.camera_properties)
picam2.close()
```

### Important Notes & Best Practices

1. **Always call `picam2.close()`** when done to release camera resources
2. **Allow stabilization time** (2 seconds) after `start()` before capturing
3. **Use context managers** for automatic cleanup:
   ```python
   with Picamera2() as picam2:
       # operations
   ```
4. **Headless systems**: Use `--no-install-recommends` for lighter installation
5. **Preview on headless**: May need alternative backends or skip preview entirely
6. **Not supported on**: Raspberry Pi OS Buster or earlier
7. **Performance**: Older Pi models (Pi 3 and earlier) may need Glamor acceleration enabled

### Common Issues & Solutions

- **Remote Desktop artifacts**: Preview may show visual glitches over VNC/RDP, but captured files are fine
- **Camera not detected**: Check cable connection, try `rpicam-still` to test
- **Permission errors**: User must be in `video` group: `sudo usermod -aG video $USER`
- **Import errors**: Ensure installed via apt, not pip

### Official Resources

- **Manual PDF**: https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf
- **GitHub**: https://github.com/raspberrypi/picamera2
- **Examples**: Check `examples/` folder in GitHub repo
- **Documentation**: https://github.com/raspberrypi/documentation/blob/develop/documentation/asciidoc/computers/camera/

---

## Development Guidelines

- Use Picamera2's native methods rather than shell commands
- Provide clear error messages for common issues
- Support both GUI and headless modes
- Allow flexible configuration (resolution, interval, duration)
- Save metadata with captures (timestamp, settings)
- Implement graceful shutdown on interrupts