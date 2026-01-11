"""Tests for database graph generator module."""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import numpy as np

# Add scripts to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from db_graphs import (
    parse_time_arg,
    format_duration,
    smooth_data,
    get_temperature_colors,
    find_mode_zones,
    fetch_data,
)


class TestParseTimeArg:
    """Test suite for parse_time_arg function."""

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
        assert parse_time_arg("-1h") == timedelta(hours=1)
        assert parse_time_arg("-24h") == timedelta(hours=24)

    def test_parse_default(self):
        """Test default value for empty string."""
        assert parse_time_arg("") == timedelta(hours=24)

    def test_parse_no_unit(self):
        """Test parsing without unit assumes hours."""
        assert parse_time_arg("6") == timedelta(hours=6)


class TestFormatDuration:
    """Test suite for format_duration function."""

    def test_format_seconds(self):
        """Test formatting seconds."""
        assert format_duration(30) == "30s"
        assert format_duration(59) == "59s"

    def test_format_minutes(self):
        """Test formatting minutes."""
        assert format_duration(60) == "1m"
        assert format_duration(120) == "2m"
        assert format_duration(3540) == "59m"

    def test_format_hours(self):
        """Test formatting hours."""
        assert format_duration(3600) == "1h"
        assert format_duration(7200) == "2h"
        assert format_duration(82800) == "23h"

    def test_format_days(self):
        """Test formatting days."""
        assert format_duration(86400) == "1d"
        assert format_duration(172800) == "2d"


class TestSmoothData:
    """Test suite for smooth_data function."""

    def test_smooth_basic(self):
        """Test basic smoothing."""
        data = [
            1.0,
            2.0,
            3.0,
            4.0,
            5.0,
            6.0,
            7.0,
            8.0,
            9.0,
            10.0,
            11.0,
            12.0,
            13.0,
            14.0,
            15.0,
            16.0,
            17.0,
            18.0,
            19.0,
            20.0,
        ]
        smoothed = smooth_data(data)

        # Output length should match input length
        assert len(smoothed) == len(data)

        # Smoothed data should be close to original for linear data
        assert abs(smoothed[10] - data[10]) < 1.0

    def test_smooth_short_data(self):
        """Test smoothing with data shorter than window."""
        data = [1.0, 2.0, 3.0]
        smoothed = smooth_data(data)

        # Should return original data if too short
        assert smoothed == data

    def test_smooth_reduces_noise(self):
        """Test that smoothing reduces noise."""
        # Create noisy data
        np.random.seed(42)
        base = np.linspace(0, 10, 50)
        noisy = base + np.random.normal(0, 1, 50)

        smoothed = smooth_data(list(noisy))

        # Calculate variance of residuals from trend
        noisy_residuals = noisy - base
        smoothed_residuals = np.array(smoothed) - base

        # Smoothed should have lower variance
        assert np.var(smoothed_residuals) < np.var(noisy_residuals)

    def test_smooth_preserves_trend(self):
        """Test that smoothing preserves overall trend."""
        data = list(range(30))  # Linear increasing
        smoothed = smooth_data(data)

        # First should be less than last
        assert smoothed[0] < smoothed[-1]


class TestGetTemperatureColors:
    """Test suite for get_temperature_colors function."""

    def test_cold_temperatures(self):
        """Test colors for cold temperatures."""
        colors = get_temperature_colors([-15.0, -10.0, -5.0])

        # All should be blue-ish (start with #4 or #5 or #6)
        for color in colors:
            assert color.startswith("#")
            # Blue component should be 'ff'
            assert color.endswith("ff") or "ff" in color[5:]

    def test_warm_temperatures(self):
        """Test colors for warm temperatures."""
        colors = get_temperature_colors([15.0, 20.0, 25.0])

        # All should be red (#ff6666)
        for color in colors:
            assert color == "#ff6666"

    def test_zero_crossing(self):
        """Test color transition around zero."""
        colors = get_temperature_colors([-5.0, 0.0, 5.0])

        # Should have 3 different colors (transition)
        assert len(colors) == 3
        # First should be more blue, last more red
        assert colors[0] != colors[2]


class TestFindModeZones:
    """Test suite for find_mode_zones function."""

    def test_single_mode(self):
        """Test with single mode throughout."""
        timestamps = [
            datetime(2026, 1, 1, 10, 0),
            datetime(2026, 1, 1, 11, 0),
            datetime(2026, 1, 1, 12, 0),
        ]
        modes = ["day", "day", "day"]

        zones = find_mode_zones(timestamps, modes)

        assert len(zones) == 1
        assert zones[0][2] == "day"

    def test_mode_transitions(self):
        """Test with mode transitions."""
        timestamps = [
            datetime(2026, 1, 1, 6, 0),
            datetime(2026, 1, 1, 7, 0),
            datetime(2026, 1, 1, 8, 0),
            datetime(2026, 1, 1, 12, 0),
            datetime(2026, 1, 1, 18, 0),
            datetime(2026, 1, 1, 19, 0),
        ]
        modes = ["night", "transition", "transition", "day", "transition", "night"]

        zones = find_mode_zones(timestamps, modes)

        # Should have multiple zones
        assert len(zones) >= 3

    def test_empty_data(self):
        """Test with empty data."""
        zones = find_mode_zones([], [])
        assert zones == []


class TestFetchData:
    """Test suite for fetch_data function."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database with test data."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Create table
        cur.execute(
            """
            CREATE TABLE captures (
                id INTEGER PRIMARY KEY,
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
                created_at TEXT
            )
        """
        )

        # Insert test data
        now = datetime.now()
        for i in range(10):
            ts = now - timedelta(hours=i)
            cur.execute(
                """
                INSERT INTO captures (
                    timestamp, unix_timestamp, camera_id, image_path,
                    exposure_time_us, analogue_gain, lux, mode,
                    brightness_mean, brightness_p5, brightness_p95,
                    underexposed_pct, overexposed_pct,
                    weather_temperature, weather_humidity,
                    weather_wind_speed, weather_wind_gust,
                    system_cpu_temp, system_load_1min, sun_elevation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    ts.isoformat(),
                    ts.timestamp(),
                    "test_camera",
                    f"/path/to/image_{i}.jpg",
                    10000 + i * 1000,
                    1.0 + i * 0.1,
                    100 + i * 10,
                    "day" if i < 5 else "night",
                    120 + i,
                    50 + i,
                    200 + i,
                    0.1,
                    0.2,
                    -5 + i,
                    70 + i,
                    3.0 + i * 0.5,
                    5.0 + i * 0.5,
                    40 + i,
                    0.5 + i * 0.1,
                    10 - i * 2,
                ),
            )

        conn.commit()
        conn.close()

        yield db_path

        # Cleanup
        os.unlink(db_path)

    def test_fetch_data_basic(self, temp_db):
        """Test basic data fetching."""
        data = fetch_data(temp_db, timedelta(hours=24))

        assert len(data["timestamps"]) == 10
        assert len(data["lux"]) == 10
        assert len(data["mode"]) == 10

    def test_fetch_data_time_filter(self, temp_db):
        """Test data fetching with time filter."""
        data = fetch_data(temp_db, timedelta(hours=3))

        # Should have fewer records
        assert len(data["timestamps"]) < 10
        assert len(data["timestamps"]) >= 3

    def test_fetch_data_all(self, temp_db):
        """Test fetching all data."""
        data = fetch_data(temp_db, show_all=True)

        assert len(data["timestamps"]) == 10

    def test_fetch_nonexistent_db(self):
        """Test fetching from non-existent database."""
        with pytest.raises(SystemExit):
            fetch_data("/nonexistent/path.db")


class TestIntegration:
    """Integration tests for graph generation."""

    @pytest.fixture
    def temp_db_with_data(self):
        """Create a temporary database with realistic test data."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Create table
        cur.execute(
            """
            CREATE TABLE captures (
                id INTEGER PRIMARY KEY,
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
                created_at TEXT
            )
        """
        )

        # Insert 50 records simulating a day
        now = datetime.now()
        for i in range(50):
            ts = now - timedelta(minutes=i * 30)
            hour = ts.hour

            # Simulate day/night cycle
            if 6 <= hour <= 18:
                mode = "day"
                lux = 1000 + (hour - 6) * 500
                exposure = 10000
            else:
                mode = "night"
                lux = 0.5 + hour * 0.1
                exposure = 20000000

            cur.execute(
                """
                INSERT INTO captures (
                    timestamp, unix_timestamp, camera_id, image_path,
                    exposure_time_us, analogue_gain, lux, mode,
                    brightness_mean, brightness_p5, brightness_p95,
                    underexposed_pct, overexposed_pct,
                    weather_temperature, weather_humidity,
                    weather_wind_speed, weather_wind_gust,
                    system_cpu_temp, system_load_1min, sun_elevation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    ts.isoformat(),
                    ts.timestamp(),
                    "test_camera",
                    f"/path/to/image_{i}.jpg",
                    exposure,
                    1.0 if mode == "day" else 6.0,
                    lux,
                    mode,
                    120,
                    50,
                    200,
                    0.1,
                    0.2,
                    -5 + (hour - 12) * 0.5,
                    75,
                    3.0,
                    5.0,
                    42 + i * 0.1,
                    0.5,
                    30 if mode == "day" else -10,
                ),
            )

        conn.commit()
        conn.close()

        yield db_path

        # Cleanup
        os.unlink(db_path)

    def test_full_graph_generation(self, temp_db_with_data):
        """Test that graph generation completes without errors."""
        from db_graphs import (
            fetch_data,
            create_lux_graph,
            create_exposure_gain_graph,
            create_brightness_graph,
            create_weather_graph,
            create_system_graph,
            create_overview_graph,
        )

        with tempfile.TemporaryDirectory() as output_dir:
            output_path = Path(output_dir)

            # Fetch data
            data = fetch_data(temp_db_with_data, timedelta(hours=48))

            assert len(data["timestamps"]) > 0

            # Generate all graphs
            create_lux_graph(data, output_path, "Test")
            create_exposure_gain_graph(data, output_path, "Test")
            create_brightness_graph(data, output_path, "Test")
            create_weather_graph(data, output_path, "Test")
            create_system_graph(data, output_path, "Test")
            create_overview_graph(data, output_path, "Test")

            # Check all files were created
            assert (output_path / "lux_levels.png").exists()
            assert (output_path / "exposure_gain.png").exists()
            assert (output_path / "brightness.png").exists()
            assert (output_path / "weather.png").exists()
            assert (output_path / "system.png").exists()
            assert (output_path / "overview.png").exists()

            # Check files have content
            for png_file in output_path.glob("*.png"):
                assert png_file.stat().st_size > 1000  # At least 1KB
