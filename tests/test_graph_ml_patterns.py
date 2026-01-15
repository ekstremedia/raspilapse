"""
Tests for Daily Solar Patterns Graph Generation.

Tests the database-based solar pattern visualization that shows
lux patterns across multiple days.
"""

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from unittest import mock

import pytest


class TestGetDbPath:
    """Tests for get_db_path function."""

    def test_returns_default_path_when_no_config(self):
        """Test that default path is returned when config doesn't exist."""
        from src.graph_ml_patterns import get_db_path

        # Function should return a path even if config doesn't exist
        result = get_db_path()
        assert result.endswith("timelapse.db")


class TestFetchDailyLuxData:
    """Tests for fetch_daily_lux_data function."""

    def test_fetch_from_empty_db(self):
        """Test fetching from database with no captures."""
        from src.graph_ml_patterns import fetch_daily_lux_data

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        # Create empty database with schema
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE captures (
                timestamp TEXT,
                lux REAL,
                mode TEXT,
                sun_elevation REAL
            )
        """
        )
        conn.commit()
        conn.close()

        result = fetch_daily_lux_data(db_path, days=7)
        assert result == {}

        os.unlink(db_path)

    def test_fetch_with_data(self):
        """Test fetching data from database with captures."""
        from src.graph_ml_patterns import fetch_daily_lux_data

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE captures (
                timestamp TEXT,
                lux REAL,
                mode TEXT,
                sun_elevation REAL
            )
        """
        )

        # Insert test data for today and yesterday
        now = datetime.now()
        yesterday = now - timedelta(days=1)

        test_data = [
            (now.replace(hour=10, minute=0).isoformat(), 100.0, "day", 5.0),
            (now.replace(hour=11, minute=0).isoformat(), 200.0, "day", 8.0),
            (now.replace(hour=12, minute=0).isoformat(), 300.0, "day", 10.0),
            (yesterday.replace(hour=10, minute=0).isoformat(), 80.0, "day", 4.0),
            (yesterday.replace(hour=11, minute=0).isoformat(), 150.0, "day", 7.0),
        ]

        conn.executemany("INSERT INTO captures VALUES (?, ?, ?, ?)", test_data)
        conn.commit()
        conn.close()

        result = fetch_daily_lux_data(db_path, days=7)

        assert len(result) == 2  # Two days of data
        today_key = now.strftime("%Y-%m-%d")
        yesterday_key = yesterday.strftime("%Y-%m-%d")

        assert today_key in result
        assert yesterday_key in result
        assert len(result[today_key]["times"]) == 3
        assert len(result[yesterday_key]["times"]) == 2

        os.unlink(db_path)

    def test_fetch_filters_by_days(self):
        """Test that data is filtered by days parameter."""
        from src.graph_ml_patterns import fetch_daily_lux_data

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE captures (
                timestamp TEXT,
                lux REAL,
                mode TEXT,
                sun_elevation REAL
            )
        """
        )

        # Insert data from 10 days ago and today
        now = datetime.now()
        old_date = now - timedelta(days=10)

        test_data = [
            (now.replace(hour=12).isoformat(), 100.0, "day", 5.0),
            (old_date.replace(hour=12).isoformat(), 100.0, "day", 5.0),
        ]

        conn.executemany("INSERT INTO captures VALUES (?, ?, ?, ?)", test_data)
        conn.commit()
        conn.close()

        # Fetch only last 7 days
        result = fetch_daily_lux_data(db_path, days=7)

        assert len(result) == 1  # Only today's data
        assert now.strftime("%Y-%m-%d") in result

        os.unlink(db_path)

    def test_fetch_nonexistent_db(self):
        """Test fetching from nonexistent database."""
        from src.graph_ml_patterns import fetch_daily_lux_data

        result = fetch_daily_lux_data("/nonexistent/db.db", days=7)
        assert result == {}


class TestCreateSolarPatternGraph:
    """Tests for create_solar_pattern_graph function."""

    def _create_test_db(self, db_path: str, num_days: int = 5):
        """Helper to create test database with sample data."""
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE captures (
                timestamp TEXT,
                lux REAL,
                mode TEXT,
                sun_elevation REAL
            )
        """
        )

        now = datetime.now()
        test_data = []

        for day_offset in range(num_days):
            day = now - timedelta(days=day_offset)
            # Add hourly data from 6am to 6pm
            for hour in range(6, 18):
                # Simulate lux pattern peaking at noon
                base_lux = 100 + (day_offset * 20)  # Increasing trend
                hour_factor = 1 - abs(hour - 12) / 6  # Peak at noon
                lux = base_lux * (0.5 + hour_factor)

                ts = day.replace(hour=hour, minute=0, second=0)
                test_data.append((ts.isoformat(), lux, "day", hour - 6))

        conn.executemany("INSERT INTO captures VALUES (?, ?, ?, ?)", test_data)
        conn.commit()
        conn.close()

    def test_create_graph_empty_db(self, capsys):
        """Test creating graph with empty database."""
        from src.graph_ml_patterns import create_solar_pattern_graph

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            output_path = os.path.join(tmpdir, "test_graph.png")

            # Create empty database
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    timestamp TEXT, lux REAL, mode TEXT, sun_elevation REAL
                )
            """
            )
            conn.close()

            result = create_solar_pattern_graph(db_path, output_path, days=7)

            assert result is False
            captured = capsys.readouterr()
            assert "No data" in captured.out

    def test_create_graph_with_data(self):
        """Test creating graph with sample data."""
        from src.graph_ml_patterns import create_solar_pattern_graph

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            output_path = os.path.join(tmpdir, "test_graph.png")

            self._create_test_db(db_path, num_days=5)

            result = create_solar_pattern_graph(db_path, output_path, days=7)

            assert result is True
            assert os.path.exists(output_path)
            # Check file size is reasonable (should be > 50KB for a graph)
            assert os.path.getsize(output_path) > 50000

    def test_create_graph_single_day(self):
        """Test creating graph with single day of data."""
        from src.graph_ml_patterns import create_solar_pattern_graph

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            output_path = os.path.join(tmpdir, "test_graph.png")

            self._create_test_db(db_path, num_days=1)

            result = create_solar_pattern_graph(db_path, output_path, days=7)

            assert result is True
            assert os.path.exists(output_path)

    def test_create_graph_many_days(self):
        """Test creating graph with many days shows trend line."""
        from src.graph_ml_patterns import create_solar_pattern_graph

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            output_path = os.path.join(tmpdir, "test_graph.png")

            self._create_test_db(db_path, num_days=14)

            result = create_solar_pattern_graph(db_path, output_path, days=14)

            assert result is True
            assert os.path.exists(output_path)


class TestLoadMlState:
    """Tests for legacy load_ml_state function (backwards compatibility)."""

    def test_load_valid_state(self):
        """Test loading valid JSON file."""
        import json
        from src.graph_ml_patterns import load_ml_state

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            state = {"test_key": "test_value"}
            json.dump(state, f)
            f.flush()

            result = load_ml_state(f.name)
            assert result["test_key"] == "test_value"
            os.unlink(f.name)

    def test_load_nonexistent_returns_empty(self):
        """Test loading nonexistent file returns empty dict."""
        from src.graph_ml_patterns import load_ml_state

        result = load_ml_state("/nonexistent/file.json")
        assert result == {}


class TestMainFunction:
    """Tests for main CLI function."""

    def test_main_with_valid_db(self):
        """Test main function with valid database."""
        from src.graph_ml_patterns import main

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            output_path = os.path.join(tmpdir, "output.png")

            # Create test database
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    timestamp TEXT, lux REAL, mode TEXT, sun_elevation REAL
                )
            """
            )
            now = datetime.now()
            for hour in range(8, 16):
                ts = now.replace(hour=hour).isoformat()
                conn.execute(
                    "INSERT INTO captures VALUES (?, ?, ?, ?)", (ts, 100.0 * hour, "day", 5.0)
                )
            conn.commit()
            conn.close()

            with mock.patch(
                "sys.argv", ["graph_ml_patterns.py", "--db", db_path, "-o", output_path, "-d", "7"]
            ):
                main()

            assert os.path.exists(output_path)

    def test_main_creates_output_dir(self):
        """Test main function creates output directory if needed."""
        from src.graph_ml_patterns import main

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            output_path = os.path.join(tmpdir, "subdir", "output.png")

            # Create test database
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    timestamp TEXT, lux REAL, mode TEXT, sun_elevation REAL
                )
            """
            )
            now = datetime.now()
            conn.execute(
                "INSERT INTO captures VALUES (?, ?, ?, ?)", (now.isoformat(), 100.0, "day", 5.0)
            )
            conn.commit()
            conn.close()

            with mock.patch(
                "sys.argv", ["graph_ml_patterns.py", "--db", db_path, "-o", output_path]
            ):
                main()

            # Output dir should be created
            assert os.path.exists(os.path.dirname(output_path))
