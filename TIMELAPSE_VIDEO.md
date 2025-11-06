# Timelapse Video Generation

Generate timelapse videos from captured images using the `make_timelapse.py` script.

## Overview

The timelapse video generator (`src/make_timelapse.py`) creates smooth timelapse videos from images captured by the Raspilapse system. It supports:

- ✅ **Time-based selection** - Select images by start/end time
- ✅ **Automatic file finding** - Searches date-organized directories
- ✅ **High-quality output** - Configurable codec, framerate, and quality
- ✅ **Logging support** - Full logging of the generation process
- ✅ **Pretty output** - Colored, descriptive terminal output
- ✅ **Testing mode** - Limit image count for quick testing

## Quick Start

### Basic Usage

Create a 24-hour timelapse from 04:00 yesterday to 04:00 today:

```bash
python3 src/make_timelapse.py --start 04:00 --end 04:00
```

### Common Examples

```bash
# 12-hour timelapse (20:00 yesterday to 08:00 today)
python3 src/make_timelapse.py --start 20:00 --end 08:00

# Test with first 100 images only
python3 src/make_timelapse.py --start 20:00 --end 08:00 --limit 100

# Custom framerate (30 fps instead of 25)
python3 src/make_timelapse.py --start 04:00 --end 04:00 --fps 30

# Custom output filename
python3 src/make_timelapse.py --start 04:00 --end 04:00 --output my_timelapse.mp4

# Use custom config file
python3 src/make_timelapse.py --start 04:00 --end 04:00 -c config/custom.yml
```

## Configuration

### Video Settings (config/config.yml)

```yaml
video:
  # Directory for generated timelapse videos
  directory: "videos"

  # Video filename pattern
  # Available placeholders: {name}, {start_date}, {end_date}
  filename_pattern: "{name}_{start_date}_to_{end_date}.mp4"

  # Video codec settings
  codec:
    # Video codec (libx264 for H.264, libx265 for H.265/HEVC)
    name: "libx264"

    # Pixel format (yuv420p for maximum compatibility)
    pixel_format: "yuv420p"

    # Constant Rate Factor (0-51, lower = better quality)
    # 18 = visually lossless, 23 = good quality, 28 = acceptable
    crf: 20

  # Frame rate (frames per second)
  # 25 fps = smooth European standard
  # 30 fps = smooth NTSC standard
  # 24 fps = cinematic
  fps: 25
```

### Quality Settings

**CRF (Constant Rate Factor):**
- `18` - Visually lossless (very large files)
- `20` - Excellent quality (recommended)
- `23` - Good quality (balanced)
- `28` - Acceptable quality (smaller files)

**Frame Rate:**
- `24 fps` - Cinematic look
- `25 fps` - European standard (recommended)
- `30 fps` - NTSC standard, very smooth
- `60 fps` - Ultra-smooth (for fast motion)

## Command-Line Arguments

```
python3 src/make_timelapse.py [OPTIONS]

Required:
  --start TIME        Start time in HH:MM format (e.g., 04:00)
  --end TIME          End time in HH:MM format (e.g., 04:00)

Optional:
  --limit N           Limit to first N images (0 = all, for testing)
  --fps N             Override frame rate from config
  --output FILE       Override output filename
  -c, --config FILE   Path to config file (default: config/config.yml)
```

## How It Works

### 1. Time Range Calculation

When you specify start and end times, the script calculates the datetime range:

- If **end time is later** than start time → Same day
  - Example: `--start 08:00 --end 16:00` → 08:00 to 16:00 today (8 hours)

- If **end time is earlier or equal** → Previous day to today
  - Example: `--start 04:00 --end 04:00` → 04:00 yesterday to 04:00 today (24 hours)
  - Example: `--start 20:00 --end 08:00` → 20:00 yesterday to 08:00 today (12 hours)

### 2. Image Discovery

The script searches for images:
1. Looks in date-organized directories (`/var/www/html/images/YYYY/MM/DD/`)
2. Matches filename pattern: `{project_name}_YYYY_MM_DD_HH_MM_SS.jpg`
3. Parses timestamps from filenames
4. Filters images within the specified time range
5. Sorts images chronologically

### 3. Video Generation

Uses `ffmpeg` to create the video:
1. Creates temporary file list of all images
2. Runs `ffmpeg` with concat demuxer
3. Applies codec settings (H.264, CRF 20, yuv420p)
4. Generates video at specified frame rate
5. Saves to configured output directory

### 4. Output

Example output:

```
══════════════════════════════════════════════════════════════════════
  🎥 TIMELAPSE VIDEO GENERATOR
══════════════════════════════════════════════════════════════════════

⏰ Time Range
──────────────────────────────────────────────────────────────────────
  Start: 2025-11-05 20:00
  End: 2025-11-06 08:00
  Duration: 12.0 hours

⚙️  Configuration
──────────────────────────────────────────────────────────────────────
  Image directory: /var/www/html/images
  Project name: kringelen
  Video settings: 25 fps, libx264, CRF 20

🔍 Searching for Images
──────────────────────────────────────────────────────────────────────
  ✓ Found 1440 images
  → First: kringelen_2025_11_05_20_00_18.jpg
  → Last:  kringelen_2025_11_06_07_59_55.jpg

🎬 Generating Video
──────────────────────────────────────────────────────────────────────
  Images: 1440 frames
  Frame rate: 25 fps
  Codec: libx264 (CRF 20)
  Pixel format: yuv420p
  Video duration: 57.6s (0.96 minutes)

⏳ Processing video with ffmpeg...
   (This may take a few minutes for large timelapses)

✓ Video created successfully!
  Output file: videos/kringelen_2025-11-05_to_2025-11-06.mp4
  File size: 98.63 MB

══════════════════════════════════════════════════════════════════════
  ✓ TIMELAPSE VIDEO CREATED SUCCESSFULLY!
══════════════════════════════════════════════════════════════════════
```

## Output Files

### Video Location

Videos are saved to the directory specified in config:

```
videos/
└── kringelen_2025-11-05_to_2025-11-06.mp4
```

### Filename Pattern

Default pattern: `{name}_{start_date}_to_{end_date}.mp4`

Example: `kringelen_2025-11-05_to_2025-11-06.mp4`

## Logging

Logs are saved to `logs/make_timelapse.log` with details including:
- Start/end times
- Number of images found
- ffmpeg command executed
- Success/failure status
- File sizes

View logs:
```bash
tail -f logs/make_timelapse.log
```

## Testing

The script includes comprehensive tests in `tests/test_make_timelapse.py`:

```bash
# Run all timelapse tests
python3 -m pytest tests/test_make_timelapse.py -v

# Run specific test class
python3 -m pytest tests/test_make_timelapse.py::TestFindImagesInRange -v
```

### Test with Subset

To test video generation quickly without processing all images:

```bash
# Generate video from first 50 images only
python3 src/make_timelapse.py --start 20:00 --end 08:00 --limit 50
```

This is useful for:
- Testing codec settings
- Verifying output quality
- Quick iteration on parameters
- Debugging issues

## Performance

### Processing Time

Approximate processing time on Raspberry Pi 4:

| Images | Resolution | Duration | Processing Time |
|--------|-----------|----------|----------------|
| 100    | 1920x1080 | 4s       | ~10 seconds    |
| 500    | 1920x1080 | 20s      | ~45 seconds    |
| 1440   | 1920x1080 | 58s      | ~2-3 minutes   |
| 2880   | 1920x1080 | 115s     | ~5-6 minutes   |

### File Sizes

Expected output file sizes (CRF 20, 25 fps):

| Video Duration | Images | File Size |
|---------------|--------|-----------|
| 4 seconds     | 100    | ~6 MB     |
| 20 seconds    | 500    | ~30 MB    |
| 1 minute      | 1500   | ~100 MB   |
| 2 minutes     | 3000   | ~200 MB   |

## Troubleshooting

### No Images Found

**Problem:** "No images found in specified time range"

**Solutions:**
1. Check image directory: `ls /var/www/html/images/2025/11/06/`
2. Verify project name in config matches filenames
3. Check date range - ensure images exist for those dates
4. Verify `organize_by_date: true` matches your directory structure

### ffmpeg Errors

**Problem:** ffmpeg fails to create video

**Solutions:**
1. Check ffmpeg is installed: `ffmpeg -version`
2. Verify images exist and are readable
3. Check disk space: `df -h`
4. Try with smaller image count: `--limit 10`

### Wrong Time Range

**Problem:** Video includes wrong images

**Solution:** Remember time logic:
- `--start 20:00 --end 08:00` → 20:00 **yesterday** to 08:00 **today**
- `--start 08:00 --end 20:00` → 08:00 **today** to 20:00 **today**

### Quality Issues

**Problem:** Video quality too low or file too large

**Solutions:**
- Increase quality: Lower CRF (20 → 18)
- Reduce file size: Higher CRF (20 → 23)
- Adjust in config or use custom config file

## Advanced Usage

### Custom Codec Settings

Create a custom config file with different codec settings:

```yaml
video:
  codec:
    name: "libx265"      # H.265 for better compression
    pixel_format: "yuv420p"
    crf: 23              # Slightly lower quality, much smaller files
  fps: 30                # Smoother playback
```

Then use it:
```bash
python3 src/make_timelapse.py --start 04:00 --end 04:00 -c config/custom.yml
```

### Batch Processing

Generate multiple timelapses:

```bash
#!/bin/bash
# Generate daily timelapses for the past week

for day in {0..6}; do
    date=$(date -d "$day days ago" +%Y-%m-%d)
    python3 src/make_timelapse.py \
        --start 00:00 --end 23:59 \
        --output "daily_${date}.mp4"
done
```

### Scheduling with Cron

Generate daily timelapse at 04:00:

```bash
# Edit crontab
crontab -e

# Add line:
0 4 * * * cd /home/pi/raspilapse && python3 src/make_timelapse.py --start 04:00 --end 04:00
```

## Integration with Raspilapse

The timelapse generator integrates with the main Raspilapse system:

1. **Images** - Uses images from `auto_timelapse.py` captures
2. **Config** - Shares same `config/config.yml` file
3. **Logging** - Uses same logging configuration
4. **Naming** - Uses project name from config

## Technical Details

### Video Specifications

Default output video:
- **Codec:** H.264 (libx264)
- **Pixel Format:** yuv420p (maximum compatibility)
- **CRF:** 20 (excellent quality)
- **Frame Rate:** 25 fps
- **Resolution:** Matches source images (1920x1080)

### ffmpeg Command

The script generates an ffmpeg command like:

```bash
ffmpeg -f concat -safe 0 -i /tmp/images.txt \
    -r 25 -vcodec libx264 -pix_fmt yuv420p -crf 20 \
    -y output.mp4
```

### Image List Format

Temporary file format for ffmpeg concat demuxer:

```
file '/var/www/html/images/2025/11/05/kringelen_2025_11_05_20_00_18.jpg'
file '/var/www/html/images/2025/11/05/kringelen_2025_11_05_20_00_48.jpg'
file '/var/www/html/images/2025/11/05/kringelen_2025_11_05_20_01_18.jpg'
...
```

## See Also

- [CLAUDE.md](CLAUDE.md) - Main project documentation
- [ADAPTIVE_TIMELAPSE_FLOW.md](ADAPTIVE_TIMELAPSE_FLOW.md) - Adaptive timelapse flow
- [config/config.yml](config/config.yml) - Full configuration reference
