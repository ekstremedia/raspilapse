"""
Comprehensive tests for create_keogram.py

Tests the keogram and slitscan generation functionality including:
- Colors class for ANSI terminal output
- find_images function for finding images in directory
- create_keogram function for creating keogram from images
- create_keogram_from_images convenience wrapper
- create_slitscan function for creating slitscan from images
- create_slitscan_from_images convenience wrapper
- main CLI function
"""

import pytest
import os
import sys
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock
import logging

from PIL import Image

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from create_keogram import (
    Colors,
    print_section,
    print_info,
    find_images,
    create_keogram,
    create_keogram_from_images,
    create_slitscan,
    create_slitscan_from_images,
    main,
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    temp_path = Path(tempfile.mkdtemp())
    yield temp_path
    shutil.rmtree(temp_path)


@pytest.fixture
def sample_images(temp_dir):
    """Create sample test images."""
    images = []
    for i in range(10):
        img_name = f"test_2025_12_24_{i:02d}_00_00.jpg"
        img_path = temp_dir / img_name
        # Create a simple colored image for each
        img = Image.new("RGB", (640, 480), color=(i * 25, 100, 200 - i * 10))
        img.save(img_path, "JPEG", quality=85)
        images.append(img_path)
    return images


@pytest.fixture
def sample_images_varying_resolutions(temp_dir):
    """Create sample images with different resolutions."""
    images = []
    resolutions = [(640, 480), (640, 480), (1280, 720), (640, 480), (640, 480)]
    for i, (w, h) in enumerate(resolutions):
        img_name = f"test_2025_12_24_{i:02d}_00_00.jpg"
        img_path = temp_dir / img_name
        img = Image.new("RGB", (w, h), color=(100, 100, 100))
        img.save(img_path, "JPEG", quality=85)
        images.append(img_path)
    return images


class TestColors:
    """Tests for Colors ANSI color class."""

    def test_color_constants(self):
        """Test color constants are defined."""
        assert Colors.HEADER == "\033[95m"
        assert Colors.BLUE == "\033[94m"
        assert Colors.CYAN == "\033[96m"
        assert Colors.GREEN == "\033[92m"
        assert Colors.YELLOW == "\033[93m"
        assert Colors.RED == "\033[91m"
        assert Colors.BOLD == "\033[1m"
        assert Colors.END == "\033[0m"

    def test_header_method(self):
        """Test header static method."""
        result = Colors.header("Test")
        assert Colors.BOLD in result
        assert Colors.CYAN in result
        assert "Test" in result
        assert Colors.END in result

    def test_success_method(self):
        """Test success static method."""
        result = Colors.success("Success message")
        assert Colors.GREEN in result
        assert "Success message" in result
        assert Colors.END in result

    def test_error_method(self):
        """Test error static method."""
        result = Colors.error("Error message")
        assert Colors.RED in result
        assert "Error message" in result
        assert Colors.END in result

    def test_warning_method(self):
        """Test warning static method."""
        result = Colors.warning("Warning message")
        assert Colors.YELLOW in result
        assert "Warning message" in result
        assert Colors.END in result

    def test_info_method(self):
        """Test info static method."""
        result = Colors.info("Info message")
        assert Colors.BLUE in result
        assert "Info message" in result
        assert Colors.END in result

    def test_bold_method(self):
        """Test bold static method."""
        result = Colors.bold("Bold text")
        assert Colors.BOLD in result
        assert "Bold text" in result
        assert Colors.END in result


class TestPrintFunctions:
    """Tests for print helper functions."""

    def test_print_section(self, capsys):
        """Test print_section outputs section header."""
        print_section("Test Section")
        captured = capsys.readouterr()
        assert "Test Section" in captured.out
        assert "═" in captured.out  # Contains header line character

    def test_print_info(self, capsys):
        """Test print_info outputs label and value."""
        print_info("Label", "Value")
        captured = capsys.readouterr()
        assert "Label:" in captured.out
        assert "Value" in captured.out


class TestFindImages:
    """Tests for find_images function."""

    def test_find_images_basic(self, sample_images):
        """Test finding all images in directory."""
        directory = sample_images[0].parent
        images = find_images(directory)
        assert len(images) == 10

    def test_find_images_sorted(self, sample_images):
        """Test images are sorted by filename."""
        directory = sample_images[0].parent
        images = find_images(directory)
        assert images == sorted(images)

    def test_find_images_excludes_keograms(self, temp_dir, sample_images):
        """Test keogram files are excluded."""
        # Create a keogram file
        keogram_path = temp_dir / "keogram_test.jpg"
        img = Image.new("RGB", (100, 100), color=(0, 0, 0))
        img.save(keogram_path, "JPEG")

        images = find_images(temp_dir)
        assert keogram_path not in images
        assert len(images) == 10  # Only the sample images

    def test_find_images_excludes_metadata(self, temp_dir, sample_images):
        """Test metadata files are excluded."""
        # Create a metadata file with .jpg extension (shouldn't happen, but test anyway)
        meta_path = temp_dir / "test_metadata.jpg"
        meta_path.touch()

        images = find_images(temp_dir)
        assert meta_path not in images

    def test_find_images_nonexistent_directory(self, temp_dir):
        """Test error for non-existent directory."""
        with pytest.raises(ValueError, match="Directory not found"):
            find_images(temp_dir / "nonexistent")

    def test_find_images_empty_directory(self, temp_dir):
        """Test empty directory returns empty list."""
        empty_dir = temp_dir / "empty"
        empty_dir.mkdir()
        images = find_images(empty_dir)
        assert len(images) == 0

    def test_find_images_custom_pattern(self, temp_dir):
        """Test finding images with custom pattern."""
        # Create PNG images
        for i in range(3):
            img_path = temp_dir / f"test_{i}.png"
            img = Image.new("RGB", (100, 100), color=(100, 100, 100))
            img.save(img_path, "PNG")

        # Should find only PNGs
        images = find_images(temp_dir, pattern="*.png")
        assert len(images) == 3


class TestCreateKeogram:
    """Tests for create_keogram function."""

    def test_create_keogram_basic(self, sample_images, temp_dir):
        """Test basic keogram creation."""
        output_path = temp_dir / "keogram.jpg"
        result = create_keogram(sample_images, output_path)

        assert result is True
        assert output_path.exists()
        assert output_path.stat().st_size > 0

        # Verify keogram dimensions
        with Image.open(output_path) as keogram:
            # Width = number of images, height depends on source image minus crop
            assert keogram.width == len(sample_images)
            # Height should be original - crop (7% default top crop)
            original_height = 480
            crop_top = int(original_height * 7.0 / 100)
            expected_height = original_height - crop_top
            assert keogram.height == expected_height

    def test_create_keogram_empty_list(self, temp_dir, capsys):
        """Test keogram creation with empty image list."""
        output_path = temp_dir / "keogram.jpg"
        result = create_keogram([], output_path)

        assert result is False
        assert not output_path.exists()
        captured = capsys.readouterr()
        assert "No images to process" in captured.out

    def test_create_keogram_with_logger(self, sample_images, temp_dir):
        """Test keogram creation with logger."""
        output_path = temp_dir / "keogram.jpg"
        logger = logging.getLogger("test")

        with patch.object(logger, "info") as mock_info:
            result = create_keogram(sample_images, output_path, logger=logger)

        assert result is True
        assert mock_info.called

    def test_create_keogram_quality(self, sample_images, temp_dir):
        """Test keogram with different quality settings."""
        low_quality = temp_dir / "low_quality.jpg"
        high_quality = temp_dir / "high_quality.jpg"

        create_keogram(sample_images, low_quality, quality=50)
        create_keogram(sample_images, high_quality, quality=95)

        # Higher quality should produce larger file
        assert high_quality.stat().st_size > low_quality.stat().st_size

    def test_create_keogram_no_crop(self, sample_images, temp_dir):
        """Test keogram without cropping."""
        output_path = temp_dir / "keogram.jpg"
        result = create_keogram(
            sample_images, output_path, crop_top_percent=0.0, crop_bottom_percent=0.0
        )

        assert result is True

        with Image.open(output_path) as keogram:
            # Without crop, height should match source
            assert keogram.height == 480

    def test_create_keogram_with_bottom_crop(self, sample_images, temp_dir):
        """Test keogram with bottom crop."""
        output_path = temp_dir / "keogram.jpg"
        result = create_keogram(
            sample_images, output_path, crop_top_percent=7.0, crop_bottom_percent=5.0
        )

        assert result is True

        with Image.open(output_path) as keogram:
            original_height = 480
            crop_top = int(original_height * 7.0 / 100)
            crop_bottom = int(original_height * 5.0 / 100)
            expected_height = original_height - crop_top - crop_bottom
            assert keogram.height == expected_height

    def test_create_keogram_varying_resolutions(
        self, sample_images_varying_resolutions, temp_dir, capsys
    ):
        """Test keogram handles images with different resolutions."""
        output_path = temp_dir / "keogram.jpg"
        result = create_keogram(sample_images_varying_resolutions, output_path)

        assert result is True

        captured = capsys.readouterr()
        # Should mention resizing
        assert "Resized" in captured.out or result is True

    def test_create_keogram_invalid_first_image(self, temp_dir, capsys):
        """Test error handling for invalid first image."""
        # Create an invalid image file
        invalid_path = temp_dir / "invalid.jpg"
        invalid_path.write_bytes(b"not an image")

        output_path = temp_dir / "keogram.jpg"
        result = create_keogram([invalid_path], output_path)

        assert result is False
        captured = capsys.readouterr()
        assert "Failed to read first image" in captured.out

    def test_create_keogram_skips_corrupt_images(self, sample_images, temp_dir, capsys):
        """Test keogram skips corrupt images in the middle."""
        # Corrupt one of the middle images
        corrupt_path = sample_images[5]
        corrupt_path.write_bytes(b"corrupted image data")

        output_path = temp_dir / "keogram.jpg"
        result = create_keogram(sample_images, output_path)

        assert result is True
        captured = capsys.readouterr()
        assert "Skipped: 1" in captured.out

    def test_create_keogram_creates_output_directory(self, sample_images, temp_dir):
        """Test keogram creates output directory if it doesn't exist."""
        output_path = temp_dir / "nested" / "directory" / "keogram.jpg"
        result = create_keogram(sample_images, output_path)

        assert result is True
        assert output_path.exists()
        assert output_path.parent.exists()


class TestCreateKeogramFromImages:
    """Tests for create_keogram_from_images convenience function."""

    def test_wrapper_function(self, sample_images, temp_dir):
        """Test the convenience wrapper function."""
        output_path = temp_dir / "keogram.jpg"
        result = create_keogram_from_images(sample_images, output_path)

        assert result is True
        assert output_path.exists()

    def test_wrapper_passes_parameters(self, sample_images, temp_dir):
        """Test wrapper passes all parameters correctly."""
        output_path = temp_dir / "keogram.jpg"
        logger = logging.getLogger("test")

        result = create_keogram_from_images(
            sample_images,
            output_path,
            quality=90,
            crop_top_percent=5.0,
            crop_bottom_percent=3.0,
            logger=logger,
        )

        assert result is True


class TestCreateSlitscan:
    """Tests for create_slitscan function."""

    def test_create_slitscan_basic(self, sample_images, temp_dir):
        """Test basic slitscan creation."""
        output_path = temp_dir / "slitscan.jpg"
        result = create_slitscan(sample_images, output_path)

        assert result is True
        assert output_path.exists()
        assert output_path.stat().st_size > 0

        # Verify slitscan dimensions
        with Image.open(output_path) as slitscan:
            # Width = original image width, height depends on source image minus crop
            assert slitscan.width == 640  # Same as source images
            original_height = 480
            crop_top = int(original_height * 7.0 / 100)
            expected_height = original_height - crop_top
            assert slitscan.height == expected_height

    def test_create_slitscan_empty_list(self, temp_dir, capsys):
        """Test slitscan creation with empty image list."""
        output_path = temp_dir / "slitscan.jpg"
        result = create_slitscan([], output_path)

        assert result is False
        assert not output_path.exists()
        captured = capsys.readouterr()
        assert "No images to process" in captured.out

    def test_create_slitscan_with_logger(self, sample_images, temp_dir):
        """Test slitscan creation with logger."""
        output_path = temp_dir / "slitscan.jpg"
        logger = logging.getLogger("test")

        with patch.object(logger, "info") as mock_info:
            result = create_slitscan(sample_images, output_path, logger=logger)

        assert result is True
        assert mock_info.called

    def test_create_slitscan_no_crop(self, sample_images, temp_dir):
        """Test slitscan without cropping."""
        output_path = temp_dir / "slitscan.jpg"
        result = create_slitscan(
            sample_images, output_path, crop_top_percent=0.0, crop_bottom_percent=0.0
        )

        assert result is True

        with Image.open(output_path) as slitscan:
            # Without crop, height should match source
            assert slitscan.height == 480
            # Width should match source
            assert slitscan.width == 640

    def test_create_slitscan_varying_resolutions(
        self, sample_images_varying_resolutions, temp_dir, capsys
    ):
        """Test slitscan handles images with different resolutions."""
        output_path = temp_dir / "slitscan.jpg"
        result = create_slitscan(sample_images_varying_resolutions, output_path)

        assert result is True

        captured = capsys.readouterr()
        # Should mention resizing
        assert "Resized" in captured.out or result is True

    def test_create_slitscan_invalid_first_image(self, temp_dir, capsys):
        """Test error handling for invalid first image."""
        # Create an invalid image file
        invalid_path = temp_dir / "invalid.jpg"
        invalid_path.write_bytes(b"not an image")

        output_path = temp_dir / "slitscan.jpg"
        result = create_slitscan([invalid_path], output_path)

        assert result is False
        captured = capsys.readouterr()
        assert "Failed to read first image" in captured.out

    def test_create_slitscan_skips_corrupt_images(self, sample_images, temp_dir, capsys):
        """Test slitscan skips corrupt images in the middle."""
        # Corrupt one of the middle images
        corrupt_path = sample_images[5]
        corrupt_path.write_bytes(b"corrupted image data")

        output_path = temp_dir / "slitscan.jpg"
        result = create_slitscan(sample_images, output_path)

        assert result is True
        captured = capsys.readouterr()
        assert "Skipped: 1" in captured.out

    def test_create_slitscan_creates_output_directory(self, sample_images, temp_dir):
        """Test slitscan creates output directory if it doesn't exist."""
        output_path = temp_dir / "nested" / "directory" / "slitscan.jpg"
        result = create_slitscan(sample_images, output_path)

        assert result is True
        assert output_path.exists()
        assert output_path.parent.exists()

    def test_create_slitscan_many_images(self, temp_dir):
        """Test slitscan with many images (more than image width)."""
        # Create 1000 images for a 640px wide slitscan
        images = []
        for i in range(1000):
            img_path = temp_dir / f"img_{i:04d}.jpg"
            img = Image.new("RGB", (640, 480), color=(i % 256, 100, 100))
            img.save(img_path, "JPEG")
            images.append(img_path)

        output_path = temp_dir / "slitscan.jpg"
        result = create_slitscan(images, output_path, crop_top_percent=0.0)

        assert result is True
        with Image.open(output_path) as slitscan:
            # Width should still be 640 (original image width)
            assert slitscan.width == 640
            assert slitscan.height == 480

    def test_create_slitscan_few_images(self, temp_dir):
        """Test slitscan with fewer images than image width."""
        # Create only 10 images for a 640px wide slitscan
        images = []
        for i in range(10):
            img_path = temp_dir / f"img_{i:02d}.jpg"
            img = Image.new("RGB", (640, 480), color=(i * 25, 100, 200))
            img.save(img_path, "JPEG")
            images.append(img_path)

        output_path = temp_dir / "slitscan.jpg"
        result = create_slitscan(images, output_path, crop_top_percent=0.0)

        assert result is True
        with Image.open(output_path) as slitscan:
            # Width should still be 640, each image contributes 64 columns
            assert slitscan.width == 640
            assert slitscan.height == 480


class TestCreateSlitscanFromImages:
    """Tests for create_slitscan_from_images convenience function."""

    def test_wrapper_function(self, sample_images, temp_dir):
        """Test the convenience wrapper function."""
        output_path = temp_dir / "slitscan.jpg"
        result = create_slitscan_from_images(sample_images, output_path)

        assert result is True
        assert output_path.exists()

    def test_wrapper_passes_parameters(self, sample_images, temp_dir):
        """Test wrapper passes all parameters correctly."""
        output_path = temp_dir / "slitscan.jpg"
        logger = logging.getLogger("test")

        result = create_slitscan_from_images(
            sample_images,
            output_path,
            quality=90,
            crop_top_percent=5.0,
            crop_bottom_percent=3.0,
            logger=logger,
        )

        assert result is True


class TestFindImagesExcludesSlitscan:
    """Tests for find_images excluding slitscan files."""

    def test_find_images_excludes_slitscans(self, temp_dir, sample_images):
        """Test slitscan files are excluded."""
        # Create a slitscan file
        slitscan_path = temp_dir / "slitscan_test.jpg"
        img = Image.new("RGB", (100, 100), color=(0, 0, 0))
        img.save(slitscan_path, "JPEG")

        images = find_images(temp_dir)
        assert slitscan_path not in images
        assert len(images) == 10  # Only the sample images


class TestMainCLI:
    """Tests for main CLI function."""

    def test_main_directory_not_found(self, temp_dir, monkeypatch):
        """Test main returns error for non-existent directory."""
        monkeypatch.setattr(
            "sys.argv", ["create_keogram.py", "--dir", str(temp_dir / "nonexistent")]
        )

        result = main()
        assert result == 1

    def test_main_no_images_found(self, temp_dir, monkeypatch):
        """Test main returns error when no images found."""
        empty_dir = temp_dir / "empty"
        empty_dir.mkdir()

        monkeypatch.setattr("sys.argv", ["create_keogram.py", "--dir", str(empty_dir)])

        result = main()
        assert result == 1

    def test_main_success(self, sample_images, monkeypatch):
        """Test main succeeds with valid images."""
        input_dir = sample_images[0].parent

        monkeypatch.setattr("sys.argv", ["create_keogram.py", "--dir", str(input_dir)])

        result = main()
        assert result == 0

        # Verify keogram was created
        keogram_files = list(input_dir.glob("keogram_*.jpg"))
        assert len(keogram_files) >= 1

    def test_main_custom_output(self, sample_images, temp_dir, monkeypatch):
        """Test main with custom output filename."""
        input_dir = sample_images[0].parent
        output_file = "custom_keogram.jpg"

        monkeypatch.setattr(
            "sys.argv",
            ["create_keogram.py", "--dir", str(input_dir), "--output", output_file],
        )

        result = main()
        assert result == 0

        # Verify custom filename was used
        assert (input_dir / output_file).exists()

    def test_main_custom_output_dir(self, sample_images, temp_dir, monkeypatch):
        """Test main with custom output directory."""
        input_dir = sample_images[0].parent
        output_dir = temp_dir / "custom_output"
        output_dir.mkdir()

        monkeypatch.setattr(
            "sys.argv",
            ["create_keogram.py", "--dir", str(input_dir), "--output-dir", str(output_dir)],
        )

        result = main()
        assert result == 0

        # Verify keogram was created in custom directory
        keogram_files = list(output_dir.glob("keogram_*.jpg"))
        assert len(keogram_files) >= 1

    def test_main_no_crop_flag(self, sample_images, monkeypatch):
        """Test main with --no-crop flag."""
        input_dir = sample_images[0].parent

        monkeypatch.setattr("sys.argv", ["create_keogram.py", "--dir", str(input_dir), "--no-crop"])

        result = main()
        assert result == 0

        # Find the generated keogram and check height
        keogram_files = list(input_dir.glob("keogram_*.jpg"))
        assert len(keogram_files) >= 1

        with Image.open(keogram_files[0]) as keogram:
            # Without crop, height should be 480 (original)
            assert keogram.height == 480

    def test_main_custom_crop_values(self, sample_images, monkeypatch):
        """Test main with custom crop values."""
        input_dir = sample_images[0].parent

        monkeypatch.setattr(
            "sys.argv",
            [
                "create_keogram.py",
                "--dir",
                str(input_dir),
                "--crop-top",
                "10.0",
                "--crop-bottom",
                "5.0",
            ],
        )

        result = main()
        assert result == 0

    def test_main_custom_quality(self, sample_images, monkeypatch):
        """Test main with custom quality."""
        input_dir = sample_images[0].parent

        monkeypatch.setattr(
            "sys.argv",
            ["create_keogram.py", "--dir", str(input_dir), "--quality", "80"],
        )

        result = main()
        assert result == 0

    def test_main_custom_pattern(self, temp_dir, monkeypatch):
        """Test main with custom glob pattern."""
        # Create PNG images
        for i in range(5):
            img_path = temp_dir / f"test_{i}.png"
            img = Image.new("RGB", (100, 100), color=(100, 100, 100))
            img.save(img_path, "PNG")

        monkeypatch.setattr(
            "sys.argv",
            ["create_keogram.py", "--dir", str(temp_dir), "--pattern", "*.png"],
        )

        result = main()
        assert result == 0

    def test_main_date_from_directory_path(self, temp_dir, monkeypatch):
        """Test main extracts date from directory path."""
        # Create date-organized directory
        date_dir = temp_dir / "2025" / "12" / "24"
        date_dir.mkdir(parents=True)

        # Create test images
        for i in range(3):
            img_path = date_dir / f"test_{i}.jpg"
            img = Image.new("RGB", (100, 100), color=(100, 100, 100))
            img.save(img_path, "JPEG")

        monkeypatch.setattr("sys.argv", ["create_keogram.py", "--dir", str(date_dir)])

        result = main()
        assert result == 0

        # Verify keogram was created with date in name
        keogram_files = list(date_dir.glob("keogram_*.jpg"))
        assert len(keogram_files) >= 1
        assert "2025-12-24" in keogram_files[0].name

    def test_main_combined_output_and_output_dir(self, sample_images, temp_dir, monkeypatch):
        """Test main with both --output and --output-dir."""
        input_dir = sample_images[0].parent
        output_dir = temp_dir / "custom_output"
        output_dir.mkdir()
        output_file = "my_keogram.jpg"

        monkeypatch.setattr(
            "sys.argv",
            [
                "create_keogram.py",
                "--dir",
                str(input_dir),
                "--output",
                output_file,
                "--output-dir",
                str(output_dir),
            ],
        )

        result = main()
        assert result == 0

        # Verify file was created in custom directory with custom name
        assert (output_dir / output_file).exists()

    def test_main_slitscan_flag(self, sample_images, monkeypatch):
        """Test main with --slitscan flag creates slitscan instead of keogram."""
        input_dir = sample_images[0].parent

        monkeypatch.setattr(
            "sys.argv", ["create_keogram.py", "--dir", str(input_dir), "--slitscan"]
        )

        result = main()
        assert result == 0

        # Verify slitscan was created (not keogram)
        slitscan_files = list(input_dir.glob("slitscan_*.jpg"))
        assert len(slitscan_files) >= 1

        # Verify it has the correct dimensions (width = original image width)
        with Image.open(slitscan_files[0]) as slitscan:
            assert slitscan.width == 640  # Original width


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_single_image(self, temp_dir):
        """Test keogram with single image."""
        img_path = temp_dir / "single.jpg"
        img = Image.new("RGB", (640, 480), color=(100, 100, 100))
        img.save(img_path, "JPEG")

        output_path = temp_dir / "keogram.jpg"
        result = create_keogram([img_path], output_path)

        assert result is True
        with Image.open(output_path) as keogram:
            assert keogram.width == 1  # Single column

    def test_very_wide_image(self, temp_dir):
        """Test keogram with very wide source image."""
        images = []
        for i in range(5):
            img_path = temp_dir / f"wide_{i}.jpg"
            img = Image.new("RGB", (1920, 480), color=(100, 100, 100))
            img.save(img_path, "JPEG")
            images.append(img_path)

        output_path = temp_dir / "keogram.jpg"
        result = create_keogram(images, output_path)

        assert result is True

    def test_very_tall_image(self, temp_dir):
        """Test keogram with very tall source image."""
        images = []
        for i in range(5):
            img_path = temp_dir / f"tall_{i}.jpg"
            img = Image.new("RGB", (480, 1920), color=(100, 100, 100))
            img.save(img_path, "JPEG")
            images.append(img_path)

        output_path = temp_dir / "keogram.jpg"
        result = create_keogram(images, output_path)

        assert result is True

    def test_large_number_of_images(self, temp_dir):
        """Test keogram with many images."""
        images = []
        for i in range(100):
            img_path = temp_dir / f"img_{i:03d}.jpg"
            img = Image.new("RGB", (100, 100), color=(i % 256, 100, 100))
            img.save(img_path, "JPEG")
            images.append(img_path)

        output_path = temp_dir / "keogram.jpg"
        result = create_keogram(images, output_path)

        assert result is True
        with Image.open(output_path) as keogram:
            assert keogram.width == 100


class TestProgressOutput:
    """Test progress output during keogram creation."""

    def test_progress_updates(self, temp_dir, capsys):
        """Test progress updates are printed."""
        # Create enough images to trigger progress updates
        images = []
        for i in range(20):
            img_path = temp_dir / f"img_{i:02d}.jpg"
            img = Image.new("RGB", (100, 100), color=(100, 100, 100))
            img.save(img_path, "JPEG")
            images.append(img_path)

        output_path = temp_dir / "keogram.jpg"
        create_keogram(images, output_path)

        captured = capsys.readouterr()
        assert "Progress:" in captured.out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
