#!/usr/bin/env python3
"""
Database statistics viewer for Raspilapse.

Shows capture statistics from the SQLite database in a nice table format.

Usage:
    python scripts/db_stats.py           # Last 1 hour (default)
    python scripts/db_stats.py -5m       # Last 5 minutes
    python scripts/db_stats.py -1h       # Last 1 hour
    python scripts/db_stats.py -24h      # Last 24 hours
    python scripts/db_stats.py -7d       # Last 7 days
    python scripts/db_stats.py --all     # All captures
    python scripts/db_stats.py -n 10     # Last 10 captures
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


from src.config_utils import format_duration, get_db_path  # noqa: E402
from src.config_utils import parse_time_arg_or_exit  # noqa: E402


def parse_time_arg(time_str: str) -> timedelta:
    """Parse a duration like '5m', '1h', '24h', '7d'. Defaults to 1 hour."""
    return parse_time_arg_or_exit(time_str, default=timedelta(hours=1))


def print_stats(
    db_path: str, time_range: timedelta = None, limit: int = None, show_all: bool = False
):
    """Print database statistics."""

    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Build query based on options
    if show_all:
        where_clause = ""
        params = []
        range_desc = "All time"
    elif limit:
        where_clause = ""
        params = []
        range_desc = f"Last {limit} captures"
    else:
        if time_range is None:
            time_range = timedelta(hours=1)
        cutoff = datetime.now() - time_range
        where_clause = "WHERE timestamp >= ?"
        params = [cutoff.isoformat()]
        range_desc = f"Last {format_duration(time_range.total_seconds())}"

    # Get summary stats
    if where_clause:
        cur.execute(
            f"""
            SELECT
                COUNT(*) as cnt,
                MIN(timestamp) as first,
                MAX(timestamp) as last,
                AVG(lux) as avg_lux,
                AVG(brightness_mean) as avg_brightness,
                AVG(exposure_time_us)/1000000.0 as avg_exp,
                AVG(weather_temperature) as avg_temp,
                AVG(system_cpu_temp) as avg_cpu,
                AVG(system_load_1min) as avg_load
            FROM captures {where_clause}
        """,
            params,
        )
    else:
        cur.execute(
            """
            SELECT
                COUNT(*) as cnt,
                MIN(timestamp) as first,
                MAX(timestamp) as last,
                AVG(lux) as avg_lux,
                AVG(brightness_mean) as avg_brightness,
                AVG(exposure_time_us)/1000000.0 as avg_exp,
                AVG(weather_temperature) as avg_temp,
                AVG(system_cpu_temp) as avg_cpu,
                AVG(system_load_1min) as avg_load
            FROM captures
        """
        )

    stats = cur.fetchone()

    if stats["cnt"] == 0:
        print(f"\n  No captures found for: {range_desc}")
        print(f"  Database: {db_path}\n")
        conn.close()
        return

    # Print header
    print()
    print(f"  📊 Raspilapse Database Stats - {range_desc}")
    print(f"  " + "=" * 60)
    print(f"  Database: {db_path}")
    print(f"  Captures: {stats['cnt']} | From: {stats['first'][:19]} | To: {stats['last'][:19]}")
    print()

    # Print averages
    print(f"  📈 Averages:")
    print(f"  " + "-" * 60)
    avg_lux = stats["avg_lux"] or 0
    avg_brightness = stats["avg_brightness"] or 0
    avg_exp = stats["avg_exp"] or 0
    avg_temp = stats["avg_temp"]
    avg_cpu = stats["avg_cpu"] or 0
    avg_load = stats["avg_load"] or 0

    print(f"  Lux: {avg_lux:.2f} | Brightness: {avg_brightness:.1f} | Exposure: {avg_exp:.2f}s")
    if avg_temp is not None:
        print(f"  Weather: {avg_temp:.1f}°C | CPU: {avg_cpu:.1f}°C | Load: {avg_load:.2f}")
    else:
        print(f"  CPU: {avg_cpu:.1f}°C | Load: {avg_load:.2f}")
    print()

    # Get recent captures
    if limit:
        cur.execute(
            f"""
            SELECT timestamp, mode, lux, brightness_mean,
                   exposure_time_us/1000000.0 as exp_sec,
                   weather_temperature, system_cpu_temp, system_load_1min
            FROM captures
            ORDER BY id DESC LIMIT ?
        """,
            [limit],
        )
    elif where_clause:
        cur.execute(
            f"""
            SELECT timestamp, mode, lux, brightness_mean,
                   exposure_time_us/1000000.0 as exp_sec,
                   weather_temperature, system_cpu_temp, system_load_1min
            FROM captures {where_clause}
            ORDER BY id DESC LIMIT 20
        """,
            params,
        )
    else:
        cur.execute(
            """
            SELECT timestamp, mode, lux, brightness_mean,
                   exposure_time_us/1000000.0 as exp_sec,
                   weather_temperature, system_cpu_temp, system_load_1min
            FROM captures
            ORDER BY id DESC LIMIT 20
        """
        )

    rows = cur.fetchall()

    if rows:
        print(f"  📷 Recent Captures (newest first):")
        print(f"  " + "-" * 60)
        print(
            f"  {'Time':<12} {'Mode':<11} {'Lux':>7} {'Bright':>7} {'Exp':>7} {'Temp':>6} {'CPU':>5} {'Load':>5}"
        )
        print(f"  " + "-" * 60)

        for row in rows:
            ts = row["timestamp"][11:19] if row["timestamp"] else "N/A"
            mode = row["mode"] or "N/A"
            lux = f"{row['lux']:.2f}" if row["lux"] is not None else "N/A"
            brightness = (
                f"{row['brightness_mean']:.1f}" if row["brightness_mean"] is not None else "N/A"
            )
            exp = f"{row['exp_sec']:.2f}s" if row["exp_sec"] is not None else "N/A"
            temp = (
                f"{row['weather_temperature']:.1f}°"
                if row["weather_temperature"] is not None
                else "N/A"
            )
            cpu = f"{row['system_cpu_temp']:.0f}°" if row["system_cpu_temp"] is not None else "N/A"
            load = (
                f"{row['system_load_1min']:.2f}" if row["system_load_1min"] is not None else "N/A"
            )

            print(
                f"  {ts:<12} {mode:<11} {lux:>7} {brightness:>7} {exp:>7} {temp:>6} {cpu:>5} {load:>5}"
            )

    print()

    # Mode distribution
    if where_clause:
        cur.execute(
            f"""
            SELECT mode, COUNT(*) as cnt
            FROM captures {where_clause}
            GROUP BY mode ORDER BY cnt DESC
        """,
            params,
        )
    else:
        cur.execute(
            """
            SELECT mode, COUNT(*) as cnt
            FROM captures
            GROUP BY mode ORDER BY cnt DESC
        """
        )

    modes = cur.fetchall()
    if modes:
        mode_str = " | ".join([f"{m['mode']}: {m['cnt']}" for m in modes])
        print(f"  📊 Mode distribution: {mode_str}")
        print()

    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="View Raspilapse database statistics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s              Last 1 hour (default)
  %(prog)s 5m           Last 5 minutes
  %(prog)s 1h           Last 1 hour
  %(prog)s 24h          Last 24 hours
  %(prog)s 7d           Last 7 days
  %(prog)s --all        All captures
  %(prog)s -n 10        Last 10 captures
        """,
    )

    parser.add_argument(
        "time", nargs="?", default="1h", help="Time range (e.g., 5m, 1h, 24h, 7d). Default: 1h"
    )

    parser.add_argument(
        "-n", "--limit", type=int, help="Show last N captures instead of time range"
    )

    parser.add_argument("--all", action="store_true", help="Show all captures")

    parser.add_argument("--db", help="Path to database file (default: from config)")

    args = parser.parse_args()

    db_path = args.db or get_db_path()

    if args.all:
        print_stats(db_path, show_all=True)
    elif args.limit:
        print_stats(db_path, limit=args.limit)
    else:
        time_range = parse_time_arg(args.time)
        print_stats(db_path, time_range=time_range)


if __name__ == "__main__":
    main()
