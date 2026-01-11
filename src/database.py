"""
Database module for Raspilapse timelapse capture storage.

Provides SQLite storage for capture metadata, brightness analysis, and weather data,
enabling historical analysis, graphs, and exposure planning.

Usage:
    from src.database import CaptureDatabase

    db = CaptureDatabase(config)
    db.store_capture(image_path, metadata, mode, lux, brightness_metrics, weather_data)
    captures = db.get_captures_in_range(start_time, end_time)
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Handle imports for both module and script execution
try:
    from src.logging_config import get_logger
except ImportError:
    try:
        from logging_config import get_logger
    except ImportError:
        import logging

        def get_logger(name):
            return logging.getLogger(name)


logger = get_logger("database")


class DatabaseConfig:
    """Database configuration loaded from config dict."""

    def __init__(self, config: Dict):
        """
        Initialize database configuration.

        Args:
            config: Full configuration dictionary
        """
        self.db_config = config.get("database", {})
        self.enabled = self.db_config.get("enabled", False)
        self.db_path = self.db_config.get("path", "data/timelapse.db")
        self.create_directories = self.db_config.get("create_directories", True)

        # Get camera_id from project_name
        self.camera_id = config.get("output", {}).get("project_name", "unknown")


class CaptureDatabase:
    """
    SQLite database for timelapse capture storage.

    Thread-safe, with connection management and graceful error handling.
    Designed to never crash the timelapse if database operations fail.

    Attributes:
        config: DatabaseConfig instance with settings
        SCHEMA_VERSION: Current database schema version
    """

    SCHEMA_VERSION = 2  # Bumped for sun_elevation column

    # Migration definitions: version -> (description, SQL statements)
    MIGRATIONS = {
        2: (
            "Add sun_elevation column for Arctic-aware ML",
            [
                "ALTER TABLE captures ADD COLUMN sun_elevation REAL",
            ],
        ),
    }

    def __init__(self, config: Dict):
        """
        Initialize the capture database.

        Args:
            config: Full configuration dictionary
        """
        self.config = DatabaseConfig(config)
        self._persistent_conn = None  # For in-memory databases

        if not self.config.enabled:
            logger.debug("Database storage disabled in config")
            return

        self._initialize_database()

    @contextmanager
    def _get_connection(self):
        """
        Context manager for database connections.

        Ensures proper connection handling and error isolation.
        For in-memory databases, uses a persistent connection.
        For file databases, creates a fresh connection each time for thread safety.

        Yields:
            sqlite3.Connection or None if connection fails
        """
        if not self.config.enabled:
            yield None
            return

        # For in-memory databases, use persistent connection
        if self.config.db_path == ":memory:":
            if self._persistent_conn is None:
                try:
                    self._persistent_conn = sqlite3.connect(
                        ":memory:",
                        timeout=10.0,
                        isolation_level=None,
                    )
                    self._persistent_conn.row_factory = sqlite3.Row
                except sqlite3.Error as e:
                    logger.warning(f"[DB] Connection error: {e}")
                    yield None
                    return
            yield self._persistent_conn
            return

        # For file databases, create fresh connection
        conn = None
        try:
            conn = sqlite3.connect(
                self.config.db_path,
                timeout=10.0,
                isolation_level=None,  # Autocommit for simple operations
            )
            conn.row_factory = sqlite3.Row  # Dict-like access
            yield conn
        except sqlite3.Error as e:
            logger.warning(f"[DB] Connection error: {e}")
            yield None
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _initialize_database(self) -> bool:
        """
        Initialize database schema if needed.

        Creates the captures table and indexes if they don't exist.
        Safe to call multiple times (uses IF NOT EXISTS).

        Returns:
            True if successful, False otherwise
        """
        try:
            # Create directory if needed
            if self.config.create_directories:
                db_dir = Path(self.config.db_path).parent
                db_dir.mkdir(parents=True, exist_ok=True)

            with self._get_connection() as conn:
                if conn is None:
                    return False

                cursor = conn.cursor()

                # Create captures table with all fields
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS captures (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,

                        -- Core identification
                        timestamp TEXT NOT NULL,
                        unix_timestamp REAL NOT NULL,
                        camera_id TEXT NOT NULL,
                        image_path TEXT NOT NULL,

                        -- Camera metadata
                        exposure_time_us INTEGER,
                        analogue_gain REAL,
                        colour_gains_r REAL,
                        colour_gains_b REAL,
                        colour_temperature INTEGER,
                        digital_gain REAL,
                        sensor_temperature REAL,

                        -- Calculated values
                        lux REAL,
                        mode TEXT,
                        sun_elevation REAL,

                        -- Brightness metrics
                        brightness_mean REAL,
                        brightness_median REAL,
                        brightness_std REAL,
                        brightness_p5 REAL,
                        brightness_p25 REAL,
                        brightness_p75 REAL,
                        brightness_p95 REAL,
                        underexposed_pct REAL,
                        overexposed_pct REAL,

                        -- Weather data
                        weather_temperature REAL,
                        weather_humidity INTEGER,
                        weather_wind_speed REAL,
                        weather_wind_gust REAL,
                        weather_wind_angle INTEGER,
                        weather_rain REAL,
                        weather_rain_1h REAL,
                        weather_rain_24h REAL,
                        weather_pressure REAL,

                        -- System metrics
                        system_cpu_temp REAL,
                        system_load_1min REAL,
                        system_load_5min REAL,
                        system_load_15min REAL,

                        -- Metadata
                        created_at TEXT DEFAULT (datetime('now')),

                        -- Constraints
                        UNIQUE(timestamp, camera_id)
                    )
                """
                )

                # Create indexes for common queries
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_captures_timestamp "
                    "ON captures(unix_timestamp)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_captures_camera_time "
                    "ON captures(camera_id, unix_timestamp)"
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_captures_lux " "ON captures(lux)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_captures_mode " "ON captures(mode)")
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_captures_brightness "
                    "ON captures(brightness_mean)"
                )

                # Create schema version table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_version (
                        version INTEGER PRIMARY KEY,
                        applied_at TEXT DEFAULT (datetime('now'))
                    )
                """
                )

                # Get current schema version
                cursor.execute("SELECT MAX(version) FROM schema_version")
                row = cursor.fetchone()
                current_version = row[0] if row[0] is not None else 0

                # Apply pending migrations
                for migration_version in sorted(self.MIGRATIONS.keys()):
                    if migration_version > current_version:
                        description, statements = self.MIGRATIONS[migration_version]
                        logger.info(f"[DB] Applying migration v{migration_version}: {description}")

                        for sql in statements:
                            try:
                                cursor.execute(sql)
                                logger.debug(f"[DB] Executed: {sql[:50]}...")
                            except sqlite3.OperationalError as e:
                                # Column may already exist (e.g., fresh database)
                                if "duplicate column" in str(e).lower():
                                    logger.debug(f"[DB] Column already exists, skipping: {e}")
                                else:
                                    raise

                        # Record migration
                        cursor.execute(
                            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
                            (migration_version,),
                        )
                        logger.info(f"[DB] Migration v{migration_version} complete")

                # Ensure current version is recorded for fresh databases
                if current_version == 0:
                    cursor.execute(
                        "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
                        (self.SCHEMA_VERSION,),
                    )

                logger.info(
                    f"[DB] Initialized: {self.config.db_path} (schema v{self.SCHEMA_VERSION})"
                )
                return True

        except Exception as e:
            logger.error(f"[DB] Failed to initialize: {e}")
            return False

    def store_capture(
        self,
        image_path: str,
        metadata: Dict,
        mode: str,
        lux: Optional[float] = None,
        brightness_metrics: Optional[Dict] = None,
        weather_data: Optional[Dict] = None,
        sun_elevation: Optional[float] = None,
        system_metrics: Optional[Dict] = None,
    ) -> bool:
        """
        Store a capture record in the database.

        All parameters except image_path and mode are optional.
        Uses INSERT OR REPLACE for idempotent updates.

        Args:
            image_path: Path to the captured image
            metadata: Camera metadata dictionary (from Picamera2)
            mode: Light mode (day/night/transition)
            lux: Calculated lux value
            brightness_metrics: Brightness analysis results dict
            weather_data: Weather data dictionary
            sun_elevation: Sun elevation in degrees
            system_metrics: System monitoring data (cpu_temp, load)

        Returns:
            True if stored successfully, False otherwise
        """
        if not self.config.enabled:
            return True  # Not an error - just disabled

        try:
            # Extract timestamp
            capture_timestamp = metadata.get("capture_timestamp")
            if capture_timestamp:
                try:
                    dt = datetime.fromisoformat(capture_timestamp)
                    unix_ts = dt.timestamp()
                except (ValueError, TypeError):
                    dt = datetime.now()
                    unix_ts = dt.timestamp()
                    capture_timestamp = dt.isoformat()
            else:
                dt = datetime.now()
                unix_ts = dt.timestamp()
                capture_timestamp = dt.isoformat()

            # Extract camera metadata
            exposure_time = metadata.get("ExposureTime")
            analogue_gain = metadata.get("AnalogueGain")
            colour_gains = metadata.get("ColourGains")
            colour_temperature = metadata.get("ColourTemperature")
            digital_gain = metadata.get("DigitalGain")
            sensor_temp = metadata.get("SensorTemperature")

            # Extract brightness metrics (with fallback to empty dict)
            b = brightness_metrics or {}

            # Extract weather data (with fallback to empty dict)
            w = weather_data or {}

            # Extract system metrics (with fallback to empty dict)
            s = system_metrics or {}
            load = s.get("load", {}) or {}

            with self._get_connection() as conn:
                if conn is None:
                    return False

                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO captures (
                        timestamp, unix_timestamp, camera_id, image_path,
                        exposure_time_us, analogue_gain, colour_gains_r, colour_gains_b,
                        colour_temperature, digital_gain, sensor_temperature,
                        lux, mode, sun_elevation,
                        brightness_mean, brightness_median, brightness_std,
                        brightness_p5, brightness_p25, brightness_p75, brightness_p95,
                        underexposed_pct, overexposed_pct,
                        weather_temperature, weather_humidity, weather_wind_speed,
                        weather_wind_gust, weather_wind_angle, weather_rain,
                        weather_rain_1h, weather_rain_24h, weather_pressure,
                        system_cpu_temp, system_load_1min, system_load_5min, system_load_15min
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        capture_timestamp,
                        unix_ts,
                        self.config.camera_id,
                        image_path,
                        exposure_time,
                        analogue_gain,
                        colour_gains[0] if colour_gains else None,
                        colour_gains[1] if colour_gains else None,
                        colour_temperature,
                        digital_gain,
                        sensor_temp,
                        lux,
                        mode,
                        sun_elevation,
                        b.get("mean_brightness"),
                        b.get("median_brightness"),
                        b.get("std_brightness"),
                        b.get("percentile_5"),
                        b.get("percentile_25"),
                        b.get("percentile_75"),
                        b.get("percentile_95"),
                        b.get("underexposed_percent"),
                        b.get("overexposed_percent"),
                        w.get("temperature"),
                        w.get("humidity"),
                        w.get("wind_speed"),
                        w.get("wind_gust"),
                        w.get("wind_angle"),
                        w.get("rain"),
                        w.get("rain_1h"),
                        w.get("rain_24h"),
                        w.get("pressure"),
                        s.get("cpu_temp"),
                        load.get("1min"),
                        load.get("5min"),
                        load.get("15min"),
                    ),
                )

                logger.debug(f"[DB] Stored capture: {capture_timestamp}")
                return True

        except Exception as e:
            logger.warning(f"[DB] Failed to store capture: {e}")
            return False

    def get_captures_in_range(
        self,
        start_time: datetime,
        end_time: datetime,
        camera_id: Optional[str] = None,
    ) -> List[Dict]:
        """
        Query captures within a time range.

        Args:
            start_time: Start of range (inclusive)
            end_time: End of range (inclusive)
            camera_id: Optional camera filter (defaults to all cameras)

        Returns:
            List of capture records as dictionaries, empty list on error
        """
        if not self.config.enabled:
            return []

        try:
            with self._get_connection() as conn:
                if conn is None:
                    return []

                cursor = conn.cursor()

                if camera_id:
                    cursor.execute(
                        """
                        SELECT * FROM captures
                        WHERE unix_timestamp BETWEEN ? AND ?
                        AND camera_id = ?
                        ORDER BY unix_timestamp
                    """,
                        (start_time.timestamp(), end_time.timestamp(), camera_id),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT * FROM captures
                        WHERE unix_timestamp BETWEEN ? AND ?
                        ORDER BY unix_timestamp
                    """,
                        (start_time.timestamp(), end_time.timestamp()),
                    )

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            logger.warning(f"[DB] Failed to query captures: {e}")
            return []

    def get_captures_by_lux_range(
        self,
        min_lux: float,
        max_lux: float,
        camera_id: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict]:
        """
        Query captures within a lux range.

        Useful for finding similar lighting conditions.

        Args:
            min_lux: Minimum lux value (inclusive)
            max_lux: Maximum lux value (inclusive)
            camera_id: Optional camera filter
            limit: Maximum number of results

        Returns:
            List of capture records as dictionaries
        """
        if not self.config.enabled:
            return []

        try:
            with self._get_connection() as conn:
                if conn is None:
                    return []

                cursor = conn.cursor()

                if camera_id:
                    cursor.execute(
                        """
                        SELECT * FROM captures
                        WHERE lux BETWEEN ? AND ?
                        AND camera_id = ?
                        ORDER BY unix_timestamp DESC
                        LIMIT ?
                    """,
                        (min_lux, max_lux, camera_id, limit),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT * FROM captures
                        WHERE lux BETWEEN ? AND ?
                        ORDER BY unix_timestamp DESC
                        LIMIT ?
                    """,
                        (min_lux, max_lux, limit),
                    )

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            logger.warning(f"[DB] Failed to query by lux: {e}")
            return []

    def get_statistics(self) -> Dict:
        """
        Get database statistics.

        Returns:
            Dictionary with:
            - enabled: bool
            - total_captures: int
            - earliest: str (ISO timestamp)
            - latest: str (ISO timestamp)
            - db_path: str
            - db_size_mb: float
            - error: str (if any error occurred)
        """
        if not self.config.enabled:
            return {"enabled": False}

        try:
            with self._get_connection() as conn:
                if conn is None:
                    return {"enabled": True, "error": "connection_failed"}

                cursor = conn.cursor()

                cursor.execute("SELECT COUNT(*) FROM captures")
                total_count = cursor.fetchone()[0]

                cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM captures")
                row = cursor.fetchone()

                # Get database file size
                db_size_mb = 0.0
                if os.path.exists(self.config.db_path):
                    db_size_mb = os.path.getsize(self.config.db_path) / (1024 * 1024)

                return {
                    "enabled": True,
                    "total_captures": total_count,
                    "earliest": row[0],
                    "latest": row[1],
                    "db_path": self.config.db_path,
                    "db_size_mb": round(db_size_mb, 2),
                }

        except Exception as e:
            logger.warning(f"[DB] Failed to get statistics: {e}")
            return {"enabled": True, "error": str(e)}

    def get_hourly_averages(
        self,
        start_time: datetime,
        end_time: datetime,
        camera_id: Optional[str] = None,
    ) -> List[Dict]:
        """
        Get hourly averages for key metrics.

        Useful for generating summary graphs.

        Args:
            start_time: Start of range
            end_time: End of range
            camera_id: Optional camera filter

        Returns:
            List of hourly averages with hour, avg_lux, avg_brightness, etc.
        """
        if not self.config.enabled:
            return []

        try:
            with self._get_connection() as conn:
                if conn is None:
                    return []

                cursor = conn.cursor()

                query = """
                    SELECT
                        strftime('%Y-%m-%d %H:00:00', timestamp) as hour,
                        COUNT(*) as capture_count,
                        AVG(lux) as avg_lux,
                        AVG(brightness_mean) as avg_brightness,
                        AVG(exposure_time_us) as avg_exposure_us,
                        AVG(analogue_gain) as avg_gain,
                        AVG(weather_temperature) as avg_temperature,
                        AVG(weather_humidity) as avg_humidity
                    FROM captures
                    WHERE unix_timestamp BETWEEN ? AND ?
                """

                params = [start_time.timestamp(), end_time.timestamp()]

                if camera_id:
                    query += " AND camera_id = ?"
                    params.append(camera_id)

                query += " GROUP BY hour ORDER BY hour"

                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            logger.warning(f"[DB] Failed to get hourly averages: {e}")
            return []

    def close(self):
        """Close database connections."""
        if self._persistent_conn:
            try:
                self._persistent_conn.close()
                self._persistent_conn = None
            except Exception:
                pass
        logger.debug("[DB] Database closed")


# Convenience function for quick testing
if __name__ == "__main__":
    import sys

    # Test with in-memory database
    test_config = {
        "database": {
            "enabled": True,
            "path": ":memory:",
            "create_directories": False,
        },
        "output": {"project_name": "test_camera"},
    }

    db = CaptureDatabase(test_config)
    print(f"Database initialized: {db.get_statistics()}")

    # Test storing a capture
    test_metadata = {
        "capture_timestamp": datetime.now().isoformat(),
        "ExposureTime": 100000,
        "AnalogueGain": 2.5,
        "ColourGains": [1.8, 2.0],
        "ColourTemperature": 5500,
    }

    test_brightness = {
        "mean_brightness": 125.5,
        "median_brightness": 128.0,
        "std_brightness": 45.2,
        "percentile_5": 20.0,
        "percentile_95": 235.0,
        "underexposed_percent": 2.5,
        "overexposed_percent": 1.0,
    }

    test_weather = {
        "temperature": -5.2,
        "humidity": 85,
        "wind_speed": 15,
        "rain": 0.0,
        "pressure": 1015,
    }

    result = db.store_capture(
        image_path="/test/image.jpg",
        metadata=test_metadata,
        mode="transition",
        lux=50.0,
        brightness_metrics=test_brightness,
        weather_data=test_weather,
        sun_elevation=15.5,
    )

    print(f"Store result: {result}")
    print(f"Statistics: {db.get_statistics()}")
