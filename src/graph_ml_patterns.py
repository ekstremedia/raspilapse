#!/usr/bin/env python3
"""
Generate graphs visualizing daily solar/lux patterns from database.

Creates a beautiful graph showing lux patterns across different days,
helping visualize how light conditions change at different times of day
and track the polar winter recovery.

Usage:
    python src/graph_ml_patterns.py
    python src/graph_ml_patterns.py --days 14
    python src/graph_ml_patterns.py --output graphs/daily_solar_patterns.png
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import numpy as np


def get_db_path() -> str:
    """Get database path from config or default."""
    project_root = Path(__file__).parent.parent
    default_path = project_root / "data" / "timelapse.db"

    # Try to load from config
    config_path = project_root / "config" / "config.yml"
    if config_path.exists():
        try:
            import yaml

            with open(config_path) as f:
                config = yaml.safe_load(f)
            db_path = config.get("database", {}).get("path", str(default_path))
            if not os.path.isabs(db_path):
                db_path = project_root / db_path
            return str(db_path)
        except (OSError, yaml.YAMLError):
            pass  # Fall back to default path

    return str(default_path)


def fetch_daily_lux_data(db_path: str, days: int = 14) -> dict:
    """Fetch lux data from database grouped by day."""
    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}")
        return {}

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cutoff = datetime.now() - timedelta(days=days)

        cur.execute(
            """
            SELECT timestamp, lux, mode, sun_elevation
            FROM captures
            WHERE timestamp >= ? AND lux > 0
            ORDER BY timestamp ASC
            """,
            [cutoff.isoformat()],
        )

        rows = cur.fetchall()

    if not rows:
        return {}

    # Group by day
    daily_data = {}
    for row in rows:
        try:
            ts = datetime.fromisoformat(row["timestamp"])
            day_key = ts.strftime("%Y-%m-%d")

            if day_key not in daily_data:
                daily_data[day_key] = {
                    "times": [],
                    "lux": [],
                    "modes": [],
                    "sun_elevations": [],
                }

            # Time as decimal hours
            time_decimal = ts.hour + ts.minute / 60 + ts.second / 3600
            daily_data[day_key]["times"].append(time_decimal)
            daily_data[day_key]["lux"].append(row["lux"] or 0.01)
            daily_data[day_key]["modes"].append(row["mode"])
            daily_data[day_key]["sun_elevations"].append(row["sun_elevation"])
        except (ValueError, KeyError, TypeError) as e:
            # Skip malformed rows but log for debugging
            print(f"    Warning: Skipping malformed row: {e}")
            continue

    return daily_data


def create_solar_pattern_graph(db_path: str, output_path: str, days: int = 14):
    """Create a graph showing daily lux patterns from database."""
    daily_data = fetch_daily_lux_data(db_path, days)

    if not daily_data:
        print("No data found in database.")
        return False

    # Set up the figure with dark theme
    plt.style.use("dark_background")
    fig, ax1 = plt.subplots(figsize=(14, 8))

    # Sort days chronologically
    sorted_days = sorted(daily_data.keys())

    # Color map for different days - use a nice gradient
    colors = plt.cm.plasma(np.linspace(0.15, 0.85, len(sorted_days)))

    # Calculate daylight duration per day from sun_elevation data
    # Find first and last time when sun is above horizon
    daylight_durations = {}  # day_key -> duration in minutes
    for day_key in sorted_days:
        day = daily_data[day_key]
        times = day["times"]
        elevations = day["sun_elevations"]
        if not elevations:
            continue

        # Sort by time
        sorted_idx = np.argsort(times)
        times_sorted = [times[j] for j in sorted_idx]
        elev_sorted = [elevations[j] for j in sorted_idx]

        # Find sunrise (first time elevation >= 0) and sunset (last time)
        sunrise = None
        sunset = None
        for t, e in zip(times_sorted, elev_sorted):
            if e is not None and e >= 0:
                if sunrise is None:
                    sunrise = t
                sunset = t

        if sunrise is not None and sunset is not None:
            daylight_durations[day_key] = (sunset - sunrise) * 60  # in minutes

    # === Plot lux by time of day for each day ===
    for i, day_key in enumerate(sorted_days):
        day = daily_data[day_key]
        times = day["times"]
        lux = day["lux"]

        if len(times) < 5:
            continue

        # Sort by time
        sorted_indices = np.argsort(times)
        times = [times[j] for j in sorted_indices]
        lux = [lux[j] for j in sorted_indices]

        # Smooth the data for prettier lines using rolling average
        if len(lux) > 10:
            window = 5
            lux_arr = np.array(lux)
            # Simple rolling average
            kernel = np.ones(window) / window
            lux_smooth = np.convolve(lux_arr, kernel, mode="same")
            # Fix edges
            lux_smooth[: window // 2] = lux_arr[: window // 2]
            lux_smooth[-window // 2 :] = lux_arr[-window // 2 :]
        else:
            lux_smooth = lux

        # Format label with daylight change
        date_obj = datetime.strptime(day_key, "%Y-%m-%d")
        label = date_obj.strftime("%b %d")

        # Show total daylight duration for each day
        if day_key in daylight_durations:
            total_mins = daylight_durations[day_key]
            hours = int(total_mins // 60)
            mins = int(total_mins % 60)
            label = f"{label} ({hours}h{mins:02d}m)"

        ax1.plot(times, lux_smooth, color=colors[i], label=label, linewidth=1.8, alpha=0.85)

    ax1.set_xlabel("Hour of Day", fontsize=11)
    ax1.set_ylabel("Light Level (lux)", fontsize=11)
    ax1.set_xlim(0, 24)
    ax1.set_xticks(range(0, 25, 2))
    ax1.set_yscale("symlog", linthresh=0.1)
    ax1.set_ylim(0.05, 100000)
    ax1.set_yticks([0.1, 1, 10, 100, 1000, 10000, 100000])
    ax1.yaxis.set_major_formatter(
        FuncFormatter(lambda x, _: f"{int(x):,}" if x >= 1 else f"{x:.1f}")
    )
    legend = ax1.legend(
        loc="upper right",
        fontsize=7,
        ncol=2,
        framealpha=0.7,
        title="Date (Daylight)",
        title_fontsize=8,
    )
    legend.get_title().set_color("white")
    ax1.grid(True, alpha=0.3)

    # Calculate sunrise/sunset range from the daylight calculation we already did
    sunrise_times = []
    sunset_times = []
    for day_key in sorted_days:
        day = daily_data[day_key]
        times = day["times"]
        elevations = day["sun_elevations"]
        if not elevations or None in elevations:
            continue
        sorted_idx = np.argsort(times)
        times_sorted = [times[j] for j in sorted_idx]
        elev_sorted = [elevations[j] for j in sorted_idx]
        for j in range(1, len(elev_sorted)):
            if elev_sorted[j - 1] is not None and elev_sorted[j] is not None:
                if elev_sorted[j - 1] < 0 and elev_sorted[j] >= 0:
                    sunrise_times.append(times_sorted[j])
                if elev_sorted[j - 1] >= 0 and elev_sorted[j] < 0:
                    sunset_times.append(times_sorted[j])

    # Add sunrise/sunset info at top
    if sunrise_times and sunset_times:
        earliest_sunrise = min(sunrise_times)
        latest_sunrise = max(sunrise_times)
        earliest_sunset = min(sunset_times)
        latest_sunset = max(sunset_times)

        def fmt_time(decimal_hours):
            h = int(decimal_hours)
            m = int((decimal_hours - h) * 60)
            return f"{h:02d}:{m:02d}"

        sun_text = f"Sunrise: {fmt_time(earliest_sunrise)} - {fmt_time(latest_sunrise)}    Sunset: {fmt_time(earliest_sunset)} - {fmt_time(latest_sunset)}"
        ax1.text(
            0.5,
            1.02,
            sun_text,
            transform=ax1.transAxes,
            fontsize=9,
            ha="center",
            va="bottom",
            color="#ffcc66",
        )

    # Add title at top
    ax1.text(
        0.5,
        1.07,
        f"Daily Light Patterns (Last {len(sorted_days)} Days)",
        transform=ax1.transAxes,
        fontsize=13,
        fontweight="bold",
        ha="center",
        va="bottom",
        color="white",
    )

    # Add twilight zone shading (approximate for 68°N winter)
    ax1.axvspan(0, 8, alpha=0.08, color="blue")
    ax1.axvspan(8, 10, alpha=0.08, color="purple")
    ax1.axvspan(10, 14, alpha=0.08, color="yellow")
    ax1.axvspan(14, 16, alpha=0.08, color="purple")
    ax1.axvspan(16, 24, alpha=0.08, color="blue")

    # Add reference lines
    ref_levels = [
        (10000, "Full daylight", "#ffaa44"),
        (1000, "Overcast", "#ffff88"),
        (100, "Twilight", "#ff88cc"),
        (10, "Deep twilight", "#8888ff"),
        (1, "Dusk/Dawn", "#4444ff"),
    ]
    for lux_val, label, color in ref_levels:
        ax1.axhline(y=lux_val, color=color, linestyle=":", linewidth=1, alpha=0.4)
        ax1.text(24.1, lux_val, f" {label}", fontsize=7, va="center", alpha=0.6, color=color)

    # Add statistics text
    total_points = sum(len(daily_data[d]["times"]) for d in daily_data)

    stats_text = (
        f"Database Statistics:\n"
        f"  Days with data: {len(sorted_days)}\n"
        f"  Total captures: {total_points:,}\n"
        f"  Date range: {sorted_days[0]} to {sorted_days[-1]}"
    )
    ax1.text(
        0.01,
        0.99,
        stats_text,
        transform=ax1.transAxes,
        fontsize=7,
        family="monospace",
        verticalalignment="top",
        horizontalalignment="left",
        linespacing=1.3,
        bbox=dict(boxstyle="round", facecolor="black", alpha=0.7),
    )

    plt.subplots_adjust(left=0.08, right=0.92, top=0.88, bottom=0.08)
    plt.savefig(output_path, dpi=150, facecolor="black", edgecolor="none")
    plt.close()

    print(f"    Saved: {output_path}")
    return True


# Keep these for backwards compatibility with db_graphs.py imports
def load_ml_state(state_file: str) -> dict:
    """Load ML state from file (legacy compatibility)."""
    import json

    if not os.path.exists(state_file):
        return {}
    try:
        with open(state_file, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def main():
    parser = argparse.ArgumentParser(description="Generate daily solar pattern graphs")
    parser.add_argument("--db", help="Path to database file (default: from config)")
    parser.add_argument(
        "--days", "-d", type=int, default=14, help="Number of days to include (default: 14)"
    )
    parser.add_argument(
        "--output", "-o", default="graphs/daily_solar_patterns.png", help="Output path for graph"
    )

    args = parser.parse_args()

    # Get database path
    db_path = args.db or get_db_path()

    # Ensure output directory exists (guard against empty dirname)
    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # Create graph
    print("  Creating solar patterns graph from database...")
    print(f"    Database: {db_path}")
    print(f"    Days: {args.days}")
    create_solar_pattern_graph(db_path, args.output, args.days)


if __name__ == "__main__":
    main()
