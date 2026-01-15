#!/usr/bin/env python3
"""
Database graph generator for Raspilapse.

Generates visually pleasing PNG graphs from capture history in the SQLite database.

Usage:
    python scripts/db_graphs.py           # Last 24 hours (default)
    python scripts/db_graphs.py -1h       # Last 1 hour
    python scripts/db_graphs.py -6h       # Last 6 hours
    python scripts/db_graphs.py -7d       # Last 7 days
    python scripts/db_graphs.py --all     # All captures
    python scripts/db_graphs.py -o /path  # Custom output directory
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")  # Non-interactive backend for headless systems
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import solar patterns graph generator
try:
    from src.graph_ml_patterns import create_solar_pattern_graph

    HAS_SOLAR_GRAPH = True
except ImportError:
    HAS_SOLAR_GRAPH = False

# === STYLING CONSTANTS ===
DARK_BG = "#1a1a1a"
AXES_BG = "#2d2d2d"
GRID_COLOR = "#555555"
TEXT_COLOR = "white"

# Graph dimensions
FIG_WIDTH = 14
FIG_HEIGHT = 8
DPI = 150

# Colors for different data series
COLORS = {
    "lux": "#ffaa00",
    "exposure": "#88ff88",
    "gain": "#ff8888",
    "brightness": "#88ccff",
    "temperature": "#ff6666",
    "humidity": "#66aaff",
    "wind": "#66ffaa",
    "cpu": "#ff9966",
    "load": "#cc99ff",
}

# Mode colors for zone shading
MODE_COLORS = {
    "day": ("#ffff88", 0.15),
    "night": ("#4444aa", 0.20),
    "transition": ("#ff88ff", 0.25),
}

# Smoothing window size (number of data points)
SMOOTH_WINDOW = 15


def smooth_data(data: List[float], window: int = SMOOTH_WINDOW) -> List[float]:
    """Apply Gaussian smoothing for visually pleasing curves."""
    if len(data) < window:
        return data

    # Create Gaussian kernel for smoother results than rolling average
    sigma = window / 4
    kernel_size = window if window % 2 == 1 else window + 1  # Ensure odd size
    x = np.linspace(-2, 2, kernel_size)
    kernel = np.exp(-(x**2) / 2)
    kernel = kernel / kernel.sum()

    # Pad edges and convolve
    pad_size = kernel_size // 2
    padded = np.pad(data, (pad_size, pad_size), mode="edge")
    smoothed = np.convolve(padded, kernel, mode="valid")

    # Ensure output matches input length
    return list(smoothed[: len(data)])


def get_temperature_colors(temperatures: List[float]) -> List[str]:
    """Get color for each temperature point: blue for <=0, red for >0, gradient in between."""
    colors = []
    for temp in temperatures:
        if temp <= -10:
            colors.append("#4488ff")  # Deep blue for very cold
        elif temp <= 0:
            # Gradient from deep blue to light blue
            ratio = (temp + 10) / 10  # -10 to 0 maps to 0 to 1
            r = int(0x44 + (0x88 - 0x44) * ratio)
            g = int(0x88 + (0xCC - 0x88) * ratio)
            b = 0xFF
            colors.append(f"#{r:02x}{g:02x}{b:02x}")
        elif temp <= 10:
            # Gradient from light blue/white to light red
            ratio = temp / 10  # 0 to 10 maps to 0 to 1
            r = int(0x88 + (0xFF - 0x88) * ratio)
            g = int(0xCC - (0xCC - 0x66) * ratio)
            b = int(0xFF - (0xFF - 0x66) * ratio)
            colors.append(f"#{r:02x}{g:02x}{b:02x}")
        else:
            colors.append("#ff6666")  # Red for warm
    return colors


def plot_gradient_line(ax, x_data, y_data, linewidth=2.5):
    """Plot a line with color gradient based on y-values (temperature)."""
    from matplotlib.collections import LineCollection

    # Create line segments
    points = np.array([mdates.date2num(x_data), y_data]).T.reshape(-1, 1, 2)
    segments = np.concatenate([points[:-1], points[1:]], axis=1)

    # Create color array based on temperature
    colors = []
    for i in range(len(y_data) - 1):
        avg_temp = (y_data[i] + y_data[i + 1]) / 2
        if avg_temp <= 0:
            # Blue gradient for cold
            ratio = max(0, min(1, (avg_temp + 15) / 15))  # -15 to 0
            colors.append((ratio * 0.5, ratio * 0.7 + 0.3, 1.0, 1.0))
        else:
            # Red gradient for warm
            ratio = min(1, avg_temp / 15)  # 0 to 15
            colors.append((1.0, 0.4 + (1 - ratio) * 0.4, 0.4 + (1 - ratio) * 0.4, 1.0))

    lc = LineCollection(segments, colors=colors, linewidth=linewidth)
    ax.add_collection(lc)
    ax.autoscale()


def parse_time_arg(time_str: str) -> timedelta:
    """Parse time string like '5m', '1h', '24h', '7d' into timedelta."""
    if not time_str:
        return timedelta(hours=24)  # Default

    time_str = time_str.lower().strip()

    # Handle with or without leading dash
    if time_str.startswith("-"):
        time_str = time_str[1:]

    try:
        if time_str.endswith("m"):
            return timedelta(minutes=int(time_str[:-1]))
        elif time_str.endswith("h"):
            return timedelta(hours=int(time_str[:-1]))
        elif time_str.endswith("d"):
            return timedelta(days=int(time_str[:-1]))
        else:
            # Assume hours if no unit
            return timedelta(hours=int(time_str))
    except ValueError:
        print(f"Invalid time format: {time_str}")
        print("Use format like: 5m, 1h, 24h, 7d")
        sys.exit(1)


def format_duration(seconds: float) -> str:
    """Format seconds into human readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.0f}m"
    elif seconds < 86400:
        return f"{seconds/3600:.0f}h"
    else:
        return f"{seconds/86400:.0f}d"


def get_db_path() -> str:
    """Get database path from config or default."""
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


def fetch_data(
    db_path: str, time_range: Optional[timedelta] = None, show_all: bool = False
) -> Dict:
    """Fetch capture data from database."""
    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Build query
    if show_all:
        query = "SELECT * FROM captures ORDER BY timestamp ASC"
        params = []
    else:
        if time_range is None:
            time_range = timedelta(hours=24)
        cutoff = datetime.now() - time_range
        query = "SELECT * FROM captures WHERE timestamp >= ? ORDER BY timestamp ASC"
        params = [cutoff.isoformat()]

    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return {}

    # Convert to dict of lists
    data = {
        "timestamps": [],
        "lux": [],
        "mode": [],
        "exposure_time": [],
        "analogue_gain": [],
        "brightness_mean": [],
        "brightness_p5": [],
        "brightness_p95": [],
        "underexposed_pct": [],
        "overexposed_pct": [],
        "weather_temperature": [],
        "weather_humidity": [],
        "weather_wind_speed": [],
        "weather_wind_gust": [],
        "system_cpu_temp": [],
        "system_load_1min": [],
        "sun_elevation": [],
    }

    for row in rows:
        try:
            ts = datetime.fromisoformat(row["timestamp"])
            data["timestamps"].append(ts)
            data["lux"].append(row["lux"] or 0)
            data["mode"].append(row["mode"] or "unknown")
            # Convert exposure from microseconds to seconds
            exp_us = row["exposure_time_us"] or 0
            data["exposure_time"].append(exp_us / 1_000_000)
            data["analogue_gain"].append(row["analogue_gain"] or 1.0)
            data["brightness_mean"].append(row["brightness_mean"])
            data["brightness_p5"].append(row["brightness_p5"])
            data["brightness_p95"].append(row["brightness_p95"])
            data["underexposed_pct"].append(row["underexposed_pct"])
            data["overexposed_pct"].append(row["overexposed_pct"])
            data["weather_temperature"].append(row["weather_temperature"])
            data["weather_humidity"].append(row["weather_humidity"])
            data["weather_wind_speed"].append(row["weather_wind_speed"])
            data["weather_wind_gust"].append(row["weather_wind_gust"])
            data["system_cpu_temp"].append(row["system_cpu_temp"])
            data["system_load_1min"].append(row["system_load_1min"])
            data["sun_elevation"].append(row["sun_elevation"])
        except Exception as e:
            continue

    return data


def find_mode_zones(
    timestamps: List[datetime], modes: List[str]
) -> List[Tuple[datetime, datetime, str]]:
    """Find continuous time ranges for each mode."""
    if not timestamps or not modes:
        return []

    zones = []
    current_mode = modes[0] or "unknown"
    zone_start = timestamps[0]

    for i in range(1, len(timestamps)):
        mode = modes[i] or "unknown"
        if mode != current_mode:
            zones.append((zone_start, timestamps[i - 1], current_mode))
            zone_start = timestamps[i]
            current_mode = mode

    zones.append((zone_start, timestamps[-1], current_mode))
    return zones


def add_mode_shading(ax, zones: List[Tuple], y_min: float, y_max: float):
    """Add colored background zones for day/night/transition modes."""
    for start, end, mode in zones:
        if mode in MODE_COLORS:
            color, alpha = MODE_COLORS[mode]
            ax.axvspan(start, end, alpha=alpha, color=color, zorder=0)


def setup_dark_style(fig, ax):
    """Apply dark theme styling to figure and axes."""
    fig.patch.set_facecolor(DARK_BG)
    ax.set_facecolor(AXES_BG)
    ax.tick_params(colors=TEXT_COLOR, which="both")
    ax.xaxis.label.set_color(TEXT_COLOR)
    ax.yaxis.label.set_color(TEXT_COLOR)
    ax.title.set_color(TEXT_COLOR)
    ax.grid(True, alpha=0.2, linestyle="--", color=GRID_COLOR)
    for spine in ax.spines.values():
        spine.set_edgecolor(GRID_COLOR)


def format_x_axis(ax, timestamps: List[datetime]):
    """Format x-axis for time display."""
    time_span = (timestamps[-1] - timestamps[0]).total_seconds() / 3600

    if time_span <= 6:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
    elif time_span <= 48:
        # Show hour number every hour for easy reading
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H"))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H"))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))

    plt.setp(ax.xaxis.get_majorticklabels(), rotation=0, ha="center", color=TEXT_COLOR, fontsize=9)


def create_lux_graph(data: Dict, output_dir: Path, time_desc: str):
    """Create lux levels graph with reference lines."""
    print("  Creating lux_levels.png...")
    fig, ax = plt.subplots(figsize=(FIG_WIDTH, FIG_HEIGHT))
    setup_dark_style(fig, ax)

    timestamps = data["timestamps"]
    lux = data["lux"]
    lux_smooth = smooth_data(lux)
    modes = data["mode"]

    # Add mode zone shading
    zones = find_mode_zones(timestamps, modes)
    min_lux = max(0.01, min(l for l in lux if l > 0)) if any(l > 0 for l in lux) else 0.01
    max_lux = max(lux) if lux else 100000
    add_mode_shading(ax, zones, min_lux, max_lux)

    # Plot smoothed lux line
    ax.plot(
        timestamps, lux_smooth, color=COLORS["lux"], linewidth=2.5, label="Light Level", zorder=5
    )
    ax.fill_between(timestamps, lux_smooth, alpha=0.3, color=COLORS["lux"], zorder=4)

    # Reference lines for common light levels
    reference_levels = [
        (100000, "Direct sunlight", "#ff4444"),
        (10000, "Full daylight", "#ffaa44"),
        (1000, "Overcast day", "#ffff88"),
        (100, "Sunrise/Sunset", "#ff88cc"),
        (10, "Twilight", "#6688ff"),
        (1, "Deep twilight", "#4444ff"),
        (0.1, "Full moon", "#888888"),
    ]

    for lux_val, label, color in reference_levels:
        if min_lux * 0.5 < lux_val < max_lux * 2:
            ax.axhline(y=lux_val, color=color, linestyle=":", linewidth=1.5, alpha=0.5, zorder=3)
            ax.text(
                timestamps[-1],
                lux_val,
                f"  {label}",
                fontsize=8,
                va="center",
                alpha=0.8,
                color=color,
            )

    ax.set_xlabel("Time", fontsize=12, fontweight="bold")
    ax.set_ylabel("Light Level (lux)", fontsize=12, fontweight="bold")
    ax.set_title(f"Light Levels - {time_desc}", fontsize=14, fontweight="bold", pad=15)
    ax.set_yscale("log")
    ax.set_ylim(min_lux * 0.5, max_lux * 2)

    # Use plain numbers instead of scientific notation
    from matplotlib.ticker import FuncFormatter, LogLocator

    def plain_number_formatter(x, pos):
        if x >= 1000:
            return f"{x:,.0f}"
        elif x >= 1:
            return f"{x:.0f}"
        elif x >= 0.1:
            return f"{x:.1f}"
        else:
            return f"{x:.2f}"

    ax.yaxis.set_major_formatter(FuncFormatter(plain_number_formatter))
    ax.yaxis.set_minor_formatter(FuncFormatter(plain_number_formatter))

    format_x_axis(ax, timestamps)

    # Legend
    legend = ax.legend(loc="upper right", fontsize=10, facecolor=AXES_BG, edgecolor=GRID_COLOR)
    plt.setp(legend.get_texts(), color=TEXT_COLOR)

    fig.tight_layout()
    output_path = output_dir / "lux_levels.png"
    plt.savefig(output_path, dpi=DPI, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"    Saved: {output_path}")


def create_exposure_gain_graph(data: Dict, output_dir: Path, time_desc: str):
    """Create exposure and gain dual-axis graph."""
    print("  Creating exposure_gain.png...")
    fig, ax1 = plt.subplots(figsize=(FIG_WIDTH, FIG_HEIGHT))
    setup_dark_style(fig, ax1)

    timestamps = data["timestamps"]
    exposure = [e * 1000 for e in data["exposure_time"]]  # Convert to ms
    exposure_smooth = smooth_data(exposure)
    gain = data["analogue_gain"]
    gain_smooth = smooth_data(gain)
    modes = data["mode"]

    # Add mode zone shading
    zones = find_mode_zones(timestamps, modes)
    add_mode_shading(ax1, zones, 0, max(exposure) if exposure else 1)

    # Plot smoothed exposure time
    ax1.semilogy(
        timestamps,
        exposure_smooth,
        color=COLORS["exposure"],
        linewidth=2,
        label="Exposure (ms)",
        zorder=5,
    )
    ax1.set_ylabel("Exposure Time (ms)", fontsize=12, color=COLORS["exposure"])
    ax1.tick_params(axis="y", labelcolor=COLORS["exposure"])

    # Create second y-axis for gain
    ax2 = ax1.twinx()
    ax2.plot(
        timestamps,
        gain_smooth,
        color=COLORS["gain"],
        linewidth=2,
        linestyle="--",
        label="Gain (ISO)",
        zorder=4,
    )
    ax2.set_ylabel("Analogue Gain", fontsize=12, color=COLORS["gain"])
    ax2.tick_params(axis="y", labelcolor=COLORS["gain"])

    ax1.set_xlabel("Time", fontsize=12, fontweight="bold", color=TEXT_COLOR)
    ax1.set_title(
        f"Exposure & Gain - {time_desc}", fontsize=14, fontweight="bold", pad=15, color=TEXT_COLOR
    )

    format_x_axis(ax1, timestamps)

    # Combined legend
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    legend = ax1.legend(
        lines1 + lines2,
        labels1 + labels2,
        loc="upper right",
        fontsize=10,
        facecolor=AXES_BG,
        edgecolor=GRID_COLOR,
    )
    plt.setp(legend.get_texts(), color=TEXT_COLOR)

    fig.tight_layout()
    output_path = output_dir / "exposure_gain.png"
    plt.savefig(output_path, dpi=DPI, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"    Saved: {output_path}")


def create_brightness_graph(data: Dict, output_dir: Path, time_desc: str):
    """Create brightness analysis graph."""
    print("  Creating brightness.png...")

    # Filter out None values
    valid_indices = [i for i, v in enumerate(data["brightness_mean"]) if v is not None]

    if not valid_indices:
        print("    Skipped: No brightness data available")
        return

    timestamps = [data["timestamps"][i] for i in valid_indices]
    brightness_mean = [data["brightness_mean"][i] for i in valid_indices]
    brightness_mean_smooth = smooth_data(brightness_mean)
    brightness_p5 = [data["brightness_p5"][i] or 0 for i in valid_indices]
    brightness_p5_smooth = smooth_data(brightness_p5)
    brightness_p95 = [data["brightness_p95"][i] or 255 for i in valid_indices]
    brightness_p95_smooth = smooth_data(brightness_p95)
    under_pct = [data["underexposed_pct"][i] or 0 for i in valid_indices]
    under_pct_smooth = smooth_data(under_pct)
    over_pct = [data["overexposed_pct"][i] or 0 for i in valid_indices]
    over_pct_smooth = smooth_data(over_pct)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(FIG_WIDTH, FIG_HEIGHT * 1.2))
    fig.patch.set_facecolor(DARK_BG)

    # Top: Brightness with range (smoothed)
    ax1.set_facecolor(AXES_BG)
    ax1.fill_between(
        timestamps,
        brightness_p5_smooth,
        brightness_p95_smooth,
        alpha=0.3,
        color=COLORS["brightness"],
        label="5th-95th percentile",
    )
    ax1.plot(
        timestamps,
        brightness_mean_smooth,
        color=COLORS["brightness"],
        linewidth=2,
        label="Mean brightness",
    )

    # Ideal range lines
    ax1.axhline(
        y=80, color="#66ff66", linestyle="--", linewidth=1.5, alpha=0.7, label="Ideal (80-180)"
    )
    ax1.axhline(y=180, color="#66ff66", linestyle="--", linewidth=1.5, alpha=0.7)

    ax1.set_ylabel("Brightness (0-255)", fontsize=11, color=TEXT_COLOR)
    ax1.set_title(
        f"Image Brightness - {time_desc}", fontsize=13, fontweight="bold", color=TEXT_COLOR
    )
    ax1.set_ylim(0, 255)
    ax1.tick_params(colors=TEXT_COLOR)
    ax1.grid(True, alpha=0.2, linestyle="--", color=GRID_COLOR)
    format_x_axis(ax1, timestamps)

    legend = ax1.legend(loc="upper right", fontsize=9, facecolor=AXES_BG, edgecolor=GRID_COLOR)
    plt.setp(legend.get_texts(), color=TEXT_COLOR)

    # Bottom: Under/overexposed (smoothed)
    ax2.set_facecolor(AXES_BG)
    ax2.fill_between(
        timestamps, under_pct_smooth, alpha=0.6, color="#aa66ff", label="Underexposed (<10)"
    )
    ax2.fill_between(
        timestamps, over_pct_smooth, alpha=0.6, color="#ff6666", label="Overexposed (>245)"
    )
    ax2.axhline(y=5, color="#ffaa00", linestyle="--", linewidth=1.5, alpha=0.7, label="5% warning")

    ax2.set_xlabel("Time", fontsize=11, color=TEXT_COLOR)
    ax2.set_ylabel("Percentage (%)", fontsize=11, color=TEXT_COLOR)
    ax2.set_title("Clipped Pixels", fontsize=12, fontweight="bold", color=TEXT_COLOR)
    ax2.tick_params(colors=TEXT_COLOR)
    ax2.grid(True, alpha=0.2, linestyle="--", color=GRID_COLOR)
    format_x_axis(ax2, timestamps)

    legend = ax2.legend(loc="upper right", fontsize=9, facecolor=AXES_BG, edgecolor=GRID_COLOR)
    plt.setp(legend.get_texts(), color=TEXT_COLOR)

    for spine in ax1.spines.values():
        spine.set_edgecolor(GRID_COLOR)
    for spine in ax2.spines.values():
        spine.set_edgecolor(GRID_COLOR)

    fig.tight_layout()
    output_path = output_dir / "brightness.png"
    plt.savefig(output_path, dpi=DPI, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"    Saved: {output_path}")


def create_weather_graph(data: Dict, output_dir: Path, time_desc: str):
    """Create weather conditions graph."""
    print("  Creating weather.png...")

    # Filter out None values
    valid_indices = [i for i, v in enumerate(data["weather_temperature"]) if v is not None]

    if not valid_indices:
        print("    Skipped: No weather data available")
        return

    timestamps = [data["timestamps"][i] for i in valid_indices]
    temperature = [data["weather_temperature"][i] for i in valid_indices]
    temperature_smooth = smooth_data(temperature)
    humidity = [data["weather_humidity"][i] or 0 for i in valid_indices]
    humidity_smooth = smooth_data(humidity)
    wind_speed = [data["weather_wind_speed"][i] or 0 for i in valid_indices]
    wind_speed_smooth = smooth_data(wind_speed)
    wind_gust = [data["weather_wind_gust"][i] or 0 for i in valid_indices]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(FIG_WIDTH, FIG_HEIGHT * 1.2))
    fig.patch.set_facecolor(DARK_BG)

    # Top: Temperature with gradient color (blue=cold, red=warm)
    ax1.set_facecolor(AXES_BG)

    # Plot temperature with color gradient based on value
    plot_gradient_line(ax1, timestamps, temperature_smooth, linewidth=2.5)

    # Add 0°C reference line
    ax1.axhline(y=0, color="#aaaaaa", linestyle="-", linewidth=2, alpha=0.8, label="0°C")

    ax1.set_ylabel("Temperature (°C)", fontsize=11, color=TEXT_COLOR)
    ax1.tick_params(axis="y", colors=TEXT_COLOR)
    ax1.tick_params(axis="x", colors=TEXT_COLOR)

    # Humidity on secondary axis
    ax1b = ax1.twinx()
    ax1b.plot(
        timestamps,
        humidity_smooth,
        color=COLORS["humidity"],
        linewidth=2,
        linestyle="--",
        label="Humidity",
        alpha=0.8,
    )
    ax1b.set_ylabel("Humidity (%)", fontsize=11, color=COLORS["humidity"])
    ax1b.tick_params(axis="y", labelcolor=COLORS["humidity"])
    ax1b.set_ylim(0, 100)

    ax1.set_title(
        f"Weather Conditions - {time_desc}", fontsize=13, fontweight="bold", color=TEXT_COLOR
    )
    ax1.grid(True, alpha=0.2, linestyle="--", color=GRID_COLOR)
    format_x_axis(ax1, timestamps)

    # Add legend with color explanation
    from matplotlib.lines import Line2D

    legend_elements = [
        Line2D([0], [0], color="#4488ff", linewidth=2.5, label="Temp (cold)"),
        Line2D([0], [0], color="#ff6666", linewidth=2.5, label="Temp (warm)"),
        Line2D([0], [0], color=COLORS["humidity"], linewidth=2, linestyle="--", label="Humidity"),
    ]
    legend = ax1.legend(
        handles=legend_elements,
        loc="upper right",
        fontsize=9,
        facecolor=AXES_BG,
        edgecolor=GRID_COLOR,
    )
    plt.setp(legend.get_texts(), color=TEXT_COLOR)

    # Bottom: Wind (smoothed)
    ax2.set_facecolor(AXES_BG)
    ax2.fill_between(
        timestamps, wind_speed_smooth, alpha=0.4, color=COLORS["wind"], label="Wind Speed"
    )
    ax2.plot(timestamps, wind_speed_smooth, color=COLORS["wind"], linewidth=2)
    # Scatter for gusts - only show if gust > wind_speed
    gust_ts = [timestamps[i] for i in range(len(wind_gust)) if wind_gust[i] > wind_speed[i] * 1.2]
    gust_vals = [wind_gust[i] for i in range(len(wind_gust)) if wind_gust[i] > wind_speed[i] * 1.2]
    if gust_ts:
        ax2.scatter(
            gust_ts,
            gust_vals,
            color="#ff6666",
            s=20,
            alpha=0.8,
            label="Gusts",
            zorder=5,
            marker="v",
        )

    ax2.set_xlabel("Time", fontsize=11, color=TEXT_COLOR)
    ax2.set_ylabel("Wind Speed (m/s)", fontsize=11, color=TEXT_COLOR)
    ax2.set_title("Wind", fontsize=12, fontweight="bold", color=TEXT_COLOR)
    ax2.tick_params(colors=TEXT_COLOR)
    ax2.grid(True, alpha=0.2, linestyle="--", color=GRID_COLOR)
    format_x_axis(ax2, timestamps)

    legend = ax2.legend(loc="upper right", fontsize=9, facecolor=AXES_BG, edgecolor=GRID_COLOR)
    plt.setp(legend.get_texts(), color=TEXT_COLOR)

    for spine in ax1.spines.values():
        spine.set_edgecolor(GRID_COLOR)
    for spine in ax2.spines.values():
        spine.set_edgecolor(GRID_COLOR)

    fig.tight_layout()
    output_path = output_dir / "weather.png"
    plt.savefig(output_path, dpi=DPI, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"    Saved: {output_path}")


def create_system_graph(data: Dict, output_dir: Path, time_desc: str):
    """Create system health graph."""
    print("  Creating system.png...")

    # Filter out None values
    valid_indices = [i for i, v in enumerate(data["system_cpu_temp"]) if v is not None]

    if not valid_indices:
        print("    Skipped: No system data available")
        return

    timestamps = [data["timestamps"][i] for i in valid_indices]
    cpu_temp = [data["system_cpu_temp"][i] for i in valid_indices]
    cpu_temp_smooth = smooth_data(cpu_temp)
    load_1min = [data["system_load_1min"][i] or 0 for i in valid_indices]
    load_1min_smooth = smooth_data(load_1min)

    fig, ax1 = plt.subplots(figsize=(FIG_WIDTH, FIG_HEIGHT))
    setup_dark_style(fig, ax1)

    # CPU Temperature (smoothed)
    ax1.plot(timestamps, cpu_temp_smooth, color=COLORS["cpu"], linewidth=2.5, label="CPU Temp")
    ax1.fill_between(timestamps, cpu_temp_smooth, alpha=0.2, color=COLORS["cpu"])
    ax1.axhline(
        y=70, color="#ff6666", linestyle="--", linewidth=1.5, alpha=0.7, label="Warning (70°C)"
    )
    ax1.set_ylabel("CPU Temperature (°C)", fontsize=12, color=COLORS["cpu"])
    ax1.tick_params(axis="y", labelcolor=COLORS["cpu"])

    # System Load (smoothed)
    ax2 = ax1.twinx()
    ax2.plot(
        timestamps,
        load_1min_smooth,
        color=COLORS["load"],
        linewidth=2,
        linestyle="--",
        label="Load (1min)",
    )
    ax2.set_ylabel("System Load", fontsize=12, color=COLORS["load"])
    ax2.tick_params(axis="y", labelcolor=COLORS["load"])

    ax1.set_xlabel("Time", fontsize=12, fontweight="bold", color=TEXT_COLOR)
    ax1.set_title(
        f"System Health - {time_desc}", fontsize=14, fontweight="bold", pad=15, color=TEXT_COLOR
    )

    format_x_axis(ax1, timestamps)

    # Combined legend
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    legend = ax1.legend(
        lines1 + lines2,
        labels1 + labels2,
        loc="upper right",
        fontsize=10,
        facecolor=AXES_BG,
        edgecolor=GRID_COLOR,
    )
    plt.setp(legend.get_texts(), color=TEXT_COLOR)

    fig.tight_layout()
    output_path = output_dir / "system.png"
    plt.savefig(output_path, dpi=DPI, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"    Saved: {output_path}")


def create_overview_graph(data: Dict, output_dir: Path, time_desc: str):
    """Create 2x2 overview panel."""
    print("  Creating overview.png...")

    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(FIG_WIDTH * 1.2, FIG_HEIGHT * 1.2))
    fig.patch.set_facecolor(DARK_BG)

    timestamps = data["timestamps"]

    # Helper to style each subplot consistently
    def style_subplot(ax):
        ax.set_facecolor(AXES_BG)
        ax.tick_params(colors=TEXT_COLOR, which="both")
        ax.grid(True, alpha=0.2, linestyle="--", color=GRID_COLOR)
        for spine in ax.spines.values():
            spine.set_edgecolor(GRID_COLOR)

    # Plain number formatter for log scales
    from matplotlib.ticker import FuncFormatter

    def plain_number_formatter(x, pos):
        if x >= 1000:
            return f"{x:,.0f}"
        elif x >= 1:
            return f"{x:.0f}"
        elif x >= 0.1:
            return f"{x:.1f}"
        else:
            return f"{x:.2f}"

    # 1. Lux (top-left) - smoothed, plain numbers
    style_subplot(ax1)
    lux_smooth = smooth_data(data["lux"])
    ax1.semilogy(timestamps, lux_smooth, color=COLORS["lux"], linewidth=2)
    ax1.fill_between(timestamps, lux_smooth, alpha=0.3, color=COLORS["lux"])
    ax1.set_ylabel("Lux", fontsize=10, color=TEXT_COLOR)
    ax1.set_title("Light Levels", fontsize=11, fontweight="bold", color=TEXT_COLOR)
    ax1.yaxis.set_major_formatter(FuncFormatter(plain_number_formatter))
    ax1.yaxis.set_minor_formatter(FuncFormatter(plain_number_formatter))
    format_x_axis(ax1, timestamps)

    # 2. Exposure (top-right) - smoothed, plain numbers
    style_subplot(ax2)
    exposure_ms = [e * 1000 for e in data["exposure_time"]]
    exposure_smooth = smooth_data(exposure_ms)
    ax2.semilogy(timestamps, exposure_smooth, color=COLORS["exposure"], linewidth=2)
    ax2.set_ylabel("Exposure (ms)", fontsize=10, color=TEXT_COLOR)
    ax2.set_title("Exposure Time", fontsize=11, fontweight="bold", color=TEXT_COLOR)
    ax2.yaxis.set_major_formatter(FuncFormatter(plain_number_formatter))
    ax2.yaxis.set_minor_formatter(FuncFormatter(plain_number_formatter))
    format_x_axis(ax2, timestamps)

    # 3. Brightness (bottom-left) - smoothed
    style_subplot(ax3)
    valid_brightness = [(i, v) for i, v in enumerate(data["brightness_mean"]) if v is not None]
    if valid_brightness:
        bright_ts = [timestamps[i] for i, _ in valid_brightness]
        bright_vals = [v for _, v in valid_brightness]
        bright_smooth = smooth_data(bright_vals)
        ax3.plot(bright_ts, bright_smooth, color=COLORS["brightness"], linewidth=2)
        ax3.axhline(y=80, color="#66ff66", linestyle="--", linewidth=1, alpha=0.5)
        ax3.axhline(y=180, color="#66ff66", linestyle="--", linewidth=1, alpha=0.5)
        ax3.set_ylim(0, 255)
        format_x_axis(ax3, bright_ts)
    ax3.set_ylabel("Brightness", fontsize=10, color=TEXT_COLOR)
    ax3.set_xlabel("Time", fontsize=10, color=TEXT_COLOR)
    ax3.set_title("Image Brightness", fontsize=11, fontweight="bold", color=TEXT_COLOR)

    # 4. Temperature (bottom-right) - gradient color line
    style_subplot(ax4)
    valid_temp = [(i, v) for i, v in enumerate(data["weather_temperature"]) if v is not None]
    if valid_temp:
        temp_ts = [timestamps[i] for i, _ in valid_temp]
        temp_vals = [v for _, v in valid_temp]
        temp_smooth = smooth_data(temp_vals)
        plot_gradient_line(ax4, temp_ts, temp_smooth, linewidth=2)
        ax4.axhline(y=0, color="#aaaaaa", linestyle="-", linewidth=1.5, alpha=0.7)
        format_x_axis(ax4, temp_ts)
    ax4.set_ylabel("Temp (°C)", fontsize=10, color=TEXT_COLOR)
    ax4.set_xlabel("Time", fontsize=10, color=TEXT_COLOR)
    ax4.set_title("Temperature", fontsize=11, fontweight="bold", color=TEXT_COLOR)

    fig.suptitle(f"Overview - {time_desc}", fontsize=14, fontweight="bold", color=TEXT_COLOR)
    fig.tight_layout()

    output_path = output_dir / "overview.png"
    plt.savefig(output_path, dpi=DPI, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"    Saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate graphs from Raspilapse database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s              Last 24 hours (default)
  %(prog)s 6h           Last 6 hours
  %(prog)s 24h          Last 24 hours
  %(prog)s 7d           Last 7 days
  %(prog)s --all        All captures
  %(prog)s -o /path     Custom output directory
        """,
    )

    parser.add_argument(
        "time", nargs="?", default="24h", help="Time range (e.g., 1h, 6h, 24h, 7d). Default: 24h"
    )

    parser.add_argument("--all", action="store_true", help="Generate graphs for all captures")

    parser.add_argument("--db", help="Path to database file (default: from config)")

    parser.add_argument(
        "-o", "--output", default="graphs", help="Output directory for graphs (default: graphs)"
    )

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  RASPILAPSE DATABASE GRAPHS")
    print("=" * 60)

    # Get database path
    db_path = args.db or get_db_path()

    # Parse time range
    if args.all:
        time_range = None
        time_desc = "All Time"
    else:
        time_range = parse_time_arg(args.time)
        time_desc = f"Last {format_duration(time_range.total_seconds())}"

    print(f"\n  Database: {db_path}")
    print(f"  Time range: {time_desc}")

    # Fetch data
    print(f"\n  Fetching data from database...")
    data = fetch_data(db_path, time_range, args.all)

    if not data or not data.get("timestamps"):
        print(f"\n  No data found for: {time_desc}")
        print(f"  Check that captures exist in the database.\n")
        sys.exit(1)

    print(f"  Found {len(data['timestamps'])} data points")
    print(f"  From: {data['timestamps'][0].strftime('%Y-%m-%d %H:%M')}")
    print(f"  To:   {data['timestamps'][-1].strftime('%Y-%m-%d %H:%M')}")

    # Create output directory
    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n  Generating graphs...")

    # Create all graphs
    create_lux_graph(data, output_dir, time_desc)
    create_exposure_gain_graph(data, output_dir, time_desc)
    create_brightness_graph(data, output_dir, time_desc)
    create_weather_graph(data, output_dir, time_desc)
    create_system_graph(data, output_dir, time_desc)
    create_overview_graph(data, output_dir, time_desc)

    # Generate daily solar patterns graph from database
    if HAS_SOLAR_GRAPH:
        print("  Creating daily_solar_patterns.png...")
        try:
            solar_output = output_dir / "daily_solar_patterns.png"
            create_solar_pattern_graph(db_path, str(solar_output), days=14)
        except Exception as e:
            print(f"    Skipped: Solar patterns graph failed - {e}")

    print(f"\n  All graphs saved to: {output_dir}")
    print()


if __name__ == "__main__":
    main()
