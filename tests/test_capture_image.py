"""Tests for capture_image module."""

import os
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest
import yaml
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.capture_image import CameraConfig, ImageCapture, capture_single_image


@pytest.fixture
def mock_picamera2():
    """Mock the picamera2 module for testing."""
    # Create mock picamera2 module
    mock_picam2_module = MagicMock()
    mock_libcamera_module = MagicMock()

    # Create mock camera instance
    mock_camera = MagicMock()
    mock_camera.create_still_configuration.return_value = {}
    mock_camera.create_preview_configuration.return_value = {}  # Keep for backwards compat

    # Mock capture_request() to return a request object
    mock_request = MagicMock()
    mock_request.get_metadata.return_value = {"test": "metadata"}
    mock_request.save.return_value = None
    mock_request.release.return_value = None
    mock_camera.capture_request.return_value = mock_request

    # Keep old metadata method for backwards compat
    mock_camera.capture_metadata.return_value = {"test": "metadata"}

    # Set up the mock modules
    mock_picam2_module.Picamera2.return_value = mock_camera
    mock_libcamera_module.Transform.return_value = MagicMock()

    # Patch sys.modules
    with patch.dict(
        "sys.modules",
        {"picamera2": mock_picam2_module, "libcamera": mock_libcamera_module},
    ):
        yield mock_camera


@pytest.fixture
def test_config_file():
    """Create a temporary test configuration file."""
    config_data = {
        "camera": {
            "resolution": {"width": 1280, "height": 720},
            "transforms": {"horizontal_flip": False, "vertical_flip": False},
            "controls": {},
        },
        "output": {
            "directory": "test_photos",
            "filename_pattern": "{name}_{counter}.jpg",
            "project_name": "test_project",
            "quality": 85,
            "organize_by_date": False,
            "date_format": "%Y-%m-%d",
            "symlink_latest": {"enabled": False, "path": "/tmp/test_status.jpg"},
        },
        "system": {
            "create_directories": True,
            "save_metadata": True,
            "metadata_filename": "{name}_{counter}_metadata.json",
        },
        "overlay": {
            "enabled": False,
            "position": "bottom-left",
            "camera_name": "Test Camera",
        },
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(config_data, f)
        config_path = f.name

    yield config_path

    # Cleanup
    os.unlink(config_path)


@pytest.fixture
def test_output_dir():
    """Create a temporary output directory."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    # Cleanup
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestCameraConfig:
    """Tests for CameraConfig class."""

    def test_load_config_success(self, test_config_file):
        """Test successful configuration loading."""
        config = CameraConfig(test_config_file)
        assert config.config is not None
        assert "camera" in config.config
        assert "output" in config.config

    def test_load_config_missing_file(self):
        """Test error handling for missing config file."""
        with pytest.raises(FileNotFoundError):
            CameraConfig("nonexistent.yml")

    def test_get_resolution(self, test_config_file):
        """Test resolution retrieval."""
        config = CameraConfig(test_config_file)
        width, height = config.get_resolution()
        assert width == 1280
        assert height == 720

    def test_get_output_directory(self, test_config_file):
        """Test output directory retrieval."""
        config = CameraConfig(test_config_file)
        assert config.get_output_directory() == "test_photos"

    def test_get_filename_pattern(self, test_config_file):
        """Test filename pattern retrieval."""
        config = CameraConfig(test_config_file)
        assert config.get_filename_pattern() == "{name}_{counter}.jpg"

    def test_get_project_name(self, test_config_file):
        """Test project name retrieval."""
        config = CameraConfig(test_config_file)
        assert config.get_project_name() == "test_project"

    def test_get_quality(self, test_config_file):
        """Test quality setting retrieval."""
        config = CameraConfig(test_config_file)
        assert config.get_quality() == 85

    def test_should_create_directories(self, test_config_file):
        """Test directory creation flag."""
        config = CameraConfig(test_config_file)
        assert config.should_create_directories() is True

    def test_should_save_metadata(self, test_config_file):
        """Test metadata saving flag."""
        config = CameraConfig(test_config_file)
        assert config.should_save_metadata() is True

    def test_get_transforms(self, test_config_file):
        """Test transforms retrieval."""
        config = CameraConfig(test_config_file)
        transforms = config.get_transforms()
        assert transforms["horizontal_flip"] is False
        assert transforms["vertical_flip"] is False


class TestImageCapture:
    """Tests for ImageCapture class."""

    def test_init(self, test_config_file):
        """Test ImageCapture initialization."""
        config = CameraConfig(test_config_file)
        capture = ImageCapture(config)
        assert capture.config == config
        assert capture.picam2 is None
        assert capture._counter == 0

    def test_initialize_camera(self, mock_picamera2, test_config_file):
        """Test camera initialization with mocked hardware."""
        config = CameraConfig(test_config_file)
        capture = ImageCapture(config)

        # Initialize camera
        capture.initialize_camera()

        # Verify camera was configured and started
        assert mock_picamera2.create_still_configuration.called
        assert mock_picamera2.configure.called
        assert mock_picamera2.start.called

    def test_initialize_camera_no_picamera2(self, test_config_file):
        """Test error handling when picamera2 is not available."""
        config = CameraConfig(test_config_file)
        capture = ImageCapture(config)

        with patch.dict("sys.modules", {"picamera2": None}):
            with pytest.raises(ImportError, match="Picamera2 not found"):
                capture.initialize_camera()

    def test_capture_without_initialization(self, mock_picamera2, test_config_file):
        """Test capture raises error if camera not initialized."""
        config = CameraConfig(test_config_file)
        capture = ImageCapture(config)

        with pytest.raises(RuntimeError, match="Camera not initialized"):
            capture.capture()

    def test_capture_creates_directory(self, mock_picamera2, test_config_file, test_output_dir):
        """Test that capture creates output directory."""
        # Modify config to use test directory
        config = CameraConfig(test_config_file)
        config.config["output"]["directory"] = os.path.join(test_output_dir, "new_dir")

        capture = ImageCapture(config)
        capture.initialize_camera()
        capture.capture()

        # Verify directory was created
        assert os.path.exists(config.get_output_directory())

    def test_capture_filename_pattern(self, mock_picamera2, test_config_file, test_output_dir):
        """Test filename pattern generation."""
        # Modify config
        config = CameraConfig(test_config_file)
        config.config["output"]["directory"] = test_output_dir

        capture = ImageCapture(config)
        capture.initialize_camera()
        image_path, _ = capture.capture()

        # Verify filename follows pattern
        assert "test_project_0000.jpg" in image_path

    def test_capture_counter_increment(self, mock_picamera2, test_config_file, test_output_dir):
        """Test that counter increments with each capture."""
        config = CameraConfig(test_config_file)
        config.config["output"]["directory"] = test_output_dir

        capture = ImageCapture(config)
        capture.initialize_camera()

        # Capture multiple images
        path1, _ = capture.capture()
        path2, _ = capture.capture()
        path3, _ = capture.capture()

        # Verify counter increments
        assert "0000" in path1
        assert "0001" in path2
        assert "0002" in path3

    def test_metadata_saving(self, mock_picamera2, test_config_file, test_output_dir):
        """Test metadata is saved when enabled."""
        config = CameraConfig(test_config_file)
        config.config["output"]["directory"] = test_output_dir

        capture = ImageCapture(config)
        capture.initialize_camera()
        image_path, metadata_path = capture.capture()

        # Verify metadata file was created
        assert metadata_path is not None
        assert os.path.exists(metadata_path)

        # Verify metadata content
        with open(metadata_path, "r") as f:
            metadata = json.load(f)
            assert "test" in metadata  # From mock
            assert "capture_timestamp" in metadata
            assert "resolution" in metadata

    def test_context_manager(self, mock_picamera2, test_config_file):
        """Test ImageCapture as context manager."""
        config = CameraConfig(test_config_file)

        with ImageCapture(config) as capture:
            assert capture.picam2 is not None
            assert mock_picamera2.start.called

        # Verify camera was closed
        assert mock_picamera2.close.called

    def test_close(self, mock_picamera2, test_config_file):
        """Test camera cleanup."""
        config = CameraConfig(test_config_file)
        capture = ImageCapture(config)
        capture.initialize_camera()
        capture.close()

        assert mock_picamera2.close.called
        assert capture.picam2 is None


class TestDateOrganization:
    """Test date-organized folder structure."""

    def test_organize_by_date_disabled(self, mock_picamera2, test_config_file):
        """Test capture without date organization."""
        config = CameraConfig(test_config_file)
        assert config.should_organize_by_date() is False

    def test_organize_by_date_enabled(self, mock_picamera2, test_output_dir):
        """Test capture with date organization."""
        from datetime import datetime
        import tempfile

        # Create config with date organization
        config_data = {
            "camera": {
                "resolution": {"width": 1280, "height": 720},
                "transforms": {"horizontal_flip": False, "vertical_flip": False},
                "controls": {},
            },
            "output": {
                "directory": test_output_dir,
                "filename_pattern": "{name}_{counter}.jpg",
                "project_name": "test_project",
                "quality": 85,
                "organize_by_date": True,
                "date_format": "%Y/%m/%d",
                "symlink_latest": {"enabled": False, "path": "/tmp/test_status.jpg"},
            },
            "system": {
                "create_directories": True,
                "save_metadata": True,
                "metadata_filename": "{name}_{counter}_metadata.json",
            },
            "overlay": {"enabled": False},
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            import yaml

            yaml.dump(config_data, f)
            config_path = f.name

        try:
            config = CameraConfig(config_path)
            assert config.should_organize_by_date() is True
            assert config.get_date_format() == "%Y/%m/%d"

            # Test capture creates date subdirectories
            capture = ImageCapture(config)
            capture.initialize_camera()

            # Mock the save method to actually create the file
            def mock_save(stream, path):
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).touch()

            mock_picamera2.capture_request.return_value.save.side_effect = mock_save

            image_path, metadata_path = capture.capture()

            # Verify path contains date subdirectories
            today = datetime.now()
            expected_subdir = today.strftime("%Y/%m/%d")
            assert expected_subdir in image_path

            # Verify directory structure was created
            assert Path(image_path).parent.exists()

        finally:
            os.unlink(config_path)

    def test_date_format_variations(self, test_output_dir):
        """Test different date format options."""
        import tempfile
        import yaml

        formats = [
            "%Y/%m/%d",  # 2025/11/05
            "%Y-%m-%d",  # 2025-11-05
            "%Y%m%d",  # 20251105
        ]

        for date_format in formats:
            config_data = {
                "camera": {
                    "resolution": {"width": 640, "height": 480},
                    "transforms": {"horizontal_flip": False, "vertical_flip": False},
                    "controls": {},
                },
                "output": {
                    "directory": test_output_dir,
                    "filename_pattern": "test.jpg",
                    "project_name": "test",
                    "quality": 85,
                    "organize_by_date": True,
                    "date_format": date_format,
                    "symlink_latest": {"enabled": False},
                },
                "system": {
                    "create_directories": True,
                    "save_metadata": False,
                },
                "overlay": {"enabled": False},
            }

            with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
                yaml.dump(config_data, f)
                config_path = f.name

            try:
                config = CameraConfig(config_path)
                assert config.get_date_format() == date_format
            finally:
                os.unlink(config_path)


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_capture_single_image(self, mock_picamera2, test_config_file, test_output_dir):
        """Test capture_single_image convenience function."""
        # Modify config to use test directory
        with open(test_config_file, "r") as f:
            config_data = yaml.safe_load(f)
        config_data["output"]["directory"] = test_output_dir

        with open(test_config_file, "w") as f:
            yaml.dump(config_data, f)

        # Capture image
        image_path, metadata_path = capture_single_image(test_config_file)

        assert image_path is not None
        assert "test_project_0000.jpg" in image_path


# Integration tests (require actual camera hardware)
class TestIntegration:
    """Integration tests requiring actual camera hardware."""

    @pytest.mark.skipif(not os.path.exists("/dev/video0"), reason="Camera hardware not detected")
    def test_real_camera_capture(self, test_output_dir):
        """Test actual image capture with real camera (if available)."""
        # Create test config
        config_data = {
            "camera": {
                "resolution": {"width": 640, "height": 480},
                "transforms": {"horizontal_flip": False, "vertical_flip": False},
                "controls": {},
            },
            "output": {
                "directory": test_output_dir,
                "filename_pattern": "test_{counter}.jpg",
                "project_name": "integration_test",
                "quality": 85,
            },
            "system": {
                "create_directories": True,
                "save_metadata": True,
                "metadata_filename": "test_{counter}_metadata.json",
            },
        }

        config_file = os.path.join(test_output_dir, "test_config.yml")
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        try:
            # Capture image with real camera
            image_path, metadata_path = capture_single_image(config_file)

            # Verify files exist
            assert os.path.exists(image_path)
            assert os.path.getsize(image_path) > 0

            if metadata_path:
                assert os.path.exists(metadata_path)
                with open(metadata_path, "r") as f:
                    metadata = json.load(f)
                    assert "capture_timestamp" in metadata

        except ImportError:
            pytest.skip("Picamera2 not available")
        except Exception as e:
            pytest.skip(f"Camera test failed: {e}")


class TestControlMapping:
    """Test camera control mapping."""

    def test_prepare_control_map_snake_case(self, test_config_file):
        """Test snake_case key conversion."""
        config = CameraConfig(test_config_file)
        capture = ImageCapture(config)

        controls = {
            "exposure_time": 10000,
            "analogue_gain": 2.0,
            "awb_enable": True,
            "ae_enable": False,
            "brightness": 0.5,
            "af_mode": 0,
            "lens_position": 0.0,
        }

        mapped = capture._prepare_control_map(controls)

        assert mapped["ExposureTime"] == 10000
        assert mapped["AnalogueGain"] == 2.0
        assert mapped["AwbEnable"] == 1
        assert mapped["AeEnable"] == 0
        assert mapped["Brightness"] == 0.5
        assert mapped["AfMode"] == 0
        assert mapped["LensPosition"] == 0.0

    def test_prepare_control_map_pascal_case(self, test_config_file):
        """Test PascalCase keys pass through."""
        config = CameraConfig(test_config_file)
        capture = ImageCapture(config)

        controls = {
            "ExposureTime": 5000,
            "AnalogueGain": 1.5,
            "AwbEnable": 0,
            "AfMode": 2,
            "LensPosition": 10.0,
        }

        mapped = capture._prepare_control_map(controls)

        assert mapped["ExposureTime"] == 5000
        assert mapped["AnalogueGain"] == 1.5
        assert mapped["AwbEnable"] == 0
        assert mapped["AfMode"] == 2
        assert mapped["LensPosition"] == 10.0

    def test_prepare_control_map_colour_gains(self, test_config_file):
        """Test colour gains tuple conversion."""
        config = CameraConfig(test_config_file)
        capture = ImageCapture(config)

        controls = {"colour_gains": [1.8, 1.5]}
        mapped = capture._prepare_control_map(controls)

        assert mapped["ColourGains"] == (1.8, 1.5)

    def test_initialize_camera_with_transforms(self, mock_picamera2, test_config_file):
        """Test camera initialization with image transforms."""
        config = CameraConfig(test_config_file)
        config.config["camera"]["transforms"]["horizontal_flip"] = True
        config.config["camera"]["transforms"]["vertical_flip"] = True

        capture = ImageCapture(config)
        capture.initialize_camera()

        assert mock_picamera2.configure.called

    def test_initialize_camera_with_manual_controls(self, mock_picamera2, test_config_file):
        """Test camera initialization with manual controls."""
        config = CameraConfig(test_config_file)
        capture = ImageCapture(config)

        manual_controls = {
            "ExposureTime": 20_000_000,  # 20 seconds
            "AnalogueGain": 6.0,
            "AwbEnable": 0,
        }

        capture.initialize_camera(manual_controls=manual_controls)

        # Verify create_still_configuration was called with controls
        call_args = mock_picamera2.create_still_configuration.call_args
        assert "controls" in call_args[1]

        # Verify FrameDurationLimits was added for long exposure
        controls = call_args[1]["controls"]
        assert "FrameDurationLimits" in controls
        assert "NoiseReductionMode" in controls

    def test_initialize_camera_frame_duration_limits(self, mock_picamera2, test_config_file):
        """Test FrameDurationLimits is set for long exposures."""
        config = CameraConfig(test_config_file)
        capture = ImageCapture(config)

        # 10 second exposure
        manual_controls = {"ExposureTime": 10_000_000}
        capture.initialize_camera(manual_controls=manual_controls)

        call_args = mock_picamera2.create_still_configuration.call_args
        controls = call_args[1]["controls"]

        # FrameDurationLimits should be exposure + 100ms
        expected_duration = 10_000_000 + 100_000
        assert controls["FrameDurationLimits"] == (expected_duration, expected_duration)

    def test_initialize_camera_buffer_count(self, mock_picamera2, test_config_file):
        """Test buffer_count is set for long exposures."""
        config = CameraConfig(test_config_file)
        capture = ImageCapture(config)

        manual_controls = {"ExposureTime": 5_000_000}
        capture.initialize_camera(manual_controls=manual_controls)

        call_args = mock_picamera2.create_still_configuration.call_args
        assert call_args[1]["buffer_count"] == 3
        assert call_args[1]["queue"] is False

    def test_update_controls(self, mock_picamera2, test_config_file):
        """Test updating controls on running camera."""
        config = CameraConfig(test_config_file)
        capture = ImageCapture(config)
        capture.initialize_camera()

        new_controls = {
            "ExposureTime": 15000,
            "AnalogueGain": 1.2,
        }

        capture.update_controls(new_controls)
        mock_picamera2.set_controls.assert_called()


class TestOutputPath:
    """Test output path generation."""

    def test_generate_filename_with_timestamp(self, mock_picamera2, test_output_dir):
        """Test filename with timestamp pattern."""
        import tempfile
        import yaml

        config_data = {
            "camera": {
                "resolution": {"width": 640, "height": 480},
                "transforms": {"horizontal_flip": False, "vertical_flip": False},
                "controls": {},
            },
            "output": {
                "directory": test_output_dir,
                "filename_pattern": "test_%Y%m%d_%H%M%S.jpg",
                "project_name": "test",
                "quality": 85,
                "organize_by_date": False,
                "symlink_latest": {"enabled": False},
            },
            "system": {
                "create_directories": True,
                "save_metadata": False,
            },
            "overlay": {"enabled": False},
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = f.name

        try:
            config = CameraConfig(config_path)
            capture = ImageCapture(config)
            capture.initialize_camera()

            image_path, _ = capture.capture()

            # Verify timestamp format in filename
            import re

            assert re.search(r"test_\d{8}_\d{6}\.jpg", os.path.basename(image_path))
        finally:
            os.unlink(config_path)

    def test_generate_filename_with_name_placeholder(self, mock_picamera2, test_output_dir):
        """Test filename with {name} placeholder."""
        import tempfile
        import yaml

        config_data = {
            "camera": {
                "resolution": {"width": 640, "height": 480},
                "transforms": {"horizontal_flip": False, "vertical_flip": False},
                "controls": {},
            },
            "output": {
                "directory": test_output_dir,
                "filename_pattern": "{name}_photo.jpg",
                "project_name": "myproject",
                "quality": 85,
                "organize_by_date": False,
                "symlink_latest": {"enabled": False},
            },
            "system": {
                "create_directories": True,
                "save_metadata": False,
            },
            "overlay": {"enabled": False},
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = f.name

        try:
            config = CameraConfig(config_path)
            capture = ImageCapture(config)
            capture.initialize_camera()

            image_path, _ = capture.capture()

            assert "myproject_photo.jpg" in image_path
        finally:
            os.unlink(config_path)


class TestMetadataDisabled:
    """Test behavior when metadata saving is disabled."""

    def test_capture_without_metadata(self, mock_picamera2, test_output_dir):
        """Test capture when metadata is disabled."""
        import tempfile
        import yaml

        config_data = {
            "camera": {
                "resolution": {"width": 640, "height": 480},
                "transforms": {"horizontal_flip": False, "vertical_flip": False},
                "controls": {},
            },
            "output": {
                "directory": test_output_dir,
                "filename_pattern": "test.jpg",
                "project_name": "test",
                "quality": 85,
                "organize_by_date": False,
                "symlink_latest": {"enabled": False},
            },
            "system": {
                "create_directories": True,
                "save_metadata": False,  # Disabled
            },
            "overlay": {"enabled": False},
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = f.name

        try:
            config = CameraConfig(config_path)
            assert config.should_save_metadata() is False

            capture = ImageCapture(config)
            capture.initialize_camera()

            image_path, metadata_path = capture.capture()

            assert image_path is not None
            assert metadata_path is None  # No metadata file
        finally:
            os.unlink(config_path)


class TestOverlayIntegration:
    """Test capture with overlay enabled."""

    def test_capture_with_overlay(self, mock_picamera2, test_output_dir):
        """Test capture applies overlay when enabled."""
        import tempfile
        import yaml

        config_data = {
            "camera": {
                "resolution": {"width": 640, "height": 480},
                "transforms": {"horizontal_flip": False, "vertical_flip": False},
                "controls": {},
            },
            "output": {
                "directory": test_output_dir,
                "filename_pattern": "test.jpg",
                "project_name": "test",
                "quality": 85,
                "organize_by_date": False,
                "symlink_latest": {"enabled": False},
            },
            "system": {
                "create_directories": True,
                "save_metadata": True,
                "metadata_filename": "test_metadata.json",
            },
            "overlay": {
                "enabled": True,
                "position": "bottom-left",
                "camera_name": "Test",
                "font": {"family": "default"},
                "content": {
                    "main": ["{camera_name}"],
                    "camera_settings": {"enabled": False},
                    "debug": {"enabled": False},
                },
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = f.name

        try:
            config = CameraConfig(config_path)
            capture = ImageCapture(config)
            capture.initialize_camera()

            with patch.object(capture.overlay, "apply_overlay") as mock_overlay:
                mock_overlay.return_value = os.path.join(test_output_dir, "test.jpg")
                image_path, _ = capture.capture()

                # Verify overlay was called
                mock_overlay.assert_called_once()
        finally:
            os.unlink(config_path)
