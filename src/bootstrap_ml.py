#!/usr/bin/env python3
"""
Bootstrap ML State from Historical Metadata

This script initializes the ML exposure predictor by learning from
existing timelapse metadata files. Run this once before enabling
ML predictions to give the system a head start.

Usage:
    python src/bootstrap_ml.py --days 7
    python src/bootstrap_ml.py --start 2026-01-02 --end 2026-01-09
"""

import argparse
import glob
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import yaml

# Add src directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ml_exposure import MLExposurePredictor

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_config(config_path: str = "config/config.yml") -> Dict:
    """Load configuration file."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def find_metadata_files(base_dir: str, start_date: datetime, end_date: datetime) -> List[str]:
    """
    Find all metadata files within the date range.

    Args:
        base_dir: Base directory for images (e.g., /var/www/html/images)
        start_date: Start of date range
        end_date: End of date range

    Returns:
        List of metadata file paths
    """
    metadata_files = []
    current = start_date

    while current <= end_date:
        # Build path for this date
        date_dir = os.path.join(
            base_dir,
            str(current.year),
            f"{current.month:02d}",
            f"{current.day:02d}",
        )

        if os.path.exists(date_dir):
            # Find all metadata files in this directory
            pattern = os.path.join(date_dir, "*_metadata.json")
            files = glob.glob(pattern)
            metadata_files.extend(files)
            logger.info(f"Found {len(files)} metadata files in {date_dir}")

        current += timedelta(days=1)

    return sorted(metadata_files)


def process_metadata_file(filepath: str) -> Optional[Dict]:
    """
    Load and validate a metadata file.

    Handles both raw camera metadata format and enriched format with diagnostics.

    Returns:
        Metadata dict if valid, None otherwise
    """
    try:
        with open(filepath, "r") as f:
            metadata = json.load(f)

        # Check for enriched format first (has diagnostics)
        diagnostics = metadata.get("diagnostics", {})
        brightness_info = diagnostics.get("brightness", {})

        lux = diagnostics.get("smoothed_lux") or diagnostics.get("raw_lux")
        brightness = brightness_info.get("mean_brightness")

        # Fall back to raw camera metadata format
        if lux is None:
            lux = metadata.get("Lux")

        exposure = metadata.get("ExposureTime")
        timestamp = metadata.get("capture_timestamp")

        # Lux and exposure are required
        if lux is None or exposure is None:
            return None

        # Normalize the metadata structure for consistent processing
        # Add a synthetic diagnostics section if using raw format
        if "diagnostics" not in metadata:
            metadata["diagnostics"] = {
                "raw_lux": lux,
                "smoothed_lux": lux,  # Use same value since we don't have history
                # brightness will be None, which is handled gracefully
            }

        return metadata

    except (json.JSONDecodeError, IOError) as e:
        logger.debug(f"Error reading {filepath}: {e}")
        return None


def bootstrap_ml(
    config: Dict,
    base_dir: str,
    start_date: datetime,
    end_date: datetime,
    output_dir: str = "ml_state",
) -> Dict:
    """
    Bootstrap ML state from historical metadata.

    Args:
        config: Configuration dict
        base_dir: Base directory for images
        start_date: Start of date range
        end_date: End of date range
        output_dir: Directory to save ML state

    Returns:
        Statistics about the bootstrap process
    """
    # Get ML config (with defaults if not present)
    ml_config = config.get("ml_exposure", {})
    ml_config.setdefault("state_file", "ml_state.json")
    ml_config.setdefault("solar_learning_rate", 0.1)
    ml_config.setdefault("exposure_learning_rate", 0.05)
    ml_config.setdefault("correction_learning_rate", 0.1)

    # Create predictor
    predictor = MLExposurePredictor(ml_config, state_dir=output_dir)

    # Find metadata files
    logger.info(f"Searching for metadata files from {start_date.date()} to {end_date.date()}")
    metadata_files = find_metadata_files(base_dir, start_date, end_date)
    logger.info(f"Found {len(metadata_files)} total metadata files")

    if not metadata_files:
        logger.warning("No metadata files found!")
        return {"files_found": 0, "files_processed": 0}

    # Process files
    processed = 0
    good_brightness = 0
    errors = 0

    for i, filepath in enumerate(metadata_files):
        if (i + 1) % 500 == 0:
            logger.info(f"Processing file {i + 1}/{len(metadata_files)}...")

        metadata = process_metadata_file(filepath)
        if metadata is None:
            errors += 1
            continue

        # Learn from this frame
        predictor.learn_from_frame(metadata)
        processed += 1

        # Track good brightness frames
        brightness = metadata.get("diagnostics", {}).get("brightness", {}).get("mean_brightness", 0)
        if 100 <= brightness <= 140:
            good_brightness += 1

    # Save final state
    predictor.save_state()

    # Get statistics
    stats = predictor.get_statistics()
    stats.update(
        {
            "files_found": len(metadata_files),
            "files_processed": processed,
            "files_with_errors": errors,
            "good_brightness_frames": good_brightness,
            "date_range": f"{start_date.date()} to {end_date.date()}",
        }
    )

    return stats


def print_learned_table(output_dir: str = "ml_state"):
    """Print the learned lux-exposure mapping table."""
    state_file = os.path.join(output_dir, "ml_state.json")
    if not os.path.exists(state_file):
        logger.error(f"State file not found: {state_file}")
        return

    with open(state_file, "r") as f:
        state = json.load(f)

    print("\n" + "=" * 60)
    print("LEARNED LUX-EXPOSURE MAPPING")
    print("=" * 60)

    lux_buckets = MLExposurePredictor.LUX_BUCKETS

    for bucket_key, (exposure, count) in sorted(
        state.get("lux_exposure_map", {}).items(), key=lambda x: int(x[0])
    ):
        bucket_idx = int(bucket_key)
        if bucket_idx < len(lux_buckets) - 1:
            lux_min = lux_buckets[bucket_idx]
            lux_max = lux_buckets[bucket_idx + 1]
        else:
            lux_min = lux_buckets[-1]
            lux_max = "+"

        print(
            f"  Lux {lux_min:>6.1f} - {str(lux_max):>6}:  {exposure:>8.3f}s  ({count:>4} samples)"
        )

    print()

    # Print solar pattern summary
    solar = state.get("solar_patterns", {})
    print("=" * 60)
    print("SOLAR PATTERNS LEARNED")
    print("=" * 60)
    print(f"  Days with data: {len(solar)}")

    # Show sample for most recent day
    if solar:
        latest_day = max(solar.keys(), key=int)
        hours = solar[latest_day]
        print(f"\n  Sample (day {latest_day}):")
        for hour in sorted(hours.keys(), key=int)[:6]:  # First 6 hours
            minute_data = hours[hour]
            if minute_data:
                avg_lux = sum(minute_data.values()) / len(minute_data)
                print(f"    Hour {int(hour):02d}: avg lux {avg_lux:.2f}")


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap ML state from historical timelapse metadata"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to process (default: 7)",
    )
    parser.add_argument(
        "--start",
        type=str,
        help="Start date (YYYY-MM-DD), overrides --days",
    )
    parser.add_argument(
        "--end",
        type=str,
        help="End date (YYYY-MM-DD), defaults to today",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default="config/config.yml",
        help="Path to config file",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="ml_state",
        help="Output directory for ML state",
    )
    parser.add_argument(
        "--show-table",
        action="store_true",
        help="Only show the learned table, don't bootstrap",
    )

    args = parser.parse_args()

    # Just show table?
    if args.show_table:
        print_learned_table(args.output)
        return

    # Load config
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        logger.error(f"Config file not found: {args.config}")
        sys.exit(1)

    # Determine date range
    if args.start:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")
    else:
        start_date = datetime.now() - timedelta(days=args.days)

    if args.end:
        end_date = datetime.strptime(args.end, "%Y-%m-%d")
    else:
        end_date = datetime.now()

    # Get base directory from config
    system_config = config.get("system", {})
    output_dir = system_config.get("output_directory", "/var/www/html/images")

    logger.info("=" * 60)
    logger.info("ML BOOTSTRAP")
    logger.info("=" * 60)
    logger.info(f"Date range: {start_date.date()} to {end_date.date()}")
    logger.info(f"Image directory: {output_dir}")
    logger.info(f"Output directory: {args.output}")
    logger.info("=" * 60)

    # Run bootstrap
    stats = bootstrap_ml(config, output_dir, start_date, end_date, args.output)

    # Print results
    logger.info("")
    logger.info("=" * 60)
    logger.info("BOOTSTRAP COMPLETE")
    logger.info("=" * 60)
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")
    logger.info("=" * 60)

    # Print learned table
    print_learned_table(args.output)


if __name__ == "__main__":
    main()
