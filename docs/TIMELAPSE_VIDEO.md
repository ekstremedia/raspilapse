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

Create a 24-hour timelapse using default times from config (05:00 yesterday to 05:00 today):

```bash
python3 src/make_timelapse.py
```

### Common Examples

```bash
# Default: uses config times (05:00 yesterday to 05:00 today)
python3 src/make_timelapse.py

# Custom time range (20:00 yesterday to 08:00 today)
python3 src/make_timelapse.py --start 20:00 --end 08:00

# Same-day timelapse (07:00 to 15:00 today)
python3 src/make_timelapse.py --start 07:00 --end 15:00 --today

# Specific date range
python3 src/make_timelapse.py --start 07:00 --end 15:00 --start-date 2025-12-24 --end-date 2025-12-25

# Test with first 100 images only
python3 src/make_timelapse.py --limit 100

# Custom framerate (30 fps instead of 25)
python3 src/make_timelapse.py --fps 30

# Custom output filename
python3 src/make_timelapse.py --output my_timelapse.mp4

# Use custom config file
python3 src/make_timelapse.py -c config/custom.yml
```

## Configuration

### Video Settings (config/config.yml)

```yaml
video:
  # Base directory for generated timelapse videos
  directory: "/var/www/html/videos"

  # Create subdirectories by date (YEAR/MONTH structure)
  # When enabled, videos are organized as: directory/YYYY/MM/filename.mp4
  # Example: /var/www/html/videos/2025/12/kringelen_nord_daily_2025-12-23.mp4
  organize_by_date: true

  # Date format for subdirectories (if organize_by_date is true)
  # %Y = 4-digit year, %m = 2-digit month
  date_format: "%Y/%m"

  # Video filename pattern
  # Available placeholders: {name}, {start_date}, {end_date}
  filename_pattern: "{name}_{start_date}_to_{end_date}.mp4"

  # Video codec settings
  codec:
    # Video codec: libx264 (software H.264 encoder)
    # Note: h264_v4l2m2m hardware encoder doesn't support 4K on Pi
    name: "libx264"

    # Pixel format (yuv420p for maximum compatibility)
    pixel_format: "yuv420p"

    # Preset for libx264 (affects speed vs quality vs memory)
    # ultrafast = fastest, lowest memory, acceptable quality
    # fast = good balance
    # slow = best quality, highest memory (may OOM on 4K)
    preset: "ultrafast"

    # Thread count (lower = less memory, slower encoding)
    # 2 = safe for Pi with 4GB RAM doing 4K
    threads: 2

    # Constant Rate Factor (0-51, lower = better quality)
    # 18 = visually lossless, 23 = good quality, 28 = acceptable
    crf: 23

  # Frame rate (frames per second)
  # 25 fps = smooth European standard
  # 30 fps = smooth NTSC standard
  # 24 fps = cinematic
  fps: 25

  # Deflicker filter - smooths exposure transitions (like sunrise spikes)
  # Uses ffmpeg's deflicker filter with Predictive Mean mode
  deflicker: true
  deflicker_size: 10  # Frames to average (higher = smoother)

  # Default time range (used when no --start/--end provided)
  # If end <= start, assumes start is from previous day
  default_start_time: "05:00"
  default_end_time: "05:00"
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

Time Selection:
  --start TIME        Start time in HH:MM format (default: from config or 05:00)
  --end TIME          End time in HH:MM format (default: from config or 05:00)
  --start-date DATE   Start date in YYYY-MM-DD format (default: auto-determined)
  --end-date DATE     End date in YYYY-MM-DD format (default: today)
  --today             Both start and end on today's date

Optional:
  --limit N           Limit to first N images (0 = all, for testing)
  --fps N             Override frame rate from config
  --output FILE       Override output filename
  --output-dir DIR    Override output directory from config
  --no-keogram        Skip keogram generation
  --keogram-only      Only generate keogram, skip video
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

Videos are saved to date-organized directories when `organize_by_date: true`:

```
/var/www/html/videos/
├── 2025/
│   ├── 11/
│   │   ├── kringelen_nord_daily_2025-11-30.mp4
│   │   └── ...
│   └── 12/
│       ├── kringelen_nord_daily_2025-12-01.mp4
│       ├── kringelen_nord_daily_2025-12-23.mp4
│       └── ...
```

### Filename Pattern

Filenames now include times to avoid overwrites:

**Same-day timelapse:**
```
{project}_{YYYY-MM-DD}_{HHMM}-{HHMM}.mp4
Example: kringelen_nord_2025-12-25_0700-1500.mp4
```

**Multi-day timelapse:**
```
{project}_{YYYY-MM-DD}_{HHMM}_to_{YYYY-MM-DD}_{HHMM}.mp4
Example: kringelen_nord_2025-12-24_0500_to_2025-12-25_0500.mp4
```

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

Approximate processing time on Raspberry Pi 4/5:

**1080p (1920x1080) with default settings:**
| Images | Duration | Processing Time |
|--------|----------|----------------|
| 100    | 4s       | ~10 seconds    |
| 500    | 20s      | ~45 seconds    |
| 1440   | 58s      | ~2-3 minutes   |
| 2880   | 115s     | ~5-6 minutes   |

**4K (3840x2160) with ultrafast preset, 2 threads:**
| Images | Duration | Processing Time |
|--------|----------|----------------|
| 100    | 4s       | ~2 minutes     |
| 500    | 20s      | ~10 minutes    |
| 1440   | 58s      | ~30 minutes    |
| 2880   | 115s     | ~60-90 minutes |

### File Sizes

Expected output file sizes (CRF 23, 25 fps, ultrafast preset):

| Resolution | Video Duration | Images | File Size |
|------------|---------------|--------|-----------|
| 1080p      | 1 minute      | 1500   | ~100 MB   |
| 1080p      | 2 minutes     | 3000   | ~200 MB   |
| 4K         | 1 minute      | 1500   | ~300 MB   |
| 4K         | 2 minutes     | 3000   | ~600 MB   |

### Memory Usage

4K encoding requires significant RAM. Use these settings to avoid OOM:

```yaml
video:
  codec:
    preset: "ultrafast"  # ~500MB RAM
    threads: 2           # Limits parallel memory usage
```

With these settings, 4K encoding uses ~1-1.5GB RAM (safe for 4GB Pi).

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

### Out of Memory (OOM) Errors

**Problem:** ffmpeg killed by OOM killer when encoding 4K video

**Symptoms:**
- Video file created but not playable (missing moov atom)
- `dmesg | grep oom` shows ffmpeg was killed
- Service fails with "exit-code" status

**Solutions:**
1. **Use memory-optimized settings** (recommended):
   ```yaml
   video:
     codec:
       preset: "ultrafast"  # Lowest memory usage
       threads: 2           # Limit parallel processing
   ```

2. **Check current memory**:
   ```bash
   free -h
   ```

3. **Monitor during encoding**:
   ```bash
   watch -n1 free -h
   ```

**Note:** The Pi's hardware encoder (h264_v4l2m2m) doesn't support 4K resolution. Use libx264 with memory-optimized settings instead.

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
