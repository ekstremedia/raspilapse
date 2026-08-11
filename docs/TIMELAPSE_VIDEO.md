# Timelapse Video Generation

Generate timelapse videos from captured images with
`python3 -m raspilapse.cli.timelapse`.

## Overview

The timelapse video generator (`raspilapse/video/timelapse.py`) creates smooth timelapse videos from images captured by the Raspilapse system. It supports:

- **Time-based selection** - Select images by start/end time
- **Automatic file finding** - Searches date-organized directories
- **High-quality output** - Configurable codec, framerate, and quality
- **Logging support** - Full logging of the generation process
- **Pretty output** - Colored, descriptive terminal output
- **Testing mode** - Limit image count for quick testing

## Quick Start

### Basic Usage

Create a 24-hour timelapse using default times from config (05:00 yesterday to 05:00 today):

```bash
python3 -m raspilapse.cli.timelapse
```

### Common Examples

```bash
# Default: uses config times (05:00 yesterday to 05:00 today)
python3 -m raspilapse.cli.timelapse

# Custom time range (20:00 yesterday to 08:00 today)
python3 -m raspilapse.cli.timelapse --start 20:00 --end 08:00

# Same-day timelapse (07:00 to 15:00 today)
python3 -m raspilapse.cli.timelapse --start 07:00 --end 15:00 --today

# Specific date range
python3 -m raspilapse.cli.timelapse --start 07:00 --end 15:00 --start-date 2025-12-24 --end-date 2025-12-25

# Test with first 100 images only
python3 -m raspilapse.cli.timelapse --limit 100

# Custom framerate (30 fps instead of 25)
python3 -m raspilapse.cli.timelapse --fps 30

# Custom output filename
python3 -m raspilapse.cli.timelapse --output my_timelapse.mp4

# Use custom config file
python3 -m raspilapse.cli.timelapse -c config/custom.yml
```

## Configuration

### Video Settings (config/config.yml)

```yaml
video:
  # Base directory for generated timelapse videos
  directory: "/var/www/html/videos"

  # Create subdirectories by date (YEAR/MONTH structure)
  # When enabled, videos are organized as: directory/YYYY/MM/filename.mp4
  # Example: /var/www/html/videos/2025/12/kringelen_2025-12-22_0500_to_2025-12-23_0500.mp4
  organize_by_date: true

  # Date format for subdirectories (if organize_by_date is true)
  # %Y = 4-digit year, %m = 2-digit month
  date_format: "%Y/%m"


  # Video codec settings
  codec:
    # Video codec: libx264 (software H.264 encoder)
    # Note: h264_v4l2m2m hardware encoder doesn't support 4K on Pi
    name: "libx264"

    # Pixel format (yuv420p for maximum compatibility)
    pixel_format: "yuv420p"

    # Preset for libx264. A preset does not set quality -- crf does. A faster
    # preset reaches the same crf with a bigger file, and at 4K the cost is
    # dominated by decode, scale and deflicker rather than bitrate, so a
    # slower preset buys very little.
    preset: "veryfast"

    # Thread count. 3 on a 4-core Pi leaves a core for the capture loop;
    # 4 measured no faster here because the job is decode-bound.
    threads: 3

    # Constant Rate Factor (0-51, lower = better quality)
    # 18 = visually lossless, 20 = keeps 4K detail, 23 = good, 28 = acceptable
    crf: 20

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
python3 -m raspilapse.cli.timelapse [OPTIONS]

Time Selection:
  --start TIME        Start time in HH:MM (default: video.default_start_time, 05:00)
  --end TIME          End time in HH:MM (default: video.default_end_time, 05:00)
  --start-date DATE   Start date in YYYY-MM-DD format (default: auto-determined)
  --end-date DATE     End date in YYYY-MM-DD format (default: today)
  --today             Both start and end on today's date

Output:
  -hd, --hd           Scale output to 1080p (1920x1080)
  -hw, --hw           Hardware H264 encoder (h264_v4l2m2m). Requires -hd: it
                      cannot encode above 1920x1080 and the run fails without it.

Optional:
  --limit N           Limit to first N images (0 = all, for testing)
  --fps N             Override frame rate from config
  --output FILE       Override output filename
  --output-dir DIR    Override output directory from config
  --no-keogram        Skip keogram generation
  --keogram-only      Only generate keogram, skip video
  --slitscan          Also generate slitscan image (full-width time-progression image)
  -c, --config FILE   Path to config file (default: config/config.yml)

Keogram/Slitscan Options (standalone `python3 -m raspilapse.video.keogram`,
which also needs --dir <image directory>):
  --crop-top PERCENT  Percentage to crop from top (default: 7% for overlay bar)
  --crop-bottom PCT   Percentage to crop from bottom (default: 0%)
  --no-crop           Disable automatic top cropping
  --slitscan          Generate slitscan instead of keogram
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
3. Applies codec settings (H.264, CRF 25, yuv420p)
4. Generates video at specified frame rate
5. Saves to configured output directory

### 4. Output

Example output, abridged (the tool prints sectioned progress with the same
facts):

```
Time Range:      2025-11-05 20:00 -> 2025-11-06 08:00  (12.0 hours)
Searching:       Found 1440 images
Generating:      1440 frames, 25 fps, libx264 (CRF 20, preset veryfast, 3 threads)
                 Deflicker: enabled (size=10 frames)
Video duration:  57.6s (0.96 minutes)
Output file:     videos/2025/11/kringelen_2025-11-05_2000_to_2025-11-06_0800.mp4
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
python3 -m raspilapse.cli.timelapse --start 20:00 --end 08:00 --limit 50
```

This is useful for:
- Testing codec settings
- Verifying output quality
- Quick iteration on parameters
- Debugging issues

## Performance

### Processing time and memory

Measured on this camera (Pi 4, 4 GB): a full 4K day — ~2880 frames at
veryfast / crf 20 / 3 threads — encodes in about 25 minutes, peaking around
1.0 GB RSS; 1080p takes a few minutes. The systemd unit runs the encode at
Nice=10 with idle I/O scheduling, so the capture loop never competes with it.

### File Sizes

At 4K / crf 20 a full day (~2880 frames, ~1m55s at 25 fps) lands around
400-600 MB depending on scene detail; 1080p is roughly a quarter of that.

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
1. **Keep the shipped settings** — `veryfast` / `crf 20` / `threads: 3`
   measured ~1.0 GB peak on a full 4K day. Still tight? Drop `threads` to 2.

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
- Increase quality: lower `crf` (20 → 18)
- Reduce file size: raise it (20 → 23)
- Adjust in config or use a custom config file

## Advanced Usage

### Custom Codec Settings

Create a custom config file with different codec settings:

```yaml
video:
  codec:
    name: "libx265"      # H.265 for better compression
    pixel_format: "yuv420p"
    crf: 25              # Slightly lower quality, much smaller files
  fps: 30                # Smoother playback
```

Then use it:
```bash
python3 -m raspilapse.cli.timelapse --start 04:00 --end 04:00 -c config/custom.yml
```

### Batch Processing

Generate multiple timelapses:

```bash
#!/bin/bash
# Generate daily timelapses for the past week

for day in {0..6}; do
    date=$(date -d "$day days ago" +%Y-%m-%d)
    python3 -m raspilapse.cli.timelapse \
        --start 00:00 --end 23:59 \
        --output "daily_${date}.mp4"
done
```

### Scheduling

Don't. `raspilapse-daily-video.timer` already does this — installed by
`./scripts/install.sh`, firing at 05:00 (plus up to 5 minutes of deliberate
jitter), covering 05:00 yesterday to 05:00 today so a night's captures land
in one video rather than being split across two.
`systemctl list-timers 'raspilapse-*'` shows when it next runs.

### Retention

Videos, keograms and slitscans older than `video.retention_days` are deleted
by the 02:00 cleanup timer (`python3 -m raspilapse.cli.prune_videos`;
`--dry-run` to preview). 0 — the default — keeps everything. A file is never
deleted while the upload queue holds it in any state other than `success`.

## Integration with Raspilapse

The timelapse generator integrates with the main Raspilapse system:

1. **Images** - Uses the capture daemon's frames (`raspilapse.cli.capture`)
2. **Config** - Shares same `config/config.yml` file
3. **Logging** - Uses same logging configuration
4. **Naming** - Uses project name from config

## Technical Details

### Video Specifications

Output video, as the shipped example configures it:
- **Codec:** H.264 (libx264)
- **Pixel Format:** yuv420p (maximum compatibility)
- **CRF:** 20 (`video.codec.crf`; also the code's fallback when the key is
  absent)
- **Frame Rate:** 25 fps
- **Resolution:** matches the source images — 3840x2160 with the example
  `camera.resolution`

### ffmpeg Command

The script generates an ffmpeg command like:

```bash
ffmpeg -stats -loglevel info \
    -r 25 -f concat -safe 0 -i /tmp/images.txt \
    -vcodec libx264 -pix_fmt yuv420p \
    -preset veryfast -threads 3 -crf 20 \
    -vf deflicker=mode=pm:size=10 \
    -movflags +faststart -y output.mp4
```

`-r` sits **before** `-i` on purpose: there it declares the rate the input is
read at, so frames in equals frames out. After `-i` it is an output option,
and against a concat input that becomes a constant-rate conversion that
duplicates or drops frames.

### Image List Format

Temporary file format for ffmpeg concat demuxer:

```
file '/var/www/html/images/2025/11/05/kringelen_2025_11_05_20_00_18.jpg'
file '/var/www/html/images/2025/11/05/kringelen_2025_11_05_20_00_48.jpg'
file '/var/www/html/images/2025/11/05/kringelen_2025_11_05_20_01_18.jpg'
...
```

## Keogram Generation

A keogram (time-slice image) is automatically generated alongside the video. It shows the passage of time by taking the center vertical column from each image and combining them horizontally.

### What is a Keogram?

A keogram displays an entire day's sky in a single image:
- Sunrise/sunset transitions appear as color gradients
- Clouds appear as horizontal streaks
- Aurora activity shows as colored bands
- Day/night cycles are clearly visible

### Automatic Cropping

By default, keograms crop 7% from the top to remove the overlay bar:
- 7% of 2160px (4K) = 151px cropped
- That clears the text; the last ~20px of the bar's fade survives the crop

### Standalone Keogram Generation

```bash
# Create keogram from a day's images
python3 -m raspilapse.video.keogram --dir /var/www/html/images/2025/12/24/

# Custom output location
python3 -m raspilapse.video.keogram --dir /path/to/images --output keogram.jpg

# Adjust crop (e.g., larger overlay)
python3 -m raspilapse.video.keogram --dir /path/to/images --crop-top 10

# No cropping (include overlay in keogram)
python3 -m raspilapse.video.keogram --dir /path/to/images --no-crop
```

### Keogram Output

Keograms are saved alongside videos:
- `/var/www/html/videos/2025/12/keogram_kringelen_2025-12-24_0500_to_2025-12-25_0500.jpg`

## Slitscan Generation

A slitscan is a full-width image where each frame contributes columns at progressive positions from left to right. Unlike a keogram (which is narrow), slitscan shows the actual scene with time progressing across the width.

### What is a Slitscan?

A slitscan creates an image where:
- The left edge shows the scene from the first frame
- The right edge shows the scene from the last frame
- Time progresses smoothly from left to right
- You can see the actual scene content, not just a narrow strip

For example, with 1920px wide images and 960 frames:
- Frame 0 contributes columns 0-1 (leftmost)
- Frame 1 contributes columns 2-3
- ...
- Frame 959 contributes columns 1918-1919 (rightmost)

### Generating Slitscan with Timelapse

```bash
# Create video with both keogram and slitscan
python3 -m raspilapse.cli.timelapse --slitscan

# The slitscan is optional - by default only keogram is generated
```

### Standalone Slitscan Generation

```bash
# Create slitscan from a day's images
python3 -m raspilapse.video.keogram --dir /var/www/html/images/2025/12/24/ --slitscan

# Custom output location
python3 -m raspilapse.video.keogram --dir /path/to/images --slitscan --output slitscan_custom.jpg

# No cropping (include overlay in slitscan)
python3 -m raspilapse.video.keogram --dir /path/to/images --slitscan --no-crop
```

### Slitscan Output

Slitscans are saved alongside videos and keograms:
- `/var/www/html/videos/2025/12/slitscan_kringelen_2025-12-24_0500_to_2025-12-25_0500.jpg`

### Daily Timelapse with Slitscan

The daily timelapse service (`raspilapse/video/daily.py`) automatically generates slitscan along with keogram and uploads both to the server:

```bash
# Manual run (slitscan is generated by default)
python3 -m raspilapse.cli.daily

# Dry run to see what would be done
python3 -m raspilapse.cli.daily --dry-run
```

The upload sends:
- `video` - The timelapse video file
- `keogram` - The keogram image
- `slitscan` - The slitscan image

## See Also

- [../README.md](../README.md) - Project overview
- [EXPOSURE.md](EXPOSURE.md) - Exposure control and transitions
- [docs/CONFIG-REFERENCE.yml](CONFIG-REFERENCE.yml) - Full configuration reference
