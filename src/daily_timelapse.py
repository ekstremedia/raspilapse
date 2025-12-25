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
import requests
import logging

# Add project root to path for imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from src.logging_config import get_logger
except ModuleNotFoundError:
    from logging_config import get_logger


def load_config(config_path: str) -> dict:
    """Load configuration from YAML file."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def find_video_file(video_dir: Path, project_name: str, date: datetime.date) -> Path:
    """Find the generated video file for a given date."""
    # Look for video with yesterday's date in the filename
    # Format: project_YYYY-MM-DD_0500-0500.mp4 or project_YYYY-MM-DD_0500_to_YYYY-MM-DD_0500.mp4
    date_str = date.strftime("%Y-%m-%d")

    patterns = [
        f"{project_name}_{date_str}_*.mp4",
        f"{project_name}_{date_str}*.mp4",
    ]

    for pattern in patterns:
        matches = list(video_dir.glob(pattern))
        if matches:
            # Return most recent match
            return sorted(matches)[-1]

    return None


def find_keogram_file(video_dir: Path, project_name: str, date: datetime.date) -> Path:
    """Find the generated keogram file for a given date."""
    date_str = date.strftime("%Y-%m-%d")

    patterns = [
        f"keogram_{project_name}_{date_str}*.jpg",
        f"keogram*{date_str}*.jpg",
        f"*keogram*{date_str}*.jpg",
        f"keogram*.jpg",  # Fallback: any keogram in directory
    ]

    for pattern in patterns:
        matches = list(video_dir.glob(pattern))
        if matches:
            # Filter to only include files with the target date
            date_matches = [m for m in matches if date_str in m.name]
            if date_matches:
                return sorted(date_matches)[-1]
            # If no exact date match on last pattern, return most recent
            if pattern == "keogram*.jpg":
                return sorted(matches, key=lambda p: p.stat().st_mtime)[-1]

    return None


def upload_to_server(
    video_path: Path,
    keogram_path: Path,
    date: str,
    upload_config: dict,
    camera_id: str,
    logger: logging.Logger,
) -> bool:
    """Upload timelapse video and images to the webserver."""

    url = upload_config["url"]
    api_key = upload_config["api_key"]

    logger.info(f"Uploading to: {url}")
    logger.info(f"Video: {video_path}")
    logger.info(f"Date: {date}")

    files = {}
    file_handles = []  # Keep track of open files to close later

    try:
        # Open all files
        if video_path and video_path.exists():
            f = open(video_path, "rb")
            file_handles.append(f)
            files["video"] = f
        else:
            logger.error(f"Video file not found: {video_path}")
            return False

        if keogram_path and keogram_path.exists():
            f = open(keogram_path, "rb")
            file_handles.append(f)
            files["keogram"] = f
            logger.info(f"Keogram: {keogram_path}")

        # Prepare request data
        data = {
            "title": video_path.name,
            "date": date,
            "camera_id": camera_id,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
        }

        logger.info(f"Uploading files: {list(files.keys())}")

        # Send POST request
        response = requests.post(url, files=files, data=data, headers=headers, timeout=300)

        if response.status_code == 200:
            logger.info("Upload successful!")
            logger.info(f"Response: {response.text}")
            return True
        else:
            logger.error(f"Upload failed with status {response.status_code}")
            logger.error(f"Response: {response.text}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"Upload request failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return False
    finally:
        # Close all file handles
        for f in file_handles:
            try:
                f.close()
            except:
                pass


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
        "-c", "--config",
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
    camera_id = upload_config.get("camera_id", config.get("output", {}).get("project_name", "unknown"))

    # Fallback: load upload config from old config file if not in new config
    if not upload_config.get("url"):
        old_config_path = "/home/pi/raspberrypi-picamera-timelapse/config.yaml"
        if os.path.exists(old_config_path):
            with open(old_config_path, "r") as f:
                old_config = yaml.safe_load(f)
            upload_config = old_config.get("video_upload", {})
            camera_id = old_config.get("camera_id", camera_id)
            logger.info("Using upload config from old config file")

    # Step 1: Create timelapse video and keogram
    if not args.only_upload:
        print("\n=== Creating Timelapse Video ===")
        logger.info("Starting timelapse creation")

        # Build command for make_timelapse.py
        # Use 05:00 to 05:00 window (same as old script)
        make_timelapse_cmd = [
            sys.executable,
            os.path.join(project_root, "src", "make_timelapse.py"),
            "--config", args.config,
            "--start", "05:00",
            "--end", "05:00",
            "--start-date", target_date.strftime("%Y-%m-%d"),
            "--end-date", (target_date + timedelta(days=1)).strftime("%Y-%m-%d"),
        ]

        if args.dry_run:
            print(f"Would run: {' '.join(make_timelapse_cmd)}")
        else:
            logger.info(f"Running: {' '.join(make_timelapse_cmd)}")
            result = subprocess.run(make_timelapse_cmd, cwd=project_root)

            if result.returncode != 0:
                logger.error(f"make_timelapse.py failed with code {result.returncode}")
                print(f"Error: Timelapse creation failed")
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

        if args.dry_run:
            print(f"Would upload:")
            print(f"  Video: {video_path}")
            print(f"  Keogram: {keogram_path}")
            print(f"  To: {upload_config.get('url', 'unknown')}")
        else:
            success = upload_to_server(
                video_path=video_path,
                keogram_path=keogram_path,
                date=target_date.strftime("%Y-%m-%d"),
                upload_config=upload_config,
                camera_id=camera_id,
                logger=logger,
            )

            if not success:
                logger.error("Upload failed")
                print("Error: Upload failed")
                return 1

            logger.info("Upload completed successfully")
    elif args.no_upload:
        print("Skipping upload (--no-upload)")
    else:
        print("Upload disabled in config")

    print("\n=== Done ===")
    logger.info("Daily timelapse completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
