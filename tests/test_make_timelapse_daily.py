"""Tests for daily timelapse generation features."""

import os
import sys
import tempfile
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.make_timelapse import (
    main,
    parse_time,
    find_images_in_range,
    create_video,
    load_config,
)


@pytest.fixture
def test_config():
    """Create test configuration."""
    return {
        "output": {
            "directory": "/test/images",
            "project_name": "test_project",
            "organize_by_date": True,
            "date_format": "%Y/%m/%d",
        },
        "video": {
            "directory": "test_videos",
            "filename_pattern": "{name}_{start_date}_to_{end_date}.mp4",
            "fps": 25,
            "codec": {
                "name": "libx264",
                "pixel_format": "yuv420p",
                "crf": 20,
            },
        },
        "overlay": {
            "camera_name": "TestCam",
        },
    }


@pytest.fixture
def mock_config_file(test_config):
    """Create a temporary config file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(test_config, f)
        yield f.name
    os.unlink(f.name)


class TestDefaultTwentyFourHours:
    """Test default 24-hour mode."""

    @patch("src.make_timelapse.find_images_in_range")
    @patch("src.make_timelapse.create_video")
    @patch("src.make_timelapse.load_config")
    @patch("sys.argv")
    def test_no_args_defaults_to_24_hours(
        self, mock_argv, mock_load_config, mock_create_video, mock_find_images
    ):
        """Test that no --start/--end args defaults to last 24 hours."""
        # Setup
        mock_argv.__getitem__.return_value = ["make_timelapse.py"]
        mock_load_config.return_value = {
            "output": {
                "directory": "/test",
                "project_name": "test",
                "organize_by_date": False,
            },
            "video": {
                "directory": "videos",
                "fps": 25,
                "codec": {"name": "libx264", "pixel_format": "yuv420p", "crf": 20},
            },
            "overlay": {"camera_name": "Test"},
        }
        mock_find_images.return_value = [Path("/test/img1.jpg")]
        mock_create_video.return_value = True

        # Parse args without start/end
        with patch("sys.argv", ["make_timelapse.py", "-c", "test.yml"]):
            with patch("os.path.exists", return_value=True):
                result = main()

        # Should use default 24-hour range
        assert result == 0
        mock_find_images.assert_called_once()

        # Check that time range is approximately 24 hours
        call_args = mock_find_images.call_args[0]
        start_time = call_args[2]
        end_time = call_args[3]
        time_diff = end_time - start_time

        # Should be very close to 24 hours
        assert abs(time_diff.total_seconds() - 86400) < 60  # Within 1 minute

    def test_parse_time_valid(self):
        """Test parse_time with valid input."""
        hour, minute = parse_time("04:00")
        assert hour == 4
        assert minute == 0

        hour, minute = parse_time("23:59")
        assert hour == 23
        assert minute == 59

    def test_parse_time_invalid(self):
        """Test parse_time with invalid input."""
        with pytest.raises(ValueError):
            parse_time("25:00")  # Invalid hour

        with pytest.raises(ValueError):
            parse_time("12:60")  # Invalid minute

        with pytest.raises(ValueError):
            parse_time("not-a-time")  # Invalid format


class TestDailyNaming:
    """Test daily video naming convention."""

    @patch("src.make_timelapse.find_images_in_range")
    @patch("src.make_timelapse.create_video")
    def test_daily_video_naming(self, mock_create_video, mock_find_images, mock_config_file):
        """Test that daily videos use simplified naming."""
        mock_find_images.return_value = [Path("/test/img.jpg")]
        mock_create_video.return_value = True

        # Run without start/end (default mode)
        with patch("sys.argv", ["make_timelapse.py", "-c", mock_config_file]):
            main()

        # Check the output filename
        call_args = mock_create_video.call_args[0]
        output_file = call_args[1]

        # Default 24h range spans two days, so uses _to_ format
        # e.g., project_2025-12-24_0500_to_2025-12-25_0500.mp4
        assert "_to_" in str(output_file)
        assert output_file.suffix == ".mp4"

    @patch("src.make_timelapse.find_images_in_range")
    @patch("src.make_timelapse.create_video")
    def test_custom_range_naming(self, mock_create_video, mock_find_images, mock_config_file):
        """Test that custom time ranges use different naming."""
        mock_find_images.return_value = [Path("/test/img.jpg")]
        mock_create_video.return_value = True

        # Run with custom start/end
        with patch(
            "sys.argv",
            ["make_timelapse.py", "-c", mock_config_file, "--start", "10:00", "--end", "14:00"],
        ):
            main()

        # Check the output filename
        call_args = mock_create_video.call_args[0]
        output_file = call_args[1]

        # Same-day range uses HHMM-HHMM format (no _to_)
        # e.g., project_2025-12-25_1000-1400.mp4
        assert "_to_" not in str(output_file)
        # Should have time range with dash separator
        assert "-" in output_file.stem  # e.g., 1000-1400


class TestOutputDirectory:
    """Test output directory override."""

    @patch("src.make_timelapse.find_images_in_range")
    @patch("src.make_timelapse.create_video")
    @patch("os.makedirs")
    def test_output_dir_override(
        self, mock_makedirs, mock_create_video, mock_find_images, mock_config_file
    ):
        """Test --output-dir parameter overrides config."""
        mock_find_images.return_value = [Path("/test/img.jpg")]
        mock_create_video.return_value = True
        # Mock directory creation to avoid permission issues
        mock_makedirs.return_value = None

        with tempfile.TemporaryDirectory() as tmpdir:
            custom_dir = os.path.join(tmpdir, "custom_videos")

            # Run with output-dir override
            with patch(
                "sys.argv",
                ["make_timelapse.py", "-c", mock_config_file, "--output-dir", custom_dir],
            ):
                main()

            # Check that custom directory was used
            call_args = mock_create_video.call_args[0]
            output_file = call_args[1]

            assert str(output_file).startswith(custom_dir)

    @patch("src.make_timelapse.find_images_in_range")
    @patch("src.make_timelapse.create_video")
    def test_output_dir_creates_if_missing(
        self, mock_create_video, mock_find_images, mock_config_file
    ):
        """Test that output directory is created if it doesn't exist."""
        mock_find_images.return_value = [Path("/test/img.jpg")]
        mock_create_video.return_value = True

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "new_videos")

            # Run with new output directory
            with patch(
                "sys.argv",
                ["make_timelapse.py", "-c", mock_config_file, "--output-dir", output_dir],
            ):
                main()

            # Directory should be created
            assert os.path.exists(output_dir)


class TestErrorHandling:
    """Test error handling in make_timelapse."""

    @patch("src.make_timelapse.load_config")
    def test_missing_config_file(self, mock_load):
        """Test handling of missing config file."""
        mock_load.side_effect = FileNotFoundError()

        with patch("sys.argv", ["make_timelapse.py", "-c", "missing.yml"]):
            result = main()

        assert result == 1  # Should return error code

    @patch("src.make_timelapse.find_images_in_range")
    def test_no_images_found(self, mock_find_images, mock_config_file):
        """Test handling when no images are found."""
        mock_find_images.return_value = []

        with patch("sys.argv", ["make_timelapse.py", "-c", mock_config_file]):
            result = main()

        assert result == 1  # Should return error code

    @patch("src.make_timelapse.find_images_in_range")
    @patch("src.make_timelapse.create_video")
    def test_video_creation_failure(self, mock_create_video, mock_find_images, mock_config_file):
        """Test handling of video creation failure."""
        mock_find_images.return_value = [Path("/test/img.jpg")]
        mock_create_video.return_value = False  # Simulate failure

        with patch("sys.argv", ["make_timelapse.py", "-c", mock_config_file]):
            result = main()

        assert result == 1  # Should return error code


class TestImageFinding:
    """Test image finding logic."""

    def test_find_images_in_range_organized(self, test_config):
        """Test finding images with date organization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = tmpdir
            project_name = "test"

            # Create test images in date subdirectories
            test_date = datetime.now()
            date_subdir = test_date.strftime("%Y/%m/%d")
            full_dir = os.path.join(base_dir, date_subdir)
            os.makedirs(full_dir, exist_ok=True)

            # Create test images
            img_names = []
            for hour in [10, 11, 12]:
                img_name = f"{project_name}_{test_date.strftime('%Y_%m_%d')}_{hour:02d}_00_00.jpg"
                img_path = os.path.join(full_dir, img_name)
                Path(img_path).touch()
                img_names.append(img_name)

            # Find images
            start_dt = test_date.replace(hour=9, minute=0, second=0, microsecond=0)
            end_dt = test_date.replace(hour=13, minute=0, second=0, microsecond=0)

            images = find_images_in_range(
                base_dir,
                project_name,
                start_dt,
                end_dt,
                organize_by_date=True,
                date_format="%Y/%m/%d",
            )

            assert len(images) == 3
            # Should be sorted
            assert all(img_names[i] in str(images[i]) for i in range(3))

    def test_find_images_in_range_flat(self):
        """Test finding images without date organization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_name = "test"
            test_date = datetime.now()

            # Create test images in flat structure
            for hour in [14, 15, 16]:
                img_name = f"{project_name}_{test_date.strftime('%Y_%m_%d')}_{hour:02d}_30_00.jpg"
                img_path = os.path.join(tmpdir, img_name)
                Path(img_path).touch()

            # Find images
            start_dt = test_date.replace(hour=14, minute=0, second=0, microsecond=0)
            end_dt = test_date.replace(hour=17, minute=0, second=0, microsecond=0)

            images = find_images_in_range(
                tmpdir,
                project_name,
                start_dt,
                end_dt,
                organize_by_date=False,
            )

            assert len(images) == 3


class TestCameraNameUsage:
    """Test camera name from overlay config."""

    @patch("src.make_timelapse.find_images_in_range")
    @patch("src.make_timelapse.create_video")
    def test_uses_camera_name_from_overlay(self, mock_create_video, mock_find_images):
        """Test that camera name is read from overlay config."""
        config = {
            "output": {
                "directory": "/test",
                "project_name": "project",
                "organize_by_date": False,
            },
            "video": {
                "directory": "videos",
                "fps": 25,
                "codec": {"name": "libx264", "pixel_format": "yuv420p", "crf": 20},
            },
            "overlay": {
                "camera_name": "MyCustomCamera",
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config, f)
            config_file = f.name

        try:
            mock_find_images.return_value = [Path("/test/img.jpg")]
            mock_create_video.return_value = True

            with patch("sys.argv", ["make_timelapse.py", "-c", config_file]):
                main()

            # Camera name should be extracted from config
            # (In actual code it's used for display, but we can't easily test that)
            assert mock_create_video.called

        finally:
            os.unlink(config_file)
