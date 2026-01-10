#!/usr/bin/env python3
"""
Generate graphs visualizing ML-learned solar patterns.

Creates a graph showing the learned lux patterns across different days,
helping visualize how the ML system understands light conditions at
different times of day.

Usage:
    python src/graph_ml_patterns.py
    python src/graph_ml_patterns.py --output graphs/ml_solar_patterns.png
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np


def load_ml_state(state_file: str = "ml_state/ml_state.json") -> dict:
    """Load ML state from file."""
    if not os.path.exists(state_file):
        print(f"Error: ML state file not found: {state_file}")
        sys.exit(1)

    with open(state_file, "r") as f:
        return json.load(f)


def day_of_year_to_date(day_of_year: int, year: int = 2026) -> datetime:
    """Convert day of year to datetime."""
    return datetime(year, 1, 1) + timedelta(days=day_of_year - 1)


def create_solar_pattern_graph(state: dict, output_path: str):
    """Create a graph showing learned solar patterns."""
    solar_patterns = state.get("solar_patterns", {})

    if not solar_patterns:
        print("No solar patterns learned yet.")
        return

    # Set up the figure with dark theme
    plt.style.use("dark_background")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))

    # Color map for different days
    days = sorted(solar_patterns.keys(), key=int)
    colors = plt.cm.viridis(np.linspace(0.2, 0.9, len(days)))

    # === Top plot: Lux by time of day for each day ===
    for i, day_key in enumerate(days):
        day_data = solar_patterns[day_key]
        times = []
        lux_values = []

        for hour_key in sorted(day_data.keys(), key=int):
            hour_data = day_data[hour_key]
            for minute_key in sorted(hour_data.keys(), key=int):
                # Create time as hours + fraction
                time_decimal = int(hour_key) + int(minute_key) / 60
                times.append(time_decimal)
                lux_values.append(hour_data[minute_key])

        if times:
            date = day_of_year_to_date(int(day_key))
            label = date.strftime("%b %d")
            ax1.plot(times, lux_values, color=colors[i], label=label, linewidth=1.5, alpha=0.8)

    ax1.set_xlabel("Hour of Day", fontsize=11)
    ax1.set_ylabel("Learned Lux Level", fontsize=11)
    ax1.set_title("ML Solar Patterns: Learned Lux by Time of Day", fontsize=13, fontweight="bold")
    ax1.set_xlim(0, 24)
    ax1.set_xticks(range(0, 25, 2))
    ax1.set_yscale("symlog", linthresh=1)  # Log scale but handles 0
    ax1.legend(loc="upper right", fontsize=9, ncol=2)
    ax1.grid(True, alpha=0.3)

    # Add twilight zones (approximate for 68.7°N in January)
    ax1.axvspan(0, 8, alpha=0.1, color="blue", label="Night")
    ax1.axvspan(8, 10, alpha=0.1, color="orange", label="Dawn")
    ax1.axvspan(10, 14, alpha=0.1, color="yellow", label="Day")
    ax1.axvspan(14, 16, alpha=0.1, color="orange", label="Dusk")
    ax1.axvspan(16, 24, alpha=0.1, color="blue", label="Night")

    # === Bottom plot: Day-over-day comparison at noon ===
    noon_lux = []
    day_dates = []

    for day_key in days:
        day_data = solar_patterns[day_key]
        # Get lux around midday (hours 10-14)
        midday_values = []
        for hour in ["10", "11", "12", "13", "14"]:
            if hour in day_data:
                for minute_key, lux in day_data[hour].items():
                    midday_values.append(lux)

        if midday_values:
            noon_lux.append(np.mean(midday_values))
            day_dates.append(day_of_year_to_date(int(day_key)))

    if noon_lux:
        ax2.bar(day_dates, noon_lux, color="gold", alpha=0.8, width=0.8)
        ax2.set_xlabel("Date", fontsize=11)
        ax2.set_ylabel("Average Midday Lux (10:00-14:00)", fontsize=11)
        ax2.set_title(
            "Daily Midday Light Levels (Polar Winter Recovery)", fontsize=13, fontweight="bold"
        )
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        ax2.xaxis.set_major_locator(mdates.DayLocator())
        plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right")
        ax2.grid(True, alpha=0.3, axis="y")

        # Add trend line
        if len(noon_lux) > 2:
            x_numeric = np.arange(len(noon_lux))
            z = np.polyfit(x_numeric, noon_lux, 1)
            p = np.poly1d(z)
            ax2.plot(
                day_dates, p(x_numeric), "r--", linewidth=2, label=f"Trend: +{z[0]:.1f} lux/day"
            )
            ax2.legend(loc="upper left", fontsize=10)

    # Add statistics text
    stats = state.get("confidence", 0)
    total = state.get("total_predictions", 0)
    trust = min(0.8, stats * 0.001)  # Approximate trust calculation

    stats_text = (
        f"ML Statistics:\n"
        f"  Frames processed: {total:,}\n"
        f"  Days learned: {len(days)}\n"
        f"  Trust level: {trust:.1%}"
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

    print(f"Graph saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate ML solar pattern graphs")
    parser.add_argument(
        "--state", "-s", default="ml_state/ml_state.json", help="Path to ML state file"
    )
    parser.add_argument(
        "--output", "-o", default="graphs/ml_solar_patterns.png", help="Output path for graph"
    )

    args = parser.parse_args()

    # Ensure output directory exists
    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    # Load state and create graph
    state = load_ml_state(args.state)
    create_solar_pattern_graph(state, args.output)


if __name__ == "__main__":
    main()
