# Image Overlay System

Modern, flexible text overlay system for timelapse images with configurable content, styling, and placement.

---

## Features

✅ **Modular Design**: Can be used during capture OR as standalone script
✅ **Highly Configurable**: Everything controlled via `config/config.yml`
✅ **Resolution Independent**: Automatic font sizing based on image dimensions
✅ **Semi-Transparent Backgrounds**: Dark backgrounds for text readability
✅ **Rich Content**: Camera settings, timestamps, debug info, custom text
✅ **Flexible Positioning**: Presets (corners) or custom positioning
✅ **Batch Processing**: Apply overlays to multiple existing images

---

## Quick Start

### 1. Enable Overlay in Config

Edit `config/config.yml`:

```yaml
overlay:
  enabled: true  # ← Set to true
  position: "bottom-left"
  camera_name: "My Timelapse"
```

### 2. Capture with Overlay (Automatic)

```bash
python3 src/auto_timelapse.py --test
```

Overlay is applied automatically during capture!

### 3. Apply to Existing Images (Manual)

```bash
# Single image
python3 src/apply_overlay.py test_photos/kringelen_2025_11_05_10_30_45.jpg

# Batch process
python3 src/apply_overlay.py test_photos/*.jpg --output-dir overlayed/
```

---

## Configuration Reference

### Basic Structure

```yaml
overlay:
  enabled: false          # Master enable/disable
  position: "bottom-left" # Where to place text
  camera_name: "Camera"   # Display name

  font:
    family: "DejaVuSans.ttf"  # Font file
    size_ratio: 0.025          # Size relative to image height
    color: [255, 255, 255, 255] # RGBA color

  background:
    enabled: true
    color: [0, 0, 0, 180]  # RGBA (180 = 70% opacity)
    padding: 0.3            # Padding around text

  content:
    main:              # Always shown
      - "{camera_name}"
      - "{date} {time}"

    camera_settings:  # Optional section
      enabled: true
      lines:
        - "Exposure: {exposure} | ISO: {iso}"

    debug:            # Optional debug section
      enabled: false
      lines:
        - "Gain: {gain} | Temp: {temperature}°C"
```

---

## Position Options

### Presets

- `"top-bar"` - Full-width navbar at top with centered text (recommended)
- `"top-left"` - Upper left corner
- `"top-right"` - Upper right corner
- `"bottom-left"` - Lower left corner
- `"bottom-right"` - Lower right corner
- `"custom"` - Use custom_position coordinates

### Custom Positioning

```yaml
position: "custom"
custom_position:
  x: 50  # 50% from left (center)
  y: 10  # 10% from top
```

Values are percentages (0-100) of image dimensions.

---

## Available Variables

Use these placeholders in your content templates:

### Time & Date
- `{date}` - Date (YYYY-MM-DD)
- `{time}` - Time (HH:MM:SS)
- `{datetime}` - Full datetime

### Camera Info
- `{camera_name}` - Camera name from config
- `{mode}` - Light mode (Day/Night/Transition)
- `{resolution}` - Image resolution (1920x1080)

### Exposure Settings
- `{exposure}` - Human-readable exposure (e.g., "1/500s", "2.5s")
- `{exposure_ms}` - Exposure in milliseconds
- `{exposure_us}` - Exposure in microseconds
- `{iso}` - ISO equivalent (e.g., "ISO 400")
- `{gain}` - Raw analogue gain value

### White Balance
- `{wb}` - WB mode (Auto/Manual)
- `{wb_gains}` - WB gains (R:1.8 B:1.5)

### Advanced
- `{lux}` - Calculated lux value
- `{temperature}` - Camera sensor temperature in °C (internal hardware temp, NOT ambient temperature)

### System Monitoring (Always Available)
- `{cpu_temp}` - CPU temperature formatted (e.g., "42.5°C")
- `{cpu_temp_raw}` - CPU temperature raw value (e.g., "42.5")
- `{disk}` - Disk space formatted (e.g., "50.2 GB free (42% used)")
- `{disk_free}` - Free disk space (e.g., "50.2 GB")
- `{disk_used}` - Used disk space (e.g., "36.8 GB")
- `{disk_total}` - Total disk space (e.g., "116.7 GB")
- `{disk_percent}` - Disk usage percentage (e.g., "31%")
- `{memory}` - Memory usage formatted (e.g., "1.2 GB / 4.0 GB (30%)")
- `{memory_used}` - Used memory (e.g., "1.2 GB")
- `{memory_free}` - Free memory (e.g., "2.8 GB")
- `{memory_total}` - Total memory (e.g., "4.0 GB")
- `{memory_percent}` - Memory usage percentage (e.g., "30%")
- `{load}` - CPU load averages (e.g., "0.52, 0.48, 0.45")
- `{load_1min}` - 1-minute load average (e.g., "0.52")
- `{load_5min}` - 5-minute load average (e.g., "0.48")
- `{load_15min}` - 15-minute load average (e.g., "0.45")
- `{uptime}` - System uptime (e.g., "2d 5h 30m")

---

## Content Sections

Organize your overlay into sections for better readability:

### Main Section (Always Visible)

```yaml
content:
  main:
    - "{camera_name}"
    - "{date} {time}"
    - "Mode: {mode}"
```

### Camera Settings (Toggle)

```yaml
camera_settings:
  enabled: true  # Set to false to hide
  lines:
    - "Exposure: {exposure} | ISO: {iso}"
    - "WB: {wb} | Lux: {lux}"
```

### Debug Info (Toggle)

```yaml
debug:
  enabled: false  # Set to true for debugging
  lines:
    - "Gain: {gain} | Temp: {temperature}°C"
    - "WB Gains: {wb_gains}"
    - "Resolution: {resolution}"
```

---

## Styling Options

### Font Settings

```yaml
font:
  # Font file (searches system font directories)
  family: "DejaVuSans.ttf"
  # Options: "DejaVuSans.ttf", "Arial.ttf", "Helvetica.ttf", "default"

  # Size as ratio of image height
  size_ratio: 0.025  # 2.5% of image height
  # Smaller = 0.02, Larger = 0.03

  # Text color (RGBA: Red, Green, Blue, Alpha)
  color: [255, 255, 255, 255]  # White, fully opaque
  # Examples:
  # [255, 255, 0, 255]   # Yellow
  # [0, 255, 0, 200]     # Green, 78% opacity
```

### Background Settings

```yaml
background:
  enabled: true  # Semi-transparent box behind text

  # Background color (RGBA)
  color: [0, 0, 0, 180]
  # Alpha values:
  # 255 = fully opaque (100%)
  # 180 = 70% opacity (recommended)
  # 128 = 50% opacity
  # 0 = fully transparent

  # Padding around text (relative to font size)
  padding: 0.3
  # 0.3 = 30% of font size
  # Smaller = tighter, Larger = more breathing room
```

### Layout Options

```yaml
layout:
  # Spacing between lines
  line_spacing: 1.3
  # 1.0 = single spacing
  # 1.5 = 1.5x spacing

  # Add blank line between sections
  section_spacing: true
```

---

## Standalone Script Usage

The `apply_overlay.py` script allows you to process existing images.

### Basic Usage

```bash
# Single image (overwrites original)
python3 src/apply_overlay.py image.jpg

# Single image with custom output
python3 src/apply_overlay.py image.jpg -o output.jpg

# Batch process to new directory
python3 src/apply_overlay.py test_photos/*.jpg --output-dir overlayed/
```

### Advanced Options

```bash
# Specify custom metadata file
python3 src/apply_overlay.py image.jpg -m custom_metadata.json

# Override light mode
python3 src/apply_overlay.py image.jpg --mode night

# Use custom config
python3 src/apply_overlay.py image.jpg -c custom_config.yml

# Verbose output
python3 src/apply_overlay.py image.jpg -v
```

### Batch Processing Examples

```bash
# Process all images in directory
python3 src/apply_overlay.py test_photos/*.jpg --output-dir overlayed/

# Process images from specific date
python3 src/apply_overlay.py test_photos/kringelen_2025_11_05_*.jpg --output-dir nov5/

# In-place processing (careful!)
python3 src/apply_overlay.py test_photos/*.jpg --in-place
```

---

## Example Configurations

### Minimal Overlay

```yaml
overlay:
  enabled: true
  position: "bottom-left"
  camera_name: "My Camera"

  content:
    main:
      - "{camera_name} | {date} {time}"
    camera_settings:
      enabled: false
    debug:
      enabled: false
```

### Detailed Overlay

```yaml
overlay:
  enabled: true
  position: "bottom-left"
  camera_name: "Kringelen Timelapse"

  font:
    family: "DejaVuSans.ttf"
    size_ratio: 0.028
    color: [255, 255, 255, 255]

  background:
    enabled: true
    color: [0, 0, 0, 200]
    padding: 0.4

  content:
    main:
      - "{camera_name}"
      - "{datetime}"
      - "Light Mode: {mode}"

    camera_settings:
      enabled: true
      lines:
        - "Exposure: {exposure} | ISO: {iso}"
        - "White Balance: {wb} | Lux: {lux}"

    debug:
      enabled: false
```

### Debug Overlay (Troubleshooting)

```yaml
overlay:
  enabled: true
  position: "bottom-left"

  content:
    main:
      - "DEBUG MODE"
      - "{datetime}"

    camera_settings:
      enabled: true
      lines:
        - "Exp: {exposure_us}µs | Gain: {gain}"
        - "ISO: {iso} | Lux: {lux}"

    debug:
      enabled: true
      lines:
        - "WB Gains: {wb_gains}"
        - "Temp: {temperature}°C"
        - "Resolution: {resolution}"
```

### System Monitoring Overlay

```yaml
overlay:
  enabled: true
  position: "top-bar"  # Full width for more info
  camera_name: "Timelapse with System Stats"

  font:
    family: "DejaVuSans-Bold.ttf"
    size_ratio: 0.020
    color: [255, 255, 255, 255]

  background:
    enabled: true
    color: [0, 0, 0, 110]  # Semi-transparent
    padding: 0.6

  content:
    # Line 1 - Camera and Time Info
    line_1_left: "{camera_name}"
    line_1_right: "{date} {time}"

    # Line 2 - System Health Metrics
    line_2_left: "CPU: {cpu_temp}, Load: {load_1min}"
    line_2_right: "Disk: {disk_free}, Memory: {memory_percent}"
```

This example shows system health metrics alongside camera info, perfect for monitoring long-running timelapses.

---

## Font Installation

### Check Available Fonts

```bash
# List installed fonts
fc-list | grep -i dejavu
fc-list | grep -i arial

# Search for TrueType fonts
find /usr/share/fonts -name "*.ttf"
```

### Install Additional Fonts (Debian/Ubuntu)

```bash
# Install DejaVu fonts (recommended)
sudo apt install fonts-dejavu

# Install Microsoft core fonts
sudo apt install ttf-mscorefonts-installer

# Install Liberation fonts
sudo apt install fonts-liberation
```

### Using Custom Fonts

Place your `.ttf` file in a known location and specify the full path:

```yaml
font:
  family: "/home/pi/fonts/MyCustomFont.ttf"
```

---

## Troubleshooting

### Overlay Not Appearing

1. **Check if enabled:**
   ```yaml
   overlay:
     enabled: true  # Must be true!
   ```

2. **Check logs:**
   ```bash
   tail -f logs/capture_image.log
   # Look for "Overlay applied" or error messages
   ```

3. **Test with standalone script:**
   ```bash
   python3 src/apply_overlay.py image.jpg -v
   ```

### Font Not Loading

If you see "Could not load font" warnings:

```bash
# Install DejaVu fonts
sudo apt install fonts-dejavu

# Or use default font
```

In `config/config.yml`:
```yaml
font:
  family: "default"  # Falls back to PIL default font
```

### Text Too Small/Large

Adjust the size ratio:

```yaml
font:
  size_ratio: 0.025  # Default
  # Too small? Try: 0.03 or 0.035
  # Too large? Try: 0.02 or 0.018
```

### Background Not Visible

Increase opacity (alpha channel):

```yaml
background:
  color: [0, 0, 0, 220]  # Increase from 180 to 220
```

### Missing Variables

If you see `{variable_name}` in your overlay instead of values:

1. Check variable name is correct (see Available Variables section)
2. Check metadata file exists and contains the data
3. Enable verbose logging to see what's available

---

## Integration with Adaptive Timelapse

The overlay system integrates seamlessly with adaptive timelapse:

1. **Automatic Mode Detection**: Shows "Day", "Night", or "Transition" mode
2. **Real-time Settings**: Displays actual exposure/ISO used for that frame
3. **No Performance Impact**: Applied after image capture (doesn't slow down camera)
4. **Optional**: Can be enabled/disabled without affecting capture

---

## Performance Notes

- **Negligible Impact**: Overlay adds ~0.1-0.5 seconds per image
- **Memory Efficient**: Processes one image at a time
- **Non-blocking**: Applied after camera release (doesn't affect capture timing)
- **Batch Friendly**: Can process hundreds of images efficiently

---

## Best Practices

1. **Start Simple**: Enable basic overlay, then add details
2. **Test First**: Use `--test` mode to check overlay appearance
3. **Resolution Aware**: Size ratio scales automatically with resolution
4. **Readability**: Keep background enabled for bright scenes
5. **Minimal Debug**: Only enable debug info when troubleshooting
6. **Batch Carefully**: Test on single image before batch processing

---

## Summary

The overlay system provides a powerful, flexible way to add professional-looking information to your timelapse images:

| Feature | Status |
|---------|--------|
| **Automatic Integration** | ✅ During capture |
| **Standalone Processing** | ✅ After capture |
| **Resolution Independent** | ✅ Scales automatically |
| **Configurable Content** | ✅ Via YAML config |
| **Semi-transparent BG** | ✅ For readability |
| **Batch Processing** | ✅ Multiple images |
| **Performance** | ✅ Fast & efficient |

Happy timelapsing! 🎥
