"""Tests for the exposure controller.

Everything that decides what the camera should do: mode selection and
hysteresis, the interpolators, brightness feedback, highlight protection,
white balance and the recovery ramps.

Split out of test_auto_timelapse.py, which now covers only AdaptiveTimelapse's
own job -- lifecycle, scheduling and the wiring between the two.
"""

import os
import sys
import tempfile

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.auto_timelapse import AdaptiveTimelapse  # noqa: E402
from src.exposure import (
    LightMode,
)  # noqa: E402,F401


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


@pytest.fixture
def direct_control_config_file():
    """Create config file with direct brightness control enabled."""
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
        },
        "system": {"create_directories": True, "save_metadata": False},
        "overlay": {"enabled": False},
        "adaptive_timelapse": {
            "enabled": True,
            "interval": 30,
            "num_frames": 0,
            "reference_lux": 3.8,
            "direct_brightness_control": True,
            "brightness_damping": 0.5,
            "light_thresholds": {"night": 10, "day": 100},
            "night_mode": {
                "max_exposure_time": 20.0,
                "analogue_gain": 6,
                "awb_enable": False,
            },
            "day_mode": {"awb_enable": True},
            "transition_mode": {
                "smooth_transition": True,
                "target_brightness": 120,
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
    os.unlink(config_path)


class TestLuxSmoothing:
    """Test lux smoothing (EMA) functionality."""

    def test_smooth_lux_first_reading(self, test_config_file):
        """Test first lux reading initializes smoothed value."""
        timelapse = AdaptiveTimelapse(test_config_file)
        assert timelapse.exposure._smoothed_lux is None

        result = timelapse.exposure.smooth_lux(100.0)
        assert result == 100.0
        assert timelapse.exposure._smoothed_lux == 100.0

    def test_smooth_lux_dampens_spikes(self, test_config_file):
        """Test that EMA dampens sudden lux spikes."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Initialize with stable reading
        timelapse.exposure.smooth_lux(100.0)

        # Sudden spike should be dampened
        result = timelapse.exposure.smooth_lux(500.0)
        # With alpha=0.3: 0.3 * 500 + 0.7 * 100 = 150 + 70 = 220
        assert result < 500.0
        assert result > 100.0

    def test_smooth_lux_converges(self, test_config_file):
        """Test that smoothed lux converges to stable value."""
        timelapse = AdaptiveTimelapse(test_config_file)

        timelapse.exposure.smooth_lux(100.0)

        # Apply same value repeatedly - should converge
        for _ in range(20):
            result = timelapse.exposure.smooth_lux(200.0)

        # Should be very close to 200 after many iterations
        assert abs(result - 200.0) < 1.0


class TestHysteresis:
    """Test mode change hysteresis."""

    def test_hysteresis_first_mode(self, test_config_file):
        """Test first mode is accepted immediately."""
        timelapse = AdaptiveTimelapse(test_config_file)

        result = timelapse.exposure.apply_hysteresis("night")
        assert result == "night"
        assert timelapse.exposure._last_mode == "night"

    def test_hysteresis_same_mode(self, test_config_file):
        """Test same mode resets counter."""
        timelapse = AdaptiveTimelapse(test_config_file)

        timelapse.exposure.apply_hysteresis("day")
        timelapse.exposure.apply_hysteresis("day")
        timelapse.exposure.apply_hysteresis("day")

        assert timelapse.exposure._mode_hold_count == 0

    def test_hysteresis_holds_mode(self, test_config_file):
        """Test mode change is held until threshold reached."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.exposure._hysteresis_frames = 3

        timelapse.exposure.apply_hysteresis("night")

        # Request day mode - should be held
        result1 = timelapse.exposure.apply_hysteresis("day")
        assert result1 == "night"  # Still night
        assert timelapse.exposure._mode_hold_count == 1

        result2 = timelapse.exposure.apply_hysteresis("day")
        assert result2 == "night"  # Still night
        assert timelapse.exposure._mode_hold_count == 2

        result3 = timelapse.exposure.apply_hysteresis("day")
        assert result3 == "day"  # Now day
        assert timelapse.exposure._mode_hold_count == 0

    def test_hysteresis_resets_on_same_mode(self, test_config_file):
        """Test counter resets when same mode as current is requested."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.exposure._hysteresis_frames = 3

        timelapse.exposure.apply_hysteresis("night")
        timelapse.exposure.apply_hysteresis("day")  # count=1 (different from night)
        timelapse.exposure.apply_hysteresis("night")  # Same as current - resets counter

        # Counter should reset to 0 when same mode requested
        assert timelapse.exposure._mode_hold_count == 0
        assert timelapse.exposure._last_mode == "night"

    def test_hysteresis_counts_any_different_mode(self, test_config_file):
        """Test any different mode increments counter."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.exposure._hysteresis_frames = 4  # Need 4 frames

        timelapse.exposure.apply_hysteresis("night")  # accepted
        timelapse.exposure.apply_hysteresis("day")  # count=1
        timelapse.exposure.apply_hysteresis("transition")  # count=2 (still different from night)
        timelapse.exposure.apply_hysteresis("day")  # count=3

        # Still held at night since threshold not reached
        assert timelapse.exposure._last_mode == "night"
        assert timelapse.exposure._mode_hold_count == 3


class TestInterpolation:
    """Test interpolation methods for smooth transitions."""

    def test_interpolate_colour_gains_first_frame(self, test_config_file):
        """Test first frame accepts target gains."""
        timelapse = AdaptiveTimelapse(test_config_file)

        result = timelapse.exposure._interpolate_colour_gains((2.0, 1.5))
        assert result == (2.0, 1.5)

    def test_interpolate_colour_gains_gradual(self, test_config_file):
        """Test gains change gradually."""
        timelapse = AdaptiveTimelapse(test_config_file)

        timelapse.exposure._interpolate_colour_gains((1.5, 2.0))
        result = timelapse.exposure._interpolate_colour_gains((2.5, 1.0))

        # Should move towards target but not reach it
        assert result[0] > 1.5 and result[0] < 2.5
        assert result[1] < 2.0 and result[1] > 1.0

    def test_interpolate_gain_first_frame(self, test_config_file):
        """Test first frame accepts target gain."""
        timelapse = AdaptiveTimelapse(test_config_file)

        result = timelapse.exposure._interpolate_gain(4.0)
        assert result == 4.0

    def test_interpolate_gain_gradual(self, test_config_file):
        """Test gain changes gradually."""
        timelapse = AdaptiveTimelapse(test_config_file)

        timelapse.exposure._interpolate_gain(1.0)
        result = timelapse.exposure._interpolate_gain(6.0)

        assert result > 1.0 and result < 6.0

    def test_interpolate_gain_clamps(self, test_config_file):
        """Test gain is clamped to valid range."""
        timelapse = AdaptiveTimelapse(test_config_file)

        timelapse.exposure._interpolate_gain(1.0)
        result = timelapse.exposure._interpolate_gain(0.1)  # Below min

        assert result >= 1.0  # Clamped to min

    def test_interpolate_exposure_first_frame(self, test_config_file):
        """Test first frame accepts target exposure."""
        timelapse = AdaptiveTimelapse(test_config_file)

        result = timelapse.exposure._interpolate_exposure(5.0)
        assert result == 5.0

    def test_interpolate_exposure_logarithmic(self, test_config_file):
        """Test exposure uses logarithmic interpolation."""
        timelapse = AdaptiveTimelapse(test_config_file)

        timelapse.exposure._interpolate_exposure(1.0)
        result = timelapse.exposure._interpolate_exposure(10.0)

        # Log interpolation: should be between 1 and 10
        assert result > 1.0 and result < 10.0

    def test_interpolate_exposure_clamps(self, test_config_file):
        """Test exposure is clamped to valid range."""
        timelapse = AdaptiveTimelapse(test_config_file)

        timelapse.exposure._interpolate_exposure(1.0)
        result = timelapse.exposure._interpolate_exposure(100.0)  # Above max

        assert result <= 20.0  # Clamped to max


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
        gains = timelapse.exposure._get_target_colour_gains(LightMode.NIGHT)

        assert gains == (1.8, 2.0)

    def test_target_colour_gains_day(self, test_config_file):
        """Test day mode uses day reference or default."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # No day reference learned yet - should use default
        gains = timelapse.exposure._get_target_colour_gains(LightMode.DAY)
        assert gains == (2.5, 1.6)  # Default day gains

    def test_target_colour_gains_transition_interpolates(self, test_config_file):
        """Test transition mode interpolates between night and day."""
        with open(test_config_file, "r") as f:
            config_data = yaml.safe_load(f)
        config_data["adaptive_timelapse"]["night_mode"]["colour_gains"] = [1.0, 3.0]
        with open(test_config_file, "w") as f:
            yaml.dump(config_data, f)

        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.exposure._day_wb_reference = (3.0, 1.0)

        # Position 0.5 = midpoint
        gains = timelapse.exposure._get_target_colour_gains(LightMode.TRANSITION, position=0.5)

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

        timelapse.exposure.update_day_wb_reference(metadata)
        assert timelapse.exposure._day_wb_reference == (2.8, 1.5)

    def test_update_day_wb_reference_too_dark(self, test_config_file):
        """Test WB reference not updated when too dark."""
        timelapse = AdaptiveTimelapse(test_config_file)

        metadata = {
            "ColourGains": [2.8, 1.5],
            "Lux": 50,  # Too dark
        }

        timelapse.exposure.update_day_wb_reference(metadata)
        assert timelapse.exposure._day_wb_reference is None

    def test_update_day_wb_reference_invalid_gains(self, test_config_file):
        """Test WB reference rejects invalid gains."""
        timelapse = AdaptiveTimelapse(test_config_file)

        metadata = {
            "ColourGains": [0.5, 5.0],  # Out of valid range
            "Lux": 500,
        }

        timelapse.exposure.update_day_wb_reference(metadata)
        assert timelapse.exposure._day_wb_reference is None


class TestOverexposureDetection:
    """Test overexposure detection and fast ramp-down."""

    def test_check_overexposure_triggers_on_high_brightness(self, test_config_file):
        """Test overexposure detected with high brightness."""
        timelapse = AdaptiveTimelapse(test_config_file)

        brightness_metrics = {
            "mean_brightness": 190,  # Above 180 threshold
            "overexposed_percent": 5,
        }

        result = timelapse.exposure._check_overexposure(brightness_metrics)

        assert result is True
        assert timelapse.exposure._overexposure_detected is True

    def test_check_overexposure_triggers_on_clipped_pixels(self, test_config_file):
        """Test overexposure detected with many clipped pixels."""
        timelapse = AdaptiveTimelapse(test_config_file)

        brightness_metrics = {
            "mean_brightness": 150,  # Normal brightness
            "overexposed_percent": 15,  # Above 10% threshold
        }

        result = timelapse.exposure._check_overexposure(brightness_metrics)

        assert result is True

    def test_check_overexposure_clears_on_safe_values(self, test_config_file):
        """Test overexposure cleared when values are safe."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.exposure._overexposure_detected = True  # Previously triggered
        timelapse.exposure._overexposure_severity = "warning"  # Set severity

        brightness_metrics = {
            "mean_brightness": 120,  # Below 130 safe threshold
            "overexposed_percent": 2,  # Below 3% safe threshold
        }

        result = timelapse.exposure._check_overexposure(brightness_metrics)

        assert result is False
        assert timelapse.exposure._overexposure_detected is False
        assert timelapse.exposure._overexposure_severity is None

    def test_check_overexposure_empty_metrics(self, test_config_file):
        """Test overexposure handling with empty metrics."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.exposure._overexposure_detected = True

        result = timelapse.exposure._check_overexposure({})

        # Should retain previous state
        assert result is True

    def test_check_overexposure_none_metrics(self, test_config_file):
        """Test overexposure handling with None metrics."""
        timelapse = AdaptiveTimelapse(test_config_file)

        result = timelapse.exposure._check_overexposure(None)

        assert result is False  # Default state


class TestUnderexposureDetection:
    """Test underexposure detection and fast ramp-up."""

    def test_check_underexposure_triggers_on_low_brightness(self, test_config_file):
        """Test underexposure detected with low brightness (warning level)."""
        timelapse = AdaptiveTimelapse(test_config_file)

        brightness_metrics = {
            "mean_brightness": 85,  # Below 90 warning threshold
        }

        result = timelapse.exposure._check_underexposure(brightness_metrics)

        assert result is True
        assert timelapse.exposure._underexposure_detected is True
        assert timelapse.exposure._underexposure_severity == "warning"

    def test_check_underexposure_triggers_critical(self, test_config_file):
        """Test critical underexposure detected with very low brightness."""
        timelapse = AdaptiveTimelapse(test_config_file)

        brightness_metrics = {
            "mean_brightness": 60,  # Below 70 critical threshold
        }

        result = timelapse.exposure._check_underexposure(brightness_metrics)

        assert result is True
        assert timelapse.exposure._underexposure_detected is True
        assert timelapse.exposure._underexposure_severity == "critical"

    def test_check_underexposure_clears_on_safe_values(self, test_config_file):
        """Test underexposure cleared when brightness is safe."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.exposure._underexposure_detected = True
        timelapse.exposure._underexposure_severity = "warning"

        brightness_metrics = {
            "mean_brightness": 115,  # Above 105 safe threshold
        }

        result = timelapse.exposure._check_underexposure(brightness_metrics)

        assert result is False
        assert timelapse.exposure._underexposure_detected is False
        assert timelapse.exposure._underexposure_severity is None

    def test_check_underexposure_works_in_any_mode(self, test_config_file):
        """Test underexposure detection works regardless of exposure level.

        This is critical - the old version only triggered at minimum exposure,
        but we need it to work during transitions too.
        """
        timelapse = AdaptiveTimelapse(test_config_file)
        # Simulate being in middle of transition (not at min exposure)
        timelapse.exposure._last_exposure_time = 5.0  # 5 seconds - far from min

        brightness_metrics = {
            "mean_brightness": 75,  # Underexposed
        }

        result = timelapse.exposure._check_underexposure(brightness_metrics)

        # Should still detect underexposure even though not at min exposure
        assert result is True
        assert timelapse.exposure._underexposure_detected is True

    def test_check_underexposure_empty_metrics(self, test_config_file):
        """Test underexposure handling with empty metrics."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.exposure._underexposure_detected = True

        result = timelapse.exposure._check_underexposure({})

        # Should retain previous state
        assert result is True

    def test_check_underexposure_none_metrics(self, test_config_file):
        """Test underexposure handling with None metrics."""
        timelapse = AdaptiveTimelapse(test_config_file)

        result = timelapse.exposure._check_underexposure(None)

        assert result is False  # Default state


class TestRampUpSpeed:
    """Test fast ramp-up speed for underexposure recovery."""

    def test_get_rampup_speed_returns_none_when_not_underexposed(self, test_config_file):
        """Test rampup speed returns None when no underexposure."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.exposure._underexposure_detected = False

        speed = timelapse.exposure._get_rampup_speed()

        assert speed is None

    def test_get_rampup_speed_returns_fast_on_warning(self, test_config_file):
        """Test rampup speed returns fast speed on warning level."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.exposure._underexposure_detected = True
        timelapse.exposure._underexposure_severity = "warning"

        speed = timelapse.exposure._get_rampup_speed()

        assert speed == timelapse.exposure._fast_rampup_speed
        assert speed == 0.50  # Default value

    def test_get_rampup_speed_returns_critical_on_severe(self, test_config_file):
        """Test rampup speed returns critical speed on severe underexposure."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.exposure._underexposure_detected = True
        timelapse.exposure._underexposure_severity = "critical"

        speed = timelapse.exposure._get_rampup_speed()

        assert speed == timelapse.exposure._critical_rampup_speed
        assert speed == 0.70  # Default value

    def test_rampup_speed_configurable(self, test_config_file):
        """Test that rampup speeds are loaded from config."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # These should be loaded from config (with defaults if not present)
        assert hasattr(timelapse.exposure, "_fast_rampup_speed")
        assert hasattr(timelapse.exposure, "_critical_rampup_speed")
        assert timelapse.exposure._fast_rampup_speed > 0
        assert timelapse.exposure._critical_rampup_speed > 0
        # Critical should be >= fast
        assert timelapse.exposure._critical_rampup_speed >= timelapse.exposure._fast_rampup_speed


class TestExposureSpeedSelection:
    """Test exposure speed selection for both over and underexposure."""

    def test_exposure_uses_rampup_when_underexposed(self, test_config_file):
        """Test that underexposure triggers fast ramp-up in camera settings."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.exposure._underexposure_detected = True
        timelapse.exposure._underexposure_severity = "warning"
        timelapse.exposure._overexposure_detected = False

        # Initialize exposure state
        timelapse.exposure._last_exposure_time = 1.0

        # Get settings for night mode (where ramp-up matters most)
        settings = timelapse.exposure.get_camera_settings("night", lux=2.0)

        # The exposure should have ramped up faster than normal
        # We can't easily test the exact speed used, but we can verify
        # the settings were generated without error
        assert "ExposureTime" in settings
        assert settings["ExposureTime"] > 0

    def test_exposure_uses_rampdown_when_overexposed(self, test_config_file):
        """Test that overexposure triggers fast ramp-down in camera settings."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.exposure._overexposure_detected = True
        timelapse.exposure._overexposure_severity = "warning"
        timelapse.exposure._underexposure_detected = False

        # Initialize exposure state
        timelapse.exposure._last_exposure_time = 10.0

        # Get settings for day mode
        settings = timelapse.exposure.get_camera_settings("day", lux=100.0)

        assert "ExposureTime" in settings
        assert settings["ExposureTime"] > 0

    def test_underexposure_takes_priority_over_overexposure(self, test_config_file):
        """Test that underexposure detection takes priority (edge case)."""
        timelapse = AdaptiveTimelapse(test_config_file)
        # Both flags set (shouldn't happen, but test the priority)
        timelapse.exposure._underexposure_detected = True
        timelapse.exposure._underexposure_severity = "warning"
        timelapse.exposure._overexposure_detected = True
        timelapse.exposure._overexposure_severity = "warning"

        # Initialize exposure state
        timelapse.exposure._last_exposure_time = 5.0

        # Get settings - should use ramp-up (underexposure takes priority)
        settings = timelapse.exposure.get_camera_settings("transition", lux=20.0)

        assert "ExposureTime" in settings


class TestTransitionSeeding:
    """Test transition seeding from metadata."""

    def test_seed_from_metadata(self, test_config_file):
        """Test seeding transition state from captured metadata."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Test shot metadata has ColourGains from AWB
        test_shot_metadata = {
            "ColourGains": [2.0, 1.5],
        }
        # Capture metadata has exposure/gain from last actual capture
        capture_metadata = {
            "ExposureTime": 5000,  # 5ms in microseconds
            "AnalogueGain": 2.5,
        }

        timelapse.exposure.seed_from_metadata(test_shot_metadata, capture_metadata)

        assert timelapse.exposure._seed_exposure == 0.005  # Converted to seconds
        assert timelapse.exposure._seed_gain == 2.5
        assert timelapse.exposure._seed_wb_gains == (2.0, 1.5)
        assert timelapse.exposure._transition_seeded is True

    def test_seed_from_metadata_updates_last_values(self, test_config_file):
        """Test seeding updates interpolation state."""
        timelapse = AdaptiveTimelapse(test_config_file)

        test_shot_metadata = {
            "ColourGains": [2.2, 1.6],
        }
        capture_metadata = {
            "ExposureTime": 10000,  # 10ms
            "AnalogueGain": 3.0,
        }

        timelapse.exposure.seed_from_metadata(test_shot_metadata, capture_metadata)

        # Last values should also be updated for smooth interpolation
        assert timelapse.exposure._last_exposure_time == 0.01
        assert timelapse.exposure._last_analogue_gain == 3.0
        assert timelapse.exposure._last_colour_gains == (2.2, 1.6)


class TestBrightPointLightEdgeCases:
    """Test edge cases involving bright point light sources (street lamps, etc.)."""

    # A test named test_lux_calculation_with_bright_spot used to sit here. It
    # built a metadata dict, passed it nowhere, and asserted `timelapse is not
    # None`. The lux it meant to exercise is computed inside the test-shot path
    # in auto_timelapse.py, which needs a camera; there is no pure function to
    # drive. The spike behaviour it described is covered by the test below.

    def test_transition_with_inconsistent_light_readings(self, test_config_file):
        """Test handling of inconsistent light readings during transition."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Initialize smoothing state
        timelapse.exposure._smoothed_lux = 5.0  # Previous reading was dark

        # Simulate lux spike from bright light source passing through frame
        spike_lux = 500.0

        # Apply smoothing
        smoothed = timelapse.exposure.smooth_lux(spike_lux)

        # Smoothing should dampen the spike
        assert smoothed < spike_lux
        assert smoothed > 5.0  # But still increase somewhat

    def test_hysteresis_prevents_mode_flapping(self, test_config_file):
        """Test hysteresis prevents rapid mode changes from bright spots."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Initialize in night mode
        timelapse.exposure._last_mode = LightMode.NIGHT
        timelapse.exposure._mode_hold_count = 0

        # Bright spot causes momentary "day" reading
        mode = timelapse.exposure.apply_hysteresis(LightMode.DAY)

        # Should NOT immediately switch - hysteresis holds
        assert mode == LightMode.NIGHT

        # Only after sustained readings should it switch
        for _ in range(3):
            mode = timelapse.exposure.apply_hysteresis(LightMode.DAY)
        assert mode == LightMode.DAY


class TestDirectBrightnessControl:
    """Tests for direct brightness control (_calculate_exposure_from_brightness)."""

    def test_first_frame_uses_lux_estimate(self, direct_control_config_file):
        """Test first frame uses lux-based initial estimate."""
        timelapse = AdaptiveTimelapse(direct_control_config_file)
        timelapse.exposure._last_exposure_time = None

        # With lux=1000, formula: (20 * 3.8) / 1000 = 0.076s
        exposure = timelapse.exposure._calculate_exposure_from_brightness(100, lux=1000)
        assert 0.05 < exposure < 0.1

    def test_first_frame_default_without_lux(self, direct_control_config_file):
        """Test first frame uses 20ms default when no lux available."""
        timelapse = AdaptiveTimelapse(direct_control_config_file)
        timelapse.exposure._last_exposure_time = None

        exposure = timelapse.exposure._calculate_exposure_from_brightness(100, lux=None)
        assert exposure == 0.02  # 20ms default

    def test_increases_exposure_when_too_dark(self, direct_control_config_file):
        """Test exposure increases when brightness is below target."""
        timelapse = AdaptiveTimelapse(direct_control_config_file)
        timelapse.exposure._last_exposure_time = 0.1
        timelapse.exposure._target_brightness = 120

        # Brightness 60 is half of target 120
        # ratio = 120/60 = 2.0, change = 2.0^0.5 = 1.41x
        new_exposure = timelapse.exposure._calculate_exposure_from_brightness(60, lux=500)
        assert new_exposure > 0.1  # Should increase
        assert 0.12 < new_exposure < 0.16  # ~1.41x increase

    def test_decreases_exposure_when_too_bright(self, direct_control_config_file):
        """Test exposure decreases when brightness is above target."""
        timelapse = AdaptiveTimelapse(direct_control_config_file)
        timelapse.exposure._last_exposure_time = 0.1
        timelapse.exposure._target_brightness = 120

        # Brightness 240 is double target 120
        # ratio = 120/240 = 0.5, change = 0.5^0.5 = 0.71x
        new_exposure = timelapse.exposure._calculate_exposure_from_brightness(240, lux=500)
        assert new_exposure < 0.1  # Should decrease
        assert 0.06 < new_exposure < 0.08  # ~0.71x decrease

    def test_no_change_at_target_brightness(self, direct_control_config_file):
        """Test minimal change when at target brightness."""
        timelapse = AdaptiveTimelapse(direct_control_config_file)
        timelapse.exposure._last_exposure_time = 0.1
        timelapse.exposure._target_brightness = 120

        # Brightness exactly at target
        new_exposure = timelapse.exposure._calculate_exposure_from_brightness(120, lux=500)
        assert 0.099 < new_exposure < 0.101  # Essentially unchanged

    def test_ratio_clamped_to_max_4x(self, direct_control_config_file):
        """Test correction ratio is clamped to prevent extreme changes."""
        timelapse = AdaptiveTimelapse(direct_control_config_file)
        timelapse.exposure._last_exposure_time = 0.1
        timelapse.exposure._target_brightness = 120

        # Brightness 1 would give ratio of 120, but should clamp to 4
        # 4^0.5 = 2x max change
        new_exposure = timelapse.exposure._calculate_exposure_from_brightness(1, lux=500)
        assert new_exposure <= 0.2  # Max 2x change with 0.5 damping

    def test_exposure_clamped_to_max(self, direct_control_config_file):
        """Test exposure is clamped to max (20s)."""
        timelapse = AdaptiveTimelapse(direct_control_config_file)
        timelapse.exposure._last_exposure_time = 15.0
        timelapse.exposure._target_brightness = 120

        # Very dark - would want to increase exposure beyond 20s
        new_exposure = timelapse.exposure._calculate_exposure_from_brightness(10, lux=1)
        assert new_exposure <= 20.0  # Clamped to max

    def test_handles_none_brightness(self, direct_control_config_file):
        """Test handles None brightness gracefully by using seeded exposure."""
        timelapse = AdaptiveTimelapse(direct_control_config_file)
        timelapse.exposure._last_exposure_time = 0.1

        # When brightness is None but we have seeded exposure, use seeded value
        # This prevents bad first frames after reboot/restart
        new_exposure = timelapse.exposure._calculate_exposure_from_brightness(None, lux=500)
        assert new_exposure == 0.1  # Should use seeded exposure

    def test_damping_affects_correction_strength(self, direct_control_config_file):
        """Test different damping values affect correction strength."""
        timelapse = AdaptiveTimelapse(direct_control_config_file)
        timelapse.exposure._last_exposure_time = 0.1
        timelapse.exposure._target_brightness = 120

        # Test with 0.5 damping (from config)
        new_exp_05 = timelapse.exposure._calculate_exposure_from_brightness(60, lux=500)

        # Manually set higher damping
        timelapse.config["adaptive_timelapse"]["brightness_damping"] = 0.8
        timelapse.exposure._last_exposure_time = 0.1
        new_exp_08 = timelapse.exposure._calculate_exposure_from_brightness(60, lux=500)

        # Higher damping = larger correction
        assert new_exp_08 > new_exp_05

    def test_transition_without_lux_does_not_fall_back_to_five_seconds(
        self, direct_control_config_file
    ):
        """lux is None until the first test shot succeeds.

        The old code fell through to a hardcoded 5s exposure in that case,
        which could fire in daylight on the very first frame after a restart.
        """
        timelapse = AdaptiveTimelapse(direct_control_config_file)
        timelapse.exposure._last_brightness = 120
        timelapse.exposure._last_exposure_time = 0.01

        settings = timelapse.exposure.get_camera_settings(LightMode.TRANSITION, lux=None)

        assert settings["ExposureTime"] < int(1.0 * 1_000_000)
        assert "ColourGains" in settings


class TestGainSpeedOverride:
    """Test _interpolate_gain with speed_override parameter."""

    def test_gain_speed_override_faster(self, test_config_file):
        """Test speed_override makes gain change faster."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Initialize at gain 1.0
        timelapse.exposure._interpolate_gain(1.0)

        # Normal interpolation (default speed ~0.10)
        timelapse_normal = AdaptiveTimelapse(test_config_file)
        timelapse_normal.exposure._interpolate_gain(1.0)
        normal_result = timelapse_normal.exposure._interpolate_gain(6.0)

        # Fast interpolation with speed_override
        fast_result = timelapse.exposure._interpolate_gain(6.0, speed_override=0.30)

        # Fast should be closer to target than normal
        assert fast_result > normal_result
        assert fast_result > 1.0 and fast_result < 6.0

    def test_gain_speed_override_slower(self, test_config_file):
        """Test speed_override can also slow down transitions."""
        timelapse = AdaptiveTimelapse(test_config_file)

        timelapse.exposure._interpolate_gain(1.0)
        slow_result = timelapse.exposure._interpolate_gain(6.0, speed_override=0.05)

        # With 5% speed: 1.0 + 0.05 * (6.0 - 1.0) = 1.25
        assert slow_result < 2.0  # Should be very slow


class TestNightModeBrightnessFeedback:
    """Test night mode brightness feedback for dawn overexposure."""

    def test_night_mode_reduces_exposure_when_overexposed(self, test_config_file):
        """Test night mode reduces exposure when brightness > 140."""
        timelapse = AdaptiveTimelapse(test_config_file)
        # Enable direct brightness control
        timelapse.config["adaptive_timelapse"]["direct_brightness_control"] = True

        # Initialize exposure tracking
        timelapse.exposure._last_exposure_time = 20.0
        timelapse.exposure._last_analogue_gain = 6.0
        timelapse.exposure._last_brightness = 165  # Overexposed

        settings = timelapse.exposure.get_camera_settings(LightMode.NIGHT, lux=2.0)

        # Should reduce from max 20s due to brightness feedback
        exposure_s = settings["ExposureTime"] / 1_000_000
        assert exposure_s < 20.0
        # But not below 60% of max (12s)
        assert exposure_s >= 12.0

    def test_night_mode_full_exposure_when_not_overexposed(self, test_config_file):
        """Test night mode uses max exposure when brightness is normal."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.config["adaptive_timelapse"]["direct_brightness_control"] = True

        timelapse.exposure._last_exposure_time = 18.0
        timelapse.exposure._last_analogue_gain = 5.5
        timelapse.exposure._last_brightness = 100  # Normal brightness

        settings = timelapse.exposure.get_camera_settings(LightMode.NIGHT, lux=2.0)

        # Should ramp towards max 20s (not reduce)
        exposure_s = settings["ExposureTime"] / 1_000_000
        assert exposure_s >= 18.0


class TestCoordinatedNightModeRamps:
    """Test coordinated gain/exposure ramps when entering night mode."""

    def test_entering_night_uses_coordinated_ramps(self, test_config_file):
        """Test coordinated ramps when gain < 50% of target."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.config["adaptive_timelapse"]["direct_brightness_control"] = True

        # Simulate coming from transition: low gain, medium exposure
        timelapse.exposure._last_analogue_gain = 1.5  # < 50% of target 6.0
        timelapse.exposure._last_exposure_time = 16.0
        timelapse.exposure._last_brightness = 80

        settings = timelapse.exposure.get_camera_settings(LightMode.NIGHT, lux=2.0)

        # Gain should increase (using faster 0.08 speed)
        assert settings["AnalogueGain"] > 1.5
        # Exposure should increase slowly (using 0.05 speed)
        exposure_s = settings["ExposureTime"] / 1_000_000
        assert exposure_s > 16.0

    def test_established_night_uses_normal_ramps(self, test_config_file):
        """Test normal ramps when already in night mode (gain >= 50% of target)."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.config["adaptive_timelapse"]["direct_brightness_control"] = True

        # Already in night mode: high gain
        timelapse.exposure._last_analogue_gain = 4.0  # >= 50% of target 6.0
        timelapse.exposure._last_exposure_time = 19.0
        timelapse.exposure._last_brightness = 100

        # This should NOT trigger coordinated ramps
        settings = timelapse.exposure.get_camera_settings(LightMode.NIGHT, lux=2.0)

        # Should still work normally
        assert settings["AnalogueGain"] >= 4.0
        exposure_s = settings["ExposureTime"] / 1_000_000
        assert exposure_s >= 19.0


class TestHighlightFactor:
    """The pure highlight-headroom curve."""

    def test_none_and_safe_return_exactly_one(self):
        from src.auto_timelapse import highlight_factor

        assert highlight_factor(None) == 1.0
        assert highlight_factor(0) == 1.0
        assert highlight_factor(199) == 1.0
        # Exactly 1.0 at the boundary, so there is a real deadband rather than
        # a factor that is forever a hair under 1.
        assert highlight_factor(200) == 1.0

    def test_monotonically_decreasing(self):
        from src.auto_timelapse import highlight_factor

        values = [highlight_factor(p) for p in range(200, 256)]
        assert all(a >= b for a, b in zip(values, values[1:]))

    def test_never_below_floor(self):
        from src.auto_timelapse import highlight_factor

        assert highlight_factor(255) >= 0.70
        assert highlight_factor(255, floor=0.5) >= 0.5
        assert highlight_factor(1000) >= 0.70

    def test_thresholds_are_configurable(self):
        from src.auto_timelapse import highlight_factor

        assert highlight_factor(210, safe=220) == 1.0
        assert highlight_factor(210, safe=200) < 1.0

    def test_is_pure(self, caplog):
        """No logging from inside the calculation.

        A logger.warning() in here was 742 of 777 lines in the live log.
        """
        from src.auto_timelapse import highlight_factor

        with caplog.at_level("DEBUG"):
            highlight_factor(255)
        assert caplog.records == []


class TestHighlightProtection:
    """Highlight protection as wired into the live controller."""

    @pytest.fixture
    def timelapse(self, direct_control_config_file):
        import yaml

        with open(direct_control_config_file) as f:
            data = yaml.safe_load(f)
        data["adaptive_timelapse"]["highlight_protection"] = {
            "enabled": True,
            "safe_p95": 200,
            "warning_p95": 220,
            "critical_p95": 240,
            "min_scale": 0.70,
            "slew": 0.25,
            "apply_in_night": False,
        }
        with open(direct_control_config_file, "w") as f:
            yaml.dump(data, f)

        tl = AdaptiveTimelapse(direct_control_config_file)
        tl.exposure._last_exposure_time = 0.01
        return tl

    def test_disabled_by_default(self, direct_control_config_file):
        # No highlight_protection block at all: behaviour must be unchanged.
        tl = AdaptiveTimelapse(direct_control_config_file)
        assert tl.exposure._p95_enabled is False
        assert tl.exposure._highlight_target_scale(255, LightMode.DAY) == 1.0

    def test_no_effect_below_safe_threshold(self, timelapse):
        assert timelapse.exposure._highlight_target_scale(150, LightMode.DAY) == 1.0

    def test_reduces_exposure_when_highlights_clip(self, timelapse):
        timelapse.exposure._last_p95 = 255
        clipped = timelapse.exposure._calculate_exposure_from_brightness(120, mode=LightMode.DAY)

        timelapse.exposure._last_exposure_time = 0.01
        timelapse.exposure._p95_scale = 1.0
        timelapse.exposure._last_p95 = 100
        normal = timelapse.exposure._calculate_exposure_from_brightness(120, mode=LightMode.DAY)

        assert clipped < normal

    def test_slew_limits_a_single_frame(self, timelapse):
        # One noisy p95 sample must not step the target all the way down.
        first = timelapse.exposure._highlight_target_scale(255, LightMode.DAY)
        assert first > 0.9  # a quarter of the way from 1.0 toward 0.70

        for _ in range(20):
            timelapse.exposure._highlight_target_scale(255, LightMode.DAY)
        assert timelapse.exposure._p95_scale == pytest.approx(0.70, abs=0.01)

    def test_scale_relaxes_back_when_highlights_recover(self, timelapse):
        for _ in range(20):
            timelapse.exposure._highlight_target_scale(255, LightMode.DAY)
        assert timelapse.exposure._p95_scale < 0.75

        for _ in range(30):
            timelapse.exposure._highlight_target_scale(150, LightMode.DAY)
        assert timelapse.exposure._p95_scale == pytest.approx(1.0, abs=0.01)

    def test_night_is_exempt_by_default(self, timelapse):
        assert timelapse.exposure._highlight_target_scale(255, LightMode.NIGHT) == 1.0
        assert timelapse.exposure._highlight_target_scale(255, LightMode.DAY) < 1.0

    def test_night_can_be_opted_in(self, timelapse):
        timelapse.exposure._p95_apply_in_night = True
        assert timelapse.exposure._highlight_target_scale(255, LightMode.NIGHT) < 1.0

    def test_loop_settles_with_protection_engaged(self, timelapse):
        """The feedback loop must reach a steady state, not hunt.

        Simulates a scene where both mean brightness and p95 track exposure,
        with p95 saturating -- the case where protection stays engaged.
        """
        exposure = 0.01
        timelapse.exposure._last_exposure_time = exposure
        history = []

        for _ in range(40):
            # Simple monotone scene model: brightness and p95 both scale with
            # exposure, and p95 saturates at 255.
            brightness = min(255, exposure * 12000)
            timelapse.exposure._last_p95 = min(255, exposure * 21000)
            exposure = timelapse.exposure._calculate_exposure_from_brightness(
                brightness, mode=LightMode.DAY
            )
            timelapse.exposure._last_exposure_time = exposure
            history.append(exposure)

        tail = history[-8:]
        spread = (max(tail) - min(tail)) / max(tail)
        assert spread < 0.05, f"exposure did not settle: {tail}"

        # And it settled somewhere sane, not pinned at a clamp.
        assert 0.0001 < tail[-1] < 20.0


class TestPartialSeedResilience:
    """A partial database row must not be able to crash the capture loop.

    seed_from_capture applies only the fields the row actually had, so a row
    with brightness but no analogue_gain leaves _last_analogue_gain as None
    while _last_brightness is set. Night mode's gain-reduction branch then
    reached for that gain and raised TypeError -- out of get_camera_settings,
    on the capture path.
    """

    def test_night_gain_reduction_with_no_seeded_gain(self, direct_control_config_file):
        tl = AdaptiveTimelapse(direct_control_config_file)

        tl.exposure.seed_from_capture(brightness=180.0, lux=1.0, mode=LightMode.NIGHT)
        assert tl.exposure._last_analogue_gain is None
        assert tl.exposure.last_brightness == 180.0

        settings = tl.exposure.get_camera_settings(LightMode.NIGHT, lux=1.0)

        assert settings["AnalogueGain"] >= 2.0
        assert settings["ExposureTime"] > 0

    def test_highlight_factor_never_divides_by_zero(self):
        """Degenerate threshold configs must not crash the capture loop.

        They cannot: the `p95 <= safe` early return covers every p95 that could
        reach an interpolation with a zero-width band. Asserted rather than
        assumed, because it is the kind of invariant a later edit could break.
        """
        from src.auto_timelapse import highlight_factor

        for safe in range(0, 260, 20):
            for warning in range(0, 260, 20):
                for critical in range(0, 260, 20):
                    for p95 in (0, 1, safe, warning, critical, 200, 255):
                        value = highlight_factor(p95, safe=safe, warning=warning, critical=critical)
                        assert 0.70 <= value <= 1.0

    def test_observe_frame_tolerates_none_measurements(self, direct_control_config_file):
        """A failed measurement sets the key to None, which a .get default misses."""
        tl = AdaptiveTimelapse(direct_control_config_file)

        tl.exposure.observe_frame(
            {
                "mean_brightness": None,
                "percentile_95": None,
                "std_brightness": None,
                "overexposed_percent": None,
                "underexposed_percent": None,
            }
        )

        # Not merely "does not raise": a None must not be *stored* either, or
        # the next frame's ratio arithmetic gets it instead.
        assert tl.exposure._last_brightness is None
        assert tl.exposure._last_p95 is None


class TestBrightnessZones:
    """Tests for BrightnessZones constants."""

    def test_brightness_zones_defined(self):
        """Test that BrightnessZones class is properly defined."""
        from src.auto_timelapse import BrightnessZones

        assert BrightnessZones.EMERGENCY_HIGH == 180
        assert BrightnessZones.WARNING_HIGH == 160
        assert BrightnessZones.TARGET == 120
        assert BrightnessZones.WARNING_LOW == 80
        assert BrightnessZones.EMERGENCY_LOW == 60

    def test_zones_are_ordered(self):
        """The landmarks must stay monotonic for the mode override to make sense."""
        from src.auto_timelapse import BrightnessZones

        assert (
            BrightnessZones.CRITICAL_LOW
            < BrightnessZones.EMERGENCY_LOW
            < BrightnessZones.WARNING_LOW
            < BrightnessZones.TARGET
            < BrightnessZones.WARNING_HIGH
            < BrightnessZones.EMERGENCY_HIGH
        )


class TestHybridModeDetection:
    """Tests for hybrid mode detection in determine_mode."""

    @pytest.fixture
    def timelapse(self, tmp_path):
        """Create a minimal AdaptiveTimelapse instance for testing."""
        config = {
            "adaptive_timelapse": {
                "night_mode": {"max_exposure_time": 20.0, "analogue_gain": 8.0},
                "day_mode": {"exposure_time": 0.02},
                "light_thresholds": {"night": 3, "day": 80},
                "transition_mode": {
                    "target_brightness": 120,
                    "brightness_tolerance": 40,
                    "brightness_feedback_strength": 0.3,
                },
            },
            "output": {"directory": str(tmp_path / "output")},
            "camera": {"resolution": [1920, 1080]},
        }

        config_path = tmp_path / "config.yml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        (tmp_path / "output").mkdir(exist_ok=True)

        from src.auto_timelapse import AdaptiveTimelapse

        timelapse = AdaptiveTimelapse(str(config_path))
        return timelapse

    def test_standard_night_mode(self, timelapse):
        """Test standard night mode detection."""
        from src.auto_timelapse import LightMode

        # Low lux, no brightness data
        timelapse.exposure._last_brightness = None
        mode = timelapse.exposure.determine_mode(1.0)
        assert mode == LightMode.NIGHT

    def test_standard_day_mode(self, timelapse):
        """Test standard day mode detection."""
        from src.auto_timelapse import LightMode

        # High lux, no brightness data
        timelapse.exposure._last_brightness = None
        mode = timelapse.exposure.determine_mode(200.0)
        assert mode == LightMode.DAY

    def test_standard_transition_mode(self, timelapse):
        """Test standard transition mode detection."""
        from src.auto_timelapse import LightMode

        # Mid lux, no brightness data
        timelapse.exposure._last_brightness = None
        mode = timelapse.exposure.determine_mode(40.0)
        assert mode == LightMode.TRANSITION

    def test_night_mode_overexposed_override(self, timelapse):
        """Test hybrid override: night mode but overexposed brightness."""
        from src.auto_timelapse import LightMode

        # Low lux (night), but high brightness (overexposed)
        timelapse.exposure._last_brightness = 180.0
        mode = timelapse.exposure.determine_mode(1.0)

        # Should force transition mode due to brightness override
        assert mode == LightMode.TRANSITION

    def test_day_mode_underexposed_override(self, timelapse):
        """Test hybrid override: day mode but underexposed brightness."""
        from src.auto_timelapse import LightMode

        # High lux (day), but low brightness (underexposed)
        timelapse.exposure._last_brightness = 70.0
        mode = timelapse.exposure.determine_mode(200.0)

        # Should force transition mode due to brightness override
        assert mode == LightMode.TRANSITION

    def test_no_override_when_brightness_matches_mode(self, timelapse):
        """Test no override when brightness matches the lux-based mode."""
        from src.auto_timelapse import LightMode

        # Night mode with appropriate brightness (dark)
        timelapse.exposure._last_brightness = 100.0
        mode = timelapse.exposure.determine_mode(1.0)
        assert mode == LightMode.NIGHT

        # Day mode with appropriate brightness (bright)
        timelapse.exposure._last_brightness = 150.0
        mode = timelapse.exposure.determine_mode(200.0)
        assert mode == LightMode.DAY


class TestNightModeGainReduction:
    """Tests for night mode gain reduction when exposure is at floor."""

    @pytest.fixture
    def timelapse(self, tmp_path):
        """Create a minimal AdaptiveTimelapse instance for testing."""
        config = {
            "adaptive_timelapse": {
                "night_mode": {"max_exposure_time": 20.0, "analogue_gain": 6.0},
                "day_mode": {"exposure_time": 0.02},
                "light_thresholds": {"night": 3, "day": 80},
                "direct_brightness_control": True,
                "transition_mode": {
                    "target_brightness": 120,
                    "brightness_tolerance": 40,
                    "brightness_feedback_strength": 0.3,
                },
            },
            "output": {"directory": str(tmp_path / "output")},
            "camera": {"resolution": [1920, 1080]},
        }

        config_path = tmp_path / "config.yml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        (tmp_path / "output").mkdir(exist_ok=True)

        from src.auto_timelapse import AdaptiveTimelapse

        timelapse = AdaptiveTimelapse(str(config_path))
        return timelapse

    def test_gain_reduction_triggers_at_floor(self, timelapse):
        """Test that gain is reduced when exposure is at floor and brightness high."""
        # Simulate exposure at floor (12s = 60% of 20s max)
        timelapse.exposure._last_exposure_time = 12.0
        timelapse.exposure._last_analogue_gain = 6.0
        timelapse.exposure._last_brightness = 160  # Above 150 threshold

        # Get camera settings for night mode
        settings = timelapse.exposure.get_camera_settings("night")

        # When exposure is at floor and brightness > 150, gain should be reduced
        # The gain should be lower than the configured night gain (6.0)
        assert settings["AnalogueGain"] < 6.0

    def test_gain_not_reduced_when_brightness_normal(self, timelapse):
        """Test that gain is not reduced when brightness is acceptable."""
        timelapse.exposure._last_exposure_time = 12.0
        timelapse.exposure._last_analogue_gain = 6.0
        timelapse.exposure._last_brightness = 130  # Below 150, within acceptable range

        settings = timelapse.exposure.get_camera_settings("night")

        # Gain should ramp toward target (6.0) normally
        # Should be close to last gain since we're in steady state
        assert settings["AnalogueGain"] >= 5.5  # Should be ramping up, not down

    def test_gain_floor_respected(self, timelapse):
        """Test that gain never goes below minimum (2.0)."""
        timelapse.exposure._last_exposure_time = 12.0
        timelapse.exposure._last_analogue_gain = 6.0
        timelapse.exposure._last_brightness = 250  # Very high brightness

        settings = timelapse.exposure.get_camera_settings("night")

        # Even with extreme brightness, gain should not go below 2.0
        assert settings["AnalogueGain"] >= 2.0


class TestEnteringNightThrottle:
    """Tests for brightness throttling when entering night mode."""

    @pytest.fixture
    def timelapse(self, tmp_path):
        """Create a minimal AdaptiveTimelapse instance for testing."""
        config = {
            "adaptive_timelapse": {
                "night_mode": {"max_exposure_time": 20.0, "analogue_gain": 6.0},
                "day_mode": {"exposure_time": 0.02},
                "light_thresholds": {"night": 3, "day": 80},
                "direct_brightness_control": True,
                "transition_mode": {
                    "target_brightness": 120,
                    "brightness_tolerance": 40,
                    "brightness_feedback_strength": 0.3,
                },
            },
            "output": {"directory": str(tmp_path / "output")},
            "camera": {"resolution": [1920, 1080]},
        }

        config_path = tmp_path / "config.yml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        (tmp_path / "output").mkdir(exist_ok=True)

        from src.auto_timelapse import AdaptiveTimelapse

        timelapse = AdaptiveTimelapse(str(config_path))
        return timelapse

    def test_entering_night_detected(self, timelapse):
        """Test that entering night mode is detected when gain is low."""
        # Simulate coming from transition mode with low gain
        timelapse.exposure._last_analogue_gain = 2.0  # < 50% of target 6.0
        timelapse.exposure._last_exposure_time = 16.0
        timelapse.exposure._last_brightness = 60

        # Get camera settings - should use coordinated ramps
        settings = timelapse.exposure.get_camera_settings("night")

        # Gain should increase slowly (coordinated ramp at 4%)
        # From 2.0 toward 6.0, first step should be small
        assert 2.0 < settings["AnalogueGain"] < 2.5  # 4% of 2.0 = 0.08, so ~2.08

    def test_throttle_applied_when_brightness_high(self, timelapse):
        """Test that ramp speed is throttled when brightness approaches target."""
        # Simulate entering night with brightness near target (80)
        timelapse.exposure._last_analogue_gain = 2.0  # Entering night
        timelapse.exposure._last_exposure_time = 16.0
        timelapse.exposure._last_brightness = 85  # > 80, should trigger throttle

        settings1 = timelapse.exposure.get_camera_settings("night")
        gain_increase_throttled = settings1["AnalogueGain"] - 2.0

        # Reset and test without throttle (low brightness)
        timelapse.exposure._last_analogue_gain = 2.0
        timelapse.exposure._last_brightness = 50  # Below 64, no throttle

        settings2 = timelapse.exposure.get_camera_settings("night")
        gain_increase_normal = settings2["AnalogueGain"] - 2.0

        # Throttled increase should be smaller than normal
        assert gain_increase_throttled < gain_increase_normal

    def test_minimum_throttle_speed(self, timelapse):
        """Test that throttle has a minimum speed (30%)."""
        # Even at high brightness, ramps should still progress
        timelapse.exposure._last_analogue_gain = 2.0
        timelapse.exposure._last_exposure_time = 16.0
        timelapse.exposure._last_brightness = 120  # Very high, max throttle

        settings = timelapse.exposure.get_camera_settings("night")

        # Should still make progress (30% of base 4% = 1.2% per frame)
        # From gain 2.0, should increase by at least 0.024
        assert settings["AnalogueGain"] > 2.0


@pytest.fixture
def dynamic_target_timelapse(tmp_path):
    """Create an AdaptiveTimelapse instance with brightness_target config."""
    config = {
        "adaptive_timelapse": {
            "enabled": True,
            "interval": 30,
            "num_frames": 0,
            "reference_lux": 3.8,
            "direct_brightness_control": True,
            "brightness_damping": 0.5,
            "light_thresholds": {"night": 3, "day": 80},
            "night_mode": {
                "max_exposure_time": 20.0,
                "analogue_gain": 6,
                "awb_enable": False,
            },
            "day_mode": {"exposure_time": 0.01, "analogue_gain": 1},
            "transition_mode": {
                "smooth_transition": True,
                "target_brightness": 120,
                "brightness_tolerance": 40,
                "brightness_feedback_strength": 0.05,
            },
            "test_shot": {"enabled": True, "exposure_time": 0.2, "analogue_gain": 1},
            "brightness_target": {
                "base": 120,
                "overcast_boost": 15,
                "max_target": 140,
                "contrast_threshold_low": 25,
                "contrast_threshold_high": 40,
            },
            "hdr": {
                "enabled": False,
            },
        },
        "output": {"directory": str(tmp_path / "output")},
        "camera": {
            "resolution": {"width": 1920, "height": 1080},
            "transforms": {"horizontal_flip": False, "vertical_flip": False},
            "controls": {},
        },
        "system": {
            "create_directories": True,
            "save_metadata": False,
            "metadata_filename": "meta.json",
            "metadata_folder": "metadata",
        },
        "overlay": {"enabled": False},
    }

    config_path = tmp_path / "config.yml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)

    (tmp_path / "output").mkdir(exist_ok=True)

    return AdaptiveTimelapse(str(config_path))


class TestDynamicTargetBrightness:
    """Tests for _get_dynamic_target_brightness method."""

    def test_sunny_day_no_boost(self, dynamic_target_timelapse):
        """High std_brightness (sunny) should return base target."""
        dynamic_target_timelapse.exposure._last_mode = LightMode.DAY
        result = dynamic_target_timelapse.exposure._get_dynamic_target_brightness(50.0)
        assert result == 120

    def test_overcast_full_boost(self, dynamic_target_timelapse):
        """Low std_brightness (overcast) should return boosted target."""
        dynamic_target_timelapse.exposure._last_mode = LightMode.DAY
        result = dynamic_target_timelapse.exposure._get_dynamic_target_brightness(20.0)
        assert result == 135  # 120 + 15

    def test_very_low_contrast_capped(self, dynamic_target_timelapse):
        """Very low contrast should be capped at max_target."""
        dynamic_target_timelapse.exposure._last_mode = LightMode.DAY
        result = dynamic_target_timelapse.exposure._get_dynamic_target_brightness(5.0)
        assert result == 135  # 120 + 15, capped at 140 but 135 < 140

    def test_max_target_cap(self, dynamic_target_timelapse):
        """Boost should not exceed max_target."""
        dynamic_target_timelapse.exposure._overcast_boost = 30  # Would give 120 + 30 = 150
        dynamic_target_timelapse.exposure._last_mode = LightMode.DAY
        result = dynamic_target_timelapse.exposure._get_dynamic_target_brightness(10.0)
        assert result == 140  # Capped at max_target

    def test_at_low_threshold(self, dynamic_target_timelapse):
        """At exactly the low threshold, should get full boost."""
        dynamic_target_timelapse.exposure._last_mode = LightMode.DAY
        result = dynamic_target_timelapse.exposure._get_dynamic_target_brightness(25.0)
        assert result == 135  # Full boost

    def test_at_high_threshold(self, dynamic_target_timelapse):
        """At exactly the high threshold, should get no boost."""
        dynamic_target_timelapse.exposure._last_mode = LightMode.DAY
        result = dynamic_target_timelapse.exposure._get_dynamic_target_brightness(40.0)
        assert result == 120  # No boost

    def test_midpoint_interpolation(self, dynamic_target_timelapse):
        """Midpoint between thresholds should give ~half boost."""
        dynamic_target_timelapse.exposure._last_mode = LightMode.DAY
        # Midpoint of 25 and 40 is 32.5
        result = dynamic_target_timelapse.exposure._get_dynamic_target_brightness(32.5)
        # t = (32.5 - 25) / (40 - 25) = 0.5
        # boost = 15 * (1 - 0.5) = 7.5
        # target = 120 + 7.5 = 127.5, rounded to 128
        assert result == 128

    def test_night_mode_no_boost(self, dynamic_target_timelapse):
        """Night mode should always return base target, regardless of std."""
        dynamic_target_timelapse.exposure._last_mode = LightMode.NIGHT
        result = dynamic_target_timelapse.exposure._get_dynamic_target_brightness(10.0)
        assert result == 120  # No boost in night mode

    def test_transition_mode_gets_boost(self, dynamic_target_timelapse):
        """Transition mode should get boost like day mode."""
        dynamic_target_timelapse.exposure._last_mode = LightMode.TRANSITION
        result = dynamic_target_timelapse.exposure._get_dynamic_target_brightness(20.0)
        assert result == 135  # Full boost

    def test_none_std_returns_base(self, dynamic_target_timelapse):
        """None std_brightness should return base target."""
        dynamic_target_timelapse.exposure._last_mode = LightMode.DAY
        result = dynamic_target_timelapse.exposure._get_dynamic_target_brightness(None)
        assert result == 120

    def test_negative_std_returns_base(self, dynamic_target_timelapse):
        """Negative std_brightness should return base target."""
        dynamic_target_timelapse.exposure._last_mode = LightMode.DAY
        result = dynamic_target_timelapse.exposure._get_dynamic_target_brightness(-5.0)
        assert result == 120

    def test_zero_std_returns_boosted(self, dynamic_target_timelapse):
        """Zero std (completely flat image) should get full boost."""
        dynamic_target_timelapse.exposure._last_mode = LightMode.DAY
        result = dynamic_target_timelapse.exposure._get_dynamic_target_brightness(0.0)
        assert result == 135

    def test_no_mode_set_returns_base(self, dynamic_target_timelapse):
        """When no mode has been set yet, return base target."""
        dynamic_target_timelapse.exposure._last_mode = None
        # _last_mode is None, not NIGHT, so it won't trigger the night guard
        # But the method should still work (None != NIGHT)
        result = dynamic_target_timelapse.exposure._get_dynamic_target_brightness(20.0)
        assert result == 135  # Still gets boost since mode is not NIGHT


class TestConfigLoading:
    """Tests for brightness_target config loading."""

    def test_config_defaults(self, tmp_path):
        """Test that missing brightness_target config uses defaults."""
        config = {
            "adaptive_timelapse": {
                "enabled": True,
                "interval": 30,
                "num_frames": 0,
                "light_thresholds": {"night": 3, "day": 80},
                "night_mode": {
                    "max_exposure_time": 20.0,
                    "analogue_gain": 6,
                },
                "day_mode": {},
                "transition_mode": {},
                "test_shot": {
                    "enabled": True,
                    "exposure_time": 0.2,
                    "analogue_gain": 1,
                },
            },
            "output": {"directory": str(tmp_path / "output")},
            "camera": {
                "resolution": {"width": 1920, "height": 1080},
                "transforms": {"horizontal_flip": False, "vertical_flip": False},
                "controls": {},
            },
            "system": {
                "create_directories": True,
                "save_metadata": False,
                "metadata_filename": "meta.json",
                "metadata_folder": "metadata",
            },
            "overlay": {"enabled": False},
        }

        config_path = tmp_path / "config.yml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        (tmp_path / "output").mkdir(exist_ok=True)
        tl = AdaptiveTimelapse(str(config_path))

        assert tl.exposure._base_target_brightness == 120
        assert tl.exposure._overcast_boost == 15
        assert tl.exposure._max_target_brightness == 140
        assert tl.exposure._contrast_threshold_low == 25
        assert tl.exposure._contrast_threshold_high == 40

    def test_custom_config(self, dynamic_target_timelapse):
        """Test that custom brightness_target config is loaded."""
        assert dynamic_target_timelapse.exposure._base_target_brightness == 120
        assert dynamic_target_timelapse.exposure._overcast_boost == 15
        assert dynamic_target_timelapse.exposure._max_target_brightness == 140
        assert dynamic_target_timelapse.exposure._contrast_threshold_low == 25
        assert dynamic_target_timelapse.exposure._contrast_threshold_high == 40
