#!/usr/bin/env python3
"""
Bootstrap ML v2 State from Database

This script initializes the ML v2 state file from historical database data.
It queries the database for frames with good brightness and builds the
lux-exposure lookup tables.

Usage:
    python src/bootstrap_ml_v2.py                    # Use default paths
    python src/bootstrap_ml_v2.py --db data/timelapse.db --output ml_state/ml_state_v2.json
    python src/bootstrap_ml_v2.py --brightness-min 95 --brightness-max 145  # Custom range
    python src/bootstrap_ml_v2.py --analyze          # Just analyze, don't write
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from typing import Dict, List, Tuple


# Lux bucket boundaries (same as ML v2)
LUX_BUCKETS = [0.0, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0]

# Time periods
TIME_PERIODS = {
    "night": list(range(0, 6)) + list(range(20, 24)),
    "morning_transition": list(range(6, 10)),
    "day": list(range(10, 14)),
    "evening_transition": list(range(14, 20)),
}


def get_lux_bucket(lux: float) -> int:
    """Get bucket index for a lux value."""
    for i, threshold in enumerate(LUX_BUCKETS[1:], 1):
        if lux < threshold:
            return i - 1
    return len(LUX_BUCKETS) - 1


def get_time_period(hour: int) -> str:
    """Get time period name for an hour."""
    for period, hours in TIME_PERIODS.items():
        if hour in hours:
            return period
    return "day"


def get_lux_range(bucket: int) -> str:
    """Get human-readable lux range for a bucket."""
    if bucket < len(LUX_BUCKETS) - 1:
        return f"{LUX_BUCKETS[bucket]}-{LUX_BUCKETS[bucket + 1]}"
    else:
        return f"{LUX_BUCKETS[-1]}+"


def analyze_database(db_path: str, brightness_min: float, brightness_max: float) -> Dict:
    """
    Analyze database and return statistics.

    Args:
        db_path: Path to SQLite database
        brightness_min: Minimum brightness for "good" frames
        brightness_max: Maximum brightness for "good" frames

    Returns:
        Dictionary with analysis results
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Total frame count
    cursor.execute("SELECT COUNT(*) FROM captures")
    total_frames = cursor.fetchone()[0]

    # Good frames count
    cursor.execute(
        """
        SELECT COUNT(*) FROM captures
        WHERE brightness_mean BETWEEN ? AND ?
        AND exposure_time_us > 0
        AND lux > 0
    """,
        (brightness_min, brightness_max),
    )
    good_frames = cursor.fetchone()[0]

    # Time range
    cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM captures")
    time_range = cursor.fetchone()

    # Brightness distribution
    cursor.execute(
        """
        SELECT
            CASE
                WHEN brightness_mean < 60 THEN 'very_dark'
                WHEN brightness_mean < 80 THEN 'dark'
                WHEN brightness_mean < 100 THEN 'slightly_dark'
                WHEN brightness_mean < 140 THEN 'good'
                WHEN brightness_mean < 160 THEN 'slightly_bright'
                WHEN brightness_mean < 180 THEN 'bright'
                ELSE 'very_bright'
            END as category,
            COUNT(*) as count
        FROM captures
        WHERE brightness_mean IS NOT NULL
        GROUP BY category
        ORDER BY
            CASE category
                WHEN 'very_dark' THEN 1
                WHEN 'dark' THEN 2
                WHEN 'slightly_dark' THEN 3
                WHEN 'good' THEN 4
                WHEN 'slightly_bright' THEN 5
                WHEN 'bright' THEN 6
                WHEN 'very_bright' THEN 7
            END
    """
    )
    brightness_dist = dict(cursor.fetchall())

    conn.close()

    return {
        "total_frames": total_frames,
        "good_frames": good_frames,
        "good_percentage": (good_frames / total_frames * 100) if total_frames > 0 else 0,
        "time_range": time_range,
        "brightness_distribution": brightness_dist,
    }


def bootstrap_from_database(
    db_path: str,
    output_path: str,
    brightness_min: float = 100,
    brightness_max: float = 140,
    min_samples: int = 10,
) -> Dict:
    """
    Bootstrap ML v2 state from database.

    Args:
        db_path: Path to SQLite database
        output_path: Path to output state file
        brightness_min: Minimum brightness for "good" frames
        brightness_max: Maximum brightness for "good" frames
        min_samples: Minimum samples required per bucket

    Returns:
        The generated state dictionary
    """
    print(f"Connecting to database: {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Query good frames
    cursor.execute(
        """
        SELECT
            lux,
            exposure_time_us,
            brightness_mean,
            brightness_p5,
            brightness_p95,
            strftime('%H', timestamp) as hour,
            timestamp
        FROM captures
        WHERE brightness_mean BETWEEN ? AND ?
        AND exposure_time_us > 0
        AND lux > 0
        ORDER BY timestamp DESC
        LIMIT 10000
    """,
        (brightness_min, brightness_max),
    )

    good_frames = cursor.fetchall()
    conn.close()

    print(f"Found {len(good_frames)} good frames (brightness {brightness_min}-{brightness_max})")

    if not good_frames:
        print("ERROR: No good frames found!")
        return None

    # Build lux-exposure map with time awareness
    temp_map = {}  # (bucket, period) -> list of exposures

    for lux, exp_us, bright, p5, p95, hour_str, timestamp in good_frames:
        if lux is None or exp_us is None:
            continue

        bucket = get_lux_bucket(lux)
        period = get_time_period(int(hour_str) if hour_str else 12)
        key = f"{bucket}_{period}"

        if key not in temp_map:
            temp_map[key] = []
        temp_map[key].append(exp_us)

    # Average each bucket and build final map
    lux_exposure_map = {}
    print("\nLux-Exposure Lookup Table:")
    print("-" * 70)

    for key in sorted(temp_map.keys()):
        exposures = temp_map[key]
        if len(exposures) >= min_samples:
            avg_exp = sum(exposures) / len(exposures)
            lux_exposure_map[key] = [avg_exp, len(exposures)]

            bucket, period = key.split("_", 1)
            lux_range = get_lux_range(int(bucket))
            print(
                f"  Lux {lux_range:12s} | {period:20s} | "
                f"{avg_exp/1e6:8.4f}s | {len(exposures):4d} samples"
            )
        else:
            bucket, period = key.split("_", 1)
            print(
                f"  Lux {get_lux_range(int(bucket)):12s} | {period:20s} | SKIPPED ({len(exposures)} < {min_samples} samples)"
            )

    # Build state
    state = {
        "lux_exposure_map": lux_exposure_map,
        "percentile_thresholds": {},
        "training_stats": {
            "total_good_frames": len(good_frames),
            "buckets_trained": len(lux_exposure_map),
            "brightness_range": [brightness_min, brightness_max],
            "min_samples": min_samples,
        },
        "last_trained": datetime.now().isoformat(),
        "version": 2,
    }

    # Save state
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(state, f, indent=2)

    print(f"\nState saved to: {output_path}")
    print(f"Total buckets: {len(lux_exposure_map)}")

    return state


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap ML v2 state from database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python src/bootstrap_ml_v2.py                    # Use default paths
    python src/bootstrap_ml_v2.py --analyze          # Just show statistics
    python src/bootstrap_ml_v2.py --brightness-min 95 --brightness-max 145
        """,
    )

    parser.add_argument(
        "--db",
        default="data/timelapse.db",
        help="Path to SQLite database (default: data/timelapse.db)",
    )
    parser.add_argument(
        "--output",
        default="ml_state/ml_state_v2.json",
        help="Path to output state file (default: ml_state/ml_state_v2.json)",
    )
    parser.add_argument(
        "--brightness-min",
        type=float,
        default=100,
        help="Minimum brightness for 'good' frames (default: 100)",
    )
    parser.add_argument(
        "--brightness-max",
        type=float,
        default=140,
        help="Maximum brightness for 'good' frames (default: 140)",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=10,
        help="Minimum samples required per bucket (default: 10)",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Just analyze database, don't write state file",
    )

    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"ERROR: Database not found at {args.db}")
        sys.exit(1)

    print("=" * 70)
    print("ML v2 Bootstrap Script")
    print("=" * 70)

    # Analyze database
    print("\nAnalyzing database...")
    analysis = analyze_database(args.db, args.brightness_min, args.brightness_max)

    print(f"\nDatabase Statistics:")
    print(f"  Total frames: {analysis['total_frames']}")
    print(f"  Good frames:  {analysis['good_frames']} ({analysis['good_percentage']:.1f}%)")
    print(f"  Time range:   {analysis['time_range'][0]} to {analysis['time_range'][1]}")

    print(f"\nBrightness Distribution:")
    for category, count in analysis["brightness_distribution"].items():
        pct = count / analysis["total_frames"] * 100 if analysis["total_frames"] > 0 else 0
        bar = "#" * int(pct / 2)
        print(f"  {category:15s}: {count:5d} ({pct:5.1f}%) {bar}")

    if args.analyze:
        print("\n[Analyze mode - not writing state file]")
        return

    print("\n" + "=" * 70)
    print("Building ML v2 State")
    print("=" * 70)

    bootstrap_from_database(
        args.db,
        args.output,
        args.brightness_min,
        args.brightness_max,
        args.min_samples,
    )

    print("\nDone!")


if __name__ == "__main__":
    main()
