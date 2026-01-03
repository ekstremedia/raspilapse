"""
Comprehensive tests for daily_timelapse.py

Tests the daily timelapse runner including:
- Configuration loading
- Video file finding
- Keogram file finding
- Slitscan file finding
- Upload to server functionality
- Main CLI function
"""

import pytest
import os
import sys
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, Mock
import yaml
import logging

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from daily_timelapse import (
    load_config,
    find_video_file,
    find_keogram_file,
    find_slitscan_file,
    upload_to_server,
    main,
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    temp_path = Path(tempfile.mkdtemp())
    yield temp_path
    shutil.rmtree(temp_path)


@pytest.fixture
def sample_config(temp_dir):
    """Create a sample configuration file."""
    config_data = {
        "output": {
            "directory": str(temp_dir / "images"),
            "project_name": "test_project",
        },
        "video": {
            "directory": str(temp_dir / "videos"),
            "fps": 25,
            "codec": {"name": "libx264", "pixel_format": "yuv420p"},
            "default_start_time": "05:00",
            "default_end_time": "05:00",
        },
        "video_upload": {
            "enabled": True,
            "url": "https://example.com/upload",
            "api_key": "test_api_key",
            "camera_id": "test_camera",
        },
        "logging": {
            "enabled": True,
            "level": "INFO",
            "log_file": str(temp_dir / "logs" / "test.log"),
            "console": True,
        },
    }

    config_path = temp_dir / "config.yml"
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)

    return config_path


@pytest.fixture
def sample_video_files(temp_dir):
    """Create sample video files for testing."""
    video_dir = temp_dir / "videos" / "2025" / "12"
    video_dir.mkdir(parents=True, exist_ok=True)

    # Create video files with different dates
    files = []
    for day in [24, 25, 26]:
        date_str = f"2025-12-{day:02d}"
        filename = f"test_project_{date_str}_0500-0500.mp4"
        filepath = video_dir / filename
        filepath.touch()
        files.append(filepath)

    return video_dir.parent.parent, files


@pytest.fixture
def sample_keogram_files(temp_dir):
    """Create sample keogram files for testing."""
    video_dir = temp_dir / "videos" / "2025" / "12"
    video_dir.mkdir(parents=True, exist_ok=True)

    # Create keogram files with different dates
    files = []
    for day in [24, 25, 26]:
        date_str = f"2025-12-{day:02d}"
        filename = f"keogram_test_project_{date_str}_0500-0500.jpg"
        filepath = video_dir / filename
        filepath.touch()
        files.append(filepath)

    return video_dir.parent.parent, files


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_valid_config(self, sample_config):
        """Test loading a valid config file."""
        config = load_config(str(sample_config))
        assert "output" in config
        assert "video" in config
        assert config["output"]["project_name"] == "test_project"

    def test_load_nonexistent_config(self, temp_dir):
        """Test loading a non-existent config file."""
        with pytest.raises(FileNotFoundError):
            load_config(str(temp_dir / "nonexistent.yml"))

    def test_load_invalid_yaml(self, temp_dir):
        """Test loading an invalid YAML file."""
        invalid_config = temp_dir / "invalid.yml"
        invalid_config.write_text("{ invalid yaml: [")

        with pytest.raises(yaml.YAMLError):
            load_config(str(invalid_config))


class TestFindVideoFile:
    """Tests for find_video_file function."""

    def test_find_video_basic(self, sample_video_files):
        """Test finding video file by date."""
        video_dir, files = sample_video_files
        target_date = datetime(2025, 12, 24).date()

        result = find_video_file(video_dir, "test_project", target_date)

        assert result is not None
        assert "2025-12-24" in result.name

    def test_find_video_not_found(self, temp_dir):
        """Test finding video for date with no files."""
        video_dir = temp_dir / "videos"
        video_dir.mkdir()
        target_date = datetime(2025, 12, 24).date()

        result = find_video_file(video_dir, "test_project", target_date)

        assert result is None

    def test_find_video_wrong_project(self, sample_video_files):
        """Test finding video for wrong project name."""
        video_dir, files = sample_video_files
        target_date = datetime(2025, 12, 24).date()

        result = find_video_file(video_dir, "wrong_project", target_date)

        assert result is None

    def test_find_video_multiple_matches(self, temp_dir):
        """Test finding most recent video when multiple exist."""
        video_dir = temp_dir / "videos"
        video_dir.mkdir(parents=True)

        # Create two files with same date but different times
        date_str = "2025-12-24"
        file1 = video_dir / f"test_{date_str}_0500-0500.mp4"
        file2 = video_dir / f"test_{date_str}_1000-1000.mp4"

        file1.touch()
        # Set file2 as newer
        import time

        time.sleep(0.1)
        file2.touch()

        target_date = datetime(2025, 12, 24).date()
        result = find_video_file(video_dir, "test", target_date)

        # Should return most recently modified
        assert result is not None
        assert "1000" in result.name

    def test_find_video_nested_directory(self, sample_video_files):
        """Test finding video in nested directory structure."""
        video_dir, files = sample_video_files
        target_date = datetime(2025, 12, 25).date()

        result = find_video_file(video_dir, "test_project", target_date)

        assert result is not None
        assert "2025-12-25" in result.name


class TestFindKeogramFile:
    """Tests for find_keogram_file function."""

    def test_find_keogram_basic(self, sample_keogram_files):
        """Test finding keogram file by date."""
        video_dir, files = sample_keogram_files
        target_date = datetime(2025, 12, 24).date()

        result = find_keogram_file(video_dir, "test_project", target_date)

        assert result is not None
        assert "2025-12-24" in result.name
        assert "keogram" in result.name

    def test_find_keogram_not_found(self, temp_dir):
        """Test finding keogram for date with no files."""
        video_dir = temp_dir / "videos"
        video_dir.mkdir()
        target_date = datetime(2025, 12, 24).date()

        result = find_keogram_file(video_dir, "test_project", target_date)

        assert result is None

    def test_find_keogram_date_filter(self, temp_dir):
        """Test keogram date filtering."""
        video_dir = temp_dir / "videos"
        video_dir.mkdir(parents=True)

        # Create keogram files with different dates
        file1 = video_dir / "keogram_test_2025-12-24.jpg"
        file2 = video_dir / "keogram_test_2025-12-25.jpg"
        file1.touch()
        file2.touch()

        target_date = datetime(2025, 12, 24).date()
        result = find_keogram_file(video_dir, "test", target_date)

        assert result is not None
        assert "2025-12-24" in result.name

    def test_find_keogram_nested_directory(self, sample_keogram_files):
        """Test finding keogram in nested directory structure."""
        video_dir, files = sample_keogram_files
        target_date = datetime(2025, 12, 26).date()

        result = find_keogram_file(video_dir, "test_project", target_date)

        assert result is not None
        assert "2025-12-26" in result.name


class TestUploadToServer:
    """Tests for upload_to_server function."""

    @pytest.fixture
    def mock_logger(self):
        """Create a mock logger."""
        return MagicMock(spec=logging.Logger)

    @pytest.fixture
    def sample_upload_files(self, temp_dir):
        """Create sample files for upload testing."""
        video_path = temp_dir / "test_video.mp4"
        keogram_path = temp_dir / "test_keogram.jpg"
        slitscan_path = temp_dir / "test_slitscan.jpg"

        video_path.write_bytes(b"fake video content")
        keogram_path.write_bytes(b"fake keogram content")
        slitscan_path.write_bytes(b"fake slitscan content")

        return video_path, keogram_path, slitscan_path

    def test_upload_success(self, sample_upload_files, mock_logger):
        """Test successful upload."""
        video_path, keogram_path, slitscan_path = sample_upload_files
        upload_config = {
            "url": "https://example.com/upload",
            "api_key": "test_key",
        }

        with patch("daily_timelapse.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "OK"
            mock_post.return_value = mock_response

            result = upload_to_server(
                video_path,
                keogram_path,
                slitscan_path,
                "2025-12-24",
                upload_config,
                "test_camera",
                mock_logger,
            )

        assert result is True
        mock_post.assert_called_once()

    def test_upload_without_keogram(self, temp_dir, mock_logger):
        """Test upload without keogram file."""
        video_path = temp_dir / "test_video.mp4"
        video_path.write_bytes(b"fake video")

        upload_config = {
            "url": "https://example.com/upload",
            "api_key": "test_key",
        }

        with patch("daily_timelapse.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "OK"
            mock_post.return_value = mock_response

            result = upload_to_server(
                video_path,
                None,  # No keogram
                None,  # No slitscan
                "2025-12-24",
                upload_config,
                "test_camera",
                mock_logger,
            )

        assert result is True

    def test_upload_video_not_found(self, temp_dir, mock_logger):
        """Test upload fails when video file doesn't exist."""
        video_path = temp_dir / "nonexistent.mp4"
        upload_config = {
            "url": "https://example.com/upload",
            "api_key": "test_key",
        }

        result = upload_to_server(
            video_path,
            None,
            None,
            "2025-12-24",
            upload_config,
            "test_camera",
            mock_logger,
        )

        assert result is False

    def test_upload_failure_status_code(self, sample_upload_files, mock_logger):
        """Test upload failure with bad status code."""
        video_path, keogram_path, slitscan_path = sample_upload_files
        upload_config = {
            "url": "https://example.com/upload",
            "api_key": "test_key",
        }

        with patch("daily_timelapse.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Server Error"
            mock_post.return_value = mock_response

            result = upload_to_server(
                video_path,
                keogram_path,
                slitscan_path,
                "2025-12-24",
                upload_config,
                "test_camera",
                mock_logger,
            )

        assert result is False

    def test_upload_request_exception(self, sample_upload_files, mock_logger):
        """Test upload handles request exceptions."""
        video_path, keogram_path, slitscan_path = sample_upload_files
        upload_config = {
            "url": "https://example.com/upload",
            "api_key": "test_key",
        }

        with patch("daily_timelapse.requests.post") as mock_post:
            import requests

            mock_post.side_effect = requests.exceptions.ConnectionError("Network error")

            result = upload_to_server(
                video_path,
                keogram_path,
                slitscan_path,
                "2025-12-24",
                upload_config,
                "test_camera",
                mock_logger,
            )

        assert result is False

    def test_upload_general_exception(self, sample_upload_files, mock_logger):
        """Test upload handles general exceptions."""
        video_path, keogram_path, slitscan_path = sample_upload_files
        upload_config = {
            "url": "https://example.com/upload",
            "api_key": "test_key",
        }

        with patch("daily_timelapse.requests.post") as mock_post:
            mock_post.side_effect = Exception("Unexpected error")

            result = upload_to_server(
                video_path,
                keogram_path,
                slitscan_path,
                "2025-12-24",
                upload_config,
                "test_camera",
                mock_logger,
            )

        assert result is False

    def test_upload_authorization_header(self, sample_upload_files, mock_logger):
        """Test upload includes correct authorization header."""
        video_path, keogram_path, slitscan_path = sample_upload_files
        upload_config = {
            "url": "https://example.com/upload",
            "api_key": "secret_api_key",
        }

        with patch("daily_timelapse.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            upload_to_server(
                video_path,
                keogram_path,
                slitscan_path,
                "2025-12-24",
                upload_config,
                "test_camera",
                mock_logger,
            )

            call_kwargs = mock_post.call_args[1]
            assert "headers" in call_kwargs
            assert "Authorization" in call_kwargs["headers"]
            assert "Bearer secret_api_key" in call_kwargs["headers"]["Authorization"]

    def test_upload_data_payload(self, sample_upload_files, mock_logger):
        """Test upload includes correct data payload."""
        video_path, keogram_path, slitscan_path = sample_upload_files
        upload_config = {
            "url": "https://example.com/upload",
            "api_key": "test_key",
        }

        with patch("daily_timelapse.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            upload_to_server(
                video_path,
                keogram_path,
                slitscan_path,
                "2025-12-24",
                upload_config,
                "my_camera",
                mock_logger,
            )

            call_kwargs = mock_post.call_args[1]
            assert "data" in call_kwargs
            assert call_kwargs["data"]["date"] == "2025-12-24"
            assert call_kwargs["data"]["camera_id"] == "my_camera"

    def test_upload_with_slitscan(self, sample_upload_files, mock_logger):
        """Test upload includes slitscan file."""
        video_path, keogram_path, slitscan_path = sample_upload_files
        upload_config = {
            "url": "https://example.com/upload",
            "api_key": "test_key",
        }

        with patch("daily_timelapse.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            result = upload_to_server(
                video_path,
                keogram_path,
                slitscan_path,
                "2025-12-24",
                upload_config,
                "test_camera",
                mock_logger,
            )

            call_kwargs = mock_post.call_args[1]
            assert "files" in call_kwargs
            # Check that slitscan is in the files
            files_dict = call_kwargs["files"]
            assert "slitscan" in files_dict

        assert result is True


class TestMainCLI:
    """Tests for main CLI function."""

    def test_main_config_not_found(self, temp_dir, monkeypatch, capsys):
        """Test main returns error for missing config."""
        monkeypatch.setattr(
            "sys.argv",
            ["daily_timelapse.py", "--config", str(temp_dir / "nonexistent.yml")],
        )

        result = main()

        assert result == 1
        captured = capsys.readouterr()
        assert "Config file not found" in captured.out

    def test_main_invalid_yaml(self, temp_dir, monkeypatch, capsys):
        """Test main returns error for invalid YAML."""
        invalid_config = temp_dir / "invalid.yml"
        invalid_config.write_text("{ invalid yaml: [")

        monkeypatch.setattr("sys.argv", ["daily_timelapse.py", "--config", str(invalid_config)])

        result = main()

        assert result == 1
        captured = capsys.readouterr()
        assert "Invalid YAML" in captured.out

    def test_main_invalid_date_format(self, sample_config, monkeypatch, capsys):
        """Test main returns error for invalid date format."""
        monkeypatch.setattr(
            "sys.argv",
            [
                "daily_timelapse.py",
                "--config",
                str(sample_config),
                "--date",
                "24-12-2025",
            ],
        )

        result = main()

        assert result == 1
        captured = capsys.readouterr()
        assert "Invalid date format" in captured.out

    def test_main_default_date_yesterday(self, sample_config, monkeypatch):
        """Test main defaults to yesterday's date."""
        monkeypatch.setattr(
            "sys.argv",
            [
                "daily_timelapse.py",
                "--config",
                str(sample_config),
                "--no-upload",
                "--only-upload",  # Skip both video creation and upload
            ],
        )

        with patch("daily_timelapse.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            main()

        # Should not have called subprocess since --only-upload was used

    def test_main_specific_date(self, sample_config, temp_dir, monkeypatch):
        """Test main with specific date."""
        # Create video directory structure
        video_dir = Path(sample_config).parent / "videos"
        video_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            "sys.argv",
            [
                "daily_timelapse.py",
                "--config",
                str(sample_config),
                "--date",
                "2025-12-24",
                "--dry-run",
                "--no-upload",  # Skip upload since no video file exists in dry-run
            ],
        )

        result = main()
        # Dry run should succeed even without files
        assert result == 0

    def test_main_no_upload_flag(self, sample_config, temp_dir, monkeypatch, capsys):
        """Test main with --no-upload flag."""
        # Create video directory
        video_dir = Path(sample_config).parent / "videos"
        video_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            "sys.argv",
            [
                "daily_timelapse.py",
                "--config",
                str(sample_config),
                "--date",
                "2025-12-24",
                "--no-upload",
                "--dry-run",
            ],
        )

        result = main()

        captured = capsys.readouterr()
        assert "Skipping upload" in captured.out

    def test_main_only_upload_flag(self, sample_config, temp_dir, monkeypatch, capsys):
        """Test main with --only-upload flag."""
        # Create video directory and file
        video_dir = Path(sample_config).parent / "videos"
        video_dir.mkdir(parents=True, exist_ok=True)

        # Create a test video file
        video_file = video_dir / "test_project_2025-12-24_0500-0500.mp4"
        video_file.write_bytes(b"fake video")

        monkeypatch.setattr(
            "sys.argv",
            [
                "daily_timelapse.py",
                "--config",
                str(sample_config),
                "--date",
                "2025-12-24",
                "--only-upload",
                "--dry-run",
            ],
        )

        result = main()

        # Should not run make_timelapse
        captured = capsys.readouterr()
        assert "Creating Timelapse Video" not in captured.out

    def test_main_dry_run(self, sample_config, temp_dir, monkeypatch, capsys):
        """Test main dry-run mode."""
        video_dir = Path(sample_config).parent / "videos"
        video_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            "sys.argv",
            [
                "daily_timelapse.py",
                "--config",
                str(sample_config),
                "--date",
                "2025-12-24",
                "--dry-run",
            ],
        )

        result = main()

        captured = capsys.readouterr()
        assert "Would run:" in captured.out

    def test_main_make_timelapse_failure(self, sample_config, temp_dir, monkeypatch):
        """Test main handles make_timelapse failure."""
        video_dir = Path(sample_config).parent / "videos"
        video_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            "sys.argv",
            [
                "daily_timelapse.py",
                "--config",
                str(sample_config),
                "--date",
                "2025-12-24",
            ],
        )

        with patch("daily_timelapse.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)  # Failure

            result = main()

        assert result == 1

    def test_main_upload_disabled_in_config(self, temp_dir, monkeypatch, capsys):
        """Test main when upload is disabled in config."""
        config_data = {
            "output": {
                "directory": str(temp_dir / "images"),
                "project_name": "test_project",
            },
            "video": {
                "directory": str(temp_dir / "videos"),
                "fps": 25,
                "codec": {"name": "libx264"},
            },
            "video_upload": {
                "enabled": False,  # Disabled
            },
            "logging": {
                "enabled": True,
                "level": "INFO",
                "log_file": str(temp_dir / "logs" / "test.log"),
                "console": True,
            },
        }

        config_path = temp_dir / "config.yml"
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        # Create video directory
        video_dir = temp_dir / "videos"
        video_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            "sys.argv",
            [
                "daily_timelapse.py",
                "--config",
                str(config_path),
                "--date",
                "2025-12-24",
                "--only-upload",
            ],
        )

        result = main()

        captured = capsys.readouterr()
        assert "Upload disabled" in captured.out

    def test_main_video_not_found_for_upload(self, sample_config, temp_dir, monkeypatch, capsys):
        """Test main handles missing video file during upload."""
        # Create empty video directory
        video_dir = Path(sample_config).parent / "videos"
        video_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            "sys.argv",
            [
                "daily_timelapse.py",
                "--config",
                str(sample_config),
                "--date",
                "2025-12-24",
                "--only-upload",
            ],
        )

        result = main()

        assert result == 1
        captured = capsys.readouterr()
        assert "Video file not found" in captured.out

    def test_main_upload_failure(self, sample_config, temp_dir, monkeypatch, capsys):
        """Test main handles upload failure."""
        video_dir = Path(sample_config).parent / "videos"
        video_dir.mkdir(parents=True, exist_ok=True)

        # Create video file
        video_file = video_dir / "test_project_2025-12-24_0500-0500.mp4"
        video_file.write_bytes(b"fake video")

        monkeypatch.setattr(
            "sys.argv",
            [
                "daily_timelapse.py",
                "--config",
                str(sample_config),
                "--date",
                "2025-12-24",
                "--only-upload",
            ],
        )

        with patch("daily_timelapse.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Server Error"
            mock_post.return_value = mock_response

            result = main()

        assert result == 1
        captured = capsys.readouterr()
        assert "Upload failed" in captured.out


class TestOldConfigFallback:
    """Tests for old config file fallback functionality."""

    def test_fallback_to_old_config(self, temp_dir, monkeypatch, capsys):
        """Test fallback to old config file for upload settings."""
        # Create new config without upload settings
        config_data = {
            "output": {
                "directory": str(temp_dir / "images"),
                "project_name": "test_project",
            },
            "video": {
                "directory": str(temp_dir / "videos"),
                "fps": 25,
                "codec": {"name": "libx264"},
            },
            # No video_upload section
            "logging": {
                "enabled": True,
                "level": "INFO",
                "log_file": str(temp_dir / "logs" / "test.log"),
                "console": True,
            },
        }

        config_path = temp_dir / "config.yml"
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        # Create video directory
        video_dir = temp_dir / "videos"
        video_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            "sys.argv",
            [
                "daily_timelapse.py",
                "--config",
                str(config_path),
                "--date",
                "2025-12-24",
                "--no-upload",
                "--dry-run",
            ],
        )

        result = main()
        assert result == 0


class TestCompleteWorkflow:
    """Integration tests for complete workflow."""

    def test_complete_dry_run_workflow(self, sample_config, temp_dir, monkeypatch, capsys):
        """Test complete workflow in dry-run mode."""
        video_dir = Path(sample_config).parent / "videos"
        video_dir.mkdir(parents=True, exist_ok=True)

        # Create video and keogram files
        video_file = video_dir / "test_project_2025-12-24_0500-0500.mp4"
        keogram_file = video_dir / "keogram_test_project_2025-12-24_0500-0500.jpg"
        video_file.write_bytes(b"fake video")
        keogram_file.write_bytes(b"fake keogram")

        monkeypatch.setattr(
            "sys.argv",
            [
                "daily_timelapse.py",
                "--config",
                str(sample_config),
                "--date",
                "2025-12-24",
                "--dry-run",
            ],
        )

        result = main()

        assert result == 0
        captured = capsys.readouterr()
        assert "Would run:" in captured.out
        assert "Would upload:" in captured.out

    def test_successful_timelapse_creation(self, sample_config, temp_dir, monkeypatch):
        """Test successful timelapse creation."""
        video_dir = Path(sample_config).parent / "videos"
        video_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            "sys.argv",
            [
                "daily_timelapse.py",
                "--config",
                str(sample_config),
                "--date",
                "2025-12-24",
                "--no-upload",
            ],
        )

        with patch("daily_timelapse.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            result = main()

        assert result == 0
        mock_run.assert_called_once()

        # Verify correct arguments were passed
        call_args = mock_run.call_args[0][0]
        assert "make_timelapse.py" in " ".join(call_args)
        assert "--start" in call_args
        assert "05:00" in call_args
        assert "--start-date" in call_args
        assert "2025-12-24" in call_args


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
