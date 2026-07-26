"""
Tests for Emergency Brightness Zones and Hybrid Mode Detection.
"""

import pytest
import tempfile
import os
import yaml


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
