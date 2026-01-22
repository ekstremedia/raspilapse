"""Tests for upload_service module."""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.upload_service import UploadService, BASE_RETRY_DELAY_MINUTES, MAX_RETRY_DELAY_MINUTES


@pytest.fixture
def upload_config():
    """Create test upload configuration."""
    return {
        "database": {
            "enabled": True,
            "path": ":memory:",
        },
        "video_upload": {
            "enabled": True,
            "url": "https://test.example.com/upload",
            "api_key": "test-api-key-123",
            "camera_id": "test_camera",
        },
        "output": {
            "project_name": "test_project",
        },
    }


@pytest.fixture
def temp_db_config():
    """Create temp file database configuration."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        config_path = os.path.join(tmpdir, "config", "config.yml")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)

        # Create a dummy config file
        with open(config_path, "w") as f:
            f.write("# test config\n")

        yield {
            "config": {
                "database": {
                    "enabled": True,
                    "path": db_path,
                },
                "video_upload": {
                    "enabled": True,
                    "url": "https://test.example.com/upload",
                    "api_key": "test-api-key-123",
                    "camera_id": "test_camera",
                },
                "output": {
                    "project_name": "test_project",
                },
            },
            "config_path": config_path,
            "tmpdir": tmpdir,
        }


@pytest.fixture
def temp_video_file():
    """Create a temporary video file."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(b"fake video content")
        yield f.name
    os.unlink(f.name)


class TestUploadServiceInit:
    """Test UploadService initialization."""

    def test_init_basic(self, upload_config):
        """Test basic initialization."""
        service = UploadService(upload_config)
        assert service.camera_id == "test_camera"
        assert service.upload_config["url"] == "https://test.example.com/upload"

    def test_init_creates_table(self, temp_db_config):
        """Test that initialization creates upload_queue table."""
        config = temp_db_config["config"]
        config["database"]["path"] = temp_db_config["config"]["database"]["path"]
        service = UploadService(config, temp_db_config["config_path"])

        # Verify table exists by querying it
        pending = service.get_pending_uploads()
        assert pending == []

    def test_init_default_camera_id(self):
        """Test default camera_id from project_name."""
        config = {
            "database": {"enabled": True, "path": ":memory:"},
            "output": {"project_name": "my_camera"},
        }
        service = UploadService(config)
        assert service.camera_id == "my_camera"


class TestQueueUpload:
    """Test queue_upload method."""

    def test_queue_upload_basic(self, upload_config, temp_video_file):
        """Test basic upload queuing."""
        service = UploadService(upload_config)

        queue_id = service.queue_upload(
            video_path=temp_video_file,
            keogram_path=None,
            slitscan_path=None,
            video_date="2026-01-21",
        )

        assert queue_id is not None
        assert queue_id > 0

    def test_queue_upload_with_images(self, upload_config, temp_video_file):
        """Test queuing with keogram and slitscan."""
        service = UploadService(upload_config)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as kg:
            kg.write(b"fake keogram")
            keogram_path = kg.name

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as ss:
            ss.write(b"fake slitscan")
            slitscan_path = ss.name

        try:
            queue_id = service.queue_upload(
                video_path=temp_video_file,
                keogram_path=keogram_path,
                slitscan_path=slitscan_path,
                video_date="2026-01-21",
            )

            assert queue_id is not None

            # Verify all paths are stored
            upload = service.get_upload_by_id(queue_id)
            assert upload["keogram_path"] == keogram_path
            assert upload["slitscan_path"] == slitscan_path
        finally:
            os.unlink(keogram_path)
            os.unlink(slitscan_path)

    def test_queue_upload_replaces_existing(self, upload_config, temp_video_file):
        """Test that queueing same date replaces existing entry."""
        service = UploadService(upload_config)

        # Queue first upload
        id1 = service.queue_upload(
            video_path="/path/to/video1.mp4",
            keogram_path=None,
            slitscan_path=None,
            video_date="2026-01-21",
        )

        # Queue second upload with same date
        id2 = service.queue_upload(
            video_path="/path/to/video2.mp4",
            keogram_path=None,
            slitscan_path=None,
            video_date="2026-01-21",
        )

        # Should have replaced, not added
        pending = service.get_pending_uploads()
        assert len(pending) == 1
        assert pending[0]["video_path"] == "/path/to/video2.mp4"

    def test_queue_upload_custom_max_retries(self, upload_config, temp_video_file):
        """Test custom max_retries setting."""
        service = UploadService(upload_config)

        queue_id = service.queue_upload(
            video_path=temp_video_file,
            keogram_path=None,
            slitscan_path=None,
            video_date="2026-01-21",
            max_retries=10,
        )

        upload = service.get_upload_by_id(queue_id)
        assert upload["max_retries"] == 10


class TestGetPendingUploads:
    """Test get_pending_uploads method."""

    def test_empty_queue(self, upload_config):
        """Test getting empty pending list."""
        service = UploadService(upload_config)
        pending = service.get_pending_uploads()
        assert pending == []

    def test_pending_uploads(self, upload_config):
        """Test getting pending uploads."""
        service = UploadService(upload_config)

        # Queue multiple uploads
        for i in range(3):
            service.queue_upload(
                video_path=f"/path/to/video{i}.mp4",
                keogram_path=None,
                slitscan_path=None,
                video_date=f"2026-01-{20+i}",
            )

        pending = service.get_pending_uploads()
        assert len(pending) == 3

    def test_excludes_success(self, upload_config):
        """Test that successful uploads are not in pending."""
        service = UploadService(upload_config)

        queue_id = service.queue_upload(
            video_path="/path/to/video.mp4",
            keogram_path=None,
            slitscan_path=None,
            video_date="2026-01-21",
        )

        # Mark as success
        service.mark_upload_success(queue_id, '{"status": "ok"}')

        pending = service.get_pending_uploads()
        assert len(pending) == 0


class TestMarkUploadStatus:
    """Test mark_upload_success and mark_upload_failed methods."""

    def test_mark_success(self, upload_config):
        """Test marking upload as successful."""
        service = UploadService(upload_config)

        queue_id = service.queue_upload(
            video_path="/path/to/video.mp4",
            keogram_path=None,
            slitscan_path=None,
            video_date="2026-01-21",
        )

        result = service.mark_upload_success(queue_id, '{"id": 123}')
        assert result is True

        upload = service.get_upload_by_id(queue_id)
        assert upload["status"] == "success"
        assert upload["completed_at"] is not None
        assert upload["server_response"] == '{"id": 123}'

    def test_mark_failed_increments_retry(self, upload_config):
        """Test that marking failed increments retry count."""
        service = UploadService(upload_config)

        queue_id = service.queue_upload(
            video_path="/path/to/video.mp4",
            keogram_path=None,
            slitscan_path=None,
            video_date="2026-01-21",
        )

        service.mark_upload_failed(queue_id, "Network error")

        upload = service.get_upload_by_id(queue_id)
        assert upload["retry_count"] == 1
        assert upload["last_error"] == "Network error"
        assert upload["status"] == "pending"  # Still pending, not exhausted

    def test_mark_failed_exhausts_retries(self, upload_config):
        """Test that max retries changes status to failed."""
        service = UploadService(upload_config)

        queue_id = service.queue_upload(
            video_path="/path/to/video.mp4",
            keogram_path=None,
            slitscan_path=None,
            video_date="2026-01-21",
            max_retries=3,
        )

        # Fail 3 times
        for i in range(3):
            service.mark_upload_failed(queue_id, f"Error {i+1}")

        upload = service.get_upload_by_id(queue_id)
        assert upload["retry_count"] == 3
        assert upload["status"] == "failed"

    def test_exponential_backoff(self, upload_config):
        """Test exponential backoff timing."""
        service = UploadService(upload_config)

        queue_id = service.queue_upload(
            video_path="/path/to/video.mp4",
            keogram_path=None,
            slitscan_path=None,
            video_date="2026-01-21",
            max_retries=10,
        )

        # Check backoff progression: 5, 10, 20, 40, 80...
        expected_delays = [5, 10, 20, 40, 80]

        for i, expected_delay in enumerate(expected_delays):
            before = datetime.now()
            service.mark_upload_failed(queue_id, f"Error {i+1}")
            upload = service.get_upload_by_id(queue_id)

            next_retry = datetime.fromisoformat(upload["next_retry_at"])
            actual_delay = (next_retry - before).total_seconds() / 60

            # Allow 1 minute tolerance
            assert (
                abs(actual_delay - expected_delay) < 1
            ), f"Retry {i+1}: expected ~{expected_delay}m, got {actual_delay:.1f}m"

    def test_backoff_caps_at_max(self, upload_config):
        """Test that backoff caps at MAX_RETRY_DELAY_MINUTES."""
        service = UploadService(upload_config)

        queue_id = service.queue_upload(
            video_path="/path/to/video.mp4",
            keogram_path=None,
            slitscan_path=None,
            video_date="2026-01-21",
            max_retries=20,
        )

        # Fail many times to exceed max delay
        for i in range(15):
            service.mark_upload_failed(queue_id, f"Error {i+1}")

        upload = service.get_upload_by_id(queue_id)
        next_retry = datetime.fromisoformat(upload["next_retry_at"])
        delay = (next_retry - datetime.now()).total_seconds() / 60

        # Should be capped at 24 hours (1440 minutes)
        assert delay <= MAX_RETRY_DELAY_MINUTES + 1


class TestCancelUpload:
    """Test cancel_upload method."""

    def test_cancel_upload(self, upload_config):
        """Test cancelling an upload."""
        service = UploadService(upload_config)

        queue_id = service.queue_upload(
            video_path="/path/to/video.mp4",
            keogram_path=None,
            slitscan_path=None,
            video_date="2026-01-21",
        )

        result = service.cancel_upload(queue_id)
        assert result is True

        # Should be gone
        upload = service.get_upload_by_id(queue_id)
        assert upload is None

    def test_cancel_nonexistent(self, upload_config):
        """Test cancelling non-existent upload."""
        service = UploadService(upload_config)
        result = service.cancel_upload(9999)
        assert result is True  # DELETE succeeds even if nothing deleted


class TestGetUploadHistory:
    """Test get_upload_history method."""

    def test_empty_history(self, upload_config):
        """Test empty history."""
        service = UploadService(upload_config)
        history = service.get_upload_history()
        assert history == []

    def test_history_limit(self, upload_config):
        """Test history respects limit."""
        service = UploadService(upload_config)

        # Queue many uploads
        for i in range(10):
            service.queue_upload(
                video_path=f"/path/to/video{i}.mp4",
                keogram_path=None,
                slitscan_path=None,
                video_date=f"2026-01-{10+i}",
            )

        history = service.get_upload_history(limit=5)
        assert len(history) == 5

    def test_history_includes_all_statuses(self, upload_config):
        """Test history includes pending, success, and failed."""
        service = UploadService(upload_config)

        # Create uploads in different states
        id1 = service.queue_upload("/path/1.mp4", None, None, "2026-01-21")
        id2 = service.queue_upload("/path/2.mp4", None, None, "2026-01-22")
        id3 = service.queue_upload("/path/3.mp4", None, None, "2026-01-23", max_retries=1)

        service.mark_upload_success(id1, None)
        service.mark_upload_failed(id3, "Error")  # Exhausts retries

        history = service.get_upload_history()
        statuses = {h["status"] for h in history}
        assert "success" in statuses
        assert "pending" in statuses
        assert "failed" in statuses


class TestGetQueueStats:
    """Test get_queue_stats method."""

    def test_empty_stats(self, upload_config):
        """Test stats on empty queue."""
        service = UploadService(upload_config)
        stats = service.get_queue_stats()
        assert stats["pending"] == 0
        assert stats["success"] == 0
        assert stats["failed"] == 0
        assert stats["total"] == 0

    def test_stats_counts(self, upload_config):
        """Test stats correctly count different statuses."""
        service = UploadService(upload_config)

        # Create uploads
        for i in range(5):
            service.queue_upload(f"/path/{i}.mp4", None, None, f"2026-01-{10+i}")

        # Mark some as success, some as failed
        uploads = service.get_pending_uploads()
        service.mark_upload_success(uploads[0]["id"], None)
        service.mark_upload_success(uploads[1]["id"], None)

        # Mark one as failed (exhaust retries)
        for _ in range(5):  # Default max_retries is 5
            service.mark_upload_failed(uploads[2]["id"], "Error")

        stats = service.get_queue_stats()
        assert stats["success"] == 2
        assert stats["failed"] == 1
        assert stats["pending"] == 2
        assert stats["total"] == 5


class TestUploadToServer:
    """Test upload_to_server method."""

    def test_upload_success(self, upload_config, temp_video_file):
        """Test successful upload."""
        service = UploadService(upload_config)

        with patch("src.upload_service.requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.text = '{"id": 123, "status": "uploaded"}'
            mock_post.return_value = mock_response

            success, error, response = service.upload_to_server(
                video_path=Path(temp_video_file),
                keogram_path=None,
                slitscan_path=None,
                date="2026-01-21",
            )

            assert success is True
            assert error is None
            assert response == '{"id": 123, "status": "uploaded"}'

    def test_upload_failure_http_error(self, upload_config, temp_video_file):
        """Test upload failure with HTTP error."""
        service = UploadService(upload_config)

        with patch("src.upload_service.requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_post.return_value = mock_response

            success, error, response = service.upload_to_server(
                video_path=Path(temp_video_file),
                keogram_path=None,
                slitscan_path=None,
                date="2026-01-21",
            )

            assert success is False
            assert "500" in error
            assert response is None

    def test_upload_failure_network_error(self, upload_config, temp_video_file):
        """Test upload failure with network error."""
        service = UploadService(upload_config)

        with patch("src.upload_service.requests.post") as mock_post:
            import requests

            mock_post.side_effect = requests.exceptions.ConnectionError("DNS resolution failed")

            success, error, response = service.upload_to_server(
                video_path=Path(temp_video_file),
                keogram_path=None,
                slitscan_path=None,
                date="2026-01-21",
            )

            assert success is False
            assert "DNS" in error or "Connection" in error
            assert response is None

    def test_upload_missing_video(self, upload_config):
        """Test upload with missing video file."""
        service = UploadService(upload_config)

        success, error, response = service.upload_to_server(
            video_path=Path("/nonexistent/video.mp4"),
            keogram_path=None,
            slitscan_path=None,
            date="2026-01-21",
        )

        assert success is False
        assert "not found" in error.lower()

    def test_upload_missing_config(self):
        """Test upload with missing URL/API key."""
        config = {
            "database": {"enabled": True, "path": ":memory:"},
            "video_upload": {},  # No URL or API key
        }
        service = UploadService(config)

        success, error, response = service.upload_to_server(
            video_path=Path("/some/video.mp4"),
            keogram_path=None,
            slitscan_path=None,
            date="2026-01-21",
        )

        assert success is False
        assert "not configured" in error.lower()


class TestRetrySingleUpload:
    """Test retry_single_upload method."""

    def test_retry_not_found(self, upload_config):
        """Test retry with non-existent upload."""
        service = UploadService(upload_config)

        success, message = service.retry_single_upload(9999)
        assert success is False
        assert "not found" in message.lower()

    def test_retry_already_success(self, upload_config):
        """Test retry on already successful upload."""
        service = UploadService(upload_config)

        queue_id = service.queue_upload("/path/video.mp4", None, None, "2026-01-21")
        service.mark_upload_success(queue_id, None)

        success, message = service.retry_single_upload(queue_id)
        assert success is False
        assert "already completed" in message.lower()

    def test_retry_respects_backoff(self, upload_config, temp_video_file):
        """Test that retry respects backoff timing (without force)."""
        service = UploadService(upload_config)

        queue_id = service.queue_upload(temp_video_file, None, None, "2026-01-21")
        # Set next_retry far in the future
        with service._get_connection() as conn:
            cursor = conn.cursor()
            future_time = (datetime.now() + timedelta(hours=1)).isoformat()
            cursor.execute(
                "UPDATE upload_queue SET next_retry_at = ? WHERE id = ?",
                (future_time, queue_id),
            )
            conn.commit()

        success, message = service.retry_single_upload(queue_id, force=False)
        assert success is False
        assert "not due" in message.lower()

    def test_retry_force_ignores_backoff(self, upload_config, temp_video_file):
        """Test that force=True ignores backoff."""
        service = UploadService(upload_config)

        queue_id = service.queue_upload(temp_video_file, None, None, "2026-01-21")
        # Set next_retry far in the future
        with service._get_connection() as conn:
            cursor = conn.cursor()
            future_time = (datetime.now() + timedelta(hours=1)).isoformat()
            cursor.execute(
                "UPDATE upload_queue SET next_retry_at = ? WHERE id = ?",
                (future_time, queue_id),
            )
            conn.commit()

        with patch("src.upload_service.requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.text = '{"status": "ok"}'
            mock_post.return_value = mock_response

            success, message = service.retry_single_upload(queue_id, force=True)
            assert success is True


class TestProcessRetryQueue:
    """Test process_retry_queue method."""

    def test_empty_queue(self, upload_config):
        """Test processing empty queue."""
        service = UploadService(upload_config)

        results = service.process_retry_queue()
        assert results["processed"] == 0
        assert results["success"] == 0
        assert results["failed"] == 0

    def test_process_skips_not_due(self, upload_config, temp_video_file):
        """Test that processing skips uploads not due yet."""
        service = UploadService(upload_config)

        queue_id = service.queue_upload(temp_video_file, None, None, "2026-01-21")

        # Set next_retry to future
        with service._get_connection() as conn:
            cursor = conn.cursor()
            future_time = (datetime.now() + timedelta(hours=1)).isoformat()
            cursor.execute(
                "UPDATE upload_queue SET next_retry_at = ? WHERE id = ?",
                (future_time, queue_id),
            )
            conn.commit()

        results = service.process_retry_queue(force=False)
        assert results["skipped"] == 1
        assert results["processed"] == 0

    def test_process_force_all(self, upload_config, temp_video_file):
        """Test force processes all regardless of timing."""
        service = UploadService(upload_config)

        for i in range(3):
            queue_id = service.queue_upload(temp_video_file, None, None, f"2026-01-{20+i}")
            # Set future retry time
            with service._get_connection() as conn:
                cursor = conn.cursor()
                future_time = (datetime.now() + timedelta(hours=1)).isoformat()
                cursor.execute(
                    "UPDATE upload_queue SET next_retry_at = ? WHERE id = ?",
                    (future_time, queue_id),
                )
                conn.commit()

        with patch("src.upload_service.requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.text = '{"status": "ok"}'
            mock_post.return_value = mock_response

            results = service.process_retry_queue(force=True)
            assert results["processed"] == 3
            assert results["success"] == 3

    def test_process_handles_failures(self, upload_config, temp_video_file):
        """Test processing handles individual failures gracefully."""
        service = UploadService(upload_config)

        for i in range(3):
            service.queue_upload(temp_video_file, None, None, f"2026-01-{20+i}")

        with patch("src.upload_service.requests.post") as mock_post:
            # First succeeds, second fails, third succeeds
            mock_post.side_effect = [
                Mock(status_code=200, text='{"status": "ok"}'),
                Mock(status_code=500, text="Server error"),
                Mock(status_code=200, text='{"status": "ok"}'),
            ]

            results = service.process_retry_queue(force=True)
            assert results["success"] == 2
            assert results["failed"] == 1
            assert len(results["errors"]) == 1


class TestDatabasePathResolution:
    """Test database path resolution."""

    def test_relative_path_resolved(self, temp_db_config):
        """Test relative database path is resolved correctly."""
        config = temp_db_config["config"].copy()
        config["database"]["path"] = "data/test.db"

        service = UploadService(config, temp_db_config["config_path"])

        # Should have resolved to absolute path
        assert os.path.isabs(service.db_path)
        assert "data/test.db" in service.db_path or "data\\test.db" in service.db_path

    def test_absolute_path_unchanged(self, temp_db_config):
        """Test absolute database path is not modified."""
        abs_path = "/absolute/path/to/test.db"
        config = temp_db_config["config"].copy()
        config["database"]["path"] = abs_path

        service = UploadService(config, temp_db_config["config_path"])

        assert service.db_path == abs_path
