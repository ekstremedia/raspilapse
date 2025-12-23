"""
Unit tests for make_timelapse.py module.

Tests the timelapse video generation functionality including:
- Time parsing
- Image finding in date ranges
- Video creation with ffmpeg
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import shutil
import yaml
import sys
import os
from unittest.mock import Mock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from make_timelapse import parse_time, find_images_in_range, load_config


class TestParseTime:
    """Test time string parsing."""

    def test_parse_valid_time(self):
        """Test parsing valid time strings."""
        hour, minute = parse_time("04:00")
        assert hour == 4
        assert minute == 0

        hour, minute = parse_time("23:59")
        assert hour == 23
        assert minute == 59

        hour, minute = parse_time("00:00")
        assert hour == 0
        assert minute == 0

    def test_parse_invalid_format(self):
        """Test parsing invalid time format."""
        # Note: "4:00" is actually valid (Python int() handles it)
        # Test truly invalid formats

        with pytest.raises(ValueError, match="Invalid time format"):
            parse_time("04-00")  # Wrong separator

        with pytest.raises(ValueError, match="Invalid time format"):
            parse_time("invalid")

        with pytest.raises(ValueError, match="Invalid time format"):
            parse_time("25:00:00")  # Too many parts

    def test_parse_invalid_range(self):
        """Test parsing time with invalid hour/minute values."""
        with pytest.raises(ValueError):
            parse_time("24:00")  # Hour out of range

        with pytest.raises(ValueError):
            parse_time("23:60")  # Minute out of range

        with pytest.raises(ValueError):
            parse_time("-01:00")  # Negative hour


class TestFindImagesInRange:
    """Test image finding functionality."""

    @pytest.fixture
    def temp_image_dir(self):
        """Create temporary directory with test images."""
        temp_dir = tempfile.mkdtemp()

        # Create date-organized structure
        date1 = Path(temp_dir) / "2025" / "11" / "05"
        date2 = Path(temp_dir) / "2025" / "11" / "06"
        date1.mkdir(parents=True)
        date2.mkdir(parents=True)

        # Create test images with timestamps
        test_images = [
            # Nov 5, 20:00-23:59
            (date1, "test_2025_11_05_20_00_00.jpg"),
            (date1, "test_2025_11_05_20_30_00.jpg"),
            (date1, "test_2025_11_05_21_00_00.jpg"),
            (date1, "test_2025_11_05_22_00_00.jpg"),
            (date1, "test_2025_11_05_23_00_00.jpg"),
            # Nov 6, 00:00-08:00
            (date2, "test_2025_11_06_00_00_00.jpg"),
            (date2, "test_2025_11_06_04_00_00.jpg"),
            (date2, "test_2025_11_06_08_00_00.jpg"),
            # Outside range
            (date1, "test_2025_11_05_19_59_59.jpg"),  # Before
            (date2, "test_2025_11_06_08_00_01.jpg"),  # After
        ]

        for dir_path, filename in test_images:
            (dir_path / filename).touch()

        yield temp_dir

        # Cleanup
        shutil.rmtree(temp_dir)

    def test_find_images_basic(self, temp_image_dir):
        """Test finding images in basic time range."""
        start = datetime(2025, 11, 5, 20, 0, 0)
        end = datetime(2025, 11, 6, 8, 0, 0)

        images = find_images_in_range(
            temp_image_dir, "test", start, end, organize_by_date=True, date_format="%Y/%m/%d"
        )

        # Should find 8 images (excluding the ones outside range)
        assert len(images) == 8

        # Check they're sorted
        assert images == sorted(images)

        # Check first and last
        assert "20_00_00" in images[0].name
        assert "08_00_00" in images[-1].name

    def test_find_images_no_results(self, temp_image_dir):
        """Test finding images when none exist in range."""
        start = datetime(2025, 11, 10, 0, 0, 0)
        end = datetime(2025, 11, 11, 0, 0, 0)

        images = find_images_in_range(
            temp_image_dir, "test", start, end, organize_by_date=True, date_format="%Y/%m/%d"
        )

        assert len(images) == 0

    def test_find_images_invalid_directory(self):
        """Test finding images in non-existent directory."""
        start = datetime(2025, 11, 5, 20, 0, 0)
        end = datetime(2025, 11, 6, 8, 0, 0)

        with pytest.raises(ValueError, match="Image directory not found"):
            find_images_in_range("/nonexistent/directory", "test", start, end)

    def test_find_images_single_day(self, temp_image_dir):
        """Test finding images within a single day."""
        start = datetime(2025, 11, 5, 20, 0, 0)
        end = datetime(2025, 11, 5, 23, 0, 0)

        images = find_images_in_range(
            temp_image_dir, "test", start, end, organize_by_date=True, date_format="%Y/%m/%d"
        )

        # Should find 4 images (20:00, 20:30, 21:00, 22:00, 23:00)
        assert len(images) == 5
        assert all("2025_11_05" in img.name for img in images)


class TestLoadConfig:
    """Test configuration loading."""

    @pytest.fixture
    def temp_config(self):
        """Create temporary config file."""
        config_data = {
            "output": {
                "directory": "/var/www/html/images",
                "project_name": "test_project",
                "organize_by_date": True,
                "date_format": "%Y/%m/%d",
            },
            "video": {
                "directory": "videos",
                "fps": 25,
                "codec": {"name": "libx264", "pixel_format": "yuv420p", "crf": 20},
                "filename_pattern": "{name}_{start_date}_to_{end_date}.mp4",
            },
            "logging": {
                "enabled": True,
                "level": "INFO",
                "log_file": "logs/make_timelapse.log",
                "console": True,
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config_data, f)
            temp_file = f.name

        yield temp_file

        os.unlink(temp_file)

    def test_load_valid_config(self, temp_config):
        """Test loading valid configuration file."""
        config = load_config(temp_config)

        assert config["output"]["project_name"] == "test_project"
        assert config["video"]["fps"] == 25
        assert config["video"]["codec"]["name"] == "libx264"

    def test_load_nonexistent_config(self):
        """Test loading non-existent config file."""
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/config.yml")


class TestTimelapseIntegration:
    """Integration tests for timelapse generation."""

    def test_datetime_range_calculation(self):
        """Test datetime range calculation logic."""
        # Test case: end time earlier than start time (crosses midnight)
        now = datetime(2025, 11, 6, 10, 0, 0)
        start_time = (4, 0)  # 04:00
        end_time = (4, 0)  # 04:00

        end_datetime = now.replace(hour=end_time[0], minute=end_time[1], second=0, microsecond=0)

        # Since times are equal, should go back to previous day
        if (end_time[0] < start_time[0]) or (
            end_time[0] == start_time[0] and end_time[1] <= start_time[1]
        ):
            start_datetime = (end_datetime - timedelta(days=1)).replace(
                hour=start_time[0], minute=start_time[1]
            )
        else:
            start_datetime = end_datetime.replace(hour=start_time[0], minute=start_time[1])

        # Should be 24 hours apart
        duration = (end_datetime - start_datetime).total_seconds() / 3600
        assert duration == 24.0

    def test_filename_generation(self):
        """Test output filename generation."""
        project_name = "kringelen"
        start_date = datetime(2025, 11, 5, 20, 0, 0)
        end_date = datetime(2025, 11, 6, 8, 0, 0)

        pattern = "{name}_{start_date}_to_{end_date}.mp4"
        filename = pattern.format(
            name=project_name,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
        )

        assert filename == "kringelen_2025-11-05_to_2025-11-06.mp4"


class TestCreateVideoCodecHandling:
    """Test codec-specific command generation in create_video."""

    @pytest.fixture
    def temp_images(self):
        """Create temporary test images."""
        temp_dir = tempfile.mkdtemp()
        images = []
        for i in range(3):
            img_path = Path(temp_dir) / f"test_{i:03d}.jpg"
            img_path.touch()
            images.append(img_path)
        yield images
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def temp_output(self):
        """Create temporary output path."""
        temp_dir = tempfile.mkdtemp()
        output = Path(temp_dir) / "test_output.mp4"
        yield output
        shutil.rmtree(temp_dir)

    @patch("make_timelapse.subprocess.run")
    def test_libx264_uses_crf_preset_threads(self, mock_run, temp_images, temp_output):
        """Test that libx264 codec uses CRF, preset, and threads."""
        from make_timelapse import create_video

        mock_run.return_value = Mock(returncode=0)
        # Create a fake output file so stat() works
        temp_output.touch()

        create_video(
            temp_images,
            temp_output,
            fps=25,
            codec="libx264",
            pixel_format="yuv420p",
            crf=23,
            preset="ultrafast",
            threads=2,
            bitrate="10M",
        )

        # Verify ffmpeg was called
        assert mock_run.called
        cmd = mock_run.call_args[0][0]

        # Check libx264 specific options
        assert "-preset" in cmd
        assert "ultrafast" in cmd
        assert "-threads" in cmd
        assert "2" in cmd
        assert "-crf" in cmd
        assert "23" in cmd
        # Should NOT have bitrate for libx264
        assert "-b:v" not in cmd

    @patch("make_timelapse.subprocess.run")
    def test_h264_v4l2m2m_uses_bitrate(self, mock_run, temp_images, temp_output):
        """Test that h264_v4l2m2m hardware encoder uses bitrate instead of CRF."""
        from make_timelapse import create_video

        mock_run.return_value = Mock(returncode=0)
        temp_output.touch()

        create_video(
            temp_images,
            temp_output,
            fps=25,
            codec="h264_v4l2m2m",
            pixel_format="yuv420p",
            crf=23,
            preset="ultrafast",
            threads=2,
            bitrate="10M",
        )

        assert mock_run.called
        cmd = mock_run.call_args[0][0]

        # Check hardware encoder specific options
        assert "-b:v" in cmd
        assert "10M" in cmd
        # Should NOT have CRF/preset/threads for hardware encoder
        assert "-crf" not in cmd
        assert "-preset" not in cmd
        assert "-threads" not in cmd

    @patch("make_timelapse.subprocess.run")
    def test_h264_omx_uses_bitrate(self, mock_run, temp_images, temp_output):
        """Test that h264_omx hardware encoder uses bitrate instead of CRF."""
        from make_timelapse import create_video

        mock_run.return_value = Mock(returncode=0)
        temp_output.touch()

        create_video(
            temp_images,
            temp_output,
            fps=25,
            codec="h264_omx",
            pixel_format="yuv420p",
            crf=23,
            preset="ultrafast",
            threads=2,
            bitrate="15M",
        )

        assert mock_run.called
        cmd = mock_run.call_args[0][0]

        # Check hardware encoder specific options
        assert "-b:v" in cmd
        assert "15M" in cmd
        # Should NOT have CRF for hardware encoder
        assert "-crf" not in cmd

    def test_create_video_empty_list(self, temp_output):
        """Test create_video with empty image list."""
        from make_timelapse import create_video

        result = create_video([], temp_output)
        assert result is False


class TestVideoDirectoryOrganization:
    """Test video output directory organization by date."""

    def test_video_path_with_date_organization(self):
        """Test that video path includes date subdirectory when organize_by_date is True."""
        from pathlib import Path
        from datetime import datetime

        video_base_dir = "/videos"
        video_organize_by_date = True
        video_date_format = "%Y/%m"
        end_datetime = datetime(2025, 11, 15, 10, 0, 0)

        video_path = Path(video_base_dir)
        if video_organize_by_date:
            date_subdir = end_datetime.strftime(video_date_format)
            video_path = video_path / date_subdir

        assert str(video_path) == "/videos/2025/11"

    def test_video_path_without_date_organization(self):
        """Test that video path is base directory when organize_by_date is False."""
        from pathlib import Path

        video_base_dir = "/videos"
        video_organize_by_date = False

        video_path = Path(video_base_dir)
        if video_organize_by_date:
            video_path = video_path / "2025/11"

        assert str(video_path) == "/videos"

    def test_video_date_format_variations(self):
        """Test different date format patterns."""
        from pathlib import Path
        from datetime import datetime

        end_datetime = datetime(2025, 12, 25, 10, 0, 0)

        # Test %Y/%m format
        date_subdir = end_datetime.strftime("%Y/%m")
        assert date_subdir == "2025/12"

        # Test %Y-%m-%d format
        date_subdir = end_datetime.strftime("%Y-%m-%d")
        assert date_subdir == "2025-12-25"

        # Test %Y format (year only)
        date_subdir = end_datetime.strftime("%Y")
        assert date_subdir == "2025"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
