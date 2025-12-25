#!/usr/bin/env python3
"""
Generate timelapse video from captured images using ffmpeg.

This script collects images from a specified time range and creates a timelapse video
with configurable framerate and quality settings.
"""

import os
import sys
import argparse
import yaml
from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import tempfile
from typing import List, Tuple
import logging

# Add project root to path for imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from src.logging_config import get_logger
    from src.create_keogram import create_keogram_from_images
except ModuleNotFoundError:
    from logging_config import get_logger
    from create_keogram import create_keogram_from_images


# ANSI color codes for pretty output
class Colors:
    """ANSI color codes for terminal output."""

    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    END = "\033[0m"

    @staticmethod
    def header(text: str) -> str:
        return f"{Colors.BOLD}{Colors.CYAN}{text}{Colors.END}"

    @staticmethod
    def success(text: str) -> str:
        return f"{Colors.GREEN}{text}{Colors.END}"

    @staticmethod
    def error(text: str) -> str:
        return f"{Colors.RED}{text}{Colors.END}"

    @staticmethod
    def warning(text: str) -> str:
        return f"{Colors.YELLOW}{text}{Colors.END}"

    @staticmethod
    def info(text: str) -> str:
        return f"{Colors.BLUE}{text}{Colors.END}"

    @staticmethod
    def bold(text: str) -> str:
        return f"{Colors.BOLD}{text}{Colors.END}"


def print_section(title: str):
    """Print a section header."""
    print(f"\n{Colors.header('═' * 70)}")
    print(f"{Colors.header(f'  {title}')}")
    print(f"{Colors.header('═' * 70)}")


def print_subsection(title: str):
    """Print a subsection header."""
    print(f"\n{Colors.bold(title)}")
    print(Colors.CYAN + "─" * 70 + Colors.END)


def print_info(label: str, value: str):
    """Print an info line with label and value."""
    print(f"  {Colors.BOLD}{label}:{Colors.END} {value}")


def load_config(config_path: str = "config/config.yml") -> dict:
    """Load configuration from YAML file."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


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
    crf: int = 23,
    preset: str = "ultrafast",
    threads: int = 2,
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
        if codec in ["h264_v4l2m2m", "h264_omx"]:
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
        if codec in ["h264_v4l2m2m", "h264_omx"]:
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
    parser = argparse.ArgumentParser(
        description="Generate timelapse video from captured images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create video using default times from config (e.g., 04:00 yesterday to 04:00 today)
  python3 src/make_timelapse.py

  # Create video from 07:00 to 15:00 today
  python3 src/make_timelapse.py --start 07:00 --end 15:00 --today

  # Create video from specific dates and times
  python3 src/make_timelapse.py --start 07:00 --end 15:00 --start-date 2025-12-24 --end-date 2025-12-25

  # Create video from 20:00 yesterday to 08:00 today
  python3 src/make_timelapse.py --start 20:00 --end 08:00

  # Use first 100 images only (for testing)
  python3 src/make_timelapse.py --limit 100

  # Custom config file
  python3 src/make_timelapse.py -c config/custom.yml

  # Save to specific output directory (for automated daily videos)
  python3 src/make_timelapse.py --output-dir /var/www/html/videos
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
        help="Start date in YYYY-MM-DD format (e.g., 2025-12-24). Default: yesterday if end time <= start time, else today.",
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

    args = parser.parse_args()

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
    use_current_time = False

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
    if use_current_time:
        end_datetime = now
    else:
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
    crf = config["video"]["codec"].get("crf", 23)
    preset = config["video"]["codec"].get("preset", "ultrafast")
    threads = config["video"]["codec"].get("threads", 2)
    bitrate = config["video"]["codec"].get("bitrate", "10M")
    deflicker = config["video"].get("deflicker", True)
    deflicker_size = config["video"].get("deflicker_size", 10)

    # Get camera name from overlay config for better video naming
    camera_name = config.get("overlay", {}).get("camera_name", project_name)

    print_subsection("⚙️  Configuration")
    print_info("Image directory", Colors.bold(base_dir))
    print_info("Project name", Colors.bold(project_name))
    print_info("Camera name", Colors.bold(camera_name))
    print_info("Video settings", f"{Colors.bold(str(fps))} fps, {codec}, CRF {crf}")

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
        msg = "No images found in specified time range"
        print(Colors.error(f"✗ {msg}"))
        logger.error(msg)
        return 1

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
            filename = f"{project_name}_{start_datetime.strftime('%Y-%m-%d')}_{start_datetime.strftime('%H%M')}-{end_datetime.strftime('%H%M')}.mp4"
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
            resolution=None,  # Use original resolution
            deflicker=deflicker,
            deflicker_size=deflicker_size,
            logger=logger,
        )

    # Create keogram (unless --no-keogram)
    keogram_success = True
    if not args.no_keogram:
        print_subsection("🌅 Generating Keogram")
        logger.info("Starting keogram generation")

        # Generate keogram filename (same as video but with keogram_ prefix and .jpg)
        if args.keogram_only and args.output:
            keogram_file = video_path / args.output
        else:
            keogram_filename = output_file.stem.replace("_daily_", "_keogram_") + ".jpg"
            if "_daily_" not in output_file.stem:
                keogram_filename = f"keogram_{output_file.stem}.jpg"
            keogram_file = video_path / keogram_filename

        keogram_success = create_keogram_from_images(
            images,
            keogram_file,
            quality=95,
            crop_top_percent=7.0,  # Crop overlay bar (2 lines + padding)
            logger=logger,
        )

        if keogram_success:
            logger.info(f"Keogram created: {keogram_file}")
        else:
            logger.warning("Keogram generation failed")

    # Report final status
    if args.keogram_only:
        if keogram_success:
            print_section("✓ KEOGRAM CREATED SUCCESSFULLY!")
            return 0
        else:
            print_section("✗ FAILED TO CREATE KEOGRAM")
            return 1
    elif video_success:
        if keogram_success:
            print_section("✓ TIMELAPSE VIDEO AND KEOGRAM CREATED SUCCESSFULLY!")
        else:
            print_section("✓ TIMELAPSE VIDEO CREATED (keogram failed)")
        logger.info("Timelapse video generation completed successfully")
        return 0
    else:
        print_section("✗ FAILED TO CREATE VIDEO")
        logger.error("Timelapse video generation failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
