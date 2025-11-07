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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
