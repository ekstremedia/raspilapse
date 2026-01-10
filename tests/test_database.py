"""Tests for database module."""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.database import CaptureDatabase, DatabaseConfig


@pytest.fixture
def db_config():
    """Create test database configuration with in-memory DB."""
    return {
        "database": {
            "enabled": True,
            "path": ":memory:",
            "create_directories": False,
        },
        "output": {
            "project_name": "test_camera",
        },
    }


@pytest.fixture
def temp_db_config():
    """Create temp file database configuration."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield {
            "database": {
                "enabled": True,
                "path": os.path.join(tmpdir, "data", "test.db"),
                "create_directories": True,
            },
            "output": {
                "project_name": "test_camera",
            },
        }


@pytest.fixture
def sample_metadata():
    """Sample camera metadata."""
    return {
        "capture_timestamp": "2025-01-10T15:30:00",
        "ExposureTime": 100000,  # 100ms
        "AnalogueGain": 2.5,
        "ColourGains": [1.8, 2.0],
        "ColourTemperature": 5500,
        "DigitalGain": 1.0,
        "SensorTemperature": 42.5,
    }


@pytest.fixture
def sample_brightness():
    """Sample brightness metrics."""
    return {
        "mean_brightness": 125.5,
        "median_brightness": 128.0,
        "std_brightness": 45.2,
        "percentile_5": 20.0,
        "percentile_25": 60.0,
        "percentile_75": 180.0,
        "percentile_95": 235.0,
        "underexposed_percent": 2.5,
        "overexposed_percent": 1.0,
    }


@pytest.fixture
def sample_weather():
    """Sample weather data."""
    return {
        "temperature": -5.2,
        "humidity": 85,
        "wind_speed": 15,
        "wind_gust": 25,
        "wind_angle": 180,
        "rain": 0.0,
        "rain_1h": 0.5,
        "rain_24h": 2.3,
        "pressure": 1015,
    }


@pytest.fixture
def sample_system():
    """Sample system metrics."""
    return {
        "cpu_temp": 45.2,
        "load": {
            "1min": 0.52,
            "5min": 0.48,
            "15min": 0.45,
        },
    }


class TestDatabaseConfig:
    """Test DatabaseConfig class."""

    def test_config_enabled(self, db_config):
        """Test config with database enabled."""
        config = DatabaseConfig(db_config)
        assert config.enabled is True
        assert config.camera_id == "test_camera"
        assert config.db_path == ":memory:"

    def test_config_disabled(self):
        """Test config with database disabled."""
        config = DatabaseConfig({"database": {"enabled": False}})
        assert config.enabled is False

    def test_config_missing(self):
        """Test config with no database section."""
        config = DatabaseConfig({})
        assert config.enabled is False

    def test_config_default_path(self):
        """Test default database path."""
        config = DatabaseConfig({"database": {"enabled": True}})
        assert config.db_path == "data/timelapse.db"

    def test_config_default_camera_id(self):
        """Test default camera_id when project_name missing."""
        config = DatabaseConfig({"database": {"enabled": True}})
        assert config.camera_id == "unknown"


class TestCaptureDatabaseInit:
    """Test CaptureDatabase initialization."""

    def test_init_disabled(self):
        """Test initialization with database disabled."""
        db = CaptureDatabase({"database": {"enabled": False}})
        assert db.config.enabled is False

    def test_init_creates_schema(self, db_config):
        """Test database schema creation."""
        db = CaptureDatabase(db_config)
        stats = db.get_statistics()
        assert stats["enabled"] is True
        assert stats["total_captures"] == 0

    def test_init_creates_directory(self, temp_db_config):
        """Test directory creation when create_directories=True."""
        db = CaptureDatabase(temp_db_config)
        db_path = Path(temp_db_config["database"]["path"])
        assert db_path.parent.exists()

    def test_init_idempotent(self, db_config):
        """Test that initialization is safe to call multiple times."""
        db1 = CaptureDatabase(db_config)
        db1.store_capture("/test/1.jpg", {"capture_timestamp": "2025-01-01T00:00:00"}, "day")

        # Create another instance pointing to same DB
        # (In memory DB won't share state, but structure should be fine)
        db2 = CaptureDatabase(db_config)
        stats = db2.get_statistics()
        assert stats["enabled"] is True


class TestStoreCapture:
    """Test store_capture method."""

    def test_store_capture_basic(self, db_config, sample_metadata):
        """Test storing a basic capture."""
        db = CaptureDatabase(db_config)
        result = db.store_capture(
            image_path="/test/image.jpg",
            metadata=sample_metadata,
            mode="day",
            lux=500.0,
        )
        assert result is True
        stats = db.get_statistics()
        assert stats["total_captures"] == 1

    def test_store_capture_with_all_data(
        self, db_config, sample_metadata, sample_brightness, sample_weather
    ):
        """Test storing capture with all data fields."""
        db = CaptureDatabase(db_config)
        result = db.store_capture(
            image_path="/test/image.jpg",
            metadata=sample_metadata,
            mode="transition",
            lux=50.0,
            brightness_metrics=sample_brightness,
            weather_data=sample_weather,
            sun_elevation=15.5,
        )
        assert result is True

        # Query back and verify
        start = datetime(2025, 1, 10, 0, 0, 0)
        end = datetime(2025, 1, 10, 23, 59, 59)
        captures = db.get_captures_in_range(start, end)
        assert len(captures) == 1
        capture = captures[0]

        # Verify camera metadata
        assert capture["exposure_time_us"] == 100000
        assert capture["analogue_gain"] == 2.5
        assert capture["colour_gains_r"] == 1.8
        assert capture["colour_gains_b"] == 2.0

        # Verify brightness metrics
        assert capture["brightness_mean"] == 125.5
        assert capture["brightness_median"] == 128.0

        # Verify weather data
        assert capture["weather_temperature"] == -5.2
        assert capture["weather_humidity"] == 85

    def test_store_capture_with_system_metrics(self, db_config, sample_metadata, sample_system):
        """Test storing with system metrics (CPU temp and load)."""
        db = CaptureDatabase(db_config)
        result = db.store_capture(
            image_path="/test/image.jpg",
            metadata=sample_metadata,
            mode="day",
            lux=500.0,
            system_metrics=sample_system,
        )
        assert result is True

        # Query back and verify
        start = datetime(2025, 1, 10, 0, 0, 0)
        end = datetime(2025, 1, 10, 23, 59, 59)
        captures = db.get_captures_in_range(start, end)
        assert len(captures) == 1
        capture = captures[0]

        # Verify system metrics
        assert capture["system_cpu_temp"] == 45.2
        assert capture["system_load_1min"] == 0.52
        assert capture["system_load_5min"] == 0.48
        assert capture["system_load_15min"] == 0.45

    def test_store_capture_disabled(self, sample_metadata):
        """Test storing when database disabled returns True (not an error)."""
        db = CaptureDatabase({"database": {"enabled": False}})
        result = db.store_capture(
            image_path="/test/image.jpg",
            metadata=sample_metadata,
            mode="day",
        )
        assert result is True

    def test_store_capture_handles_missing_metadata(self, db_config):
        """Test storing with minimal/empty metadata."""
        db = CaptureDatabase(db_config)
        result = db.store_capture(
            image_path="/test/image.jpg",
            metadata={},  # Empty metadata
            mode="day",
        )
        assert result is True
        stats = db.get_statistics()
        assert stats["total_captures"] == 1

    def test_store_capture_handles_none_values(self, db_config):
        """Test storing with None values for optional fields."""
        db = CaptureDatabase(db_config)
        result = db.store_capture(
            image_path="/test/image.jpg",
            metadata={"capture_timestamp": "2025-01-10T12:00:00"},
            mode="night",
            lux=None,
            brightness_metrics=None,
            weather_data=None,
            sun_elevation=None,
        )
        assert result is True

    def test_store_capture_multiple(self, db_config, sample_metadata):
        """Test storing multiple captures."""
        db = CaptureDatabase(db_config)

        for i in range(10):
            metadata = sample_metadata.copy()
            metadata["capture_timestamp"] = f"2025-01-10T12:{i:02d}:00"
            result = db.store_capture(f"/test/image_{i}.jpg", metadata, "day", lux=100 + i)
            assert result is True

        stats = db.get_statistics()
        assert stats["total_captures"] == 10

    def test_unique_constraint_updates(self, db_config, sample_metadata):
        """Test unique constraint causes update, not duplicate."""
        db = CaptureDatabase(db_config)

        # Store same timestamp twice with different data
        db.store_capture("/test/image1.jpg", sample_metadata, "day", lux=100)
        db.store_capture("/test/image2.jpg", sample_metadata, "night", lux=5)

        stats = db.get_statistics()
        assert stats["total_captures"] == 1  # Updated, not duplicated

        # Verify updated values
        start = datetime(2025, 1, 10, 0, 0, 0)
        end = datetime(2025, 1, 10, 23, 59, 59)
        captures = db.get_captures_in_range(start, end)
        assert captures[0]["mode"] == "night"
        assert captures[0]["lux"] == 5


class TestQueryCaptures:
    """Test query methods."""

    def test_query_captures_in_range(self, db_config, sample_metadata):
        """Test querying captures by time range."""
        db = CaptureDatabase(db_config)

        # Store captures at different times
        for i in range(5):
            metadata = sample_metadata.copy()
            dt = datetime(2025, 1, 10, 12, i * 10, 0)
            metadata["capture_timestamp"] = dt.isoformat()
            db.store_capture(f"/test/image_{i}.jpg", metadata, "day")

        # Query range that includes 4 captures (0, 10, 20, 30 minutes)
        start = datetime(2025, 1, 10, 12, 0, 0)
        end = datetime(2025, 1, 10, 12, 30, 0)
        results = db.get_captures_in_range(start, end)

        assert len(results) == 4

    def test_query_captures_empty_range(self, db_config):
        """Test querying empty time range."""
        db = CaptureDatabase(db_config)
        start = datetime(2025, 1, 1)
        end = datetime(2025, 1, 2)
        results = db.get_captures_in_range(start, end)
        assert results == []

    def test_query_captures_by_camera_id(self, db_config, sample_metadata):
        """Test filtering by camera_id."""
        db = CaptureDatabase(db_config)

        # Store captures
        for i in range(3):
            metadata = sample_metadata.copy()
            metadata["capture_timestamp"] = f"2025-01-10T12:{i:02d}:00"
            db.store_capture(f"/test/image_{i}.jpg", metadata, "day")

        # Query with matching camera_id
        start = datetime(2025, 1, 10, 0, 0, 0)
        end = datetime(2025, 1, 10, 23, 59, 59)
        results = db.get_captures_in_range(start, end, camera_id="test_camera")
        assert len(results) == 3

        # Query with non-matching camera_id
        results = db.get_captures_in_range(start, end, camera_id="other_camera")
        assert len(results) == 0

    def test_query_captures_disabled(self):
        """Test querying when database disabled returns empty list."""
        db = CaptureDatabase({"database": {"enabled": False}})
        results = db.get_captures_in_range(datetime.now(), datetime.now())
        assert results == []

    def test_query_by_lux_range(self, db_config, sample_metadata):
        """Test querying by lux range."""
        db = CaptureDatabase(db_config)

        # Store captures with different lux values
        lux_values = [5, 50, 100, 500, 1000]
        for i, lux in enumerate(lux_values):
            metadata = sample_metadata.copy()
            metadata["capture_timestamp"] = f"2025-01-10T12:{i:02d}:00"
            db.store_capture(f"/test/image_{i}.jpg", metadata, "day", lux=lux)

        # Query lux range 40-150 (should include 50 and 100)
        results = db.get_captures_by_lux_range(40, 150)
        assert len(results) == 2

    def test_get_hourly_averages(self, db_config, sample_metadata, sample_brightness):
        """Test hourly averages aggregation."""
        db = CaptureDatabase(db_config)

        # Store multiple captures across 2 hours
        for hour in [12, 13]:
            for minute in [0, 15, 30, 45]:
                metadata = sample_metadata.copy()
                metadata["capture_timestamp"] = f"2025-01-10T{hour}:{minute:02d}:00"
                db.store_capture(
                    f"/test/image_{hour}_{minute}.jpg",
                    metadata,
                    "day",
                    lux=100 + hour * 10,
                    brightness_metrics=sample_brightness,
                )

        start = datetime(2025, 1, 10, 12, 0, 0)
        end = datetime(2025, 1, 10, 14, 0, 0)
        results = db.get_hourly_averages(start, end)

        assert len(results) == 2  # 2 hours
        assert results[0]["capture_count"] == 4
        assert results[1]["capture_count"] == 4


class TestStatistics:
    """Test get_statistics method."""

    def test_statistics_empty_db(self, db_config):
        """Test statistics on empty database."""
        db = CaptureDatabase(db_config)
        stats = db.get_statistics()
        assert stats["enabled"] is True
        assert stats["total_captures"] == 0
        assert stats["earliest"] is None
        assert stats["latest"] is None

    def test_statistics_with_data(self, db_config, sample_metadata):
        """Test statistics with captures."""
        db = CaptureDatabase(db_config)

        for i in range(5):
            metadata = sample_metadata.copy()
            metadata["capture_timestamp"] = f"2025-01-{10+i:02d}T12:00:00"
            db.store_capture(f"/test/image_{i}.jpg", metadata, "day")

        stats = db.get_statistics()
        assert stats["total_captures"] == 5
        assert stats["earliest"] == "2025-01-10T12:00:00"
        assert stats["latest"] == "2025-01-14T12:00:00"

    def test_statistics_disabled(self):
        """Test statistics when database disabled."""
        db = CaptureDatabase({"database": {"enabled": False}})
        stats = db.get_statistics()
        assert stats == {"enabled": False}


class TestErrorHandling:
    """Test database error handling."""

    def test_store_never_raises(self, db_config, sample_metadata):
        """Test that store_capture never raises exceptions."""
        db = CaptureDatabase(db_config)

        # Force an error by mocking connection
        with patch.object(db, "_get_connection") as mock_conn:
            mock_ctx = Mock()
            mock_ctx.__enter__ = Mock(return_value=None)
            mock_ctx.__exit__ = Mock(return_value=False)
            mock_conn.return_value = mock_ctx

            # Should return False, not raise
            result = db.store_capture("/test/image.jpg", sample_metadata, "day")
            assert result is False

    def test_query_returns_empty_on_error(self, db_config):
        """Test that queries return empty list on error."""
        db = CaptureDatabase(db_config)

        with patch.object(db, "_get_connection") as mock_conn:
            mock_ctx = Mock()
            mock_ctx.__enter__ = Mock(return_value=None)
            mock_ctx.__exit__ = Mock(return_value=False)
            mock_conn.return_value = mock_ctx

            results = db.get_captures_in_range(datetime.now(), datetime.now())
            assert results == []

    def test_statistics_returns_error_info(self, db_config):
        """Test statistics returns error info on failure."""
        db = CaptureDatabase(db_config)

        with patch.object(db, "_get_connection") as mock_conn:
            mock_ctx = Mock()
            mock_ctx.__enter__ = Mock(return_value=None)
            mock_ctx.__exit__ = Mock(return_value=False)
            mock_conn.return_value = mock_ctx

            stats = db.get_statistics()
            assert stats["enabled"] is True
            assert "error" in stats


class TestPersistence:
    """Test database persistence across sessions."""

    def test_persistence_across_sessions(self, temp_db_config, sample_metadata):
        """Test that data persists across database sessions."""
        # First session - store data
        db1 = CaptureDatabase(temp_db_config)
        db1.store_capture("/test/image.jpg", sample_metadata, "day", lux=100)
        db1.close()

        # Second session - verify data exists
        db2 = CaptureDatabase(temp_db_config)
        stats = db2.get_statistics()
        assert stats["total_captures"] == 1
        db2.close()

    def test_full_workflow(
        self, temp_db_config, sample_metadata, sample_brightness, sample_weather
    ):
        """Test complete capture workflow over time."""
        db = CaptureDatabase(temp_db_config)

        # Simulate timelapse captures over several hours
        base_time = datetime(2025, 1, 10, 0, 0, 0)  # Start at midnight

        for i in range(24):  # 24 captures, one per hour
            capture_time = base_time + timedelta(hours=i)
            metadata = sample_metadata.copy()
            metadata["capture_timestamp"] = capture_time.isoformat()

            # Simulate day/night cycle
            hour = capture_time.hour
            if 7 <= hour <= 17:
                mode = "day"
                lux = 500 + (hour - 7) * 50  # Peak at noon
            elif hour in [6, 18]:
                mode = "transition"
                lux = 50
            else:
                mode = "night"
                lux = 5

            db.store_capture(
                image_path=f"/var/www/html/images/frame_{i:04d}.jpg",
                metadata=metadata,
                mode=mode,
                lux=lux,
                brightness_metrics=sample_brightness,
                weather_data=sample_weather,
                sun_elevation=30 - abs(12 - hour) * 3,
            )

        # Verify storage
        stats = db.get_statistics()
        assert stats["total_captures"] == 24

        # Query daytime captures only
        start = datetime(2025, 1, 10, 7, 0, 0)
        end = datetime(2025, 1, 10, 17, 0, 0)
        day_captures = db.get_captures_in_range(start, end)
        assert len(day_captures) == 11  # 7 AM to 5 PM inclusive

        # Query by lux (nighttime low lux)
        night_captures = db.get_captures_by_lux_range(0, 10)
        assert len(night_captures) > 0

        # Get hourly averages
        full_start = datetime(2025, 1, 10, 0, 0, 0)
        full_end = datetime(2025, 1, 11, 0, 0, 0)
        hourly = db.get_hourly_averages(full_start, full_end)
        assert len(hourly) == 24

        db.close()


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_invalid_timestamp_format(self, db_config):
        """Test handling of invalid timestamp format."""
        db = CaptureDatabase(db_config)
        result = db.store_capture(
            image_path="/test/image.jpg",
            metadata={"capture_timestamp": "invalid-timestamp"},
            mode="day",
        )
        # Should still succeed, using current time as fallback
        assert result is True

    def test_missing_colour_gains(self, db_config):
        """Test handling of missing ColourGains (None)."""
        db = CaptureDatabase(db_config)
        result = db.store_capture(
            image_path="/test/image.jpg",
            metadata={
                "capture_timestamp": "2025-01-10T12:00:00",
                "ColourGains": None,  # Explicitly None
            },
            mode="day",
        )
        assert result is True

    def test_empty_brightness_dict(self, db_config):
        """Test handling of empty brightness metrics dict."""
        db = CaptureDatabase(db_config)
        result = db.store_capture(
            image_path="/test/image.jpg",
            metadata={"capture_timestamp": "2025-01-10T12:00:00"},
            mode="day",
            brightness_metrics={},  # Empty dict
        )
        assert result is True

    def test_extreme_values(self, db_config):
        """Test handling of extreme values."""
        db = CaptureDatabase(db_config)
        result = db.store_capture(
            image_path="/test/image.jpg",
            metadata={
                "capture_timestamp": "2025-01-10T12:00:00",
                "ExposureTime": 20000000,  # 20 seconds
                "AnalogueGain": 16.0,  # Max gain
            },
            mode="night",
            lux=0.001,  # Very low lux
            brightness_metrics={
                "mean_brightness": 5.0,
                "overexposed_percent": 0.0,
                "underexposed_percent": 95.0,
            },
            weather_data={
                "temperature": -40.0,  # Extreme cold
                "wind_speed": 150,  # Hurricane
            },
            sun_elevation=-18.0,  # Astronomical twilight
        )
        assert result is True
