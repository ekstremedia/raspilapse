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

    def test_brightness_zone_factors(self):
        """Test that correction factors are properly defined."""
        from src.auto_timelapse import BrightnessZones

        # Factors for overexposure should reduce exposure (< 1.0)
        assert BrightnessZones.EMERGENCY_HIGH_FACTOR < 1.0
        assert BrightnessZones.WARNING_HIGH_FACTOR < 1.0

        # Factors for underexposure should increase exposure (> 1.0)
        assert BrightnessZones.WARNING_LOW_FACTOR > 1.0
        assert BrightnessZones.EMERGENCY_LOW_FACTOR > 1.0


class TestEmergencyBrightnessFactor:
    """Tests for _get_emergency_brightness_factor method."""

    @pytest.fixture
    def timelapse(self, tmp_path):
        """Create a minimal AdaptiveTimelapse instance for testing."""
        # Create minimal config file
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

        # Create output directory
        (tmp_path / "output").mkdir(exist_ok=True)

        from src.auto_timelapse import AdaptiveTimelapse

        timelapse = AdaptiveTimelapse(str(config_path))
        return timelapse

    def test_emergency_high_factor(self, timelapse):
        """Test emergency factor for severe overexposure."""
        from src.auto_timelapse import BrightnessZones

        # Test severe overexposure (>180)
        factor = timelapse._get_emergency_brightness_factor(200)
        assert factor == BrightnessZones.EMERGENCY_HIGH_FACTOR
        assert factor == 0.7

    def test_warning_high_factor(self, timelapse):
        """Test emergency factor for moderate overexposure."""
        from src.auto_timelapse import BrightnessZones

        # Test moderate overexposure (>160, <=180)
        factor = timelapse._get_emergency_brightness_factor(170)
        assert factor == BrightnessZones.WARNING_HIGH_FACTOR
        assert factor == 0.85

    def test_no_factor_in_normal_range(self, timelapse):
        """Test no emergency factor in normal brightness range."""
        # Test normal range (80-160)
        factor = timelapse._get_emergency_brightness_factor(120)
        assert factor == 1.0

        factor = timelapse._get_emergency_brightness_factor(100)
        assert factor == 1.0

        factor = timelapse._get_emergency_brightness_factor(150)
        assert factor == 1.0

    def test_warning_low_factor(self, timelapse):
        """Test emergency factor for moderate underexposure."""
        from src.auto_timelapse import BrightnessZones

        # Test moderate underexposure (<80, >=60)
        factor = timelapse._get_emergency_brightness_factor(70)
        assert factor == BrightnessZones.WARNING_LOW_FACTOR
        assert factor == 1.2

    def test_emergency_low_factor(self, timelapse):
        """Test emergency factor for severe underexposure."""
        from src.auto_timelapse import BrightnessZones

        # Test severe underexposure (<60)
        factor = timelapse._get_emergency_brightness_factor(50)
        assert factor == BrightnessZones.EMERGENCY_LOW_FACTOR
        assert factor == 1.4

    def test_none_brightness_returns_one(self, timelapse):
        """Test that None brightness returns factor of 1.0."""
        factor = timelapse._get_emergency_brightness_factor(None)
        assert factor == 1.0


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
        timelapse._last_brightness = None
        mode = timelapse.determine_mode(1.0)
        assert mode == LightMode.NIGHT

    def test_standard_day_mode(self, timelapse):
        """Test standard day mode detection."""
        from src.auto_timelapse import LightMode

        # High lux, no brightness data
        timelapse._last_brightness = None
        mode = timelapse.determine_mode(200.0)
        assert mode == LightMode.DAY

    def test_standard_transition_mode(self, timelapse):
        """Test standard transition mode detection."""
        from src.auto_timelapse import LightMode

        # Mid lux, no brightness data
        timelapse._last_brightness = None
        mode = timelapse.determine_mode(40.0)
        assert mode == LightMode.TRANSITION

    def test_night_mode_overexposed_override(self, timelapse):
        """Test hybrid override: night mode but overexposed brightness."""
        from src.auto_timelapse import LightMode

        # Low lux (night), but high brightness (overexposed)
        timelapse._last_brightness = 180.0
        mode = timelapse.determine_mode(1.0)

        # Should force transition mode due to brightness override
        assert mode == LightMode.TRANSITION

    def test_day_mode_underexposed_override(self, timelapse):
        """Test hybrid override: day mode but underexposed brightness."""
        from src.auto_timelapse import LightMode

        # High lux (day), but low brightness (underexposed)
        timelapse._last_brightness = 70.0
        mode = timelapse.determine_mode(200.0)

        # Should force transition mode due to brightness override
        assert mode == LightMode.TRANSITION

    def test_no_override_when_brightness_matches_mode(self, timelapse):
        """Test no override when brightness matches the lux-based mode."""
        from src.auto_timelapse import LightMode

        # Night mode with appropriate brightness (dark)
        timelapse._last_brightness = 100.0
        mode = timelapse.determine_mode(1.0)
        assert mode == LightMode.NIGHT

        # Day mode with appropriate brightness (bright)
        timelapse._last_brightness = 150.0
        mode = timelapse.determine_mode(200.0)
        assert mode == LightMode.DAY


class TestUrgencyScaledFeedback:
    """Tests for urgency-scaled brightness feedback."""

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
        # Initialize last brightness
        timelapse._last_brightness = None
        return timelapse

    def test_normal_urgency_small_error(self, timelapse):
        """Test normal urgency for small brightness error."""
        # Small error (within tolerance)
        timelapse._brightness_correction_factor = 1.0
        timelapse._apply_brightness_feedback(110)  # Error = -10

        # Correction should be within tolerance, factor decays toward 1.0
        # or stays near 1.0

    def test_elevated_urgency_medium_error(self, timelapse):
        """Test elevated urgency for medium brightness error."""
        timelapse._brightness_correction_factor = 1.0
        initial_factor = timelapse._brightness_correction_factor

        # Medium error (>40)
        timelapse._apply_brightness_feedback(170)  # Error = 50

        # Factor should decrease (reduce exposure) due to overexposure
        assert timelapse._brightness_correction_factor < initial_factor

    def test_urgent_correction_large_error(self, timelapse):
        """Test urgent correction for large brightness error."""
        timelapse._brightness_correction_factor = 1.0
        initial_factor = timelapse._brightness_correction_factor

        # Large error (>60)
        timelapse._apply_brightness_feedback(200)  # Error = 80

        # Factor should decrease significantly
        change = initial_factor - timelapse._brightness_correction_factor

        # With 3x urgency multiplier, change should be significant
        assert change > 0.1  # At least 10% change
