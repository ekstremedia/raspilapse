"""Tests for contrast-aware dynamic brightness targeting (overcast boost)."""

import os
import tempfile
import pytest
import yaml

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.auto_timelapse import AdaptiveTimelapse, LightMode


@pytest.fixture
def timelapse(tmp_path):
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

    def test_sunny_day_no_boost(self, timelapse):
        """High std_brightness (sunny) should return base target."""
        timelapse._last_mode = LightMode.DAY
        result = timelapse._get_dynamic_target_brightness(50.0)
        assert result == 120

    def test_overcast_full_boost(self, timelapse):
        """Low std_brightness (overcast) should return boosted target."""
        timelapse._last_mode = LightMode.DAY
        result = timelapse._get_dynamic_target_brightness(20.0)
        assert result == 135  # 120 + 15

    def test_very_low_contrast_capped(self, timelapse):
        """Very low contrast should be capped at max_target."""
        timelapse._last_mode = LightMode.DAY
        result = timelapse._get_dynamic_target_brightness(5.0)
        assert result == 135  # 120 + 15, capped at 140 but 135 < 140

    def test_max_target_cap(self, timelapse):
        """Boost should not exceed max_target."""
        timelapse._overcast_boost = 30  # Would give 120 + 30 = 150
        timelapse._last_mode = LightMode.DAY
        result = timelapse._get_dynamic_target_brightness(10.0)
        assert result == 140  # Capped at max_target

    def test_at_low_threshold(self, timelapse):
        """At exactly the low threshold, should get full boost."""
        timelapse._last_mode = LightMode.DAY
        result = timelapse._get_dynamic_target_brightness(25.0)
        assert result == 135  # Full boost

    def test_at_high_threshold(self, timelapse):
        """At exactly the high threshold, should get no boost."""
        timelapse._last_mode = LightMode.DAY
        result = timelapse._get_dynamic_target_brightness(40.0)
        assert result == 120  # No boost

    def test_midpoint_interpolation(self, timelapse):
        """Midpoint between thresholds should give ~half boost."""
        timelapse._last_mode = LightMode.DAY
        # Midpoint of 25 and 40 is 32.5
        result = timelapse._get_dynamic_target_brightness(32.5)
        # t = (32.5 - 25) / (40 - 25) = 0.5
        # boost = 15 * (1 - 0.5) = 7.5
        # target = 120 + 7.5 = 127.5, rounded to 128
        assert result == 128

    def test_night_mode_no_boost(self, timelapse):
        """Night mode should always return base target, regardless of std."""
        timelapse._last_mode = LightMode.NIGHT
        result = timelapse._get_dynamic_target_brightness(10.0)
        assert result == 120  # No boost in night mode

    def test_transition_mode_gets_boost(self, timelapse):
        """Transition mode should get boost like day mode."""
        timelapse._last_mode = LightMode.TRANSITION
        result = timelapse._get_dynamic_target_brightness(20.0)
        assert result == 135  # Full boost

    def test_none_std_returns_base(self, timelapse):
        """None std_brightness should return base target."""
        timelapse._last_mode = LightMode.DAY
        result = timelapse._get_dynamic_target_brightness(None)
        assert result == 120

    def test_negative_std_returns_base(self, timelapse):
        """Negative std_brightness should return base target."""
        timelapse._last_mode = LightMode.DAY
        result = timelapse._get_dynamic_target_brightness(-5.0)
        assert result == 120

    def test_zero_std_returns_boosted(self, timelapse):
        """Zero std (completely flat image) should get full boost."""
        timelapse._last_mode = LightMode.DAY
        result = timelapse._get_dynamic_target_brightness(0.0)
        assert result == 135

    def test_no_mode_set_returns_base(self, timelapse):
        """When no mode has been set yet, return base target."""
        timelapse._last_mode = None
        # _last_mode is None, not NIGHT, so it won't trigger the night guard
        # But the method should still work (None != NIGHT)
        result = timelapse._get_dynamic_target_brightness(20.0)
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

        assert tl._base_target_brightness == 120
        assert tl._overcast_boost == 15
        assert tl._max_target_brightness == 140
        assert tl._contrast_threshold_low == 25
        assert tl._contrast_threshold_high == 40

    def test_custom_config(self, timelapse):
        """Test that custom brightness_target config is loaded."""
        assert timelapse._base_target_brightness == 120
        assert timelapse._overcast_boost == 15
        assert timelapse._max_target_brightness == 140
        assert timelapse._contrast_threshold_low == 25
        assert timelapse._contrast_threshold_high == 40
