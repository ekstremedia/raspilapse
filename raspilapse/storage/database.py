"""
Database module for Raspilapse timelapse capture storage.

Provides SQLite storage for capture metadata, brightness analysis, and weather data,
enabling historical analysis, graphs, and exposure planning.

A capture row deliberately outlives its JPEG. cleanup_old_images.sh deletes
images after 7 days; rows are kept for database.retention_days because they hold
the lux, brightness and weather history the graphs are built from. Only
image_path goes stale.

Usage:
    from raspilapse.storage.database import CaptureDatabase

    db = CaptureDatabase(config)
    db.store_capture(image_path, metadata, mode, lux, brightness_metrics, weather_data)
    captures = db.get_captures_in_range(start_time, end_time)

    python3 -m raspilapse.cli.db --stats
    python3 -m raspilapse.cli.db --prune
"""

import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Handle imports for both module and script execution
from raspilapse.logging_setup import configure_logging, get_logger

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
        # 0 (and a missing key) means keep everything, so existing installs
        # never lose rows just by pulling this change. The example config ships
        # 180 days: a full seasonal cycle, which is the point of the graphs.
        self.retention_days = int(self.db_config.get("retention_days", 0) or 0)

        # Get camera_id from project_name
        self.camera_id = config.get("output", {}).get("project_name", "unknown")


CAPTURES_DDL = """
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

# Indexes for the queries that actually run. Deliberately none on lux, mode or
# brightness_mean -- see migration 5.
CAPTURES_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_captures_timestamp ON captures(unix_timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_captures_camera_time ON captures(camera_id, unix_timestamp)",
)


def apply_schema(conn: sqlite3.Connection, wal: bool = True) -> bool:
    """
    Create and migrate the whole schema on an existing connection.

    The single definition of the schema. UploadService used to carry its own
    copy of the upload_queue DDL, and the two had drifted -- whichever process
    opened the file first decided which indexes existed, which left one camera
    pinned at schema v3 with the v4 index missing.

    Args:
        conn: An open connection. Not closed here.
        wal: Enable WAL journalling. False for :memory:, which cannot use it.

    Returns:
        True if the schema is present and up to date
    """
    cursor = conn.cursor()

    if wal:
        # WAL lets the dashboard and graph scripts read while the capture loop
        # writes, instead of contending for a lock every 30 seconds. It is a
        # persistent property of the file, so this sticks after the first run.
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error as e:
            # Fails while another connection is open; the rollback journal
            # still works, so this is not fatal.
            logger.warning(f"[DB] Could not enable WAL: {e}")

    cursor.execute(CAPTURES_DDL)
    for statement in CAPTURES_INDEXES:
        cursor.execute(statement)

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT DEFAULT (datetime('now'))
        )
        """
    )

    cursor.execute("SELECT MAX(version) FROM schema_version")
    row = cursor.fetchone()
    current_version = row[0] if row and row[0] is not None else 0

    for version in sorted(CaptureDatabase.MIGRATIONS):
        if version <= current_version:
            continue
        description, statements = CaptureDatabase.MIGRATIONS[version]
        logger.info(f"[DB] Applying migration v{version}: {description}")

        for sql in statements:
            try:
                cursor.execute(sql)
            except sqlite3.OperationalError as e:
                # Fresh databases already have the column the migration adds.
                if "duplicate column" in str(e).lower():
                    logger.debug(f"[DB] Column already exists, skipping: {e}")
                else:
                    raise

        cursor.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (version,))
        logger.info(f"[DB] Migration v{version} complete")

    if current_version == 0:
        cursor.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
            (CaptureDatabase.SCHEMA_VERSION,),
        )

    conn.commit()
    return True


class CaptureDatabase:
    """
    SQLite database for timelapse capture storage.

    Thread-safe, with connection management and graceful error handling.
    Designed to never crash the timelapse if database operations fail.

    Attributes:
        config: DatabaseConfig instance with settings
        SCHEMA_VERSION: Current database schema version
    """

    SCHEMA_VERSION = 5  # Bumped for dropping three unused captures indexes

    # Migration definitions: version -> (description, SQL statements)
    MIGRATIONS = {
        2: (
            "Add sun_elevation column for Arctic-aware ML",
            [
                "ALTER TABLE captures ADD COLUMN sun_elevation REAL",
            ],
        ),
        3: (
            "Add upload_queue table for retry mechanism",
            [
                """CREATE TABLE IF NOT EXISTS upload_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_date DATE NOT NULL UNIQUE,
                    video_path TEXT NOT NULL,
                    keogram_path TEXT,
                    slitscan_path TEXT,
                    status TEXT DEFAULT 'pending',
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 5,
                    created_at TEXT DEFAULT (datetime('now')),
                    last_attempt_at TEXT,
                    next_retry_at TEXT,
                    completed_at TEXT,
                    last_error TEXT,
                    server_response TEXT
                )""",
                "CREATE INDEX IF NOT EXISTS idx_upload_queue_status ON upload_queue(status)",
            ],
        ),
        4: (
            "Add composite index for retry-queue scans",
            [
                "CREATE INDEX IF NOT EXISTS idx_upload_queue_status_retry "
                "ON upload_queue(status, next_retry_at)",
            ],
        ),
        5: (
            "Drop three unused captures indexes",
            [
                # Measured with dbstat on a 515k-row database: these three cost
                # 26 MB (a third of all index space) and three extra B-tree
                # writes on every capture, twice a minute, forever.
                #
                #   idx_captures_lux        - get_captures_by_lux_range() has no
                #                             production caller, only tests
                #   idx_captures_brightness - nothing filters or sorts on it
                #   idx_captures_mode       - three distinct values across 515k
                #                             rows; SQLite scans anyway
                "DROP INDEX IF EXISTS idx_captures_lux",
                "DROP INDEX IF EXISTS idx_captures_brightness",
                "DROP INDEX IF EXISTS idx_captures_mode",
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
            # NORMAL is the standard pairing with WAL: durable across process
            # crashes, and only at risk from a power cut mid-write. On an SD
            # card the fsync savings matter. Not persistent, unlike the
            # journal_mode set in _initialize_database, so set it per connection.
            conn.execute("PRAGMA synchronous=NORMAL")
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
        Create and migrate the schema on this instance's connection.

        Safe to call repeatedly.

        Returns:
            True if successful, False otherwise
        """
        try:
            if self.config.create_directories and self.config.db_path != ":memory:":
                Path(self.config.db_path).parent.mkdir(parents=True, exist_ok=True)

            with self._get_connection() as conn:
                if conn is None:
                    return False
                return apply_schema(conn, wal=self.config.db_path != ":memory:")

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

    def get_last_capture(self, camera_id: Optional[str] = None) -> Optional[Dict]:
        """
        Get the most recent capture from the database.

        Useful for seeding exposure settings on startup after a reboot/restart.
        Only returns captures with valid exposure data and good brightness (not overexposed).

        Args:
            camera_id: Optional camera filter (defaults to configured camera)

        Returns:
            Dictionary with capture data, or None if no valid capture found
        """
        if not self.config.enabled:
            return None

        try:
            with self._get_connection() as conn:
                if conn is None:
                    return None

                cursor = conn.cursor()

                # Use configured camera_id if not specified
                cam_id = camera_id or self.config.camera_id

                # Get the most recent capture with valid exposure data and good brightness
                # Exclude overexposed frames (brightness > 180 or overexposed_pct > 10)
                cursor.execute(
                    """
                    SELECT * FROM captures
                    WHERE camera_id = ?
                    AND exposure_time_us IS NOT NULL
                    AND analogue_gain IS NOT NULL
                    AND (brightness_mean IS NULL OR brightness_mean < 180)
                    AND (overexposed_pct IS NULL OR overexposed_pct < 10)
                    ORDER BY unix_timestamp DESC
                    LIMIT 1
                """,
                    (cam_id,),
                )

                row = cursor.fetchone()
                if row:
                    return dict(row)
                return None

        except Exception as e:
            logger.warning(f"[DB] Failed to get last capture: {e}")
            return None

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

    def prune(self, retention_days: Optional[int] = None, dry_run: bool = False) -> Dict[str, int]:
        """
        Delete rows older than the retention window.

        Note that a capture row outliving its JPEG is intended, not a bug.
        cleanup_old_images.sh removes images after 7 days; the rows are kept far
        longer because they hold the lux, brightness and weather history the
        graphs are built from. Only image_path goes stale.

        Args:
            retention_days: Override the configured window. 0 means keep everything.
            dry_run: Count what would be deleted without deleting it.

        Returns:
            {"captures": n, "upload_queue": n}
        """
        days = self.config.retention_days if retention_days is None else int(retention_days)
        result = {"captures": 0, "upload_queue": 0}

        if days <= 0:
            logger.debug("[DB] Retention disabled (retention_days=0), nothing to prune")
            return result

        try:
            with self._get_connection() as conn:
                if conn is None:
                    return result

                cursor = conn.cursor()
                cutoff = f"-{days} days"

                cursor.execute(
                    "SELECT COUNT(*) FROM captures WHERE unix_timestamp < "
                    "strftime('%s', 'now', ?)",
                    (cutoff,),
                )
                result["captures"] = cursor.fetchone()[0]

                # Terminal queue rows are only kept as history; three months is
                # plenty to answer "did last quarter's uploads go through".
                cursor.execute(
                    "SELECT COUNT(*) FROM upload_queue "
                    "WHERE status IN ('success', 'cancelled') "
                    "AND completed_at IS NOT NULL "
                    "AND completed_at < datetime('now', '-90 days')"
                )
                result["upload_queue"] = cursor.fetchone()[0]

                if dry_run:
                    logger.info(
                        f"[DB] Would prune {result['captures']} capture(s) older than "
                        f"{days} days and {result['upload_queue']} completed upload(s)"
                    )
                    return result

                cursor.execute(
                    "DELETE FROM captures WHERE unix_timestamp < strftime('%s', 'now', ?)",
                    (cutoff,),
                )
                cursor.execute(
                    "DELETE FROM upload_queue "
                    "WHERE status IN ('success', 'cancelled') "
                    "AND completed_at IS NOT NULL "
                    "AND completed_at < datetime('now', '-90 days')"
                )

                # Fold the WAL back into the main file, otherwise a large
                # delete leaves a -wal that never shrinks.
                if self.config.db_path != ":memory:":
                    try:
                        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    except sqlite3.Error as e:
                        logger.debug(f"[DB] WAL checkpoint skipped: {e}")

                if result["captures"] or result["upload_queue"]:
                    logger.info(
                        f"[DB] Pruned {result['captures']} capture(s) older than {days} days "
                        f"and {result['upload_queue']} completed upload(s)"
                    )
                return result

        except Exception as e:
            logger.warning(f"[DB] Prune failed: {e}")
            return result

    def vacuum(self) -> bool:
        """
        Rebuild the database file to reclaim space freed by prune().

        Needs as much free disk as the database currently occupies and takes
        minutes on a large file, which is why it is never run from the timer.
        """
        try:
            with self._get_connection() as conn:
                if conn is None:
                    return False
                conn.execute("VACUUM")
                logger.info("[DB] Vacuum complete")
                return True
        except Exception as e:
            logger.warning(f"[DB] Vacuum failed: {e}")
            return False

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


def main() -> int:
    """Database maintenance CLI.

    Run by raspilapse-cleanup.service alongside the image cleanup, so both
    kinds of expired data go at the same time.
    """
    import argparse

    import yaml

    parser = argparse.ArgumentParser(
        description="Raspilapse database maintenance",
        epilog=(
            "Examples:\n"
            "  python3 -m raspilapse.cli.db --stats\n"
            "  python3 -m raspilapse.cli.db --prune --dry-run\n"
            "  python3 -m raspilapse.cli.db --prune\n"
            "  python3 -m raspilapse.cli.db --prune --vacuum   # reclaims disk, slow\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-c", "--config", default="config/config.yml", help="Config file")
    parser.add_argument("--stats", action="store_true", help="Show database statistics")
    parser.add_argument(
        "--prune", action="store_true", help="Delete rows past database.retention_days"
    )
    parser.add_argument("--dry-run", action="store_true", help="With --prune: only count")
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Rebuild the file to reclaim space. Needs free disk equal to the "
        "database size and takes minutes; never run from the timer.",
    )
    parser.add_argument("--retention-days", type=int, help="Override database.retention_days")
    args = parser.parse_args()

    configure_logging(args.config)

    try:
        with open(args.config) as f:
            config = yaml.safe_load(f) or {}
    except OSError as e:
        print(f"Error: could not read {args.config}: {e}")
        return 1

    db = CaptureDatabase(config)
    if not db.config.enabled:
        print("Database is disabled in config; nothing to do.")
        return 0

    if args.stats or not (args.prune or args.vacuum):
        stats = db.get_statistics()
        print(f"Database:  {db.config.db_path}")
        print(f"Captures:  {stats.get('total_captures', 0):,}")
        print(f"Earliest:  {stats.get('earliest') or '-'}")
        print(f"Latest:    {stats.get('latest') or '-'}")
        print(f"Size:      {stats.get('db_size_mb', 0):.1f} MB")
        retention = db.config.retention_days
        print(f"Retention: {retention} days" if retention else "Retention: keep everything")
        if not (args.prune or args.vacuum):
            return 0

    if args.prune:
        removed = db.prune(retention_days=args.retention_days, dry_run=args.dry_run)
        verb = "Would delete" if args.dry_run else "Deleted"
        print(f"{verb} {removed['captures']:,} capture(s), {removed['upload_queue']} queue row(s)")

    if args.vacuum:
        print("Vacuuming (this can take several minutes)...")
        if not db.vacuum():
            return 1
        print(f"Size now:  {db.get_statistics().get('db_size_mb', 0):.1f} MB")

    return 0


if __name__ == "__main__":
    sys.exit(main())
