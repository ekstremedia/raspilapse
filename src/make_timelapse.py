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
except ModuleNotFoundError:
    from logging_config import get_logger


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

        # Add resolution scaling if specified
        if resolution:
            width, height = resolution
            cmd.extend(["-vf", f"scale={width}:{height}"])

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
            print_info("Codec", f"{Colors.bold(codec)} (CRF {crf}, preset {preset}, {threads} threads)")
        print_info("Pixel format", Colors.bold(pixel_format))

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
  # Create video from last 24 hours (default)
  python3 src/make_timelapse.py

  # Create video from 04:00 yesterday to 04:00 today
  python3 src/make_timelapse.py --start 04:00 --end 04:00

  # Create video from 20:00 yesterday to 08:00 today (test)
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
        help="Start time in HH:MM format (e.g., 04:00). If end time is same or earlier, assumes previous day. Default: 24 hours ago from now.",
    )
    parser.add_argument(
        "--end", help="End time in HH:MM format (e.g., 04:00). Default: current time."
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

    # Default to last 24 hours if no start/end times provided
    if not args.start and not args.end:
        # Default: last 24 hours
        end_datetime = now
        start_datetime = now - timedelta(hours=24)
        logger.info("Using default time range: last 24 hours")
    elif args.start and args.end:
        # Both start and end provided
        try:
            start_hour, start_min = parse_time(args.start)
            end_hour, end_min = parse_time(args.end)
        except ValueError as e:
            print(Colors.error(f"✗ {e}"))
            logger.error(str(e))
            return 1

        end_datetime = now.replace(hour=end_hour, minute=end_min, second=0, microsecond=0)

        # If end time is same or earlier than start time, assume start was yesterday
        if (end_hour < start_hour) or (end_hour == start_hour and end_min <= start_min):
            start_datetime = (end_datetime - timedelta(days=1)).replace(
                hour=start_hour, minute=start_min
            )
        else:
            start_datetime = end_datetime.replace(hour=start_hour, minute=start_min)
    else:
        # Only one provided - error
        print(
            Colors.error("✗ Must provide both --start and --end, or neither for default 24 hours")
        )
        logger.error("Invalid time arguments")
        return 1

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
        # Enhanced filename generation for better organization
        # If it's a 24-hour video (default), use simpler naming
        if not args.start and not args.end:
            # For daily videos, use the date of the end time (today)
            # Format: cameraname_daily_YYYY-MM-DD.mp4
            filename = f"{project_name}_daily_{end_datetime.strftime('%Y-%m-%d')}.mp4"
        else:
            # For custom time ranges, use the pattern from config
            filename_pattern = config["video"]["filename_pattern"]
            filename = filename_pattern.format(
                name=project_name,
                start_date=start_datetime.strftime("%Y-%m-%d"),
                end_date=end_datetime.strftime("%Y-%m-%d"),
            )
        output_file = video_path / filename

    # Create video
    success = create_video(
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
        logger=logger,
    )

    if success:
        print_section("✓ TIMELAPSE VIDEO CREATED SUCCESSFULLY!")
        logger.info("Timelapse video generation completed successfully")
        return 0
    else:
        print_section("✗ FAILED TO CREATE VIDEO")
        logger.error("Timelapse video generation failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
