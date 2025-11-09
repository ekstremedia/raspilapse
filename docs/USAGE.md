# Raspilapse Usage Guide

Complete guide to using Raspilapse for capturing images and creating timelapses.

---

## Quick Start

### Capture a Single Image

```bash
cd ~/raspilapse
python3 src/capture_image.py
```

This captures an image using default settings from `config/config.yml`.

### View the Results

Images are saved to the directory specified in your config (default: `test_photos/`):

```bash
ls -lh test_photos/
```

Check the logs:

```bash
cat logs/capture_image.log
```

---

## Configuration

All settings are configured in `config/config.yml`. Edit this file to customize behavior.

### Camera Settings

#### Resolution

```yaml
camera:
  resolution:
    width: 1920
    height: 1080
```

**Common resolutions for Camera V3:**
- `4608 × 2592` - Full 11.9MP sensor
- `2304 × 1296` - 3MP (2×2 binned, faster)
- `1920 × 1080` - Full HD
- `1280 × 720` - HD (smaller files)

#### Image Transforms

Flip the image horizontally or vertically:

```yaml
camera:
  transforms:
    horizontal_flip: false
    vertical_flip: false
```

#### Camera Controls (Advanced)

Fine-tune camera behavior by uncommenting and adjusting these settings:

```yaml
camera:
  controls:
    # Exposure time in microseconds (e.g., 20000 = 20ms)
    exposure_time: 20000

    # Analogue gain (1.0 = normal, higher = brighter)
    analogue_gain: 1.5

    # Auto white balance
    awb_enable: true

    # Manual colour gains (if awb disabled)
    colour_gains: [1.5, 1.5]

    # Brightness (-1.0 to 1.0, 0 = normal)
    brightness: 0.0

    # Contrast (0.0 to 2.0, 1.0 = normal)
    contrast: 1.0

    # Autofocus mode (if camera supports it)
    # 0 = Manual, 1 = Single AF, 2 = Continuous AF
    af_mode: 2
```

### Output Settings

#### Output Directory

```yaml
output:
  directory: "captured_images"
```

Supports:
- Relative paths: `images/timelapse`
- Absolute paths: `/home/pi/photos`

#### Filename Pattern

Customize how files are named:

```yaml
output:
  filename_pattern: "{name}_{counter}.jpg"
  project_name: "my_timelapse"
```

**Available placeholders:**
- `{name}` - Project name
- `{counter}` - Auto-incrementing counter (4 digits, zero-padded)
- `{timestamp}` - ISO format timestamp
- Strftime directives: `%Y%m%d_%H%M%S` (date/time formatting)

**Examples:**

```yaml
# Simple sequential: project_0000.jpg, project_0001.jpg, ...
filename_pattern: "{name}_{counter}.jpg"

# With timestamp: timelapse_2025-11-04T18:30:00.jpg
filename_pattern: "{name}_{timestamp}.jpg"

# Date-based: sunset_20251104_183000.jpg
filename_pattern: "{name}_%Y%m%d_%H%M%S.jpg"
```

#### Image Quality

JPEG compression quality (0-100):

```yaml
output:
  quality: 95  # Higher = better quality, larger files
```

### System Settings

```yaml
system:
  # Auto-create output directory if missing
  create_directories: true

  # Save metadata JSON file with each image
  save_metadata: true

  # Metadata filename pattern
  metadata_filename: "{name}_{counter}_metadata.json"
```

### Logging Settings

Control logging behavior:

```yaml
logging:
  # Enable/disable logging
  enabled: true

  # Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL
  level: "INFO"

  # Log file path (relative to project root)
  # {script} is replaced with script name
  log_file: "logs/{script}.log"

  # Log message format
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

  # Timestamp format
  date_format: "%Y-%m-%d %H:%M:%S"

  # Also show logs in console
  console: true

  # Rotate log files when they reach this size (MB)
  max_size_mb: 10

  # Keep this many backup log files
  backup_count: 5
```

**Log Levels Explained:**
- `DEBUG` - Detailed information for diagnosing problems
- `INFO` - Confirmation that things are working as expected (default)
- `WARNING` - Something unexpected happened, but still working
- `ERROR` - A serious problem occurred
- `CRITICAL` - Very serious error, program may not continue

---

## Command Line Usage

### Basic Commands

#### Capture with Default Config

```bash
python3 src/capture_image.py
```

#### Use Custom Config File

```bash
python3 src/capture_image.py -c /path/to/custom_config.yml
```

#### Specify Custom Output Path

```bash
python3 src/capture_image.py -o /path/to/custom_image.jpg
```

#### Combine Options

```bash
python3 src/capture_image.py -c config/night_config.yml -o night_sky.jpg
```

### Help

```bash
python3 src/capture_image.py --help
```

---

## Use Cases

### 1. Standard Timelapse Setup

**Goal:** Capture images every 5 seconds for a construction timelapse

**Config settings:**
```yaml
output:
  directory: "construction_timelapse"
  filename_pattern: "frame_%Y%m%d_%H%M%S.jpg"
  project_name: "construction"

camera:
  resolution:
    width: 1920
    height: 1080

logging:
  level: "INFO"
```

**Run with cron (every 5 seconds not supported by cron, use while loop):**

```bash
#!/bin/bash
cd ~/raspilapse
while true; do
    python3 src/capture_image.py
    sleep 5
done
```

### 2. High-Resolution Daily Photos

**Goal:** Take one high-quality photo per day at noon

**Config settings:**
```yaml
camera:
  resolution:
    width: 4608
    height: 2592

output:
  quality: 100
  filename_pattern: "daily_%Y-%m-%d.jpg"
  directory: "daily_photos"
```

**Cron job (run daily at 12:00 PM):**

```bash
crontab -e
```

Add:
```
0 12 * * * cd ~/raspilapse && python3 src/capture_image.py
```

### 3. Debug Mode for Testing

**Config settings:**
```yaml
logging:
  level: "DEBUG"
  console: true
```

This provides detailed logging to help diagnose issues.

---

## Metadata Files

When `save_metadata: true`, Raspilapse saves a JSON file alongside each image containing:

- Capture timestamp
- Camera resolution
- Image quality setting
- Raw camera metadata (exposure, gains, etc.)
- File path

**Example metadata:**

```json
{
  "ExposureTime": 13968,
  "AnalogueGain": 1.2,
  "ColourGains": [1.95, 1.88],
  "capture_timestamp": "2025-11-04T19:00:12.345678",
  "image_path": "test_photos/raspilapse_0000.jpg",
  "resolution": [1920, 1080],
  "quality": 95
}
```

This is invaluable for analyzing lighting conditions, troubleshooting issues, or creating advanced timelapses.

---

## Logs

Logs are stored in the `logs/` directory with automatic rotation.

### View Recent Logs

```bash
tail -f logs/capture_image.log
```

### View All Logs

```bash
cat logs/capture_image.log
```

### Check for Errors Only

```bash
grep ERROR logs/capture_image.log
```

### Log Rotation

When log files reach the `max_size_mb` limit, they are automatically rotated:
- `capture_image.log` (current)
- `capture_image.log.1` (previous)
- `capture_image.log.2` (older)
- ...up to `backup_count` files

---

## Tips and Best Practices

### 1. Test Before Timelapses

Always capture a test image before starting a long timelapse:

```bash
python3 src/capture_image.py
```

Check the image quality, framing, exposure, etc.

### 2. Monitor Disk Space

Long timelapses generate many files. Check available space:

```bash
df -h
```

### 3. Use Appropriate Resolution

- High resolution = better quality but larger files
- Lower resolution = smaller files, faster capture

For timelapses, 1920×1080 is usually sufficient.

### 4. Adjust Exposure for Time of Day

Create separate configs for day/night if needed:

```bash
python3 src/capture_image.py -c config/day_config.yml
python3 src/capture_image.py -c config/night_config.yml
```

### 5. Enable Metadata

Always keep `save_metadata: true` - it helps troubleshoot issues and provides valuable data.

### 6. Monitor Logs

Periodically check logs to ensure captures are succeeding:

```bash
tail logs/capture_image.log
```

### 7. Backup Important Captures

Timelapses take time to create. Regularly backup your images:

```bash
rsync -av captured_images/ /backup/location/
```

---

## Creating Videos from Images

Once you have a series of images, create a timelapse video using `ffmpeg`:

### Install ffmpeg

```bash
sudo apt install -y ffmpeg
```

### Basic Timelapse (30 FPS)

```bash
cd captured_images
ffmpeg -framerate 30 -pattern_type glob -i "*.jpg" \
    -c:v libx264 -pix_fmt yuv420p \
    timelapse.mp4
```

### With Custom Frame Rate

```bash
# Slower (15 FPS)
ffmpeg -framerate 15 -pattern_type glob -i "*.jpg" \
    -c:v libx264 -pix_fmt yuv420p \
    timelapse_slow.mp4

# Faster (60 FPS)
ffmpeg -framerate 60 -pattern_type glob -i "*.jpg" \
    -c:v libx264 -pix_fmt yuv420p \
    timelapse_fast.mp4
```

### High Quality Encoding

```bash
ffmpeg -framerate 30 -pattern_type glob -i "*.jpg" \
    -c:v libx264 -preset slow -crf 18 \
    -pix_fmt yuv420p \
    timelapse_hq.mp4
```

**CRF values:** Lower = better quality (18 = high, 23 = default, 28 = lower quality)

---

## Troubleshooting

### Images Are Too Dark/Bright

Adjust exposure in config:

```yaml
camera:
  controls:
    exposure_time: 30000  # Increase for brighter
    analogue_gain: 1.5    # Increase for brighter
```

### Images Are Blurry

- Use tripod or stable mount
- Increase `exposure_time` if too dark
- Enable autofocus if camera supports it

### Camera Not Responding

Check logs and test with:

```bash
rpicam-still -o test.jpg
```

### Out of Disk Space

Check space and remove old captures:

```bash
df -h
rm -rf old_captures/
```

---

## Next Steps

- Experiment with different resolutions and quality settings
- Set up automated timelapses with cron
- Create your first timelapse video
- Share your results!

---

## Getting Help

- Check logs: `logs/capture_image.log`
- See [INSTALL.md](INSTALL.md) for installation issues
- Visit GitHub repository for community support
- Read [Picamera2 Manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)
