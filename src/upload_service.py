"""
Upload Service - Handles video uploads with retry queue management.

Provides:
- upload_to_server() - Upload video/keogram/slitscan to server
- queue_upload() - Add failed upload to retry queue
- get_pending_uploads() - List pending/failed uploads
- get_upload_history() - List completed uploads
- mark_upload_success/failed() - Update upload status
- retry_single_upload() - Retry one upload
- process_retry_queue() - Process all due for retry

Exponential backoff: 5min, 10min, 20min, 40min, 80min... capped at 24h
"""

import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# Add project root to path for imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import logging


# Setup fallback logger
def _get_fallback_logger(name):
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


try:
    from src.logging_config import get_logger as _get_project_logger
except ImportError:
    try:
        from logging_config import get_logger as _get_project_logger
    except ImportError:
        _get_project_logger = None


def get_logger(name, config_path=None):
    """Get logger, using project logger if config_path provided, else fallback."""
    if config_path and _get_project_logger:
        return _get_project_logger(name, config_path)
    return _get_fallback_logger(name)


# Exponential backoff settings
BASE_RETRY_DELAY_MINUTES = 5
MAX_RETRY_DELAY_MINUTES = 24 * 60  # 24 hours


class UploadService:
    """
    Service for uploading timelapse videos with retry queue support.

    Manages upload attempts and maintains a SQLite queue for retries.
    """

    def __init__(self, config: Dict, config_path: str = None):
        """
        Initialize the upload service.

        Args:
            config: Full configuration dictionary
            config_path: Optional path to config file for logger
        """
        self.config = config
        self.config_path = config_path
        self.upload_config = config.get("video_upload", {})
        self.db_config = config.get("database", {})
        self.db_path = self.db_config.get("path", "data/timelapse.db")

        # Make db_path absolute relative to config file location
        if config_path and not os.path.isabs(self.db_path):
            config_dir = os.path.dirname(os.path.abspath(config_path))
            project_dir = os.path.dirname(config_dir)
            self.db_path = os.path.join(project_dir, self.db_path)

        self.camera_id = self.upload_config.get(
            "camera_id", config.get("output", {}).get("project_name", "unknown")
        )
        self.logger = get_logger("upload_service", config_path)
        self._persistent_conn = None  # For in-memory databases

        # Ensure database has the upload_queue table
        self._ensure_table_exists()

    @contextmanager
    def _get_connection(self):
        """
        Context manager for database connections.

        For in-memory databases, uses a persistent connection.
        For file databases, creates a fresh connection each time for thread safety.
        """
        # For in-memory databases, use persistent connection
        if self.db_path == ":memory:":
            if self._persistent_conn is None:
                try:
                    self._persistent_conn = sqlite3.connect(
                        ":memory:",
                        timeout=10.0,
                    )
                    self._persistent_conn.row_factory = sqlite3.Row
                except sqlite3.Error as e:
                    self.logger.warning(f"[Upload] Connection error: {e}")
                    yield None
                    return
            yield self._persistent_conn
            return

        # For file databases, create fresh connection
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            yield conn
        except sqlite3.Error as e:
            self.logger.warning(f"[Upload] Database connection error: {e}")
            yield None
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _ensure_table_exists(self) -> bool:
        """Ensure the upload_queue table exists."""
        try:
            with self._get_connection() as conn:
                if conn is None:
                    return False

                cursor = conn.cursor()
                cursor.execute(
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
                    )"""
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_upload_queue_status ON upload_queue(status)"
                )
                conn.commit()
                return True
        except Exception as e:
            self.logger.error(f"[Upload] Failed to ensure table exists: {e}")
            return False

    def upload_to_server(
        self,
        video_path: Path,
        keogram_path: Optional[Path],
        slitscan_path: Optional[Path],
        date: str,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Upload timelapse video and images to the webserver.

        Args:
            video_path: Path to video file
            keogram_path: Optional path to keogram image
            slitscan_path: Optional path to slitscan image
            date: Date string (YYYY-MM-DD)

        Returns:
            Tuple of (success, error_message, server_response)
        """
        url = self.upload_config.get("url")
        api_key = self.upload_config.get("api_key")

        if not url or not api_key:
            return False, "Upload URL or API key not configured", None

        self.logger.info(f"[Upload] Uploading to: {url}")
        self.logger.info(f"[Upload] Video: {video_path}")
        self.logger.info(f"[Upload] Date: {date}")

        files = {}
        file_handles = []

        try:
            # Open video file
            if video_path and Path(video_path).exists():
                f = open(video_path, "rb")
                file_handles.append(f)
                files["video"] = f
            else:
                return False, f"Video file not found: {video_path}", None

            # Open keogram if exists
            if keogram_path and Path(keogram_path).exists():
                f = open(keogram_path, "rb")
                file_handles.append(f)
                files["keogram"] = f
                self.logger.info(f"[Upload] Keogram: {keogram_path}")

            # Open slitscan if exists
            if slitscan_path and Path(slitscan_path).exists():
                f = open(slitscan_path, "rb")
                file_handles.append(f)
                files["slitscan"] = f
                self.logger.info(f"[Upload] Slitscan: {slitscan_path}")

            # Prepare request
            data = {
                "title": Path(video_path).name,
                "date": date,
                "camera_id": self.camera_id,
            }

            headers = {
                "Authorization": f"Bearer {api_key}",
            }

            self.logger.info(f"[Upload] Uploading files: {list(files.keys())}")

            # Send POST request
            response = requests.post(url, files=files, data=data, headers=headers, timeout=300)

            if response.status_code == 200:
                self.logger.info("[Upload] Upload successful!")
                return True, None, response.text
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                self.logger.error(f"[Upload] Upload failed: {error_msg}")
                return False, error_msg, None

        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            self.logger.error(f"[Upload] Request failed: {error_msg}")
            return False, error_msg, None
        except Exception as e:
            error_msg = str(e)
            self.logger.error(f"[Upload] Upload error: {error_msg}")
            return False, error_msg, None
        finally:
            for f in file_handles:
                try:
                    f.close()
                except Exception:
                    pass

    def queue_upload(
        self,
        video_path: str,
        keogram_path: Optional[str],
        slitscan_path: Optional[str],
        video_date: str,
        max_retries: int = 5,
    ) -> Optional[int]:
        """
        Add an upload to the retry queue.

        Args:
            video_path: Path to video file
            keogram_path: Optional path to keogram
            slitscan_path: Optional path to slitscan
            video_date: Date string (YYYY-MM-DD)
            max_retries: Maximum retry attempts (default 5)

        Returns:
            Queue ID if successful, None otherwise
        """
        try:
            with self._get_connection() as conn:
                if conn is None:
                    return None

                cursor = conn.cursor()

                # Calculate next retry time (immediate for new entries)
                next_retry = datetime.now().isoformat()

                # Use INSERT OR REPLACE to handle re-queueing same date
                cursor.execute(
                    """INSERT OR REPLACE INTO upload_queue
                       (video_date, video_path, keogram_path, slitscan_path,
                        status, retry_count, max_retries, created_at, next_retry_at)
                       VALUES (?, ?, ?, ?, 'pending', 0, ?, datetime('now'), ?)""",
                    (video_date, video_path, keogram_path, slitscan_path, max_retries, next_retry),
                )
                conn.commit()

                queue_id = cursor.lastrowid
                self.logger.info(f"[Upload] Queued upload for {video_date} (id={queue_id})")
                return queue_id

        except Exception as e:
            self.logger.error(f"[Upload] Failed to queue upload: {e}")
            return None

    def get_pending_uploads(self) -> List[Dict]:
        """
        Get all pending/failed uploads.

        Returns:
            List of upload records with status 'pending' or 'failed'
        """
        try:
            with self._get_connection() as conn:
                if conn is None:
                    return []

                cursor = conn.cursor()
                cursor.execute(
                    """SELECT * FROM upload_queue
                       WHERE status IN ('pending', 'failed')
                       ORDER BY created_at DESC"""
                )
                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.warning(f"[Upload] Failed to get pending uploads: {e}")
            return []

    def get_upload_history(self, limit: int = 50) -> List[Dict]:
        """
        Get upload history (all statuses).

        Args:
            limit: Maximum number of records to return

        Returns:
            List of upload records
        """
        try:
            with self._get_connection() as conn:
                if conn is None:
                    return []

                cursor = conn.cursor()
                cursor.execute(
                    """SELECT * FROM upload_queue
                       ORDER BY created_at DESC
                       LIMIT ?""",
                    (limit,),
                )
                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.warning(f"[Upload] Failed to get upload history: {e}")
            return []

    def get_upload_by_id(self, upload_id: int) -> Optional[Dict]:
        """
        Get a single upload by ID.

        Args:
            upload_id: Upload queue ID

        Returns:
            Upload record or None
        """
        try:
            with self._get_connection() as conn:
                if conn is None:
                    return None

                cursor = conn.cursor()
                cursor.execute("SELECT * FROM upload_queue WHERE id = ?", (upload_id,))
                row = cursor.fetchone()
                return dict(row) if row else None

        except Exception as e:
            self.logger.warning(f"[Upload] Failed to get upload {upload_id}: {e}")
            return None

    def mark_upload_success(self, upload_id: int, server_response: str = None) -> bool:
        """
        Mark an upload as successful.

        Args:
            upload_id: Upload queue ID
            server_response: Optional server response JSON

        Returns:
            True if successful
        """
        try:
            with self._get_connection() as conn:
                if conn is None:
                    return False

                cursor = conn.cursor()
                cursor.execute(
                    """UPDATE upload_queue
                       SET status = 'success',
                           completed_at = datetime('now'),
                           last_attempt_at = datetime('now'),
                           server_response = ?,
                           last_error = NULL
                       WHERE id = ?""",
                    (server_response, upload_id),
                )
                conn.commit()

                self.logger.info(f"[Upload] Marked upload {upload_id} as success")
                return True

        except Exception as e:
            self.logger.error(f"[Upload] Failed to mark success: {e}")
            return False

    def mark_upload_failed(self, upload_id: int, error: str) -> bool:
        """
        Mark an upload as failed and schedule next retry.

        Uses exponential backoff for retry timing.

        Args:
            upload_id: Upload queue ID
            error: Error message

        Returns:
            True if successful
        """
        try:
            with self._get_connection() as conn:
                if conn is None:
                    return False

                cursor = conn.cursor()

                # Get current retry count and max_retries
                cursor.execute(
                    "SELECT retry_count, max_retries FROM upload_queue WHERE id = ?",
                    (upload_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return False

                retry_count = row["retry_count"] + 1
                max_retries = row["max_retries"]

                # Calculate next retry with exponential backoff
                delay_minutes = min(
                    BASE_RETRY_DELAY_MINUTES * (2 ** (retry_count - 1)),
                    MAX_RETRY_DELAY_MINUTES,
                )
                next_retry = datetime.now() + timedelta(minutes=delay_minutes)

                # Determine status
                if retry_count >= max_retries:
                    status = "failed"
                    self.logger.warning(
                        f"[Upload] Upload {upload_id} exhausted retries ({retry_count}/{max_retries})"
                    )
                else:
                    status = "pending"
                    self.logger.info(
                        f"[Upload] Upload {upload_id} failed, retry {retry_count}/{max_retries} "
                        f"scheduled in {delay_minutes} minutes"
                    )

                cursor.execute(
                    """UPDATE upload_queue
                       SET status = ?,
                           retry_count = ?,
                           last_attempt_at = datetime('now'),
                           next_retry_at = ?,
                           last_error = ?
                       WHERE id = ?""",
                    (status, retry_count, next_retry.isoformat(), error, upload_id),
                )
                conn.commit()

                return True

        except Exception as e:
            self.logger.error(f"[Upload] Failed to mark failed: {e}")
            return False

    def cancel_upload(self, upload_id: int) -> bool:
        """
        Cancel/remove an upload from the queue.

        Args:
            upload_id: Upload queue ID

        Returns:
            True if successful
        """
        try:
            with self._get_connection() as conn:
                if conn is None:
                    return False

                cursor = conn.cursor()
                cursor.execute("DELETE FROM upload_queue WHERE id = ?", (upload_id,))
                conn.commit()

                self.logger.info(f"[Upload] Cancelled upload {upload_id}")
                return True

        except Exception as e:
            self.logger.error(f"[Upload] Failed to cancel upload: {e}")
            return False

    def retry_single_upload(self, upload_id: int, force: bool = False) -> Tuple[bool, str]:
        """
        Retry a single upload.

        Args:
            upload_id: Upload queue ID
            force: If True, retry even if not due yet

        Returns:
            Tuple of (success, message)
        """
        upload = self.get_upload_by_id(upload_id)
        if not upload:
            return False, f"Upload {upload_id} not found"

        if upload["status"] == "success":
            return False, "Upload already completed successfully"

        # Check if due for retry (unless forced)
        if not force and upload["next_retry_at"]:
            next_retry = datetime.fromisoformat(upload["next_retry_at"])
            if datetime.now() < next_retry:
                return False, f"Not due for retry until {next_retry}"

        # Mark as uploading
        try:
            with self._get_connection() as conn:
                if conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE upload_queue SET status = 'uploading' WHERE id = ?",
                        (upload_id,),
                    )
                    conn.commit()
        except Exception:
            pass

        # Attempt upload
        video_path = Path(upload["video_path"])
        keogram_path = Path(upload["keogram_path"]) if upload["keogram_path"] else None
        slitscan_path = Path(upload["slitscan_path"]) if upload["slitscan_path"] else None

        success, error, response = self.upload_to_server(
            video_path, keogram_path, slitscan_path, upload["video_date"]
        )

        if success:
            self.mark_upload_success(upload_id, response)
            return True, "Upload successful"
        else:
            self.mark_upload_failed(upload_id, error)
            return False, error or "Unknown error"

    def process_retry_queue(self, force: bool = False) -> Dict[str, Any]:
        """
        Process all uploads due for retry.

        Args:
            force: If True, retry all pending regardless of schedule

        Returns:
            Summary dict with processed, success, failed counts
        """
        results = {"processed": 0, "success": 0, "failed": 0, "skipped": 0, "errors": []}

        pending = self.get_pending_uploads()
        self.logger.info(f"[Upload] Processing retry queue: {len(pending)} pending uploads")

        for upload in pending:
            upload_id = upload["id"]

            # Check if due for retry
            if not force and upload["next_retry_at"]:
                try:
                    next_retry = datetime.fromisoformat(upload["next_retry_at"])
                    if datetime.now() < next_retry:
                        results["skipped"] += 1
                        continue
                except ValueError:
                    pass  # Invalid date, try anyway

            results["processed"] += 1
            success, message = self.retry_single_upload(upload_id, force=True)

            if success:
                results["success"] += 1
            else:
                results["failed"] += 1
                results["errors"].append({"id": upload_id, "error": message})

        self.logger.info(
            f"[Upload] Queue processing complete: "
            f"{results['success']} success, {results['failed']} failed, "
            f"{results['skipped']} skipped"
        )

        return results

    def get_queue_stats(self) -> Dict[str, int]:
        """
        Get upload queue statistics.

        Returns:
            Dict with pending, success, failed, total counts
        """
        try:
            with self._get_connection() as conn:
                if conn is None:
                    return {"pending": 0, "success": 0, "failed": 0, "total": 0}

                cursor = conn.cursor()
                cursor.execute(
                    """SELECT status, COUNT(*) as count
                       FROM upload_queue
                       GROUP BY status"""
                )

                stats = {"pending": 0, "uploading": 0, "success": 0, "failed": 0, "total": 0}
                for row in cursor.fetchall():
                    stats[row["status"]] = row["count"]
                    stats["total"] += row["count"]

                return stats

        except Exception as e:
            self.logger.warning(f"[Upload] Failed to get stats: {e}")
            return {"pending": 0, "success": 0, "failed": 0, "total": 0}
