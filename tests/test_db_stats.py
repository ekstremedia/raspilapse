"""Tests for db_stats.py database statistics viewer."""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from db_stats import format_duration, parse_time_arg, print_stats


class TestParseTimeArg:
    """Test time argument parsing."""

    def test_parse_minutes(self):
        """Test parsing minute values."""
        assert parse_time_arg("5m") == timedelta(minutes=5)
        assert parse_time_arg("30m") == timedelta(minutes=30)

    def test_parse_hours(self):
        """Test parsing hour values."""
        assert parse_time_arg("1h") == timedelta(hours=1)
        assert parse_time_arg("24h") == timedelta(hours=24)

    def test_parse_days(self):
        """Test parsing day values."""
        assert parse_time_arg("1d") == timedelta(days=1)
        assert parse_time_arg("7d") == timedelta(days=7)

    def test_parse_with_dash(self):
        """Test parsing with leading dash."""
        assert parse_time_arg("-5m") == timedelta(minutes=5)
        assert parse_time_arg("-1h") == timedelta(hours=1)

    def test_parse_uppercase(self):
        """Test parsing uppercase units."""
        assert parse_time_arg("5M") == timedelta(minutes=5)
        assert parse_time_arg("1H") == timedelta(hours=1)
        assert parse_time_arg("7D") == timedelta(days=7)

    def test_parse_default(self):
        """Test default value for empty/None."""
        assert parse_time_arg("") == timedelta(hours=1)
        assert parse_time_arg(None) == timedelta(hours=1)

    def test_parse_number_only(self):
        """Test parsing number without unit assumes hours."""
        assert parse_time_arg("2") == timedelta(hours=2)

    def test_parse_invalid_exits(self):
        """Test invalid format exits."""
        with pytest.raises(SystemExit):
            parse_time_arg("invalid")


class TestFormatDuration:
    """Test duration formatting."""

    def test_format_seconds(self):
        """Test formatting seconds."""
        assert format_duration(30) == "30.0s"
        assert format_duration(59.9) == "59.9s"

    def test_format_minutes(self):
        """Test formatting minutes."""
        assert format_duration(60) == "1.0m"
        assert format_duration(300) == "5.0m"
        assert format_duration(3599) == "60.0m"

    def test_format_hours(self):
        """Test formatting hours."""
        assert format_duration(3600) == "1.0h"
        assert format_duration(7200) == "2.0h"
        assert format_duration(86399) == "24.0h"

    def test_format_days(self):
        """Test formatting days."""
        assert format_duration(86400) == "1.0d"
        assert format_duration(604800) == "7.0d"


class TestPrintStats:
    """Test stats printing functionality."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database with test data."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Create schema
        cur.execute(
            """
            CREATE TABLE captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                unix_timestamp REAL NOT NULL,
                camera_id TEXT NOT NULL,
                image_path TEXT NOT NULL,
                exposure_time_us INTEGER,
                analogue_gain REAL,
                colour_gains_r REAL,
                colour_gains_b REAL,
                colour_temperature INTEGER,
                digital_gain REAL,
                sensor_temperature REAL,
                lux REAL,
                mode TEXT,
                sun_elevation REAL,
                brightness_mean REAL,
                brightness_median REAL,
                brightness_std REAL,
                brightness_p5 REAL,
                brightness_p25 REAL,
                brightness_p75 REAL,
                brightness_p95 REAL,
                underexposed_pct REAL,
                overexposed_pct REAL,
                weather_temperature REAL,
                weather_humidity INTEGER,
                weather_wind_speed REAL,
                weather_wind_gust REAL,
                weather_wind_angle INTEGER,
                weather_rain REAL,
                weather_rain_1h REAL,
                weather_rain_24h REAL,
                weather_pressure REAL,
                system_cpu_temp REAL,
                system_load_1min REAL,
                system_load_5min REAL,
                system_load_15min REAL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """
        )

        # Insert test data
        now = datetime.now()
        for i in range(10):
            ts = now - timedelta(minutes=i * 5)
            cur.execute(
                """
                INSERT INTO captures (
                    timestamp, unix_timestamp, camera_id, image_path,
                    exposure_time_us, lux, mode, brightness_mean,
                    weather_temperature, system_cpu_temp, system_load_1min
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    ts.isoformat(),
                    ts.timestamp(),
                    "test_camera",
                    f"/test/image_{i}.jpg",
                    20000000 if i > 5 else 100000,  # 20s night, 0.1s day
                    0.5 if i > 5 else 500,  # Low lux night, high day
                    "night" if i > 5 else "day",
                    60.0 + i,
                    -5.0,
                    30.0 + i,
                    0.5,
                ),
            )

        conn.commit()
        conn.close()

        yield db_path

        # Cleanup
        os.unlink(db_path)

    def test_print_stats_default(self, temp_db, capsys):
        """Test default 1 hour stats."""
        print_stats(temp_db, time_range=timedelta(hours=1))
        captured = capsys.readouterr()

        assert "Raspilapse Database Stats" in captured.out
        assert "Captures: 10" in captured.out
        assert "Averages:" in captured.out
        assert "Recent Captures" in captured.out

    def test_print_stats_short_range(self, temp_db, capsys):
        """Test short time range."""
        print_stats(temp_db, time_range=timedelta(minutes=10))
        captured = capsys.readouterr()

        assert "Last 10.0m" in captured.out
        # Should have fewer captures
        assert "Captures:" in captured.out

    def test_print_stats_limit(self, temp_db, capsys):
        """Test limit option."""
        print_stats(temp_db, limit=3)
        captured = capsys.readouterr()

        assert "Last 3 captures" in captured.out
        # Count table rows - lines that contain mode (night/day) but not "distribution"
        table_rows = [
            l
            for l in captured.out.split("\n")
            if ("night" in l or "day" in l) and "distribution" not in l and "Mode:" not in l
        ]
        assert len(table_rows) == 3

    def test_print_stats_all(self, temp_db, capsys):
        """Test all captures option."""
        print_stats(temp_db, show_all=True)
        captured = capsys.readouterr()

        assert "All time" in captured.out
        assert "Captures: 10" in captured.out

    def test_print_stats_mode_distribution(self, temp_db, capsys):
        """Test mode distribution is shown."""
        print_stats(temp_db, show_all=True)
        captured = capsys.readouterr()

        assert "Mode distribution:" in captured.out
        assert "night:" in captured.out
        assert "day:" in captured.out

    def test_print_stats_missing_db(self, capsys):
        """Test handling of missing database."""
        with pytest.raises(SystemExit):
            print_stats("/nonexistent/path/db.db")

    def test_print_stats_empty_range(self, temp_db, capsys):
        """Test empty time range."""
        # Query for future time - should be empty
        print_stats(temp_db, time_range=timedelta(seconds=1))
        captured = capsys.readouterr()

        # Should either show 0 captures or recent data depending on timing
        assert "Raspilapse Database Stats" in captured.out


class TestEmptyDatabase:
    """Test handling of empty database."""

    @pytest.fixture
    def empty_db(self):
        """Create an empty database."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE captures (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                unix_timestamp REAL,
                camera_id TEXT,
                image_path TEXT,
                exposure_time_us INTEGER,
                lux REAL,
                mode TEXT,
                brightness_mean REAL,
                weather_temperature REAL,
                system_cpu_temp REAL,
                system_load_1min REAL
            )
        """
        )
        conn.commit()
        conn.close()

        yield db_path
        os.unlink(db_path)

    def test_empty_db_no_crash(self, empty_db, capsys):
        """Test empty database doesn't crash."""
        print_stats(empty_db, show_all=True)
        captured = capsys.readouterr()

        assert "No captures found" in captured.out


class TestIntegration:
    """Integration tests for the full script."""

    def test_script_help(self):
        """Test script help output."""
        import subprocess

        result = subprocess.run(
            ["python3", "scripts/db_stats.py", "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )

        assert result.returncode == 0
        assert "View Raspilapse database statistics" in result.stdout
        assert "5m" in result.stdout
        assert "24h" in result.stdout
