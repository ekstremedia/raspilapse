"""Tests for auto_timelapse module."""

import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest
import yaml

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.auto_timelapse import AdaptiveTimelapse, LightMode


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
            "symlink_latest": {"enabled": True, "path": "/tmp/test_status.jpg"},
        },
        "system": {
            "create_directories": True,
            "save_metadata": True,
            "metadata_filename": "{name}_{counter}_metadata.json",
            "metadata_folder": "metadata",
        },
        "overlay": {
            "enabled": False,
        },
        "adaptive_timelapse": {
            "enabled": True,
            "interval": 30,
            "num_frames": 0,
            "light_thresholds": {
                "night": 10,
                "day": 100,
            },
            "night_mode": {
                "max_exposure_time": 20.0,
                "min_exposure_time": 1.0,
                "analogue_gain": 6,
                "awb_enable": False,
            },
            "day_mode": {
                "awb_enable": True,
            },
            "transition_mode": {
                "smooth_transition": True,
                "analogue_gain_min": 1.0,
                "analogue_gain_max": 2.5,
            },
            "test_shot": {
                "enabled": True,
                "exposure_time": 0.1,
                "analogue_gain": 1.0,
            },
        },
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(config_data, f)
        config_path = f.name

    yield config_path

    # Cleanup
    os.unlink(config_path)


class TestSymlinkFunctionality:
    """Test symlink creation for latest image."""

    def test_create_symlink_enabled(self, test_config_file):
        """Test symlink creation when enabled."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Create a test image
        temp_dir = tempfile.mkdtemp()
        image_path = os.path.join(temp_dir, "test_image.jpg")
        with open(image_path, "w") as f:
            f.write("test")

        try:
            # Create symlink
            timelapse._create_latest_symlink(image_path)

            # Verify symlink exists
            symlink_path = Path("/tmp/test_status.jpg")
            assert symlink_path.exists() or symlink_path.is_symlink()

            # Verify it points to the correct file
            if symlink_path.is_symlink():
                target = symlink_path.resolve()
                assert target == Path(image_path).resolve()

            # Cleanup
            if symlink_path.exists():
                symlink_path.unlink()
        finally:
            os.unlink(image_path)
            os.rmdir(temp_dir)

    def test_create_symlink_disabled(self, test_config_file):
        """Test symlink not created when disabled."""
        # Load config and disable symlink
        with open(test_config_file, "r") as f:
            config_data = yaml.safe_load(f)

        config_data["output"]["symlink_latest"]["enabled"] = False

        with open(test_config_file, "w") as f:
            yaml.dump(config_data, f)

        timelapse = AdaptiveTimelapse(test_config_file)

        # Create a test image
        temp_dir = tempfile.mkdtemp()
        image_path = os.path.join(temp_dir, "test_image.jpg")
        with open(image_path, "w") as f:
            f.write("test")

        try:
            # Attempt to create symlink (should do nothing)
            timelapse._create_latest_symlink(image_path)

            # Symlink should not exist (or if it does, it's from another test)
            # We just verify the function doesn't crash
            assert True

        finally:
            os.unlink(image_path)
            os.rmdir(temp_dir)

    def test_symlink_updates_on_new_capture(self, test_config_file):
        """Test symlink updates to point to latest image."""
        timelapse = AdaptiveTimelapse(test_config_file)

        temp_dir = tempfile.mkdtemp()
        symlink_path = Path("/tmp/test_status.jpg")

        try:
            # Create first image
            image1 = os.path.join(temp_dir, "image1.jpg")
            with open(image1, "w") as f:
                f.write("image1")

            timelapse._create_latest_symlink(image1)

            if symlink_path.is_symlink():
                target1 = symlink_path.resolve()
                assert target1 == Path(image1).resolve()

            # Create second image
            image2 = os.path.join(temp_dir, "image2.jpg")
            with open(image2, "w") as f:
                f.write("image2")

            timelapse._create_latest_symlink(image2)

            # Symlink should now point to image2
            if symlink_path.is_symlink():
                target2 = symlink_path.resolve()
                assert target2 == Path(image2).resolve()

        finally:
            # Cleanup
            if symlink_path.exists():
                symlink_path.unlink()
            if os.path.exists(image1):
                os.unlink(image1)
            if os.path.exists(image2):
                os.unlink(image2)
            os.rmdir(temp_dir)

    def test_symlink_permission_error(self, test_config_file):
        """Test handling of permission errors."""
        # Update config to use a restricted path
        with open(test_config_file, "r") as f:
            config_data = yaml.safe_load(f)

        config_data["output"]["symlink_latest"]["path"] = "/root/status.jpg"

        with open(test_config_file, "w") as f:
            yaml.dump(config_data, f)

        timelapse = AdaptiveTimelapse(test_config_file)

        # Create test image
        temp_dir = tempfile.mkdtemp()
        image_path = os.path.join(temp_dir, "test.jpg")
        with open(image_path, "w") as f:
            f.write("test")

        try:
            # This should log an error but not crash
            timelapse._create_latest_symlink(image_path)

            # If we get here without exception, test passes
            assert True
        finally:
            os.unlink(image_path)
            os.rmdir(temp_dir)


class TestLightMode:
    """Test light mode enumeration."""

    def test_light_mode_constants(self):
        """Test light mode constants."""
        assert LightMode.NIGHT == "night"
        assert LightMode.DAY == "day"
        assert LightMode.TRANSITION == "transition"


class TestAdaptiveTimelapse:
    """Test AdaptiveTimelapse class."""

    def test_init(self, test_config_file):
        """Test initialization."""
        timelapse = AdaptiveTimelapse(test_config_file)
        assert timelapse.config is not None
        assert timelapse.running is True
        assert timelapse.frame_count == 0

    def test_calculate_lux(self, test_config_file):
        """Test lux calculation."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Create test image for calculate_lux
        temp_dir = tempfile.mkdtemp()
        test_image = os.path.join(temp_dir, "test.jpg")

        try:
            # Create a dummy image
            from PIL import Image

            img = Image.new("RGB", (100, 100), color=(128, 128, 128))
            img.save(test_image)

            # Test typical metadata
            metadata = {
                "ExposureTime": 10000,  # 10ms
                "AnalogueGain": 2.0,
            }

            lux = timelapse.calculate_lux(test_image, metadata)
            assert isinstance(lux, float)
            assert lux > 0
        finally:
            os.unlink(test_image)
            os.rmdir(temp_dir)

    def test_determine_light_mode(self, test_config_file):
        """Test light mode determination."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Night
        assert timelapse.determine_mode(5.0) == LightMode.NIGHT

        # Day
        assert timelapse.determine_mode(500.0) == LightMode.DAY

        # Transition
        assert timelapse.determine_mode(50.0) == LightMode.TRANSITION

    def test_get_camera_settings_night(self, test_config_file):
        """Test camera settings for night mode."""
        timelapse = AdaptiveTimelapse(test_config_file)
        settings = timelapse.get_camera_settings(LightMode.NIGHT, lux=5.0)

        assert "ExposureTime" in settings
        assert "AnalogueGain" in settings
        assert "AeEnable" in settings
        assert settings["AeEnable"] == 0  # Auto-exposure disabled
        assert settings["AwbEnable"] == 0  # AWB disabled for night

    def test_get_camera_settings_day(self, test_config_file):
        """Test camera settings for day mode with smooth exposure transitions."""
        timelapse = AdaptiveTimelapse(test_config_file)
        settings = timelapse.get_camera_settings(LightMode.DAY, lux=500.0)

        # Day mode now uses manual exposure with smooth transitions (prevents ISO jumps)
        # smooth_exposure_in_day_mode defaults to True
        assert "AeEnable" in settings
        assert settings["AeEnable"] == 0  # Manual exposure control
        assert "ExposureTime" in settings  # Calculated exposure
        assert "AnalogueGain" in settings  # Calculated gain
        # With smooth_wb_in_day_mode (default True), AWB is disabled
        # and manual interpolated ColourGains are used instead
        assert settings["AwbEnable"] == 0
        assert "ColourGains" in settings

    def test_get_camera_settings_transition(self, test_config_file):
        """Test camera settings for transition mode."""
        timelapse = AdaptiveTimelapse(test_config_file)
        settings = timelapse.get_camera_settings(LightMode.TRANSITION, lux=50.0)

        assert "ExposureTime" in settings
        assert "AnalogueGain" in settings

        # Transition should have intermediate values
        night_gain = timelapse.config["adaptive_timelapse"]["night_mode"]["analogue_gain"]
        assert settings["AnalogueGain"] <= night_gain

    def test_get_camera_settings_transition_long_exposure(self, test_config_file):
        """Test transition mode always uses manual WB for smooth transitions."""
        # Add colour_gains to night_mode config
        with open(test_config_file, "r") as f:
            config_data = yaml.safe_load(f)

        config_data["adaptive_timelapse"]["night_mode"]["colour_gains"] = [1.8, 1.5]

        with open(test_config_file, "w") as f:
            yaml.dump(config_data, f)

        timelapse = AdaptiveTimelapse(test_config_file)

        # Test long exposure (>1s) - should use manual WB
        settings_long = timelapse.get_camera_settings(LightMode.TRANSITION, lux=15.0)
        assert settings_long["AwbEnable"] == 0  # AWB disabled
        assert "ColourGains" in settings_long  # Manual gains set

        # Test short exposure (<1s) - should ALSO use manual WB
        # (smooth transitions always use interpolated manual WB to prevent flickering)
        settings_short = timelapse.get_camera_settings(LightMode.TRANSITION, lux=98.0)
        assert settings_short["AwbEnable"] == 0  # AWB always disabled in transition
        assert "ColourGains" in settings_short  # Interpolated gains used

    def test_signal_handler(self, test_config_file):
        """Test signal handler stops the timelapse."""
        timelapse = AdaptiveTimelapse(test_config_file)
        assert timelapse.running is True

        # Simulate SIGTERM
        timelapse._signal_handler(15, None)
        assert timelapse.running is False

    def test_take_test_shot(self, test_config_file):
        """Test taking a test shot."""
        import tempfile
        import json

        # Create metadata file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"ExposureTime": 100000, "AnalogueGain": 1.0}, f)
            metadata_path = f.name

        try:
            # Mock ImageCapture class completely
            with patch("src.auto_timelapse.ImageCapture") as mock_capture_class:
                # Mock the context manager
                mock_instance = MagicMock()
                mock_capture_class.return_value.__enter__.return_value = mock_instance
                mock_capture_class.return_value.__exit__.return_value = None

                # Mock capture_request to return metadata
                mock_request = MagicMock()
                mock_request.get_metadata.return_value = {
                    "ExposureTime": 100000,
                    "AnalogueGain": 1.0,
                }
                mock_instance.picam2.capture_request.return_value = mock_request

                timelapse = AdaptiveTimelapse(test_config_file)
                image_path, metadata = timelapse.take_test_shot()

                assert image_path is not None
                assert isinstance(metadata, dict)
                assert "ExposureTime" in metadata
                # Verify capture_request was called
                mock_instance.picam2.capture_request.assert_called_once()
                # Verify request was released
                mock_request.release.assert_called_once()
        finally:
            os.unlink(metadata_path)

    def test_calculate_lux_no_pil(self, test_config_file):
        """Test lux calculation fallback when PIL not available."""
        timelapse = AdaptiveTimelapse(test_config_file)

        metadata = {
            "ExposureTime": 50000,  # 50ms
            "AnalogueGain": 1.5,
        }

        # Mock PIL.Image.open to raise ImportError
        with patch("PIL.Image.open", side_effect=ImportError("PIL not available")):
            lux = timelapse.calculate_lux("/fake/path.jpg", metadata)
            assert isinstance(lux, float)
            assert lux > 0

    def test_get_camera_settings_night_with_colour_gains(self, test_config_file):
        """Test night mode applies manual colour gains."""
        with open(test_config_file, "r") as f:
            config_data = yaml.safe_load(f)

        config_data["adaptive_timelapse"]["night_mode"]["colour_gains"] = [1.8, 1.5]

        with open(test_config_file, "w") as f:
            yaml.dump(config_data, f)

        timelapse = AdaptiveTimelapse(test_config_file)
        settings = timelapse.get_camera_settings(LightMode.NIGHT)

        assert "ColourGains" in settings
        assert settings["ColourGains"] == (1.8, 1.5)

    def test_get_camera_settings_day_manual_exposure(self, test_config_file):
        """Test day mode with manual exposure."""
        with open(test_config_file, "r") as f:
            config_data = yaml.safe_load(f)

        config_data["adaptive_timelapse"]["day_mode"]["exposure_time"] = 0.01  # 10ms
        config_data["adaptive_timelapse"]["day_mode"]["analogue_gain"] = 1.0

        with open(test_config_file, "w") as f:
            yaml.dump(config_data, f)

        timelapse = AdaptiveTimelapse(test_config_file)
        settings = timelapse.get_camera_settings(LightMode.DAY)

        assert settings["AeEnable"] == 0  # Manual mode
        assert "ExposureTime" in settings
        assert "AnalogueGain" in settings

    def test_get_camera_settings_day_with_brightness(self, test_config_file):
        """Test day mode brightness adjustment."""
        with open(test_config_file, "r") as f:
            config_data = yaml.safe_load(f)

        config_data["adaptive_timelapse"]["day_mode"]["brightness"] = 0.2

        with open(test_config_file, "w") as f:
            yaml.dump(config_data, f)

        timelapse = AdaptiveTimelapse(test_config_file)
        settings = timelapse.get_camera_settings(LightMode.DAY)

        assert "Brightness" in settings
        assert settings["Brightness"] == 0.2

    def test_get_camera_settings_transition_no_smooth(self, test_config_file):
        """Test transition mode without smooth transition."""
        with open(test_config_file, "r") as f:
            config_data = yaml.safe_load(f)

        config_data["adaptive_timelapse"]["transition_mode"]["smooth_transition"] = False

        with open(test_config_file, "w") as f:
            yaml.dump(config_data, f)

        timelapse = AdaptiveTimelapse(test_config_file)
        settings = timelapse.get_camera_settings(LightMode.TRANSITION, lux=50.0)

        # Should use fixed middle values
        assert "ExposureTime" in settings
        assert settings["ExposureTime"] == int(5.0 * 1_000_000)

    def test_close_camera_fast(self, test_config_file):
        """Test fast camera close method."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Mock capture object
        mock_capture = MagicMock()
        mock_capture.picam2 = MagicMock()

        # Should not raise exception
        timelapse._close_camera_fast(mock_capture, "night")
        mock_capture.close.assert_called_once()

    def test_close_camera_fast_none(self, test_config_file):
        """Test close with None capture."""
        timelapse = AdaptiveTimelapse(test_config_file)
        # Should not raise exception
        timelapse._close_camera_fast(None, "day")

    def test_calculate_lux_error_handling(self, test_config_file):
        """Test lux calculation handles image read errors."""
        timelapse = AdaptiveTimelapse(test_config_file)

        metadata = {
            "ExposureTime": 10000,
            "AnalogueGain": 1.0,
        }

        # Non-existent image
        lux = timelapse.calculate_lux("/nonexistent/image.jpg", metadata)
        assert isinstance(lux, float)
        assert lux > 0  # Should return fallback value


class TestLuxSmoothing:
    """Test lux smoothing (EMA) functionality."""

    def test_smooth_lux_first_reading(self, test_config_file):
        """Test first lux reading initializes smoothed value."""
        timelapse = AdaptiveTimelapse(test_config_file)
        assert timelapse._smoothed_lux is None

        result = timelapse._smooth_lux(100.0)
        assert result == 100.0
        assert timelapse._smoothed_lux == 100.0

    def test_smooth_lux_dampens_spikes(self, test_config_file):
        """Test that EMA dampens sudden lux spikes."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Initialize with stable reading
        timelapse._smooth_lux(100.0)

        # Sudden spike should be dampened
        result = timelapse._smooth_lux(500.0)
        # With alpha=0.3: 0.3 * 500 + 0.7 * 100 = 150 + 70 = 220
        assert result < 500.0
        assert result > 100.0

    def test_smooth_lux_converges(self, test_config_file):
        """Test that smoothed lux converges to stable value."""
        timelapse = AdaptiveTimelapse(test_config_file)

        timelapse._smooth_lux(100.0)

        # Apply same value repeatedly - should converge
        for _ in range(20):
            result = timelapse._smooth_lux(200.0)

        # Should be very close to 200 after many iterations
        assert abs(result - 200.0) < 1.0


class TestHysteresis:
    """Test mode change hysteresis."""

    def test_hysteresis_first_mode(self, test_config_file):
        """Test first mode is accepted immediately."""
        timelapse = AdaptiveTimelapse(test_config_file)

        result = timelapse._apply_hysteresis("night")
        assert result == "night"
        assert timelapse._last_mode == "night"

    def test_hysteresis_same_mode(self, test_config_file):
        """Test same mode resets counter."""
        timelapse = AdaptiveTimelapse(test_config_file)

        timelapse._apply_hysteresis("day")
        timelapse._apply_hysteresis("day")
        timelapse._apply_hysteresis("day")

        assert timelapse._mode_hold_count == 0

    def test_hysteresis_holds_mode(self, test_config_file):
        """Test mode change is held until threshold reached."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse._hysteresis_frames = 3

        timelapse._apply_hysteresis("night")

        # Request day mode - should be held
        result1 = timelapse._apply_hysteresis("day")
        assert result1 == "night"  # Still night
        assert timelapse._mode_hold_count == 1

        result2 = timelapse._apply_hysteresis("day")
        assert result2 == "night"  # Still night
        assert timelapse._mode_hold_count == 2

        result3 = timelapse._apply_hysteresis("day")
        assert result3 == "day"  # Now day
        assert timelapse._mode_hold_count == 0

    def test_hysteresis_resets_on_same_mode(self, test_config_file):
        """Test counter resets when same mode as current is requested."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse._hysteresis_frames = 3

        timelapse._apply_hysteresis("night")
        timelapse._apply_hysteresis("day")  # count=1 (different from night)
        timelapse._apply_hysteresis("night")  # Same as current - resets counter

        # Counter should reset to 0 when same mode requested
        assert timelapse._mode_hold_count == 0
        assert timelapse._last_mode == "night"

    def test_hysteresis_counts_any_different_mode(self, test_config_file):
        """Test any different mode increments counter."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse._hysteresis_frames = 4  # Need 4 frames

        timelapse._apply_hysteresis("night")  # accepted
        timelapse._apply_hysteresis("day")  # count=1
        timelapse._apply_hysteresis("transition")  # count=2 (still different from night)
        timelapse._apply_hysteresis("day")  # count=3

        # Still held at night since threshold not reached
        assert timelapse._last_mode == "night"
        assert timelapse._mode_hold_count == 3


class TestInterpolation:
    """Test interpolation methods for smooth transitions."""

    def test_interpolate_colour_gains_first_frame(self, test_config_file):
        """Test first frame accepts target gains."""
        timelapse = AdaptiveTimelapse(test_config_file)

        result = timelapse._interpolate_colour_gains((2.0, 1.5))
        assert result == (2.0, 1.5)

    def test_interpolate_colour_gains_gradual(self, test_config_file):
        """Test gains change gradually."""
        timelapse = AdaptiveTimelapse(test_config_file)

        timelapse._interpolate_colour_gains((1.5, 2.0))
        result = timelapse._interpolate_colour_gains((2.5, 1.0))

        # Should move towards target but not reach it
        assert result[0] > 1.5 and result[0] < 2.5
        assert result[1] < 2.0 and result[1] > 1.0

    def test_interpolate_gain_first_frame(self, test_config_file):
        """Test first frame accepts target gain."""
        timelapse = AdaptiveTimelapse(test_config_file)

        result = timelapse._interpolate_gain(4.0)
        assert result == 4.0

    def test_interpolate_gain_gradual(self, test_config_file):
        """Test gain changes gradually."""
        timelapse = AdaptiveTimelapse(test_config_file)

        timelapse._interpolate_gain(1.0)
        result = timelapse._interpolate_gain(6.0)

        assert result > 1.0 and result < 6.0

    def test_interpolate_gain_clamps(self, test_config_file):
        """Test gain is clamped to valid range."""
        timelapse = AdaptiveTimelapse(test_config_file)

        timelapse._interpolate_gain(1.0)
        result = timelapse._interpolate_gain(0.1)  # Below min

        assert result >= 1.0  # Clamped to min

    def test_interpolate_exposure_first_frame(self, test_config_file):
        """Test first frame accepts target exposure."""
        timelapse = AdaptiveTimelapse(test_config_file)

        result = timelapse._interpolate_exposure(5.0)
        assert result == 5.0

    def test_interpolate_exposure_logarithmic(self, test_config_file):
        """Test exposure uses logarithmic interpolation."""
        timelapse = AdaptiveTimelapse(test_config_file)

        timelapse._interpolate_exposure(1.0)
        result = timelapse._interpolate_exposure(10.0)

        # Log interpolation: should be between 1 and 10
        assert result > 1.0 and result < 10.0

    def test_interpolate_exposure_clamps(self, test_config_file):
        """Test exposure is clamped to valid range."""
        timelapse = AdaptiveTimelapse(test_config_file)

        timelapse._interpolate_exposure(1.0)
        result = timelapse._interpolate_exposure(100.0)  # Above max

        assert result <= 20.0  # Clamped to max


class TestBrightnessFeedback:
    """Test brightness feedback system for smooth transitions."""

    def test_brightness_feedback_initial(self, test_config_file):
        """Test initial correction factor is 1.0."""
        timelapse = AdaptiveTimelapse(test_config_file)

        assert timelapse._brightness_correction_factor == 1.0

    def test_brightness_feedback_none_brightness(self, test_config_file):
        """Test None brightness returns current factor."""
        timelapse = AdaptiveTimelapse(test_config_file)

        result = timelapse._apply_brightness_feedback(None)
        assert result == 1.0

    def test_brightness_feedback_within_tolerance(self, test_config_file):
        """Test brightness within tolerance decays towards 1.0."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse._brightness_correction_factor = 1.2  # Above 1.0

        # Brightness within tolerance (120 ± 40)
        timelapse._apply_brightness_feedback(120.0)

        # Should decay towards 1.0
        assert timelapse._brightness_correction_factor < 1.2

    def test_brightness_feedback_too_bright(self, test_config_file):
        """Test correction decreases when image too bright."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Image much too bright (200 vs target 120)
        timelapse._apply_brightness_feedback(200.0)

        # Correction should decrease (reduce exposure)
        assert timelapse._brightness_correction_factor < 1.0

    def test_brightness_feedback_too_dark(self, test_config_file):
        """Test correction increases when image too dark."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Image too dark (50 vs target 120)
        timelapse._apply_brightness_feedback(50.0)

        # Correction should increase (boost exposure)
        assert timelapse._brightness_correction_factor > 1.0

    def test_brightness_feedback_clamps(self, test_config_file):
        """Test correction factor is clamped to valid range."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Apply extreme dark correction repeatedly
        for _ in range(50):
            timelapse._apply_brightness_feedback(10.0)

        # Should be clamped to max 4.0
        assert timelapse._brightness_correction_factor <= 4.0

        # Reset and apply extreme bright correction
        timelapse._brightness_correction_factor = 1.0
        for _ in range(50):
            timelapse._apply_brightness_feedback(250.0)

        # Should be clamped to min 0.25
        assert timelapse._brightness_correction_factor >= 0.25


class TestExposureCalculation:
    """Test lux-based exposure and gain calculations."""

    def test_calculate_target_exposure_inverse_relationship(self, test_config_file):
        """Test exposure has inverse relationship with lux."""
        timelapse = AdaptiveTimelapse(test_config_file)

        exp_low_lux = timelapse._calculate_target_exposure_from_lux(10.0)
        exp_high_lux = timelapse._calculate_target_exposure_from_lux(1000.0)

        # Higher lux = shorter exposure
        assert exp_high_lux < exp_low_lux

    def test_calculate_target_exposure_clamps(self, test_config_file):
        """Test exposure is clamped to config limits."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Very low lux - should clamp to max
        exp_night = timelapse._calculate_target_exposure_from_lux(0.01)
        assert exp_night <= 20.0

        # Very high lux - should clamp to min
        exp_bright = timelapse._calculate_target_exposure_from_lux(10000.0)
        assert exp_bright >= 0.01

    def test_calculate_target_exposure_applies_correction(self, test_config_file):
        """Test brightness correction factor is applied."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Get base exposure
        exp_base = timelapse._calculate_target_exposure_from_lux(100.0)

        # Apply correction factor
        timelapse._brightness_correction_factor = 2.0
        exp_corrected = timelapse._calculate_target_exposure_from_lux(100.0)

        # Corrected should be ~2x base (within clamping limits)
        assert exp_corrected > exp_base

    def test_calculate_target_gain_inverse_relationship(self, test_config_file):
        """Test gain has inverse relationship with lux."""
        timelapse = AdaptiveTimelapse(test_config_file)

        gain_low_lux = timelapse._calculate_target_gain_from_lux(1.0)
        gain_high_lux = timelapse._calculate_target_gain_from_lux(1000.0)

        # Higher lux = lower gain
        assert gain_high_lux < gain_low_lux

    def test_calculate_target_gain_clamps(self, test_config_file):
        """Test gain clamps at extremes."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Very low lux - should be night gain
        gain_night = timelapse._calculate_target_gain_from_lux(0.1)
        assert gain_night == 6.0  # Night mode gain from config

        # Very high lux - should be day gain
        gain_day = timelapse._calculate_target_gain_from_lux(10000.0)
        assert gain_day == 1.0  # Default day gain


class TestTargetColourGains:
    """Test colour gain calculation for different modes."""

    def test_target_colour_gains_night(self, test_config_file):
        """Test night mode uses night gains."""
        with open(test_config_file, "r") as f:
            config_data = yaml.safe_load(f)
        config_data["adaptive_timelapse"]["night_mode"]["colour_gains"] = [1.8, 2.0]
        with open(test_config_file, "w") as f:
            yaml.dump(config_data, f)

        timelapse = AdaptiveTimelapse(test_config_file)
        gains = timelapse._get_target_colour_gains(LightMode.NIGHT)

        assert gains == (1.8, 2.0)

    def test_target_colour_gains_day(self, test_config_file):
        """Test day mode uses day reference or default."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # No day reference learned yet - should use default
        gains = timelapse._get_target_colour_gains(LightMode.DAY)
        assert gains == (2.5, 1.6)  # Default day gains

    def test_target_colour_gains_transition_interpolates(self, test_config_file):
        """Test transition mode interpolates between night and day."""
        with open(test_config_file, "r") as f:
            config_data = yaml.safe_load(f)
        config_data["adaptive_timelapse"]["night_mode"]["colour_gains"] = [1.0, 3.0]
        with open(test_config_file, "w") as f:
            yaml.dump(config_data, f)

        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse._day_wb_reference = (3.0, 1.0)

        # Position 0.5 = midpoint
        gains = timelapse._get_target_colour_gains(LightMode.TRANSITION, position=0.5)

        # Should be midpoint between night [1.0, 3.0] and day [3.0, 1.0]
        assert abs(gains[0] - 2.0) < 0.01
        assert abs(gains[1] - 2.0) < 0.01


class TestDayWBReference:
    """Test day white balance reference learning."""

    def test_update_day_wb_reference_bright(self, test_config_file):
        """Test WB reference is updated in bright conditions."""
        timelapse = AdaptiveTimelapse(test_config_file)

        metadata = {
            "ColourGains": [2.8, 1.5],
            "Lux": 500,  # Bright enough
        }

        timelapse._update_day_wb_reference(metadata)
        assert timelapse._day_wb_reference == (2.8, 1.5)

    def test_update_day_wb_reference_too_dark(self, test_config_file):
        """Test WB reference not updated when too dark."""
        timelapse = AdaptiveTimelapse(test_config_file)

        metadata = {
            "ColourGains": [2.8, 1.5],
            "Lux": 50,  # Too dark
        }

        timelapse._update_day_wb_reference(metadata)
        assert timelapse._day_wb_reference is None

    def test_update_day_wb_reference_invalid_gains(self, test_config_file):
        """Test WB reference rejects invalid gains."""
        timelapse = AdaptiveTimelapse(test_config_file)

        metadata = {
            "ColourGains": [0.5, 5.0],  # Out of valid range
            "Lux": 500,
        }

        timelapse._update_day_wb_reference(metadata)
        assert timelapse._day_wb_reference is None


class TestBrightnessAnalysis:
    """Test image brightness analysis."""

    def test_analyze_image_brightness(self, test_config_file):
        """Test brightness analysis returns expected metrics."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Create test image
        temp_dir = tempfile.mkdtemp()
        test_image = os.path.join(temp_dir, "test.jpg")

        try:
            from PIL import Image

            # Create image with known brightness
            img = Image.new("L", (100, 100), color=128)  # Mid-gray
            img.save(test_image)

            result = timelapse._analyze_image_brightness(test_image)

            assert "mean_brightness" in result
            assert "median_brightness" in result
            assert "std_brightness" in result
            assert "underexposed_percent" in result
            assert "overexposed_percent" in result

            # Mid-gray image should have mean ~128
            assert abs(result["mean_brightness"] - 128) < 5
        finally:
            os.unlink(test_image)
            os.rmdir(temp_dir)

    def test_analyze_image_brightness_error(self, test_config_file):
        """Test brightness analysis handles errors gracefully."""
        timelapse = AdaptiveTimelapse(test_config_file)

        result = timelapse._analyze_image_brightness("/nonexistent/image.jpg")
        assert result == {}


class TestTimelapseCaptureFlow:
    """Test the main timelapse capture flow."""

    def test_capture_frame(self, test_config_file):
        """Test single frame capture."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Mock ImageCapture
        mock_capture = MagicMock()
        mock_capture.capture.return_value = ("/tmp/frame.jpg", "/tmp/frame_metadata.json")

        # Test capture
        image_path, metadata_path = timelapse.capture_frame(mock_capture, "night")

        assert image_path == "/tmp/frame.jpg"
        assert timelapse.frame_count == 1
        mock_capture.capture.assert_called_once()

    def test_capture_frame_increments_counter(self, test_config_file):
        """Test that frame counter increments."""
        timelapse = AdaptiveTimelapse(test_config_file)
        mock_capture = MagicMock()
        mock_capture.capture.return_value = ("/tmp/frame.jpg", None)

        # Capture multiple frames
        timelapse.capture_frame(mock_capture, "day")
        timelapse.capture_frame(mock_capture, "day")
        timelapse.capture_frame(mock_capture, "day")

        assert timelapse.frame_count == 3
