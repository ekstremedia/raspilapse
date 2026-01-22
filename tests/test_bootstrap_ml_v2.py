"""
Tests for ML v2 Bootstrap Script.
"""

import json
import os
import sqlite3
import tempfile

import pytest


class TestLuxBucket:
    """Tests for get_lux_bucket function."""

    def test_lux_bucket_zero(self):
        """Test bucket for very low lux."""
        from src.bootstrap_ml_v2 import get_lux_bucket

        assert get_lux_bucket(0.1) == 0
        assert get_lux_bucket(0.4) == 0

    def test_lux_bucket_boundaries(self):
        """Test bucket boundaries."""
        from src.bootstrap_ml_v2 import get_lux_bucket

        # Bucket 1: 0.5-1.0
        assert get_lux_bucket(0.5) == 1
        assert get_lux_bucket(0.9) == 1

        # Bucket 2: 1.0-2.0
        assert get_lux_bucket(1.0) == 2
        assert get_lux_bucket(1.9) == 2

        # Bucket 5: 10.0-20.0
        assert get_lux_bucket(10.0) == 5
        assert get_lux_bucket(15.0) == 5

    def test_lux_bucket_high_values(self):
        """Test bucket for high lux values."""
        from src.bootstrap_ml_v2 import get_lux_bucket

        assert get_lux_bucket(1000.0) == 11
        assert get_lux_bucket(5000.0) == 11
        assert get_lux_bucket(100000.0) == 11


class TestTimePeriod:
    """Tests for get_time_period function (clock-based fallback)."""

    def test_night_hours(self):
        """Test night period detection."""
        from src.bootstrap_ml_v2 import get_time_period

        assert get_time_period(0) == "night"
        assert get_time_period(3) == "night"
        assert get_time_period(5) == "night"
        assert get_time_period(22) == "night"
        assert get_time_period(23) == "night"

    def test_twilight_hours(self):
        """Test twilight period detection."""
        from src.bootstrap_ml_v2 import get_time_period

        # Morning twilight
        assert get_time_period(6) == "twilight"
        assert get_time_period(8) == "twilight"
        # Evening twilight
        assert get_time_period(16) == "twilight"
        assert get_time_period(19) == "twilight"

    def test_day_hours(self):
        """Test day period detection."""
        from src.bootstrap_ml_v2 import get_time_period

        assert get_time_period(10) == "day"
        assert get_time_period(12) == "day"
        assert get_time_period(15) == "day"


class TestSolarPeriod:
    """Tests for get_solar_period function (Arctic-aware)."""

    def test_deep_night(self):
        """Test night period for sun below -12 degrees."""
        from src.bootstrap_ml_v2 import get_solar_period

        assert get_solar_period(-15) == "night"
        assert get_solar_period(-20) == "night"
        assert get_solar_period(-50) == "night"

    def test_twilight(self):
        """Test twilight period for sun between -12 and 0 degrees."""
        from src.bootstrap_ml_v2 import get_solar_period

        assert get_solar_period(-11) == "twilight"
        assert get_solar_period(-6) == "twilight"
        assert get_solar_period(-1) == "twilight"

    def test_day(self):
        """Test day period for sun above 0 degrees."""
        from src.bootstrap_ml_v2 import get_solar_period

        assert get_solar_period(0) == "day"
        assert get_solar_period(15) == "day"
        assert get_solar_period(45) == "day"

    def test_boundary_at_minus_12(self):
        """Test boundary at -12 degrees (night/twilight boundary)."""
        from src.bootstrap_ml_v2 import get_solar_period

        assert get_solar_period(-12) == "twilight"
        assert get_solar_period(-12.1) == "night"


class TestLuxRange:
    """Tests for get_lux_range function."""

    def test_lux_range_low_buckets(self):
        """Test lux range strings for low buckets."""
        from src.bootstrap_ml_v2 import get_lux_range

        assert get_lux_range(0) == "0.0-0.5"
        assert get_lux_range(1) == "0.5-1.0"
        assert get_lux_range(2) == "1.0-2.0"

    def test_lux_range_high_bucket(self):
        """Test lux range string for highest bucket."""
        from src.bootstrap_ml_v2 import get_lux_range

        assert get_lux_range(11) == "1000.0+"


class TestAnalyzeDatabase:
    """Tests for analyze_database function."""

    def test_analyze_empty_database(self):
        """Test analysis of empty database."""
        from src.bootstrap_ml_v2 import analyze_database

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    lux REAL,
                    exposure_time_us INTEGER,
                    brightness_mean REAL,
                    brightness_p5 REAL,
                    brightness_p95 REAL,
                    sun_elevation REAL
                )
            """
            )
            conn.close()

            result = analyze_database(db_path, 100, 140)

            assert result["total_frames"] == 0
            assert result["good_frames"] == 0

    def test_analyze_with_good_frames(self):
        """Test analysis with good brightness frames."""
        from src.bootstrap_ml_v2 import analyze_database

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    lux REAL,
                    exposure_time_us INTEGER,
                    brightness_mean REAL,
                    brightness_p5 REAL,
                    brightness_p95 REAL,
                    sun_elevation REAL
                )
            """
            )

            # Insert mix of good and bad frames
            for i in range(10):
                conn.execute(
                    """
                    INSERT INTO captures (timestamp, lux, exposure_time_us, brightness_mean, brightness_p5, brightness_p95)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (f"2026-01-11T12:00:{i:02d}", 100.0, 50000, 120.0, 80.0, 160.0),
                )  # Good

            for i in range(5):
                conn.execute(
                    """
                    INSERT INTO captures (timestamp, lux, exposure_time_us, brightness_mean, brightness_p5, brightness_p95)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (f"2026-01-11T13:00:{i:02d}", 100.0, 50000, 200.0, 150.0, 250.0),
                )  # Bad (overexposed)

            conn.commit()
            conn.close()

            result = analyze_database(db_path, 100, 140)

            assert result["total_frames"] == 15
            assert result["good_frames_standard"] == 10
            assert result["good_percentage"] == pytest.approx(66.67, rel=0.1)

    def test_analyze_with_aurora_frames(self):
        """Test analysis counts aurora frames correctly."""
        from src.bootstrap_ml_v2 import analyze_database

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    lux REAL,
                    exposure_time_us INTEGER,
                    brightness_mean REAL,
                    brightness_p5 REAL,
                    brightness_p95 REAL,
                    sun_elevation REAL
                )
            """
            )

            # Insert aurora frames (dark overall, bright highlights, low lux)
            for i in range(5):
                conn.execute(
                    """
                    INSERT INTO captures (timestamp, lux, exposure_time_us, brightness_mean, brightness_p5, brightness_p95)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (f"2026-01-11T02:00:{i:02d}", 1.0, 20000000, 50.0, 20.0, 180.0),
                )  # Aurora

            conn.commit()
            conn.close()

            result = analyze_database(db_path, 100, 140)

            assert result["good_frames_aurora"] == 5


class TestBootstrapFromDatabase:
    """Tests for bootstrap_from_database function."""

    def test_bootstrap_empty_database(self):
        """Test bootstrap with no good frames."""
        from src.bootstrap_ml_v2 import bootstrap_from_database

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            output_path = os.path.join(tmpdir, "ml_state.json")

            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    lux REAL,
                    exposure_time_us INTEGER,
                    brightness_mean REAL,
                    brightness_p5 REAL,
                    brightness_p95 REAL,
                    sun_elevation REAL
                )
            """
            )
            conn.close()

            result = bootstrap_from_database(db_path, output_path)

            assert result is None

    def test_bootstrap_creates_state_file(self):
        """Test that bootstrap creates state file with correct structure."""
        from src.bootstrap_ml_v2 import bootstrap_from_database

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            output_path = os.path.join(tmpdir, "ml_state.json")

            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    lux REAL,
                    exposure_time_us INTEGER,
                    brightness_mean REAL,
                    brightness_p5 REAL,
                    brightness_p95 REAL,
                    sun_elevation REAL
                )
            """
            )

            # Insert enough good frames to meet min_samples
            for i in range(15):
                conn.execute(
                    """
                    INSERT INTO captures (timestamp, lux, exposure_time_us, brightness_mean, brightness_p5, brightness_p95, sun_elevation)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        f"2026-01-11T12:{i:02d}:00",
                        500.0,
                        50000,
                        120.0,
                        80.0,
                        160.0,
                        30.0,
                    ),
                )

            conn.commit()
            conn.close()

            result = bootstrap_from_database(db_path, output_path, min_samples=5)

            assert result is not None
            assert os.path.exists(output_path)

            with open(output_path) as f:
                state = json.load(f)

            assert "lux_exposure_map" in state
            assert "training_stats" in state
            assert "version" in state
            assert state["version"] == 2

    def test_bootstrap_uses_solar_period(self):
        """Test that bootstrap uses sun_elevation for period when available."""
        from src.bootstrap_ml_v2 import bootstrap_from_database

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            output_path = os.path.join(tmpdir, "ml_state.json")

            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    lux REAL,
                    exposure_time_us INTEGER,
                    brightness_mean REAL,
                    brightness_p5 REAL,
                    brightness_p95 REAL,
                    sun_elevation REAL
                )
            """
            )

            # Insert frames with twilight sun elevation but noon timestamp
            for i in range(15):
                conn.execute(
                    """
                    INSERT INTO captures (timestamp, lux, exposure_time_us, brightness_mean, brightness_p5, brightness_p95, sun_elevation)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        "2026-01-11T12:00:00",  # Noon by clock
                        75.0,  # Bucket 7
                        500000,
                        120.0,
                        80.0,
                        160.0,
                        -5.0,  # Twilight by sun elevation
                    ),
                )

            conn.commit()
            conn.close()

            result = bootstrap_from_database(db_path, output_path, min_samples=5)

            # Should use twilight (from sun_elevation), not day (from clock)
            assert "7_twilight" in result["lux_exposure_map"]
            assert "7_day" not in result["lux_exposure_map"]

    def test_bootstrap_falls_back_to_clock(self):
        """Test that bootstrap falls back to clock when sun_elevation is NULL."""
        from src.bootstrap_ml_v2 import bootstrap_from_database

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            output_path = os.path.join(tmpdir, "ml_state.json")

            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    lux REAL,
                    exposure_time_us INTEGER,
                    brightness_mean REAL,
                    brightness_p5 REAL,
                    brightness_p95 REAL,
                    sun_elevation REAL
                )
            """
            )

            # Insert frames WITHOUT sun_elevation (NULL)
            for i in range(15):
                conn.execute(
                    """
                    INSERT INTO captures (timestamp, lux, exposure_time_us, brightness_mean, brightness_p5, brightness_p95)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        "2026-01-11T12:00:00",  # Noon = day by clock
                        300.0,  # Bucket 9 (200-500)
                        50000,
                        120.0,
                        80.0,
                        160.0,
                    ),
                )

            conn.commit()
            conn.close()

            result = bootstrap_from_database(db_path, output_path, min_samples=5)

            # Should use day (from clock fallback)
            assert "9_day" in result["lux_exposure_map"]
