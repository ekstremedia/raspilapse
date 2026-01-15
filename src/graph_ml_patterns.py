#!/usr/bin/env python3
"""
Generate graphs visualizing daily solar/lux patterns from database.

Creates a beautiful graph showing lux patterns across different days,
helping visualize how light conditions change at different times of day
and track the polar winter recovery.

Usage:
    python src/graph_ml_patterns.py
    python src/graph_ml_patterns.py --days 14
    python src/graph_ml_patterns.py --output graphs/ml_solar_patterns.png
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
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
        except Exception:
            pass

    return str(default_path)


def fetch_daily_lux_data(db_path: str, days: int = 14) -> dict:
    """Fetch lux data from database grouped by day."""
    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}")
        return {}

    conn = sqlite3.connect(db_path)
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
    conn.close()

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
        except Exception:
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
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))

    # Sort days chronologically
    sorted_days = sorted(daily_data.keys())

    # Color map for different days - use a nice gradient
    colors = plt.cm.plasma(np.linspace(0.15, 0.85, len(sorted_days)))

    # === Top plot: Lux by time of day for each day ===
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

        # Format label as "Jan 15"
        date_obj = datetime.strptime(day_key, "%Y-%m-%d")
        label = date_obj.strftime("%b %d")

        ax1.plot(times, lux_smooth, color=colors[i], label=label, linewidth=1.8, alpha=0.85)

    ax1.set_xlabel("Hour of Day", fontsize=11)
    ax1.set_ylabel("Light Level (lux)", fontsize=11)
    ax1.set_title(
        f"Daily Light Patterns (Last {len(sorted_days)} Days)", fontsize=13, fontweight="bold"
    )
    ax1.set_xlim(0, 24)
    ax1.set_xticks(range(0, 25, 2))
    ax1.set_yscale("symlog", linthresh=1)
    ax1.legend(loc="upper right", fontsize=8, ncol=3, framealpha=0.7)
    ax1.grid(True, alpha=0.3)

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

    # === Bottom plot: Daily midday light levels with trend ===
    midday_lux = []
    day_dates = []

    for day_key in sorted_days:
        day = daily_data[day_key]
        times = day["times"]
        lux = day["lux"]

        # Get lux values around midday (10:00-14:00)
        midday_values = [lux[j] for j in range(len(times)) if 10 <= times[j] <= 14]

        if midday_values:
            midday_lux.append(np.mean(midday_values))
            day_dates.append(datetime.strptime(day_key, "%Y-%m-%d"))

    if midday_lux and len(midday_lux) > 1:
        # Create bar chart
        bar_colors = plt.cm.plasma(np.linspace(0.15, 0.85, len(midday_lux)))
        bars = ax2.bar(day_dates, midday_lux, color=bar_colors, alpha=0.8, width=0.8)

        ax2.set_xlabel("Date", fontsize=11)
        ax2.set_ylabel("Average Midday Lux (10:00-14:00)", fontsize=11)
        ax2.set_title(
            "Daily Midday Light Levels (Polar Winter Recovery)", fontsize=13, fontweight="bold"
        )
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        ax2.xaxis.set_major_locator(mdates.DayLocator())
        plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right")
        ax2.grid(True, alpha=0.3, axis="y")
        ax2.set_yscale("symlog", linthresh=1)

        # Add trend line
        if len(midday_lux) > 2:
            x_numeric = np.arange(len(midday_lux))
            z = np.polyfit(x_numeric, np.log10(np.array(midday_lux) + 1), 1)

            # Calculate daily change
            first_val = midday_lux[0]
            last_val = midday_lux[-1]
            days_span = len(midday_lux)
            if first_val > 0:
                pct_change = ((last_val / first_val) ** (1 / days_span) - 1) * 100
                trend_label = f"Trend: {pct_change:+.1f}%/day"
            else:
                trend_label = "Trend"

            # Fit exponential trend
            p = np.poly1d(z)
            trend_y = 10 ** p(x_numeric) - 1
            ax2.plot(day_dates, trend_y, "r--", linewidth=2.5, label=trend_label, alpha=0.9)
            ax2.legend(loc="upper left", fontsize=10)

    # Add statistics text
    total_points = sum(len(daily_data[d]["times"]) for d in daily_data)

    stats_text = (
        f"Database Statistics:\n"
        f"  Days with data: {len(sorted_days)}\n"
        f"  Total captures: {total_points:,}\n"
        f"  Date range: {sorted_days[0]} to {sorted_days[-1]}"
    )
    fig.text(
        0.02,
        0.02,
        stats_text,
        fontsize=9,
        family="monospace",
        verticalalignment="bottom",
        bbox=dict(boxstyle="round", facecolor="black", alpha=0.7),
    )

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="black", edgecolor="none")
    plt.close()

    print(f"    Saved: {output_path}")
    return True


# Keep these for backwards compatibility with db_graphs.py imports
def load_ml_state(state_file: str) -> dict:
    """Load ML state from file (legacy compatibility)."""
    import json

    if not os.path.exists(state_file):
        return {}
    with open(state_file, "r") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Generate daily solar pattern graphs")
    parser.add_argument("--db", help="Path to database file (default: from config)")
    parser.add_argument(
        "--days", "-d", type=int, default=14, help="Number of days to include (default: 14)"
    )
    parser.add_argument(
        "--output", "-o", default="graphs/ml_solar_patterns.png", help="Output path for graph"
    )

    args = parser.parse_args()

    # Get database path
    db_path = args.db or get_db_path()

    # Ensure output directory exists
    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    # Create graph
    print(f"  Creating solar patterns graph from database...")
    print(f"    Database: {db_path}")
    print(f"    Days: {args.days}")
    create_solar_pattern_graph(db_path, args.output, args.days)


if __name__ == "__main__":
    main()
