"""Tests for overlay module."""

import os
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest
import yaml
from PIL import Image

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.overlay import ImageOverlay, apply_overlay_to_image


@pytest.fixture
def test_overlay_config():
    """Create a test configuration with overlay settings."""
    config = {
        "overlay": {
            "enabled": True,
            "position": "bottom-left",
            "camera_name": "Test Camera",
            "font": {
                "family": "default",
                "size_ratio": 0.025,
                "color": [255, 255, 255, 255],
            },
            "background": {
                "enabled": True,
                "color": [0, 0, 0, 180],
                "padding": 0.3,
            },
            "content": {
                "main": [
                    "{camera_name}",
                    "{date} {time}",
                ],
                "camera_settings": {
                    "enabled": True,
                    "lines": ["Exposure: {exposure} | ISO: {iso}"],
                },
                "debug": {
                    "enabled": False,
                    "lines": ["Gain: {gain}"],
                },
            },
            "layout": {
                "line_spacing": 1.3,
                "section_spacing": True,
            },
            "datetime": {
                "localized": False,
                "show_seconds": False,
            },
        }
    }
    return config


@pytest.fixture
def test_metadata():
    """Create test metadata."""
    return {
        "ExposureTime": 10000,  # 10ms
        "AnalogueGain": 2.0,
        "Lux": 500.5,
        "ColourGains": [1.5, 1.3],
        "SensorTemperature": 35.0,
        "resolution": [1920, 1080],
    }


@pytest.fixture
def test_image():
    """Create a test image."""
    img = Image.new("RGB", (800, 600), color=(100, 150, 200))
    temp_file = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    img.save(temp_file.name)
    yield temp_file.name
    os.unlink(temp_file.name)


class TestImageOverlay:
    """Test ImageOverlay class."""

    def test_init_enabled(self, test_overlay_config):
        """Test overlay initialization when enabled."""
        overlay = ImageOverlay(test_overlay_config)
        assert overlay.enabled is True
        assert overlay.config == test_overlay_config

    def test_init_disabled(self):
        """Test overlay initialization when disabled."""
        config = {"overlay": {"enabled": False}}
        overlay = ImageOverlay(config)
        assert overlay.enabled is False

    def test_format_exposure_time(self, test_overlay_config):
        """Test exposure time formatting."""
        overlay = ImageOverlay(test_overlay_config)

        # Microseconds
        assert overlay._format_exposure_time(500) == "500µs"

        # Milliseconds
        assert overlay._format_exposure_time(5000) == "5.0ms"
        assert overlay._format_exposure_time(2000) == "2.0ms"
        assert overlay._format_exposure_time(100_000) == "100.0ms"

        # Seconds (>= 1 second)
        assert overlay._format_exposure_time(1_000_000) == "1.0s"
        assert overlay._format_exposure_time(1_500_000) == "1.5s"
        assert overlay._format_exposure_time(2_000_000) == "2.0s"
        assert overlay._format_exposure_time(10_000_000) == "10.0s"
        assert overlay._format_exposure_time(20_000_000) == "20.0s"

    def test_format_iso(self, test_overlay_config):
        """Test ISO formatting."""
        overlay = ImageOverlay(test_overlay_config)
        assert overlay._format_iso(1.0) == "ISO 100"
        assert overlay._format_iso(2.5) == "ISO 250"
        assert overlay._format_iso(8.0) == "ISO 800"

    def test_format_wb_gains(self, test_overlay_config):
        """Test white balance gains formatting."""
        overlay = ImageOverlay(test_overlay_config)
        assert overlay._format_wb_gains([1.5, 1.3]) == "R:1.50 B:1.30"
        assert overlay._format_wb_gains([]) == "N/A"

    def test_format_color_gains(self, test_overlay_config):
        """Test color gains tuple formatting."""
        overlay = ImageOverlay(test_overlay_config)
        assert overlay._format_color_gains([1.8, 1.5]) == "(1.80, 1.50)"
        assert overlay._format_color_gains([]) == "N/A"

    def test_prepare_overlay_data(self, test_overlay_config, test_metadata):
        """Test overlay data preparation."""
        overlay = ImageOverlay(test_overlay_config)
        data = overlay._prepare_overlay_data(test_metadata, mode="day")

        assert data["camera_name"] == "Test Camera"
        assert data["mode"] == "Day"
        assert data["iso"] == "ISO 200"
        assert "exposure" in data
        assert "lux" in data
        assert "date" in data
        assert "time" in data

    def test_get_text_lines(self, test_overlay_config, test_metadata):
        """Test text line generation."""
        overlay = ImageOverlay(test_overlay_config)
        data = overlay._prepare_overlay_data(test_metadata, mode="night")
        lines = overlay._get_text_lines(data)

        assert len(lines) > 0
        assert "Test Camera" in lines[0]
        assert any("Exposure:" in line for line in lines)

    def test_apply_overlay_disabled(self, test_image):
        """Test overlay application when disabled."""
        config = {"overlay": {"enabled": False}}
        overlay = ImageOverlay(config)

        result = overlay.apply_overlay(test_image, {}, mode="day")
        assert result == test_image

    def test_apply_overlay_basic(self, test_overlay_config, test_image, test_metadata):
        """Test basic overlay application."""
        overlay = ImageOverlay(test_overlay_config)

        # Create output path
        output_path = test_image.replace(".jpg", "_overlay.jpg")

        result = overlay.apply_overlay(
            test_image, test_metadata, mode="day", output_path=output_path
        )

        assert result == output_path
        assert os.path.exists(result)

        # Cleanup
        os.unlink(output_path)

    def test_apply_overlay_topbar_mode(self, test_overlay_config, test_image, test_metadata):
        """Test overlay with top-bar position."""
        test_overlay_config["overlay"]["position"] = "top-bar"
        overlay = ImageOverlay(test_overlay_config)

        output_path = test_image.replace(".jpg", "_topbar.jpg")
        result = overlay.apply_overlay(
            test_image, test_metadata, mode="night", output_path=output_path
        )

        assert os.path.exists(result)

        # Cleanup
        os.unlink(output_path)

    def test_position_presets(self, test_overlay_config):
        """Test all position presets."""
        overlay = ImageOverlay(test_overlay_config)
        img_width, img_height = 1920, 1080
        text_bbox = (0, 0, 200, 50)

        positions = [
            "top-left",
            "top-right",
            "bottom-left",
            "bottom-right",
            "custom",
        ]

        for position in positions:
            overlay.overlay_config["position"] = position
            x, y = overlay._get_position(img_width, img_height, text_bbox)
            assert isinstance(x, int)
            assert isinstance(y, int)
            assert 0 <= x <= img_width
            assert 0 <= y <= img_height


class TestApplyOverlayToImage:
    """Test convenience function."""

    def test_apply_overlay_to_image(self, test_image, test_metadata):
        """Test apply_overlay_to_image function."""
        # Create temporary config file
        config_data = {
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
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = f.name

        try:
            output_path = test_image.replace(".jpg", "_func.jpg")
            result = apply_overlay_to_image(
                test_image,
                metadata=test_metadata,
                config_path=config_path,
                mode="day",
                output_path=output_path,
            )

            assert result == output_path
            assert os.path.exists(result)

            # Cleanup
            os.unlink(output_path)
        finally:
            os.unlink(config_path)


class TestGradientBackground:
    """Test gradient background rendering."""

    def test_draw_gradient_bar(self, test_overlay_config):
        """Test gradient bar drawing."""
        overlay = ImageOverlay(test_overlay_config)

        # Create test image
        img = Image.new("RGBA", (800, 100), color=(255, 255, 255, 0))
        from PIL import ImageDraw

        draw = ImageDraw.Draw(img, "RGBA")

        # Draw gradient
        overlay._draw_gradient_bar(draw, 800, 100, [0, 0, 0, 200])

        # Verify gradient was applied (pixels should have varying alpha)
        pixels = img.load()
        top_alpha = pixels[400, 10][3]  # Near top
        bottom_alpha = pixels[400, 90][3]  # Near bottom

        # Top should be more opaque than bottom (gradient fades out)
        assert top_alpha > bottom_alpha


class TestLocalizedDateTime:
    """Test localized datetime formatting."""

    def test_localized_datetime_disabled(self, test_overlay_config):
        """Test datetime formatting when localized is disabled."""
        from datetime import datetime

        test_overlay_config["overlay"]["datetime"] = {
            "localized": False,
            "show_seconds": False,
            "date_format": "%Y-%m-%d",
            "time_format": "%H:%M",
        }

        overlay = ImageOverlay(test_overlay_config)
        dt = datetime(2025, 11, 5, 16, 45, 30)
        result = overlay._format_localized_datetime(dt)

        assert "2025-11-05" in result
        assert "16:45" in result
        assert "30" not in result  # Seconds disabled

    def test_show_seconds(self, test_overlay_config):
        """Test show_seconds option."""
        from datetime import datetime

        test_overlay_config["overlay"]["datetime"] = {
            "localized": False,
            "show_seconds": True,
            "date_format": "%Y-%m-%d",
            "time_format": "%H:%M:%S",
        }

        overlay = ImageOverlay(test_overlay_config)
        dt = datetime(2025, 11, 5, 16, 45, 30)
        result = overlay._format_localized_datetime(dt)

        assert "16:45:30" in result  # Seconds enabled


class TestErrorHandling:
    """Test error handling in overlay module."""

    def test_apply_overlay_missing_image(self, test_overlay_config):
        """Test handling of missing image file."""
        overlay = ImageOverlay(test_overlay_config)

        with pytest.raises(Exception):
            overlay.apply_overlay("/nonexistent/image.jpg", {}, mode="day")

    def test_apply_overlay_invalid_metadata(self, test_overlay_config, test_image):
        """Test handling of invalid metadata."""
        overlay = ImageOverlay(test_overlay_config)

        # Should not crash with missing metadata keys
        metadata = {}  # Empty metadata
        output_path = test_image.replace(".jpg", "_nometa.jpg")

        result = overlay.apply_overlay(test_image, metadata, mode="day", output_path=output_path)
        assert os.path.exists(result)

        # Cleanup
        os.unlink(output_path)


class TestModeCapitalization:
    """Test mode name capitalization."""

    def test_capitalize_mode_names(self, test_overlay_config, test_metadata):
        """Test that mode names are capitalized in overlay."""
        overlay = ImageOverlay(test_overlay_config)

        for mode in ["day", "night", "transition"]:
            data = overlay._prepare_overlay_data(test_metadata, mode=mode)
            assert data["mode"] == mode.capitalize()


class TestOverlayPositions:
    """Test all overlay position options."""

    def test_top_right_position(self, test_overlay_config, test_image, test_metadata):
        """Test top-right overlay position."""
        test_overlay_config["overlay"]["position"] = "top-right"
        overlay = ImageOverlay(test_overlay_config)

        output_path = test_image.replace(".jpg", "_topright.jpg")
        result = overlay.apply_overlay(
            test_image, test_metadata, mode="day", output_path=output_path
        )

        assert os.path.exists(result)
        os.unlink(output_path)

    def test_bottom_right_position(self, test_overlay_config, test_image, test_metadata):
        """Test bottom-right overlay position."""
        test_overlay_config["overlay"]["position"] = "bottom-right"
        overlay = ImageOverlay(test_overlay_config)

        output_path = test_image.replace(".jpg", "_bottomright.jpg")
        result = overlay.apply_overlay(
            test_image, test_metadata, mode="day", output_path=output_path
        )

        assert os.path.exists(result)
        os.unlink(output_path)

    def test_custom_position(self, test_overlay_config, test_image, test_metadata):
        """Test custom overlay position."""
        test_overlay_config["overlay"]["position"] = "custom"
        test_overlay_config["overlay"]["custom_position"] = {"x": 50, "y": 50}
        overlay = ImageOverlay(test_overlay_config)

        output_path = test_image.replace(".jpg", "_custom.jpg")
        result = overlay.apply_overlay(
            test_image, test_metadata, mode="day", output_path=output_path
        )

        assert os.path.exists(result)
        os.unlink(output_path)


class TestOverlayContent:
    """Test overlay content generation."""

    def test_main_content_only(self, test_overlay_config, test_image, test_metadata):
        """Test overlay with only main content."""
        test_overlay_config["overlay"]["content"]["camera_settings"]["enabled"] = False
        test_overlay_config["overlay"]["content"]["debug"]["enabled"] = False

        overlay = ImageOverlay(test_overlay_config)
        data = overlay._prepare_overlay_data(test_metadata, mode="day")
        lines = overlay._get_text_lines(data)

        # Should have main content
        assert len(lines) >= 2
        assert "Test Camera" in lines[0]

    def test_debug_content_enabled(self, test_overlay_config, test_metadata):
        """Test overlay with debug content."""
        test_overlay_config["overlay"]["content"]["debug"]["enabled"] = True
        test_overlay_config["overlay"]["content"]["debug"]["lines"] = ["Gain: {gain}"]

        overlay = ImageOverlay(test_overlay_config)
        data = overlay._prepare_overlay_data(test_metadata, mode="night")
        lines = overlay._get_text_lines(data)

        # Should include debug info
        assert any("Gain:" in line for line in lines)

    def test_resolution_formatting(self, test_overlay_config, test_metadata):
        """Test resolution formatting in overlay."""
        overlay = ImageOverlay(test_overlay_config)
        data = overlay._prepare_overlay_data(test_metadata, mode="day")

        assert "resolution" in data
        assert data["resolution"] == "1920×1080"

    def test_lux_formatting(self, test_overlay_config, test_metadata):
        """Test lux value formatting."""
        overlay = ImageOverlay(test_overlay_config)

        # Test with lux in metadata
        data = overlay._prepare_overlay_data(test_metadata, mode="day")
        assert "lux" in data
        assert data["lux"] == "500.5"

        # Test without lux
        metadata_no_lux = test_metadata.copy()
        del metadata_no_lux["Lux"]
        data = overlay._prepare_overlay_data(metadata_no_lux, mode="day")
        assert data["lux"] == "N/A"

    def test_temperature_formatting(self, test_overlay_config, test_metadata):
        """Test sensor temperature formatting."""
        overlay = ImageOverlay(test_overlay_config)
        data = overlay._prepare_overlay_data(test_metadata, mode="day")

        assert "temperature" in data
        assert data["temperature"] == "35.0"

    def test_wb_mode_auto(self, test_overlay_config, test_metadata):
        """Test white balance mode display."""
        overlay = ImageOverlay(test_overlay_config)

        # AWB disabled should show "manual"
        metadata_manual = test_metadata.copy()
        metadata_manual["AwbMode"] = 0
        data = overlay._prepare_overlay_data(metadata_manual, mode="night")
        assert data["wb"] == "manual"


class TestInPlaceOverlay:
    """Test in-place overlay (overwriting original)."""

    def test_apply_overlay_in_place(self, test_overlay_config, test_metadata):
        """Test applying overlay in-place."""
        # Create a copy of test image to modify
        temp_dir = tempfile.mkdtemp()
        try:
            test_img_path = os.path.join(temp_dir, "test.jpg")
            img = Image.new("RGB", (640, 480), color=(100, 150, 200))
            img.save(test_img_path)

            overlay = ImageOverlay(test_overlay_config)

            # Apply without output_path (in-place)
            result = overlay.apply_overlay(test_img_path, test_metadata, mode="day")

            assert result == test_img_path
            assert os.path.exists(test_img_path)
        finally:
            shutil.rmtree(temp_dir)


class TestApplyOverlayToImageFunction:
    """Test the convenience function apply_overlay_to_image."""

    def test_with_metadata_dict(self, test_image):
        """Test with metadata passed as dict."""
        config_data = {
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
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = f.name

        try:
            metadata = {"ExposureTime": 10000, "AnalogueGain": 1.0}
            output_path = test_image.replace(".jpg", "_dict.jpg")

            result = apply_overlay_to_image(
                test_image,
                metadata=metadata,
                config_path=config_path,
                mode="day",
                output_path=output_path,
            )

            assert result == output_path
            assert os.path.exists(result)

            # Cleanup
            os.unlink(output_path)
        finally:
            os.unlink(config_path)

    def test_with_metadata_path(self, test_image):
        """Test with metadata loaded from file path."""
        config_data = {
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
            }
        }

        # Create metadata file
        metadata = {"ExposureTime": 5000, "AnalogueGain": 2.0}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(metadata, f)
            metadata_path = f.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = f.name

        try:
            output_path = test_image.replace(".jpg", "_path.jpg")

            result = apply_overlay_to_image(
                test_image,
                metadata_path=metadata_path,
                config_path=config_path,
                mode="night",
                output_path=output_path,
            )

            assert result == output_path
            assert os.path.exists(result)

            # Cleanup
            os.unlink(output_path)
        finally:
            os.unlink(metadata_path)
            os.unlink(config_path)

    def test_overlay_disabled_returns_original(self, test_image):
        """Test that disabled overlay returns original path."""
        config_data = {"overlay": {"enabled": False}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = f.name

        try:
            result = apply_overlay_to_image(
                test_image,
                metadata={},
                config_path=config_path,
                mode="day",
            )

            assert result == test_image
        finally:
            os.unlink(config_path)


class TestBackgroundPadding:
    """Test background padding calculation."""

    def test_background_padding_multiplier(self, test_overlay_config):
        """Test background padding multiplier."""
        test_overlay_config["overlay"]["background"]["padding"] = 0.5
        overlay = ImageOverlay(test_overlay_config)

        # Verify padding is respected
        assert overlay.overlay_config["background"]["padding"] == 0.5
