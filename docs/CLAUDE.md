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

## CRITICAL: Long Exposure Performance Optimization (Camera V3 / IMX708)

### Problem
By default, long exposures (10-20+ seconds) can take 5x longer than expected. A 20-second exposure may take 99-124 seconds to complete due to libcamera pipeline delays.

### Root Causes
1. **FrameDurationLimits not set**: Pipeline picks arbitrary long frame durations
2. **Auto White Balance enabled**: AWB causes massive slowdown during long exposures (5x!)
3. **Buffer configuration**: Default single buffer causes frame queuing delays
4. **stop() blocking**: Waits for in-flight long exposure frame to complete
5. **capture_metadata() blocking**: Calling `capture_metadata()` after long exposure waits for next frame period (20+ seconds!)
6. **Camera state conflicts**: Multiple camera instances or unclosed cameras cause "Camera in Running state" errors

### Required Optimizations

#### 1. Configure FrameDurationLimits (CRITICAL!)
```python
exposure_us = 20_000_000  # 20 seconds in microseconds
frame_duration_us = exposure_us + 100_000  # Exposure + 100ms slack

controls = {
    "AeEnable": 0,                    # Disable auto-exposure
    "AwbEnable": 0,                   # CRITICAL: Disable AWB for long exposures!
    "ExposureTime": exposure_us,
    "AnalogueGain": 2.5,
    "FrameDurationLimits": (frame_duration_us, frame_duration_us),  # Pin frame period
    "NoiseReductionMode": 0           # Keep pipeline light
}

camera_config = picam2.create_still_configuration(
    main={"size": (1920, 1080), "format": "YUV420"},  # Native JPEG path
    raw=None,              # Disable RAW for performance
    buffer_count=3,        # CRITICAL: Prevents frame queuing delays
    queue=False,           # Ensures fresh frame after request
    display=None,
    controls=controls
)
```

#### 2. Lock AWB for Night Mode (CRITICAL!)
Auto white balance **must** be disabled (`AwbEnable: 0`) during long exposures. Leaving it enabled causes 80+ second delays on top of the actual exposure time.

```python
# For night/long exposure mode
settings = {
    "AeEnable": 0,
    "AwbEnable": 0,        # MUST be 0 for long exposures!
    "ExposureTime": 20_000_000,
    "AnalogueGain": 2.5,
}

# Optional: Set fixed color gains for consistent white balance
if "colour_gains" in night_config:
    settings["ColourGains"] = tuple(night_config["colour_gains"])
```

#### 3. Fast Stop After Long Exposures
To avoid 20+ second blocking on `stop()`:

```python
# Before stop(): flush pipeline with one short frame
picam2.set_controls({
    "AeEnable": 0,
    "ExposureTime": 1000,      # 1ms
    "AnalogueGain": 1.0,
    "FrameDurationLimits": (10_000, 10_000)
})
picam2.capture_request().release()  # Flush with quick frame
picam2.stop()  # Now returns immediately
```

#### 4. Use YUV420 Format
For JPEG output, use YUV420 instead of BGR888 to avoid unnecessary RGB conversion:

```python
main={"size": (1920, 1080), "format": "YUV420"}  # Faster than BGR888
```

#### 5. Use capture_request() Instead of capture_file() + capture_metadata() (CRITICAL!)
The `capture_metadata()` method blocks waiting for the next frame period after a long exposure. This causes a 20+ second delay even after the image is saved.

**Problem**:
```python
# BAD: This pattern causes 20s blocking delay
picam2.capture_file("image.jpg")
metadata = picam2.capture_metadata()  # BLOCKS for 20+ seconds!
```

**Solution**:
```python
# GOOD: Get both image and metadata from the same request
request = picam2.capture_request()
try:
    # Save the image
    request.save("main", "image.jpg")

    # Get metadata from request (no blocking!)
    metadata = request.get_metadata()

    # Save metadata to file
    with open("metadata.json", "w") as f:
        json.dump(metadata, f, indent=2, default=str)
finally:
    # Always release the request
    request.release()
```

**Why this works**: `capture_request()` returns immediately with both the image data AND metadata from the same frame, avoiding the pipeline wait.

#### 6. Close Camera Between Configurations (CRITICAL for Adaptive Timelapse!)
When switching between different camera configurations (e.g., test shots vs. timelapse captures), you MUST close the camera completely before reinitializing.

**Problem**:
```python
# BAD: Camera still running from previous capture
capture = ImageCapture(config)
capture.initialize_camera(settings_A)
capture.capture("frame1.jpg")

# ERROR: "Camera in Running state trying acquire()"
test_capture = ImageCapture(config)
test_capture.initialize_camera(settings_B)  # FAILS!
```

**Solution**:
```python
# GOOD: Close camera before new instance
capture = ImageCapture(config)
capture.initialize_camera(settings_A)
capture.capture("frame1.jpg")
capture.close()  # CRITICAL: Release camera resources
capture = None

# Now safe to create new instance
test_capture = ImageCapture(config)
test_capture.initialize_camera(settings_B)  # Works!
```

**Why this matters**: The camera hardware can only have ONE active instance at a time. Even using context managers, you must ensure the camera is fully closed before creating a new instance.

### Performance Results

#### Before All Optimizations:
- 20s exposure capture: **99-124 seconds** (5x slowdown!)
- Post-capture metadata: **+20 seconds blocking**
- Camera reinitialization: **Fails with "Camera in Running state"**
- Total time between frames: **140+ seconds**

#### After All Optimizations:
- 20s exposure capture: **18-20 seconds**
- Post-capture metadata: **0 seconds** (non-blocking)
- Camera reinitialization: **Works perfectly**
- Total time between frames: **~60 seconds** (60s interval)

#### Overall Improvement: ~7x faster!

### Implementation in Raspilapse
These optimizations are implemented in:
- `src/capture_image.py`:
  - `initialize_camera()`: Applies FrameDurationLimits, buffer_count=3, AWB controls
  - `capture()`: Uses `capture_request()` instead of `capture_file()` + `capture_metadata()`
- `src/auto_timelapse.py`:
  - Closes camera before test shots to avoid state conflicts
  - Night mode locks AWB (`AwbEnable: 0`) and sets manual exposure

### Quick Reference: Optimized Long Exposure Pattern

```python
from picamera2 import Picamera2
import json

# 1. Configure with FrameDurationLimits and buffer settings
exposure_us = 20_000_000  # 20 seconds
frame_duration_us = exposure_us + 100_000

picam2 = Picamera2()
config = picam2.create_still_configuration(
    main={"size": (1920, 1080), "format": "YUV420"},
    raw=None,
    buffer_count=3,  # CRITICAL
    queue=False,
    controls={
        "AeEnable": 0,
        "AwbEnable": 0,  # CRITICAL for long exposures
        "ExposureTime": exposure_us,
        "AnalogueGain": 2.5,
        "FrameDurationLimits": (frame_duration_us, frame_duration_us),  # CRITICAL
        "NoiseReductionMode": 0,
    }
)
picam2.configure(config)
picam2.start()
time.sleep(2)  # Stabilization

# 2. Capture using capture_request() for non-blocking metadata
request = picam2.capture_request()
try:
    request.save("main", "night_frame.jpg")
    metadata = request.get_metadata()  # Non-blocking!
    with open("metadata.json", "w") as f:
        json.dump(metadata, f, indent=2, default=str)
finally:
    request.release()

# 3. Close camera properly
picam2.close()
```

### Common Pitfalls to Avoid

**DON'T**: Use `capture_file()` + `capture_metadata()` with long exposures
**DO**: Use `capture_request()` to get both image and metadata

**DON'T**: Leave AWB enabled (`AwbEnable: 1`) for night captures
**DO**: Disable AWB (`AwbEnable: 0`) for long exposures

**DON'T**: Forget to set `FrameDurationLimits` matching exposure time
**DO**: Set `FrameDurationLimits = (exposure + 100ms, exposure + 100ms)`

**DON'T**: Create new camera instance without closing the previous one
**DO**: Always call `picam2.close()` before creating new instance

**DON'T**: Use default buffer settings for long exposures
**DO**: Set `buffer_count=3` and `queue=False` in configuration

### References
- GitHub Discussion #343: https://github.com/raspberrypi/picamera2/discussions/343
- Stack Overflow: FrameDurationLimits control for frame period
- Raspberry Pi Picamera2 Manual: buffer_count for still captures
- Picamera2 Request API: https://github.com/raspberrypi/picamera2/blob/main/examples/capture_request.py

---

## Adaptive Timelapse Architecture

### How It Works

The adaptive timelapse (`src/auto_timelapse.py`) automatically adjusts exposure settings for 24/7 capture:

**Per-Frame Process:**
1. **Test Shot** → Measure light (saves to `metadata/test_shot.jpg`, overwritten each time)
2. **Calculate Lux** → Determine day/night/transition mode
3. **Close Camera** → Release test shot camera instance
4. **Actual Capture** → Apply adaptive settings, save to `test_photos/kringelen_YYYY_MM_DD_HH_MM_SS.jpg` with metadata
5. **Wait** → Sleep until next interval

**Metadata Handling:**
- Saved after EVERY shot (both test shots and actual frames)
- Uses `capture_request()` method - gets image + metadata in ONE operation
- Non-blocking - no delays waiting for metadata
- Does NOT close/reopen camera between image and metadata

**Metadata Directory:**
- Stored in `metadata/` directory with fixed filenames (overwritten each time)
- `metadata/test_shot.jpg` - Latest test shot for light measurement
- `metadata/test_shot_metadata.json` - Latest test metadata
- Fixed settings (0.1s exposure, gain 1.0) for consistent light measurement
- NOT part of your timelapse output
- Only 2 files (never accumulates)

**Camera State:**
- Hardware limitation: Only ONE camera instance at a time
- Test shot uses context manager (`with`) → auto-closes
- Main capture camera stays open if mode unchanged
- Closes and reinitializes only when switching modes

**Test Mode:**
```bash
python3 src/auto_timelapse.py --test  # Capture one image then exit
```

For detailed flow documentation, see `ADAPTIVE_TIMELAPSE_FLOW.md`.

### Overlay System

Modern text overlay system for adding camera information to images:

**Features:**
- Configurable content (timestamps, camera settings, debug info)
- Semi-transparent backgrounds for readability
- Resolution-independent sizing
- Flexible positioning (corners or custom)
- Automatic during capture OR standalone script

**Quick Enable:**
```yaml
# config/config.yml
overlay:
  enabled: true
  position: "bottom-left"
  camera_name: "My Timelapse"
```

**Standalone Usage:**
```bash
# Apply to existing images
python3 src/apply_overlay.py test_photos/*.jpg --output-dir overlayed/
```

**Documentation:** See `OVERLAY.md` for complete configuration reference.

---

## Utility Scripts

### Status Display (src/status.py)

Beautiful colored status display showing system state, configuration, and recent captures.

**Features:**
- Service status (running/stopped/failed) with color indicators
- Configuration summary (resolution, interval, light thresholds)
- Overlay settings display
- Recent captures with timing analysis
- Average interval calculation
- Symlink status for web display
- ANSI colored output for readability

**Usage:**
```bash
# Show full status
python3 src/status.py

# With custom config
python3 src/status.py -c config/custom.yml
```

**Output Example:**
```
============================================================
  RASPILAPSE STATUS
============================================================

SERVICE STATUS
────────────────────────────────────────────────────────────
  Status:      RUNNING
  Description: Service is running normally

CONFIGURATION
────────────────────────────────────────────────────────────
  Resolution:  1920x1080 (2.1MP)
  Interval:    30s (2.0 captures/min)
  Day Mode:    >100 lux
  Night Mode:  <10 lux
  Max Exposure: 20s (ISO 600)
  Output:      /var/www/html/images

OVERLAY
────────────────────────────────────────────────────────────
  Enabled
  Position:    top-bar
  Camera Name: Kringelen Timelapse
  Font:        DejaVuSans-Bold.ttf (size: 0.020)
  Background:  Enabled (43% opacity)

RECENT CAPTURES
────────────────────────────────────────────────────────────
  Average Interval: 30.5s (target: 30s)

  ● kringelen_2025_11_05_18_42_00.jpg (2m ago)
    2025-11-05 18:42:00 · 440.5 KB
  ○ kringelen_2025_11_05_18_40_30.jpg (4m ago)
    2025-11-05 18:40:30 · 439.0 KB
```

### Test Script (test.sh)

Comprehensive test suite for verifying installation and configuration.

**What it checks:**
- Python dependencies (picamera2, yaml, PIL, numpy)
- Configuration file syntax
- Camera hardware detection
- Output directory permissions
- Service installation and status
- Optional test capture
- Full status display

**Usage:**
```bash
# Run all tests
./test.sh

# Tests will prompt for optional camera test
# (skip if service is running)
```

**Output includes:**
- Color-coded pass/fail indicators
- Missing dependency warnings
- Permission issues
- Service status
- Final status report

### Analysis Script (src/analyze_timelapse.py)

Powerful analysis tool that generates beautiful graphs and Excel reports from timelapse metadata.

**Features:**
- **Fast**: Only reads JSON metadata files (no image processing)
- **Beautiful dark-themed lux graph** with day/night zones
- **5 detailed graphs**: Lux, Exposure, Gain, White Balance, Overview
- **Excel export** with 3 sheets: Raw Data, Statistics, Hourly Averages
- **Real-world lux references** (sunlight, twilight, full moon, etc.)
- **Chronologically sorted** data from earliest to latest

**Usage:**
```bash
# Analyze last 24 hours (default)
python3 src/analyze_timelapse.py

# Analyze last 48 hours
python3 src/analyze_timelapse.py --hours 48

# Analyze last week
python3 src/analyze_timelapse.py --hours 168

# Use custom config
python3 src/analyze_timelapse.py -c config/custom.yml
```

**Generated Files** (in `graphs/` directory):

1. **`lux_levels.png`** (Most Important)
   - Dark-themed graph with colored day/night/twilight zones
   - Orange glowing line with fill effect
   - Real-world reference lines (sunlight, twilight, etc.)
   - Your configured day/night thresholds

2. **`exposure_time.png`** - Camera exposure over time
   - Shows max night exposure threshold from config

3. **`analogue_gain.png`** - ISO/Gain levels

4. **`white_balance.png`** - Color temperature + RGB gains
   - Two-panel graph

5. **`overview.png`** - 4-panel summary of all key metrics

6. **`daily_solar_patterns.png`** - Daily light patterns from database
   - Lux curves by time of day for each recent day (14 days)
   - Daily midday light levels with trend (polar winter recovery)

7. **`timelapse_analysis_24h.xlsx`** - Excel file with:
   - **Raw Data**: Every image with timestamp, lux, exposure, gain, temp, etc.
   - **Statistics**: Min/Max/Avg/Median for all metrics
   - **Hourly Averages**: Data aggregated by hour

**How It Works:**

The script intelligently matches JPG files with their corresponding `_metadata.json` files by modification time (within 60 seconds), handling the ~18 second delay between image capture and metadata save. It then:

1. Loads all metadata files from the time window
2. Extracts key metrics (lux, exposure, gain, temperature, etc.)
3. Sorts chronologically from earliest to latest
4. Generates beautiful themed graphs
5. Exports to Excel for detailed analysis

**Performance:**
- **~3 seconds** to analyze 2,880 images (24 hours)
- Progress updates every 100 files

**Lux Reference Guide:**
- **100,000 lux**: Direct sunlight
- **10,000 lux**: Full daylight
- **1,000 lux**: Overcast day
- **400 lux**: Sunrise/Sunset
- **100 lux**: Very dark day
- **10 lux**: Twilight (your night threshold)
- **1 lux**: Deep twilight
- **0.1 lux**: Full moon

---

## ML-Based Adaptive Exposure System

### Overview
A lightweight machine learning system that continuously learns and improves timelapse exposure settings. Designed for Raspberry Pi with minimal compute requirements.

### How It Works
The system runs automatically as part of `auto_timelapse.py`:

1. **Every frame**: Learns from capture metadata (lux, exposure, brightness)
2. **Before capture**: Predicts optimal exposure based on learned patterns
3. **Blending**: ML predictions blended with formula based on trust level

### Components

```
┌─────────────────────────────────────────────────────────────┐
│                    ML Exposure Predictor                     │
├─────────────────────────────────────────────────────────────┤
│  1. Solar Pattern Memory    - Expected lux by time/day      │
│  2. Lux-Exposure Mapper     - Optimal exposure per lux      │
│  3. Trend Predictor         - Anticipate light changes      │
│  4. Correction Memory       - What brightness fixes worked  │
└─────────────────────────────────────────────────────────────┘
```

### Trust System
- **Initial trust**: 0% (formula only)
- **Increment**: +0.1% per good prediction (brightness 100-140)
- **Maximum**: 80% (formula always has 20% influence)

Formula: `final = trust × ML + (1-trust) × formula`

### Aurora-Safe Learning
The ML system accepts two types of "good" frames for learning:

1. **Standard Day/Twilight**: Mean brightness 105-135 (near target 120)
2. **High-Contrast Night** (Aurora/Stars):
   - Lux < 10 (night conditions)
   - Mean brightness 30-105 (dark sky)
   - Percentile 95 > 150 (bright highlights from Aurora/stars)

This prevents the system from rejecting valid night photography where the overall image is dark but contains bright Aurora or stars.

### Files
| File | Purpose |
|------|---------|
| `src/ml_exposure_v2.py` | ML v2 predictor (database-trained, arctic-aware) |
| `src/ml_exposure.py` | Legacy ML v1 predictor class |
| `src/bootstrap_ml_v2.py` | Bootstrap v2 from database |
| `src/graph_ml_patterns.py` | Generate daily solar pattern visualization from database |
| `ml_state/ml_state_v2.json` | ML v2 persisted state |
| `docs/ML_EXPOSURE_SYSTEM.md` | Full documentation |

### Configuration
```yaml
# config/config.yml
adaptive_timelapse:
  ml_exposure:
    enabled: true           # ML active
    shadow_mode: false      # Use predictions (not just log)
    initial_trust: 0.0      # Start with formula only
    max_trust: 0.8          # Cap ML influence at 80%
```

### Commands
```bash
# Bootstrap ML v2 from database (auto-retrains daily)
python src/bootstrap_ml_v2.py

# Generate daily solar patterns graph (last 14 days from database)
python src/graph_ml_patterns.py --days 14

# Or run all graphs including solar patterns via db_graphs.py
python scripts/db_graphs.py
```

### Polar Location Adaptation
At 68.7°N latitude, the system handles:
- **January**: Polar twilight, very short days
- **March**: Days lengthening ~7 min/day
- **May-July**: 24-hour sun (midnight sun)
- **September**: Days shortening rapidly

Solar patterns indexed by day-of-year automatically adapt to seasonal changes.

### ML v2 (Arctic-Aware Database-Driven)

Enhanced ML that trains only on good frames from the database:

| File | Purpose |
|------|---------|
| `src/ml_exposure_v2.py` | Database-driven ML predictor |
| `src/bootstrap_ml_v2.py` | Bootstrap from database |
| `ml_state/ml_state_v2.json` | Persisted state |
| `ML.md` | Full ML documentation |

**Arctic-Aware Time Periods** (uses sun elevation, not clock):
- Night: sun < -12° (astronomical night)
- Twilight: sun -12° to 0° (civil + nautical)
- Day: sun > 0° (above horizon)

**Commands:**
```bash
python src/bootstrap_ml_v2.py           # Bootstrap from database
python src/bootstrap_ml_v2.py --analyze # Just show statistics
```

---

## SQLite Database Storage

Historical capture data storage for analysis, graphs, and exposure planning.

### Features
- **Denormalized storage**: Single table for efficient queries
- **Complete capture data**: Metadata, brightness metrics, weather
- **Time-based queries**: By range, by lux, hourly averages
- **Graceful error handling**: Never crashes timelapse
- **Auto-initialization**: Creates schema and directories

### Files
| File | Purpose |
|------|---------|
| `src/database.py` | CaptureDatabase class |
| `tests/test_database.py` | 34 comprehensive tests |
| `data/timelapse.db` | SQLite database file |

### Configuration
```yaml
# config/config.yml
database:
  enabled: true                # Enable database storage
  path: "data/timelapse.db"    # Database location
  create_directories: true     # Auto-create data dir
```

### Schema
```sql
-- Key fields (36 total columns)
timestamp, unix_timestamp, camera_id, image_path,
exposure_time_us, analogue_gain, colour_gains_r/b, colour_temperature,
lux, mode, sun_elevation,
brightness_mean/median/std, brightness_p5/p25/p75/p95,
underexposed_pct, overexposed_pct,
weather_temperature/humidity/wind_speed/wind_gust/rain/pressure,
system_cpu_temp, system_load_1min/5min/15min
```

### Migrations
Database auto-migrates on startup. No manual steps required.

- **Schema v1**: Initial schema
- **Schema v2**: Added `sun_elevation` column for Arctic-aware ML

When pulling new code to cameras with older databases:
```
[DB] Applying migration v2: Add sun_elevation column for Arctic-aware ML
[DB] Migration v2 complete
```

### Usage Examples
```python
from src.database import CaptureDatabase

# Initialize
db = CaptureDatabase(config)

# Store capture
db.store_capture(image_path, metadata, mode, lux, brightness, weather, sun_elev)

# Query captures
captures = db.get_captures_in_range(start_time, end_time)
captures = db.get_captures_by_lux_range(0, 10)  # Night captures
hourly = db.get_hourly_averages(start_time, end_time)

# Statistics
stats = db.get_statistics()  # total_captures, earliest, latest, db_size_mb
```

### Commands
```bash
# View database statistics (recommended)
python scripts/db_stats.py           # Last 1 hour (default)
python scripts/db_stats.py 5m        # Last 5 minutes
python scripts/db_stats.py 24h       # Last 24 hours
python scripts/db_stats.py 7d        # Last 7 days
python scripts/db_stats.py --all     # All captures
python scripts/db_stats.py -n 10     # Last 10 captures

# Check database status (programmatic)
python3 -c "
from src.database import CaptureDatabase
import yaml
with open('config/config.yml') as f:
    config = yaml.safe_load(f)
db = CaptureDatabase(config)
print(db.get_statistics())
"
```

---

## Development Guidelines

- Use Picamera2's native methods rather than shell commands
- Provide clear error messages for common issues
- Support both GUI and headless modes
- Allow flexible configuration (resolution, interval, duration)
- Save metadata with captures (timestamp, settings)
- Implement graceful shutdown on interrupts
- **For long exposures (>5s)**: Always set FrameDurationLimits and disable AWB