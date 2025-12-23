#!/usr/bin/env python3
"""
One-time batch script to generate timelapse videos for specific past days.
Run with: python3 batch_videos.py
"""

import sys
import os
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from make_timelapse import load_config, find_images_in_range, create_video

# Days to process (Dec 15-22, 2025)
DAYS = [15, 16, 17, 18, 19, 20, 21, 22]


def main():
    config = load_config("config/config.yml")

    base_dir = config["output"]["directory"]
    project_name = config["output"]["project_name"]
    organize_by_date = config["output"].get("organize_by_date", True)
    date_format = config["output"].get("date_format", "%Y/%m/%d")

    fps = config["video"]["fps"]
    codec = config["video"]["codec"]["name"]
    pixel_format = config["video"]["codec"]["pixel_format"]
    crf = config["video"]["codec"].get("crf", 23)
    preset = config["video"]["codec"].get("preset", "ultrafast")
    threads = config["video"]["codec"].get("threads", 2)
    bitrate = config["video"]["codec"].get("bitrate", "10M")

    output_dir = Path("/var/www/html/videos/2025/12")
    output_dir.mkdir(parents=True, exist_ok=True)

    for day in DAYS:
        print(f"\n{'='*60}")
        print(f"Processing December {day}, 2025")
        print("=" * 60)

        start_dt = datetime(2025, 12, day, 0, 0, 0)
        end_dt = datetime(2025, 12, day, 23, 59, 59)

        output_file = output_dir / f"kringelen_nord_daily_2025-12-{day:02d}.mp4"

        if output_file.exists():
            print(f"  Skipping - already exists: {output_file}")
            continue

        try:
            images = find_images_in_range(
                base_dir, project_name, start_dt, end_dt, organize_by_date, date_format
            )

            if not images:
                print(f"  No images found for Dec {day}")
                continue

            print(f"  Found {len(images)} images")
            print(f"  Creating: {output_file}")

            success = create_video(
                images,
                output_file,
                fps,
                codec,
                pixel_format,
                crf,
                preset=preset,
                threads=threads,
                bitrate=bitrate,
            )

            if success:
                print(f"  ✓ Done: {output_file}")
            else:
                print(f"  ✗ Failed to create video")

        except Exception as e:
            print(f"  ✗ Error: {e}")


if __name__ == "__main__":
    main()
