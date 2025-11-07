"""
Tests for analyze_timelapse.py

Tests the analysis script's ability to:
- Find and match images with metadata files
- Load and parse metadata
- Generate graphs
- Export to Excel
"""

import pytest
import json
import tempfile
import shutil
import os
from pathlib import Path
from datetime import datetime, timedelta
import yaml

# Import functions from analyze_timelapse
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from analyze_timelapse import (
    load_config,
    find_recent_images,
    load_metadata,
    analyze_images,
    print_statistics,
    export_to_excel,
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    temp_path = Path(tempfile.mkdtemp())
    yield temp_path
    shutil.rmtree(temp_path)


@pytest.fixture
def sample_config(temp_dir):
    """Create a sample config file."""
    config = {
        "output": {"directory": str(temp_dir / "images")},
        "graphs": {
            "directory": str(temp_dir / "graphs"),
            "width": 14,
            "height": 8,
            "dpi": 150,
            "default_hours": 24,
        },
        "adaptive_timelapse": {
            "light_thresholds": {"night": 10, "day": 100},
            "night_mode": {"max_exposure_time": 20.0},
        },
    }

    config_path = temp_dir / "config.yml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)

    return config_path


@pytest.fixture
def sample_images_with_metadata(temp_dir):
    """Create sample images and metadata files."""
    images_dir = temp_dir / "images" / "2025" / "11" / "07"
    images_dir.mkdir(parents=True, exist_ok=True)

    image_metadata_pairs = []
    base_time = datetime.now()

    # Create 10 sample images with metadata
    for i in range(10):
        timestamp = base_time - timedelta(minutes=i * 5)

        # Create image file (empty, we don't actually analyze it)
        img_name = f"test_{timestamp.strftime('%Y_%m_%d_%H_%M_%S')}.jpg"
        img_path = images_dir / img_name
        img_path.write_bytes(b"\xff\xd8\xff\xe0")  # Minimal JPEG header

        # Create metadata file
        metadata = {
            "capture_timestamp": timestamp.isoformat(),
            "Lux": 1000 + (i * 100),
            "ExposureTime": 5000 + (i * 1000),  # microseconds
            "AnalogueGain": 1.5 + (i * 0.1),
            "SensorTemperature": 45.0 + i,
            "ColourTemperature": 6500 + (i * 100),
            "ColourGains": [1.5, 1.3],
            "DigitalGain": 1.0,
        }

        meta_name = f"test_{timestamp.strftime('%Y_%m_%d_%H_%M_%S')}_metadata.json"
        meta_path = images_dir / meta_name
        with open(meta_path, "w") as f:
            json.dump(metadata, f)

        # Set modification times to match the timestamp for proper sorting
        mtime = timestamp.timestamp()
        os.utime(img_path, (mtime, mtime))
        os.utime(meta_path, (mtime, mtime))

        image_metadata_pairs.append((img_path, meta_path))

    return images_dir, image_metadata_pairs


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_valid_config(self, sample_config):
        """Test loading a valid config file."""
        config = load_config(sample_config)

        assert "output" in config
        assert "graphs" in config
        assert config["graphs"]["width"] == 14
        assert config["adaptive_timelapse"]["light_thresholds"]["night"] == 10

    def test_load_nonexistent_config(self, temp_dir):
        """Test loading a non-existent config file."""
        with pytest.raises(FileNotFoundError):
            load_config(temp_dir / "nonexistent.yml")


class TestFindRecentImages:
    """Tests for find_recent_images function."""

    def test_find_all_images(self, sample_images_with_metadata):
        """Test finding all images within time window."""
        images_dir, expected_pairs = sample_images_with_metadata

        # Find images from last 24 hours
        found_pairs = find_recent_images(images_dir.parent.parent.parent, hours=24)

        # Should find all 10 test images
        assert len(found_pairs) == 10

        # Check that pairs are sorted chronologically
        timestamps = [datetime.fromtimestamp(p[0].stat().st_mtime) for p in found_pairs]
        assert timestamps == sorted(timestamps), "Images should be sorted chronologically"

    def test_find_limited_timeframe(self, sample_images_with_metadata):
        """Test finding images within a limited time window."""
        images_dir, _ = sample_images_with_metadata

        # Find images from last 15 minutes (should get ~3 images)
        found_pairs = find_recent_images(images_dir.parent.parent.parent, hours=0.25)

        # Should find fewer images (or equal if all created within window)
        assert len(found_pairs) <= 10
        assert len(found_pairs) > 0

    def test_empty_directory(self, temp_dir):
        """Test finding images in an empty directory."""
        empty_dir = temp_dir / "empty"
        empty_dir.mkdir()

        found_pairs = find_recent_images(empty_dir, hours=24)

        assert len(found_pairs) == 0


class TestLoadMetadata:
    """Tests for load_metadata function."""

    def test_load_valid_metadata(self, sample_images_with_metadata):
        """Test loading valid metadata file."""
        _, pairs = sample_images_with_metadata
        _, meta_path = pairs[0]

        metadata = load_metadata(meta_path)

        assert "Lux" in metadata
        assert "ExposureTime" in metadata
        assert isinstance(metadata["Lux"], (int, float))

    def test_load_nonexistent_metadata(self, temp_dir):
        """Test loading non-existent metadata file."""
        fake_path = temp_dir / "nonexistent_metadata.json"

        metadata = load_metadata(fake_path)

        # Should return empty dict on error
        assert metadata == {}

    def test_load_invalid_json(self, temp_dir):
        """Test loading invalid JSON file."""
        bad_json = temp_dir / "bad_metadata.json"
        bad_json.write_text("{ invalid json }")

        metadata = load_metadata(bad_json)

        # Should return empty dict on error
        assert metadata == {}


class TestAnalyzeImages:
    """Tests for analyze_images function."""

    def test_analyze_valid_images(self, sample_images_with_metadata):
        """Test analyzing valid images with metadata."""
        _, pairs = sample_images_with_metadata

        data = analyze_images(pairs, hours=24)

        # Check that all expected keys are present
        assert "timestamps" in data
        assert "lux" in data
        assert "exposure_time" in data
        assert "analogue_gain" in data
        assert "sensor_temp" in data

        # Check data length
        assert len(data["timestamps"]) == 10
        assert len(data["lux"]) == 10

        # Check that lux values are in expected range
        assert all(1000 <= lux <= 2000 for lux in data["lux"])

        # Check that exposure times are converted to seconds
        assert all(0.001 < exp < 1.0 for exp in data["exposure_time"])

    def test_analyze_empty_list(self):
        """Test analyzing empty list of images."""
        data = analyze_images([], hours=24)

        assert len(data["timestamps"]) == 0
        assert len(data["lux"]) == 0


class TestPrintStatistics:
    """Tests for print_statistics function."""

    def test_print_valid_statistics(self, capsys):
        """Test printing statistics with valid data."""
        data = {
            "timestamps": [datetime.now() - timedelta(hours=i) for i in range(5)],
            "lux": [100, 500, 1000, 5000, 10000],
            "exposure_time": [0.001, 0.01, 0.1, 1.0, 2.0],
            "analogue_gain": [1.0, 1.5, 2.0, 2.5, 3.0],
            "sensor_temp": [40, 45, 50, 55, 60],
            "colour_temp": [5000, 6000, 7000, 8000, 9000],
        }

        print_statistics(data, hours=24)

        captured = capsys.readouterr()
        assert "STATISTICAL SUMMARY" in captured.out
        assert "Light Levels" in captured.out
        assert "Exposure Time" in captured.out

    def test_print_empty_statistics(self, capsys):
        """Test printing statistics with empty data."""
        data = {
            "timestamps": [],
            "lux": [],
            "exposure_time": [],
            "analogue_gain": [],
            "sensor_temp": [],
            "colour_temp": [],
        }

        print_statistics(data, hours=24)

        # Should handle empty data gracefully (no output)
        captured = capsys.readouterr()
        assert captured.out == ""


class TestExportToExcel:
    """Tests for export_to_excel function."""

    def test_export_valid_data(self, temp_dir, sample_config, sample_images_with_metadata):
        """Test exporting valid data to Excel."""
        _, pairs = sample_images_with_metadata

        data = {
            "timestamps": [datetime.now() - timedelta(hours=i) for i in range(5)],
            "lux": [100, 500, 1000, 5000, 10000],
            "exposure_time": [0.001, 0.01, 0.1, 1.0, 2.0],
            "analogue_gain": [1.0, 1.5, 2.0, 2.5, 3.0],
            "sensor_temp": [40, 45, 50, 55, 60],
            "colour_temp": [5000, 6000, 7000, 8000, 9000],
            "colour_gains_red": [1.5, 1.6, 1.7, 1.8, 1.9],
            "colour_gains_blue": [1.3, 1.4, 1.5, 1.6, 1.7],
            "digital_gain": [1.0, 1.0, 1.0, 1.0, 1.0],
            "filenames": [f"test_{i}.jpg" for i in range(5)],
        }

        config = load_config(sample_config)
        output_path = temp_dir / "test_export.xlsx"

        export_to_excel(data, output_path, hours=24, config=config, image_pairs=pairs[:5])

        # Check that file was created
        assert output_path.exists()
        assert output_path.stat().st_size > 0

        # Try to open with openpyxl to verify it's valid
        from openpyxl import load_workbook

        wb = load_workbook(output_path)

        # Check that all sheets exist
        assert "Raw Data" in wb.sheetnames
        assert "Statistics" in wb.sheetnames
        assert "Hourly Averages" in wb.sheetnames

        # Check Raw Data sheet has data
        ws = wb["Raw Data"]
        assert ws.max_row >= 6  # Header + 5 data rows

        wb.close()


class TestEndToEnd:
    """End-to-end integration tests."""

    def test_complete_analysis_workflow(self, temp_dir, sample_config, sample_images_with_metadata):
        """Test the complete analysis workflow."""
        images_dir, pairs = sample_images_with_metadata
        config = load_config(sample_config)

        # 1. Find images
        found_pairs = find_recent_images(images_dir.parent.parent.parent, hours=24)
        assert len(found_pairs) == 10

        # 2. Analyze images
        data = analyze_images(found_pairs, hours=24)
        assert len(data["timestamps"]) == 10

        # 3. Export to Excel
        output_path = temp_dir / "complete_test.xlsx"
        export_to_excel(data, output_path, hours=24, config=config, image_pairs=found_pairs)
        assert output_path.exists()

        # 4. Verify Excel contents
        from openpyxl import load_workbook

        wb = load_workbook(output_path)

        ws_raw = wb["Raw Data"]
        assert ws_raw.max_row == 11  # Header + 10 data rows

        # Verify data is chronologically sorted
        timestamps = [ws_raw.cell(row=i, column=1).value for i in range(2, 12)]
        assert timestamps == sorted(timestamps), "Excel data should be chronologically sorted"

        wb.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
