#!/usr/bin/env python3
"""
Daily Timelapse Runner - Creates timelapse video and uploads to webserver.

This script:
1. Runs make_timelapse.py to create the daily video and keogram
2. Uploads the video, thumbnail, keogram, and images to the configured webserver

Designed to be run via cron at 5 AM daily.
"""

import os
import sys
import argparse
import yaml
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path for imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from src.config_utils import load_config
    from src.logging_config import configure_logging, get_logger
    from src.upload_service import UploadService
except ModuleNotFoundError:
    from config_utils import load_config
    from logging_config import configure_logging, get_logger
    from upload_service import UploadService


def _find_dated_file(video_dir: Path, patterns: list, date_str: str) -> Path:
    """
    Return the newest file matching the first pattern that hits.

    Patterns are ordered most to least specific, and each is tried both nested
    and flat. Newest by mtime rather than alphabetically, so a regenerated file
    wins over the original.

    Args:
        video_dir: Directory to search
        patterns: Glob patterns, most specific first
        date_str: YYYY-MM-DD, also used to confirm the match really is that day

    Returns:
        The matching path, or None
    """
    for pattern in patterns:
        matches = [m for m in video_dir.glob(pattern) if date_str in m.name]
        if matches:
            return max(matches, key=lambda p: p.stat().st_mtime)
    return None


def _dated_patterns(prefix: str, project_name: str, date_str: str, suffix: str) -> list:
    """Build the nested-then-flat glob list for a prefixed, date-stamped file."""
    stem = f"{prefix}_{project_name}_{date_str}" if prefix else f"{project_name}_{date_str}"
    loose = f"{prefix}*{date_str}" if prefix else f"{project_name}_{date_str}"
    return [
        f"**/{stem}_*{suffix}",
        f"**/{stem}*{suffix}",
        f"**/{loose}*{suffix}",
        f"{stem}_*{suffix}",
        f"{stem}*{suffix}",
        f"{loose}*{suffix}",
    ]


def find_video_file(video_dir: Path, project_name: str, date: datetime.date) -> Path:
    """Find the generated video file for a given date."""
    # e.g. project_2026-07-25_0500-0500.mp4
    date_str = date.strftime("%Y-%m-%d")
    return _find_dated_file(
        video_dir, _dated_patterns("", project_name, date_str, ".mp4"), date_str
    )


def find_keogram_file(video_dir: Path, project_name: str, date: datetime.date) -> Path:
    """Find the generated keogram file for a given date."""
    date_str = date.strftime("%Y-%m-%d")
    return _find_dated_file(
        video_dir, _dated_patterns("keogram", project_name, date_str, ".jpg"), date_str
    )


def find_slitscan_file(video_dir: Path, project_name: str, date: datetime.date) -> Path:
    """Find the generated slitscan file for a given date."""
    date_str = date.strftime("%Y-%m-%d")
    return _find_dated_file(
        video_dir, _dated_patterns("slitscan", project_name, date_str, ".jpg"), date_str
    )


def main():
    parser = argparse.ArgumentParser(
        description="Daily timelapse runner - creates video and uploads to server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run for yesterday (default, designed for 5 AM cron job)
  python3 src/daily_timelapse.py

  # Run for a specific date
  python3 src/daily_timelapse.py --date 2025-12-24

  # Skip upload (just create video)
  python3 src/daily_timelapse.py --no-upload

  # Only upload (video already exists)
  python3 src/daily_timelapse.py --only-upload --date 2025-12-24
        """,
    )

    parser.add_argument(
        "--date",
        help="Date for timelapse in YYYY-MM-DD format (default: yesterday)",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config/config.yml",
        help="Path to configuration file (default: config/config.yml)",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Skip upload step (just create video and keogram)",
    )
    parser.add_argument(
        "--only-upload",
        action="store_true",
        help="Skip video creation (just upload existing files)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without actually doing it",
    )

    args = parser.parse_args()
    configure_logging(args.config)

    # Change to project directory
    os.chdir(project_root)

    # Load configuration
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(f"Error: Config file not found: {args.config}")
        return 1
    except yaml.YAMLError as e:
        print(f"Error: Invalid YAML in config file: {e}")
        return 1

    # Setup logger
    logger = get_logger("daily_timelapse", args.config)

    # Determine the date for the timelapse
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Error: Invalid date format '{args.date}'. Use YYYY-MM-DD")
            return 1
    else:
        # Default: yesterday
        target_date = datetime.now().date() - timedelta(days=1)

    logger.info(f"Daily timelapse for: {target_date}")
    print(f"Creating daily timelapse for: {target_date}")

    # Get config values
    project_name = config["output"]["project_name"]
    video_dir = Path(config["video"]["directory"])

    # Get upload config
    upload_config = config.get("video_upload", {})
    camera_id = upload_config.get(
        "camera_id", config.get("output", {}).get("project_name", "unknown")
    )

    # Step 1: Create timelapse video, keogram, and slitscan
    if not args.only_upload:
        print("\n=== Creating Timelapse Video ===")
        logger.info("Starting timelapse creation")

        # Build command for make_timelapse.py
        # Use 05:00 to 05:00 window (same as old script)
        # Include --slitscan to generate slitscan image
        make_timelapse_cmd = [
            sys.executable,
            os.path.join(project_root, "src", "make_timelapse.py"),
            "--config",
            args.config,
            "--start",
            "05:00",
            "--end",
            "05:00",
            "--start-date",
            target_date.strftime("%Y-%m-%d"),
            "--end-date",
            (target_date + timedelta(days=1)).strftime("%Y-%m-%d"),
            "--slitscan",  # Generate slitscan image alongside keogram
        ]

        if args.dry_run:
            print(f"Would run: {' '.join(make_timelapse_cmd)}")
        else:
            logger.info(f"Running: {' '.join(make_timelapse_cmd)}")
            result = subprocess.run(make_timelapse_cmd, cwd=project_root)

            # Exit 2 from make_timelapse.py means "no images for this date".
            # That is a normal outcome (camera was off, fresh install), not a
            # failure - return 0 so the systemd unit does not go to failed.
            if result.returncode == 2:
                msg = f"No images for {target_date.strftime('%Y-%m-%d')} - nothing to do"
                logger.warning(msg)
                print(msg)
                return 0

            if result.returncode != 0:
                logger.error(f"make_timelapse.py failed with code {result.returncode}")
                print("Error: Timelapse creation failed")
                return 1

            logger.info("Timelapse creation completed")

    # Step 2: Upload to server
    if not args.no_upload and upload_config and upload_config.get("enabled", True):
        print("\n=== Uploading to Server ===")
        logger.info("Starting upload")

        # Find the generated video file
        video_path = find_video_file(video_dir, project_name, target_date)
        if not video_path:
            logger.error(f"Could not find video file in {video_dir}")
            print(f"Error: Video file not found")
            return 1

        logger.info(f"Found video: {video_path}")

        # Find keogram
        keogram_path = find_keogram_file(video_dir, project_name, target_date)
        if keogram_path:
            logger.info(f"Found keogram: {keogram_path}")

        # Find slitscan
        slitscan_path = find_slitscan_file(video_dir, project_name, target_date)
        if slitscan_path:
            logger.info(f"Found slitscan: {slitscan_path}")

        if args.dry_run:
            print(f"Would upload:")
            print(f"  Video: {video_path}")
            print(f"  Keogram: {keogram_path}")
            print(f"  Slitscan: {slitscan_path}")
            print(f"  To: {upload_config.get('url', 'unknown')}")
        else:
            # Use UploadService for upload with retry queue support
            upload_service = UploadService(config, args.config)
            date_str = target_date.strftime("%Y-%m-%d")

            # Check if already successfully uploaded (e.g. by retry_uploads after a reboot)
            existing = upload_service.get_upload_by_date(date_str)
            if existing and existing["status"] == "success":
                logger.info(f"Upload for {date_str} already completed successfully, skipping")
                print(f"Upload already completed for {date_str}, skipping")
                print("\n=== Done ===")
                return 0

            success, error, _response = upload_service.upload_to_server(
                video_path=video_path,
                keogram_path=keogram_path,
                slitscan_path=slitscan_path,
                date=date_str,
            )

            if success:
                logger.info("Upload completed successfully")
                # Record success in queue so retry_uploads won't re-upload this date
                upload_service.record_upload_success(
                    video_path=str(video_path),
                    keogram_path=str(keogram_path) if keogram_path else None,
                    slitscan_path=str(slitscan_path) if slitscan_path else None,
                    video_date=date_str,
                    server_response=_response,
                )
            else:
                # Queue for retry - don't fail the script since video was created
                queue_id = upload_service.queue_upload(
                    video_path=str(video_path),
                    keogram_path=str(keogram_path) if keogram_path else None,
                    slitscan_path=str(slitscan_path) if slitscan_path else None,
                    video_date=date_str,
                )
                if queue_id:
                    upload_service.mark_upload_failed(queue_id, error or "Unknown error")
                    logger.warning(f"Upload failed, queued for retry (id={queue_id}): {error}")
                    print(f"Warning: Upload failed, queued for retry: {error}")
                else:
                    logger.error(f"Upload failed and could not queue for retry: {error}")
                    print(f"Error: Upload failed: {error}")
                    return 1
    elif args.no_upload:
        print("Skipping upload (--no-upload)")
    else:
        print("Upload disabled in config")

    print("\n=== Done ===")
    logger.info("Daily timelapse completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
