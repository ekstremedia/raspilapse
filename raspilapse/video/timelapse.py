#!/usr/bin/env python3
"""
Generate timelapse video from captured images using ffmpeg.

This script collects images from a specified time range and creates a timelapse video
with configurable framerate and quality settings.
"""

import argparse
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple

import yaml

from raspilapse.config import load_config as _load_config
from raspilapse.console import Colors, print_info, print_section, print_subsection
from raspilapse.logging_setup import configure_logging, get_logger
from raspilapse.video.keogram import create_time_slices

# Encoders that take a bitrate rather than a CRF, and that the Pi's V4L2 stack
# cannot drive above 1080p. Named once so the two places that care -- the
# resolution guard and the bitrate branch -- cannot disagree.
HARDWARE_CODECS = ("h264_v4l2m2m", "h264_omx")

# The Pi 4's hardware H.264 encoder limit.
HARDWARE_MAX_RESOLUTION = (1920, 1080)


def load_config(config_path: str = "config/config.yml") -> dict:
    """Load configuration from YAML file."""
    return _load_config(config_path)


def parse_time(time_str: str) -> Tuple[int, int]:
    """
    Parse time string in HH:MM format.

    Args:
        time_str: Time in "HH:MM" format

    Returns:
        Tuple of (hour, minute)
    """
    try:
        hour, minute = map(int, time_str.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Invalid time range")
        return hour, minute
    except ValueError as e:
        raise ValueError(f"Invalid time format '{time_str}'. Expected HH:MM (e.g., '04:00')") from e


def find_images_in_range(
    base_dir: str,
    project_name: str,
    start_datetime: datetime,
    end_datetime: datetime,
    organize_by_date: bool = True,
    date_format: str = "%Y/%m/%d",
) -> List[Path]:
    """
    Find all images within the specified datetime range.

    Args:
        base_dir: Base directory containing images
        project_name: Project name used in filenames
        start_datetime: Start datetime (inclusive)
        end_datetime: End datetime (inclusive)
        organize_by_date: Whether images are organized in date subdirectories
        date_format: Date format for subdirectories

    Returns:
        List of image paths sorted by filename
    """
    images = []
    base_path = Path(base_dir)

    if not base_path.exists():
        raise ValueError(f"Image directory not found: {base_dir}")

    # Generate list of dates to search
    current_date = start_datetime.date()
    end_date = end_datetime.date()

    while current_date <= end_date:
        if organize_by_date:
            # Search in date-organized subdirectories
            date_subdir = current_date.strftime(date_format)
            search_dir = base_path / date_subdir
        else:
            # Search in base directory
            search_dir = base_path

        if search_dir.exists():
            # Find all images for this date
            pattern = f"{project_name}_{current_date.strftime('%Y_%m_%d')}_*.jpg"
            for img_path in search_dir.glob(pattern):
                # Parse timestamp from filename
                try:
                    # Extract timestamp from filename: project_YYYY_MM_DD_HH_MM_SS.jpg
                    parts = img_path.stem.split("_")
                    if len(parts) >= 6:
                        img_datetime = datetime(
                            int(parts[-6]),  # year
                            int(parts[-5]),  # month
                            int(parts[-4]),  # day
                            int(parts[-3]),  # hour
                            int(parts[-2]),  # minute
                            int(parts[-1]),  # second
                        )

                        # Check if within time range
                        if start_datetime <= img_datetime <= end_datetime:
                            images.append(img_path)
                except (ValueError, IndexError):
                    # Skip files that don't match expected format
                    continue

        current_date += timedelta(days=1)

    # Sort by filename (which includes timestamp)
    images.sort()
    return images


def create_video(
    image_list: List[Path],
    output_path: Path,
    fps: int = 25,
    codec: str = "libx264",
    pixel_format: str = "yuv420p",
    crf: int = 20,
    preset: str = "veryfast",
    threads: int = 3,
    bitrate: str = "10M",
    resolution: Tuple[int, int] = None,
    deflicker: bool = True,
    deflicker_size: int = 10,
    logger: logging.Logger = None,
) -> bool:
    """
    Create timelapse video from image list using ffmpeg.

    Args:
        image_list: List of image paths
        output_path: Output video path
        fps: Frames per second
        codec: Video codec (e.g., "libx264")
        pixel_format: Pixel format (e.g., "yuv420p")
        crf: Constant Rate Factor (quality, 0-51, lower = better)
        resolution: Optional (width, height) to scale video
        deflicker: Enable deflicker filter to smooth exposure transitions
        deflicker_size: Deflicker window size (frames to average)
        logger: Optional logger instance

    Returns:
        True if successful, False otherwise
    """
    # Validate deflicker_size
    if deflicker and deflicker_size < 1:
        raise ValueError(f"deflicker_size must be positive, got {deflicker_size}")

    # The Pi's V4L2 encoder tops out at 1080p. Above that ffmpeg gets as far as
    # "can't configure encoder" and exits, which -- since nothing sets a
    # resolution by default -- means simply setting codec.name to h264_v4l2m2m
    # silently converts the 05:00 job into a nightly failure. Verified on this
    # hardware: it fails at both 3840x2160 and 2560x1440, succeeds at 1920x1080.
    if codec in HARDWARE_CODECS:
        max_w, max_h = HARDWARE_MAX_RESOLUTION
        if resolution is None or resolution[0] > max_w or resolution[1] > max_h:
            requested = (
                "source resolution" if resolution is None else f"{resolution[0]}x{resolution[1]}"
            )
            msg = (
                f"{codec} cannot encode above {max_w}x{max_h}; requested {requested}. "
                f"Pass --hd to scale to {max_w}x{max_h}, or set "
                f"video.codec.name: libx264 to keep the source resolution."
            )
            print(Colors.error(f"✗ {msg}"))
            if logger:
                logger.error(msg)
            return False

    if not image_list:
        msg = "No images to process"
        print(Colors.error(f"✗ {msg}"))
        if logger:
            logger.error(msg)
        return False

    # Create temporary file with list of images
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        list_file = f.name
        for img_path in image_list:
            # ffmpeg concat demuxer format: file 'path'
            f.write(f"file '{img_path.absolute()}'\n")

    if logger:
        logger.info(f"Created image list file: {list_file}")

    try:
        # Build ffmpeg command
        cmd = [
            "ffmpeg",
            "-stats",  # Show encoding progress
            "-loglevel",
            "info",  # Show informational messages
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_file,
            "-r",
            str(fps),
            "-vcodec",
            codec,
            "-pix_fmt",
            pixel_format,
        ]

        # Hardware encoders (h264_v4l2m2m, h264_omx) use bitrate, software (libx264) uses CRF
        if codec in HARDWARE_CODECS:
            cmd.extend(["-b:v", bitrate])
        else:
            # libx264: use preset and threads to control memory usage
            cmd.extend(["-preset", preset, "-threads", str(threads), "-crf", str(crf)])

        # Build video filter chain
        filters = []

        # Add resolution scaling if specified
        if resolution:
            width, height = resolution
            filters.append(f"scale={width}:{height}")

        # Add deflicker filter to smooth exposure transitions (like sunrise spikes)
        # mode=pm: Predictive Mean (best for timelapses)
        # size: Averages luminance over N frames (smooths single spikes)
        if deflicker:
            filters.append(f"deflicker=mode=pm:size={deflicker_size}")

        # Apply filter chain if any filters exist
        if filters:
            cmd.extend(["-vf", ",".join(filters)])

        # Add faststart flag for web streaming and better resilience
        # This writes the moov atom at the beginning of the file
        cmd.extend(["-movflags", "+faststart"])

        # Add output path (overwrite if exists)
        cmd.extend(["-y", str(output_path)])

        print_subsection("🎬 Generating Video")
        print_info("Images", f"{Colors.bold(str(len(image_list)))} frames")
        print_info("Frame rate", f"{Colors.bold(str(fps))} fps")
        if codec in HARDWARE_CODECS:
            print_info("Codec", f"{Colors.bold(codec)} (bitrate {bitrate})")
        else:
            print_info(
                "Codec", f"{Colors.bold(codec)} (CRF {crf}, preset {preset}, {threads} threads)"
            )
        print_info("Pixel format", Colors.bold(pixel_format))
        if deflicker:
            print_info("Deflicker", f"{Colors.bold('enabled')} (size={deflicker_size} frames)")

        duration_seconds = len(image_list) / fps
        print_info(
            "Video duration",
            f"{Colors.bold(f'{duration_seconds:.1f}s')} ({duration_seconds/60:.2f} minutes)",
        )

        if logger:
            logger.info(f"Running ffmpeg: {' '.join(cmd)}")

        print(f"\n{Colors.CYAN}⏳ Processing video with ffmpeg...{Colors.END}")
        print(f"{Colors.YELLOW}   (This may take a few minutes for large timelapses){Colors.END}")
        print()  # Add blank line before ffmpeg output

        # Run ffmpeg with real-time output
        # stderr is where ffmpeg writes its progress info
        result = subprocess.run(cmd, capture_output=False, text=True)

        print()  # Add blank line after ffmpeg output
        if result.returncode == 0:
            # Show file size
            size_mb = output_path.stat().st_size / (1024 * 1024)

            print(f"\n{Colors.success('✓ Video created successfully!')}")
            print_info("Output file", Colors.bold(str(output_path)))
            print_info("File size", Colors.bold(f"{size_mb:.2f} MB"))

            if logger:
                logger.info(f"Video created: {output_path} ({size_mb:.2f} MB)")
            return True
        else:
            print(f"\n{Colors.error('✗ ffmpeg failed with return code ' + str(result.returncode))}")
            print(f"{Colors.YELLOW}Check the ffmpeg output above for error details{Colors.END}")

            if logger:
                logger.error(f"ffmpeg failed with return code {result.returncode}")
            return False

    finally:
        # Clean up temporary file
        try:
            os.unlink(list_file)
            if logger:
                logger.debug(f"Cleaned up temporary file: {list_file}")
        except Exception as e:
            if logger:
                logger.warning(f"Failed to clean up temp file {list_file}: {e}")


def main():
    """Assemble captured frames into a video with ffmpeg."""
    parser = argparse.ArgumentParser(
        description="Generate timelapse video from captured images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create video using default times from config (e.g., 04:00 yesterday to 04:00 today)
  python3 -m raspilapse.cli.timelapse

  # Create video from 07:00 to 15:00 today
  python3 -m raspilapse.cli.timelapse --start 07:00 --end 15:00 --today

  # Create video from specific dates and times
  python3 -m raspilapse.cli.timelapse --start 07:00 --end 15:00 --start-date 2025-12-24 --end-date 2025-12-25

  # Create video from 20:00 yesterday to 08:00 today
  python3 -m raspilapse.cli.timelapse --start 20:00 --end 08:00

  # Use first 100 images only (for testing)
  python3 -m raspilapse.cli.timelapse --limit 100

  # Custom config file
  python3 -m raspilapse.cli.timelapse -c config/custom.yml

  # Save to specific output directory (for automated daily videos)
  python3 -m raspilapse.cli.timelapse --output-dir /var/www/html/videos

  # Create 1080p video using hardware encoder (faster on Raspberry Pi)
  python3 -m raspilapse.cli.timelapse -hd -hw
        """,
    )

    parser.add_argument(
        "--start",
        help="Start time in HH:MM format (e.g., 07:00). Default: from config or 00:00.",
    )
    parser.add_argument(
        "--end",
        help="End time in HH:MM format (e.g., 15:00). Default: from config or current time.",
    )
    parser.add_argument(
        "--start-date",
        help="Start date in YYYY-MM-DD format (e.g., 2025-12-24). "
        "Default: yesterday if end time <= start time, else today.",
    )
    parser.add_argument(
        "--end-date",
        help="End date in YYYY-MM-DD format (e.g., 2025-12-25). Default: today.",
    )
    parser.add_argument(
        "--today",
        action="store_true",
        help="Both start and end on today's date (use with --start and --end times).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of images to process (0 = all images, useful for testing)",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config/config.yml",
        help="Path to configuration file (default: config/config.yml)",
    )
    parser.add_argument(
        "--fps", type=int, help="Override frame rate from config (frames per second)"
    )
    parser.add_argument("--output", help="Override output filename")
    parser.add_argument(
        "--output-dir", help="Override output directory from config (e.g., /var/www/html/videos)"
    )
    parser.add_argument(
        "--no-keogram",
        action="store_true",
        help="Skip keogram generation (default: keogram is created alongside video)",
    )
    parser.add_argument(
        "--keogram-only",
        action="store_true",
        help="Only generate keogram, skip video creation",
    )
    parser.add_argument(
        "--slitscan",
        action="store_true",
        help="Also generate slitscan image (full-width image where time progresses left to right)",
    )
    parser.add_argument(
        "-hd",
        "--hd",
        action="store_true",
        help="Scale output to 1080p resolution (1920x1080)",
    )
    parser.add_argument(
        "-hw",
        "--hw",
        action="store_true",
        help=(
            "Use hardware H264 encoder (h264_v4l2m2m) instead of libx264. "
            "Requires --hd: the Pi's encoder cannot exceed 1920x1080, and "
            "without it the output stays at the source resolution."
        ),
    )

    args = parser.parse_args()
    configure_logging(args.config)

    # Load configuration
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(Colors.error(f"✗ Config file not found: {args.config}"))
        return 1
    except yaml.YAMLError as e:
        print(Colors.error(f"✗ Invalid YAML in config file: {e}"))
        return 1

    # Setup logger
    logger = get_logger("make_timelapse", args.config)

    # Calculate datetime range
    now = datetime.now()
    today = now.date()
    yesterday = today - timedelta(days=1)

    # Get default times from config (or use sensible defaults)
    default_start_time = config.get("video", {}).get("default_start_time", "05:00")
    default_end_time = config.get("video", {}).get("default_end_time", "05:00")

    # Parse start time
    if args.start:
        try:
            start_hour, start_min = parse_time(args.start)
        except ValueError as e:
            print(Colors.error(f"✗ {e}"))
            logger.error(str(e))
            return 1
    else:
        # Use config default
        try:
            start_hour, start_min = parse_time(default_start_time)
        except ValueError:
            start_hour, start_min = 5, 0

    # Parse end time
    if args.end:
        try:
            end_hour, end_min = parse_time(args.end)
        except ValueError as e:
            print(Colors.error(f"✗ {e}"))
            logger.error(str(e))
            return 1
    else:
        # Use config default
        try:
            end_hour, end_min = parse_time(default_end_time)
        except ValueError:
            end_hour, end_min = 5, 0

    # Parse dates
    if args.start_date:
        try:
            start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
        except ValueError:
            print(
                Colors.error(
                    f"✗ Invalid start date format '{args.start_date}'. Expected YYYY-MM-DD"
                )
            )
            return 1
    else:
        start_date = None  # Will be determined below

    if args.end_date:
        try:
            end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()
        except ValueError:
            print(Colors.error(f"✗ Invalid end date format '{args.end_date}'. Expected YYYY-MM-DD"))
            return 1
    else:
        end_date = today  # Default to today

    # Determine start date if not provided
    if start_date is None:
        if args.today:
            # Both start and end on today
            start_date = today
        elif (end_hour < start_hour) or (end_hour == start_hour and end_min <= start_min):
            # End time is same or earlier than start time - start was yesterday
            start_date = yesterday
        else:
            # Same day
            start_date = end_date

    # Build datetime objects
    start_datetime = datetime.combine(start_date, datetime.min.time()).replace(
        hour=start_hour, minute=start_min, second=0, microsecond=0
    )
    end_datetime = datetime.combine(end_date, datetime.min.time()).replace(
        hour=end_hour, minute=end_min, second=0, microsecond=0
    )

    # Validate range
    if start_datetime >= end_datetime:
        print(
            Colors.error(
                f"✗ Start time ({start_datetime}) must be before end time ({end_datetime})"
            )
        )
        logger.error("Invalid time range: start >= end")
        return 1

    logger.info(f"Time range: {start_datetime} to {end_datetime}")

    logger.info(f"Starting timelapse generation: {start_datetime} to {end_datetime}")

    # Print header
    print_section("🎥 TIMELAPSE VIDEO GENERATOR")
    print_subsection("⏰ Time Range")
    print_info("Start", Colors.bold(start_datetime.strftime("%Y-%m-%d %H:%M")))
    print_info("End", Colors.bold(end_datetime.strftime("%Y-%m-%d %H:%M")))
    duration_hours = (end_datetime - start_datetime).total_seconds() / 3600
    print_info("Duration", Colors.bold(f"{duration_hours:.1f} hours"))

    # Get config values
    base_dir = config["output"]["directory"]
    project_name = config["output"]["project_name"]
    organize_by_date = config["output"].get("organize_by_date", True)
    date_format = config["output"].get("date_format", "%Y/%m/%d")

    # Use output-dir override if provided, otherwise use config
    video_base_dir = args.output_dir if args.output_dir else config["video"]["directory"]
    video_organize_by_date = config["video"].get("organize_by_date", False)
    video_date_format = config["video"].get("date_format", "%Y/%m")
    fps = args.fps if args.fps else config["video"]["fps"]
    codec = config["video"]["codec"]["name"]
    pixel_format = config["video"]["codec"]["pixel_format"]
    # Fallbacks for a config that predates these keys. They were ultrafast/23/2,
    # chosen when 4K encoding was OOMing; measured on this camera's frames the
    # new values peak at 1021 MB against the 1399 MB the fast/25/2 config
    # actually running in production reaches, so this is not a relaxation of
    # that fix -- it uses less memory than the thing it replaces.
    crf = config["video"]["codec"].get("crf", 20)
    preset = config["video"]["codec"].get("preset", "veryfast")
    threads = config["video"]["codec"].get("threads", 3)
    bitrate = config["video"]["codec"].get("bitrate", "10M")
    deflicker = config["video"].get("deflicker", True)
    deflicker_size = config["video"].get("deflicker_size", 10)

    # Get camera name from overlay config for better video naming
    camera_name = config.get("overlay", {}).get("camera_name", project_name)

    print_subsection("⚙️  Configuration")
    print_info("Image directory", Colors.bold(base_dir))
    print_info("Project name", Colors.bold(project_name))
    print_info("Camera name", Colors.bold(camera_name))

    # Apply --hw flag: use hardware encoder
    if args.hw:
        codec = "h264_v4l2m2m"
        logger.info("Using hardware encoder: h264_v4l2m2m")

    # Apply --hd flag: set 1080p resolution
    resolution = (1920, 1080) if args.hd else None

    # Build video settings string
    if codec in HARDWARE_CODECS:
        video_settings_str = f"{Colors.bold(str(fps))} fps, {codec} (HW), bitrate {bitrate}"
    else:
        video_settings_str = f"{Colors.bold(str(fps))} fps, {codec}, CRF {crf}"
    if args.hd:
        video_settings_str += ", 1080p"
    print_info("Video settings", video_settings_str)

    # Find images
    print_subsection("🔍 Searching for Images")
    logger.info(f"Searching for images in {base_dir}")

    try:
        images = find_images_in_range(
            base_dir, project_name, start_datetime, end_datetime, organize_by_date, date_format
        )
    except ValueError as e:
        print(Colors.error(f"✗ {e}"))
        logger.error(str(e))
        return 1

    if not images:
        # Exit 2 means "nothing to do", not "something broke". Callers (notably
        # daily_timelapse.py) treat it as success so an empty day does not leave
        # a systemd unit in the failed state. Exit 1 stays reserved for errors.
        msg = f"No images found between {start_datetime} and {end_datetime} - nothing to render"
        print(Colors.warning(f"⚠ {msg}"))
        logger.warning(msg)
        return 2

    print(f"  {Colors.success('✓')} Found {Colors.bold(str(len(images)))} images")
    logger.info(f"Found {len(images)} images")

    # Apply limit if specified
    if args.limit > 0 and len(images) > args.limit:
        print(
            f"  {Colors.warning('⚠')} Limiting to first {Colors.bold(str(args.limit))} images {Colors.YELLOW}(testing mode){Colors.END}"
        )
        logger.info(f"Limiting to {args.limit} images for testing")
        images = images[: args.limit]

    # Show first and last image
    print(f"  {Colors.CYAN}→{Colors.END} First: {Colors.bold(images[0].name)}")
    print(f"  {Colors.CYAN}→{Colors.END} Last:  {Colors.bold(images[-1].name)}")

    # Create output directory (with optional date organization)
    video_path = Path(video_base_dir)
    if video_organize_by_date:
        # Use end_datetime to determine the subdirectory
        date_subdir = end_datetime.strftime(video_date_format)
        video_path = video_path / date_subdir
    video_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output directory: {video_path}")

    # Generate output filename
    if args.output:
        output_file = video_path / args.output
    else:
        # Generate filename with dates and times to avoid overwrites
        # Format: projectname_YYYY-MM-DD_HHMM_to_YYYY-MM-DD_HHMM.mp4
        start_str = start_datetime.strftime("%Y-%m-%d_%H%M")
        end_str = end_datetime.strftime("%Y-%m-%d_%H%M")

        # If same date, use shorter format
        if start_datetime.date() == end_datetime.date():
            # Same day: projectname_YYYY-MM-DD_HHMM-HHMM.mp4
            date_str = start_datetime.strftime("%Y-%m-%d")
            start_time = start_datetime.strftime("%H%M")
            end_time = end_datetime.strftime("%H%M")
            filename = f"{project_name}_{date_str}_{start_time}-{end_time}.mp4"
        else:
            # Different days: projectname_YYYY-MM-DD_HHMM_to_YYYY-MM-DD_HHMM.mp4
            filename = f"{project_name}_{start_str}_to_{end_str}.mp4"

        output_file = video_path / filename

    # Create video (unless keogram-only mode)
    video_success = True
    if not args.keogram_only:
        video_success = create_video(
            images,
            output_file,
            fps=fps,
            codec=codec,
            pixel_format=pixel_format,
            crf=crf,
            preset=preset,
            threads=threads,
            bitrate=bitrate,
            resolution=resolution,
            deflicker=deflicker,
            deflicker_size=deflicker_size,
            logger=logger,
        )

    # Keogram and slitscan are both a vertical strip taken from every frame;
    # only which strip and where it lands differs. Generating them separately
    # decoded the whole day twice. Measured on 300 real 4K frames from this
    # camera: 60.7s for two passes against 27.6s for one, with both outputs
    # byte-identical (sha256) to what the split version produced.
    keogram_success = True
    slitscan_success = True

    keogram_file = None
    if not args.no_keogram:
        # Generate keogram filename (same as video but with keogram_ prefix and .jpg)
        if args.keogram_only and args.output:
            # Ensure .jpg extension for keogram
            custom_output = Path(args.output)
            if custom_output.suffix.lower() != ".jpg":
                custom_output = custom_output.with_suffix(".jpg")
            keogram_file = video_path / custom_output.name
        else:
            keogram_filename = output_file.stem.replace("_daily_", "_keogram_") + ".jpg"
            if "_daily_" not in output_file.stem:
                keogram_filename = f"keogram_{output_file.stem}.jpg"
            keogram_file = video_path / keogram_filename

    slitscan_file = None
    if args.slitscan:
        # Generate slitscan filename (similar to keogram naming)
        slitscan_filename = output_file.stem.replace("_daily_", "_slitscan_") + ".jpg"
        if "_daily_" not in output_file.stem:
            slitscan_filename = f"slitscan_{output_file.stem}.jpg"
        slitscan_file = video_path / slitscan_filename

    if keogram_file or slitscan_file:
        wanted = [
            name for name, path in (("Keogram", keogram_file), ("Slitscan", slitscan_file)) if path
        ]
        print_subsection(f"\U0001f305 Generating {' and '.join(wanted)}")
        logger.info(f"Starting {' and '.join(w.lower() for w in wanted)} generation")

        slices = create_time_slices(
            images,
            keogram_path=keogram_file,
            slitscan_path=slitscan_file,
            quality=95,
            crop_top_percent=7.0,  # Crop overlay bar (2 lines + padding)
            logger=logger,
        )

        if keogram_file:
            keogram_success = slices.get("keogram", False)
            if keogram_success:
                logger.info(f"Keogram created: {keogram_file}")
            else:
                logger.warning("Keogram generation failed")

        if slitscan_file:
            slitscan_success = slices.get("slitscan", False)
            if slitscan_success:
                logger.info(f"Slitscan created: {slitscan_file}")
            else:
                logger.warning("Slitscan generation failed")

    # Report final status
    if args.keogram_only:
        if keogram_success:
            print_section("✓ KEOGRAM CREATED SUCCESSFULLY!")
            return 0
        else:
            print_section("✗ FAILED TO CREATE KEOGRAM")
            return 1
    elif video_success:
        extras = []
        if not args.no_keogram and keogram_success:
            extras.append("keogram")
        if args.slitscan and slitscan_success:
            extras.append("slitscan")

        if extras:
            print_section(
                f"✓ TIMELAPSE VIDEO AND {', '.join(extras).upper()} CREATED SUCCESSFULLY!"
            )
        else:
            if not args.no_keogram and not keogram_success:
                print_section("✓ TIMELAPSE VIDEO CREATED (keogram failed)")
            else:
                print_section("✓ TIMELAPSE VIDEO CREATED SUCCESSFULLY!")
        logger.info("Timelapse video generation completed successfully")
        return 0
    else:
        print_section("✗ FAILED TO CREATE VIDEO")
        logger.error("Timelapse video generation failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
