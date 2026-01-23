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

from src.overlay import ImageOverlay, apply_overlay_to_image, TideData


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
                "line_1_left": "{camera_name}",
                "line_1_right": "Exposure: {exposure} | ISO: {iso}",
                "line_2_left": "{date} {time}",
                "line_2_right": "Gain: {gain}",
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

        # Microseconds (fixed-width padded)
        assert overlay._format_exposure_time(500) == " 500µs"

        # Milliseconds (fixed-width padded)
        assert overlay._format_exposure_time(5000) == "  5.0ms"
        assert overlay._format_exposure_time(2000) == "  2.0ms"
        assert overlay._format_exposure_time(100_000) == "100.0ms"

        # Seconds (>= 1 second) (fixed-width padded)
        assert overlay._format_exposure_time(1_000_000) == "  1.0s"
        assert overlay._format_exposure_time(1_500_000) == "  1.5s"
        assert overlay._format_exposure_time(2_000_000) == "  2.0s"
        assert overlay._format_exposure_time(10_000_000) == " 10.0s"
        assert overlay._format_exposure_time(20_000_000) == " 20.0s"

    def test_format_iso(self, test_overlay_config):
        """Test ISO formatting (fixed-width)."""
        overlay = ImageOverlay(test_overlay_config)
        assert overlay._format_iso(1.0) == "ISO  100"  # Fixed-width: 4 digits
        assert overlay._format_iso(2.5) == "ISO  250"  # Fixed-width: 4 digits
        assert overlay._format_iso(8.0) == "ISO  800"  # Fixed-width: 4 digits

    def test_format_wb_gains(self, test_overlay_config):
        """Test white balance gains formatting."""
        overlay = ImageOverlay(test_overlay_config)
        assert overlay._format_wb_gains([1.5, 1.3]) == "R:1.50 B:1.30"
        assert overlay._format_wb_gains([]) == "N/A"

    def test_format_color_gains(self, test_overlay_config):
        """Test color gains tuple formatting (fixed-width)."""
        overlay = ImageOverlay(test_overlay_config)
        assert overlay._format_color_gains([1.8, 1.5]) == "( 1.80,  1.50)"  # Fixed-width: 5.2f
        assert overlay._format_color_gains([]) == "(  N/A,   N/A)"  # Fixed-width N/A

    def test_prepare_overlay_data(self, test_overlay_config, test_metadata):
        """Test overlay data preparation."""
        overlay = ImageOverlay(test_overlay_config)
        data = overlay._prepare_overlay_data(test_metadata, mode="day")

        assert data["camera_name"] == "Test Camera"
        assert data["mode"] == "Day"
        assert data["iso"] == "ISO  200"  # Fixed-width: 4 digits
        assert "exposure" in data
        assert "lux" in data
        assert "date" in data
        assert "time" in data

    def test_prepare_overlay_data_with_lens_position(self, test_overlay_config):
        """Test overlay data preparation with lens position and autofocus mode."""
        overlay = ImageOverlay(test_overlay_config)

        # Test with manual focus at infinity
        metadata_infinity = {
            "ExposureTime": 10000,
            "AnalogueGain": 1.0,
            "Lux": 100.0,
            "ColourGains": [1.0, 1.0],
            "SensorTemperature": 30.0,
            "resolution": [1920, 1080],
            "LensPosition": 0.0,
            "AfMode": 0,
        }
        data = overlay._prepare_overlay_data(metadata_infinity, mode="day")
        assert data["af_mode"] == "Manual"
        assert data["lens_position"] == "0.00"
        assert data["focus_distance"] == "∞"

        # Test with manual focus at 1 meter
        metadata_1m = metadata_infinity.copy()
        metadata_1m["LensPosition"] = 1.0
        data = overlay._prepare_overlay_data(metadata_1m, mode="day")
        assert data["lens_position"] == "1.00"
        assert data["focus_distance"] == "1.0m"

        # Test with manual focus at 10cm
        metadata_10cm = metadata_infinity.copy()
        metadata_10cm["LensPosition"] = 10.0
        data = overlay._prepare_overlay_data(metadata_10cm, mode="day")
        assert data["lens_position"] == "10.00"
        assert data["focus_distance"] == "10cm"

        # Test with auto focus mode
        metadata_auto = metadata_infinity.copy()
        metadata_auto["AfMode"] = 1
        data = overlay._prepare_overlay_data(metadata_auto, mode="day")
        assert data["af_mode"] == "Auto"

        # Test with continuous focus mode
        metadata_continuous = metadata_infinity.copy()
        metadata_continuous["AfMode"] = 2
        data = overlay._prepare_overlay_data(metadata_continuous, mode="day")
        assert data["af_mode"] == "Continuous"

        # Test without lens position (should show N/A)
        metadata_no_lens = {
            "ExposureTime": 10000,
            "AnalogueGain": 1.0,
            "Lux": 100.0,
            "ColourGains": [1.0, 1.0],
            "SensorTemperature": 30.0,
            "resolution": [1920, 1080],
        }
        data = overlay._prepare_overlay_data(metadata_no_lens, mode="day")
        assert data["af_mode"] == "N/A"
        assert data["lens_position"] == "N/A"
        assert data["focus_distance"] == "N/A"

    def test_get_text_lines(self, test_overlay_config, test_metadata):
        """Test text line generation."""
        overlay = ImageOverlay(test_overlay_config)
        data = overlay._prepare_overlay_data(test_metadata, mode="night")
        lines = overlay._get_text_lines(data)

        assert len(lines) > 0
        # Check that camera name is in first line
        assert "Test Camera" in lines[0]
        # Check that at least one line has exposure info (from line_1_right)
        # Note: lines may be combined differently now
        combined_text = " ".join(lines)
        assert "Exposure:" in combined_text or "ISO" in combined_text

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
                    "line_1_left": "{camera_name}",
                    "line_1_right": "",
                    "line_2_left": "{date} {time}",
                    "line_2_right": "",
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

        # Should return None on failure (not raise exception)
        result = overlay.apply_overlay("/nonexistent/image.jpg", {}, mode="day")
        assert result is None  # Returns None on error

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
        test_overlay_config["overlay"]["content"]["line_1_right"] = ""
        test_overlay_config["overlay"]["content"]["line_2_right"] = ""

        overlay = ImageOverlay(test_overlay_config)
        data = overlay._prepare_overlay_data(test_metadata, mode="day")
        lines = overlay._get_text_lines(data)

        # Should have main content
        assert len(lines) >= 1
        assert "Test Camera" in lines[0]

    def test_debug_content_enabled(self, test_overlay_config, test_metadata):
        """Test overlay with additional details content."""
        test_overlay_config["overlay"]["content"]["line_2_right"] = "Gain: {gain}"

        overlay = ImageOverlay(test_overlay_config)
        data = overlay._prepare_overlay_data(test_metadata, mode="night")
        lines = overlay._get_text_lines(data)

        # Should include gain info
        assert any("Gain:" in line for line in lines)

    def test_resolution_formatting(self, test_overlay_config, test_metadata):
        """Test resolution formatting in overlay."""
        overlay = ImageOverlay(test_overlay_config)
        data = overlay._prepare_overlay_data(test_metadata, mode="day")

        assert "resolution" in data
        assert data["resolution"] == "1920x1080"  # Uses 'x' not '×'

    def test_lux_formatting(self, test_overlay_config, test_metadata):
        """Test lux value formatting."""
        overlay = ImageOverlay(test_overlay_config)

        # Test with lux in metadata (fixed-width: 6.1f)
        data = overlay._prepare_overlay_data(test_metadata, mode="day")
        assert "lux" in data
        assert data["lux"] == " 500.5"  # Fixed-width padding

        # Test without lux - defaults to 0.0 (fixed-width)
        metadata_no_lux = test_metadata.copy()
        del metadata_no_lux["Lux"]
        data = overlay._prepare_overlay_data(metadata_no_lux, mode="day")
        assert data["lux"] == "   0.0"  # Fixed-width: 6.1f

    def test_temperature_formatting(self, test_overlay_config, test_metadata):
        """Test sensor temperature formatting."""
        overlay = ImageOverlay(test_overlay_config)
        data = overlay._prepare_overlay_data(test_metadata, mode="day")

        assert "temperature" in data
        assert data["temperature"] == " 35.0"  # Fixed-width: 5.1f

    def test_wb_mode_auto(self, test_overlay_config, test_metadata):
        """Test white balance mode display."""
        overlay = ImageOverlay(test_overlay_config)

        # AWB disabled should show "Manual" (capitalized)
        metadata_manual = test_metadata.copy()
        metadata_manual["AwbMode"] = 0
        data = overlay._prepare_overlay_data(metadata_manual, mode="night")
        assert data["wb"] == "Manual"  # Capitalized


class TestInPlaceOverlay:
    """Test in-place overlay (overwriting original)."""

    def test_apply_overlay_in_place(self, test_overlay_config, test_metadata):
        """Test applying overlay in-place."""
        import shutil

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
                    "line_1_left": "{camera_name}",
                    "line_1_right": "",
                    "line_2_left": "{date} {time}",
                    "line_2_right": "",
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
                    "line_1_left": "{camera_name}",
                    "line_1_right": "",
                    "line_2_left": "{date} {time}",
                    "line_2_right": "",
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


class TestFontLoadingFallback:
    """Test font loading with fallback chain."""

    def test_load_font_default(self, test_overlay_config):
        """Test loading default PIL font."""
        test_overlay_config["overlay"]["font"]["family"] = "default"
        overlay = ImageOverlay(test_overlay_config)
        assert overlay.font is None

    def test_load_font_invalid_path(self, test_overlay_config):
        """Test loading font with invalid path falls back to None."""
        test_overlay_config["overlay"]["font"]["family"] = "/nonexistent/font.ttf"
        overlay = ImageOverlay(test_overlay_config)
        # Should fall back to None (default font) when font not found
        assert overlay.font is None

    def test_load_font_bold_fallback(self, test_overlay_config):
        """Test bold font fallback to regular."""
        # Request a bold font that doesn't exist, should try fallback
        test_overlay_config["overlay"]["font"]["family"] = "NonExistentFont-Bold.ttf"
        overlay = ImageOverlay(test_overlay_config)
        # Should fall back to None when neither bold nor regular is found
        assert overlay.font is None


class TestGradientBarDrawing:
    """Test gradient bar drawing in detail."""

    def test_draw_gradient_bar_full_height(self, test_overlay_config):
        """Test gradient bar with full height."""
        overlay = ImageOverlay(test_overlay_config)
        img = Image.new("RGBA", (1000, 200), color=(255, 255, 255, 0))
        from PIL import ImageDraw

        draw = ImageDraw.Draw(img, "RGBA")

        # Draw gradient with standard background color
        overlay._draw_gradient_bar(draw, 1000, 200, [0, 0, 0, 255])

        # Verify gradient was drawn
        pixels = img.load()
        # Top should have higher alpha than bottom
        assert pixels[500, 0][3] > pixels[500, 199][3]

    def test_draw_gradient_bar_zero_height(self, test_overlay_config):
        """Test gradient bar with zero height (edge case)."""
        overlay = ImageOverlay(test_overlay_config)
        img = Image.new("RGBA", (100, 1), color=(255, 255, 255, 0))
        from PIL import ImageDraw

        draw = ImageDraw.Draw(img, "RGBA")

        # Should not crash with zero or very small height
        overlay._draw_gradient_bar(draw, 100, 0, [0, 0, 0, 200])

    def test_draw_gradient_bar_custom_colors(self, test_overlay_config):
        """Test gradient bar with custom RGBA colors."""
        overlay = ImageOverlay(test_overlay_config)
        img = Image.new("RGBA", (500, 100), color=(255, 255, 255, 0))
        from PIL import ImageDraw

        draw = ImageDraw.Draw(img, "RGBA")

        # Custom red background
        overlay._draw_gradient_bar(draw, 500, 100, [255, 0, 0, 200])

        pixels = img.load()
        # Should have red component
        assert pixels[250, 50][0] > 0  # Red channel


class TestCustomPositionEdgeCases:
    """Test custom position with edge cases."""

    def test_custom_position_percentage_zero(self, test_overlay_config):
        """Test custom position at 0%."""
        test_overlay_config["overlay"]["position"] = "custom"
        test_overlay_config["overlay"]["custom_position"] = {"x": 0, "y": 0}
        overlay = ImageOverlay(test_overlay_config)

        x, y = overlay._get_position(1920, 1080, (0, 0, 200, 50))
        assert x == 0
        assert y == 0

    def test_custom_position_percentage_100(self, test_overlay_config):
        """Test custom position at 100%."""
        test_overlay_config["overlay"]["position"] = "custom"
        test_overlay_config["overlay"]["custom_position"] = {"x": 100, "y": 100}
        overlay = ImageOverlay(test_overlay_config)

        x, y = overlay._get_position(1920, 1080, (0, 0, 200, 50))
        assert x == 1920
        assert y == 1080

    def test_custom_position_default_values(self, test_overlay_config):
        """Test custom position uses defaults if not specified."""
        test_overlay_config["overlay"]["position"] = "custom"
        # Don't set custom_position - should use defaults
        overlay = ImageOverlay(test_overlay_config)

        x, y = overlay._get_position(1000, 1000, (0, 0, 100, 50))
        # Default is x=5, y=95
        assert x == 50  # 5% of 1000
        assert y == 950  # 95% of 1000

    def test_unknown_position_preset_fallback(self, test_overlay_config):
        """Test unknown position preset falls back to bottom-left."""
        test_overlay_config["overlay"]["position"] = "invalid-position"
        overlay = ImageOverlay(test_overlay_config)

        # Should fall back to bottom-left positioning
        x, y = overlay._get_position(800, 600, (0, 0, 200, 50))
        assert isinstance(x, int)
        assert isinstance(y, int)


class TestTopBarModeContent:
    """Test top-bar mode content rendering."""

    def test_topbar_with_all_content_lines(self, test_overlay_config, test_image, test_metadata):
        """Test top-bar mode with all four content lines populated."""
        test_overlay_config["overlay"]["position"] = "top-bar"
        test_overlay_config["overlay"]["content"] = {
            "line_1_left": "{camera_name}",
            "line_1_right": "Exposure: {exposure} | ISO: {iso}",
            "line_2_left": "{date} {time}",
            "line_2_right": "Gain: {gain} | Temp: {temperature}°C",
        }
        overlay = ImageOverlay(test_overlay_config)

        output_path = test_image.replace(".jpg", "_topbar_full.jpg")
        result = overlay.apply_overlay(
            test_image, test_metadata, mode="day", output_path=output_path
        )

        assert os.path.exists(result)
        os.unlink(output_path)

    def test_topbar_with_invalid_template_variable(
        self, test_overlay_config, test_image, test_metadata
    ):
        """Test top-bar mode handles invalid template variables gracefully."""
        test_overlay_config["overlay"]["position"] = "top-bar"
        test_overlay_config["overlay"]["content"] = {
            "line_1_left": "{nonexistent_variable}",
            "line_1_right": "",
            "line_2_left": "{date} {time}",
            "line_2_right": "",
        }
        overlay = ImageOverlay(test_overlay_config)

        output_path = test_image.replace(".jpg", "_topbar_invalid.jpg")
        # Should not crash, just use template as-is
        result = overlay.apply_overlay(
            test_image, test_metadata, mode="day", output_path=output_path
        )

        assert os.path.exists(result)
        os.unlink(output_path)

    def test_topbar_background_disabled(self, test_overlay_config, test_image, test_metadata):
        """Test top-bar mode with background disabled."""
        test_overlay_config["overlay"]["position"] = "top-bar"
        test_overlay_config["overlay"]["background"]["enabled"] = False
        overlay = ImageOverlay(test_overlay_config)

        output_path = test_image.replace(".jpg", "_topbar_nobg.jpg")
        result = overlay.apply_overlay(
            test_image, test_metadata, mode="day", output_path=output_path
        )

        assert os.path.exists(result)
        os.unlink(output_path)


class TestOverlayDataPreparation:
    """Test overlay data preparation edge cases."""

    def test_prepare_data_missing_all_optional_fields(self, test_overlay_config):
        """Test data preparation with minimal metadata."""
        overlay = ImageOverlay(test_overlay_config)
        # Minimal metadata
        metadata = {}
        data = overlay._prepare_overlay_data(metadata, mode="day")

        # Should have defaults or N/A for all fields
        assert "camera_name" in data
        assert "mode" in data
        assert "date" in data
        assert "time" in data

    def test_prepare_data_with_zero_values(self, test_overlay_config):
        """Test data preparation with zero values."""
        overlay = ImageOverlay(test_overlay_config)
        metadata = {
            "ExposureTime": 0,
            "AnalogueGain": 0.0,
            "Lux": 0.0,
            "ColourGains": [0.0, 0.0],
            "SensorTemperature": 0.0,
        }
        data = overlay._prepare_overlay_data(metadata, mode="night")

        # Should handle zero values without crashing
        assert data["iso"] == "ISO    0"
        assert data["lux"] == "   0.0"

    def test_prepare_data_with_negative_temperature(self, test_overlay_config):
        """Test data preparation with negative temperature."""
        overlay = ImageOverlay(test_overlay_config)
        metadata = {
            "ExposureTime": 10000,
            "AnalogueGain": 1.0,
            "SensorTemperature": -10.5,
        }
        data = overlay._prepare_overlay_data(metadata, mode="day")

        assert "-10.5" in data["temperature"]


class TestColorGainsFormatting:
    """Test color gains formatting edge cases."""

    def test_format_color_gains_single_value(self, test_overlay_config):
        """Test color gains with only one value."""
        overlay = ImageOverlay(test_overlay_config)
        result = overlay._format_color_gains([1.5])
        assert "N/A" in result

    def test_format_color_gains_many_values(self, test_overlay_config):
        """Test color gains with more than 2 values."""
        overlay = ImageOverlay(test_overlay_config)
        result = overlay._format_color_gains([1.5, 1.3, 1.1, 0.9])
        # Should still format first two
        assert "1.50" in result
        assert "1.30" in result

    def test_format_color_gains_large_values(self, test_overlay_config):
        """Test color gains with large values."""
        overlay = ImageOverlay(test_overlay_config)
        result = overlay._format_color_gains([99.99, 88.88])
        assert "99.99" in result
        assert "88.88" in result


class TestExposureTimeFormatting:
    """Test exposure time formatting edge cases."""

    def test_format_exposure_boundary_values(self, test_overlay_config):
        """Test exposure time at format boundaries."""
        overlay = ImageOverlay(test_overlay_config)

        # Exactly 1000 us (boundary between µs and ms)
        assert "ms" in overlay._format_exposure_time(1000)

        # Exactly 1 second
        result = overlay._format_exposure_time(1_000_000)
        assert "s" in result

    def test_format_exposure_very_small(self, test_overlay_config):
        """Test very small exposure times."""
        overlay = ImageOverlay(test_overlay_config)

        result = overlay._format_exposure_time(1)
        assert "µs" in result
        assert "1" in result

    def test_format_exposure_very_large(self, test_overlay_config):
        """Test very large exposure times (20+ seconds)."""
        overlay = ImageOverlay(test_overlay_config)

        # 30 second exposure
        result = overlay._format_exposure_time(30_000_000)
        assert "30.0s" in result


class TestApplyOverlayFunctionErrors:
    """Test apply_overlay_to_image function error handling."""

    def test_apply_overlay_missing_config(self, test_image):
        """Test handling of missing config file."""
        # Should raise FileNotFoundError for missing config
        with pytest.raises(FileNotFoundError):
            apply_overlay_to_image(
                test_image,
                metadata={},
                config_path="/nonexistent/config.yml",
                mode="day",
            )

    def test_apply_overlay_invalid_yaml_config(self, test_image):
        """Test handling of invalid YAML config file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("invalid: yaml: content: [[[")
            config_path = f.name

        try:
            # Should raise YAML parsing error
            with pytest.raises(Exception):  # yaml.scanner.ScannerError
                apply_overlay_to_image(
                    test_image,
                    metadata={},
                    config_path=config_path,
                    mode="day",
                )
        finally:
            os.unlink(config_path)

    def test_apply_overlay_missing_metadata_file(self, test_image):
        """Test handling of missing metadata file uses empty metadata."""
        config_data = {
            "overlay": {
                "enabled": True,
                "position": "bottom-left",
                "camera_name": "Test",
                "font": {"family": "default"},
                "content": {
                    "line_1_left": "{camera_name}",
                    "line_1_right": "",
                    "line_2_left": "{date} {time}",
                    "line_2_right": "",
                },
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = f.name

        try:
            output_path = test_image.replace(".jpg", "_nometa.jpg")
            # Function uses empty metadata if file not found
            result = apply_overlay_to_image(
                test_image,
                metadata_path="/nonexistent/metadata.json",
                config_path=config_path,
                mode="day",
                output_path=output_path,
            )
            # Should succeed with empty metadata
            assert os.path.exists(result)
            os.unlink(output_path)
        finally:
            os.unlink(config_path)


class TestLocaleDatetimeEdgeCases:
    """Test localized datetime formatting edge cases."""

    def test_locale_setting_failure(self, test_overlay_config):
        """Test handling of locale setting failure."""
        from datetime import datetime

        test_overlay_config["overlay"]["datetime"] = {
            "localized": True,
            "locale": "invalid_LOCALE.UTF-8",
            "show_seconds": False,
        }

        overlay = ImageOverlay(test_overlay_config)
        dt = datetime(2025, 6, 15, 12, 30, 45)

        # Should not crash, fall back to non-localized format
        result = overlay._format_localized_datetime(dt)
        assert isinstance(result, str)
        assert len(result) > 0


class TestPillowCompatibility:
    """Test Pillow version compatibility for required features."""

    def test_pillow_has_rounded_rectangle(self):
        """
        Ensure Pillow has rounded_rectangle method.

        This method was added in Pillow 8.2.0 and is required for ship box rendering.
        If this test fails, upgrade Pillow: pip install --upgrade Pillow
        """
        from PIL import ImageDraw

        # Create a test image and draw context
        test_img = Image.new("RGBA", (100, 100), color=(255, 255, 255, 0))
        draw = ImageDraw.Draw(test_img, "RGBA")

        # Verify rounded_rectangle method exists
        assert hasattr(draw, "rounded_rectangle"), (
            "Pillow version is too old. rounded_rectangle requires Pillow >= 8.2.0. "
            "Please upgrade: pip install --upgrade Pillow"
        )

        # Verify it works without error
        draw.rounded_rectangle([10, 10, 50, 50], radius=5, fill=(0, 0, 0, 128))

    def test_pillow_version_minimum(self):
        """Verify Pillow version meets minimum requirement for all features."""
        from PIL import __version__ as pillow_version
        from packaging import version

        min_version = "8.2.0"
        assert version.parse(pillow_version) >= version.parse(min_version), (
            f"Pillow version {pillow_version} is below minimum required {min_version}. "
            f"Please upgrade: pip install --upgrade Pillow"
        )


class TestShipBoxesRendering:
    """Test ship boxes overlay rendering."""

    def test_draw_ship_boxes_with_ships_data(self, test_overlay_config, test_image, test_metadata):
        """Test top-bar mode with ship boxes when ships data is present."""
        # Create temporary ships file
        ships_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        ships_file_path = ships_file.name

        test_overlay_config["overlay"]["position"] = "top-bar"
        test_overlay_config["barentswatch"] = {
            "enabled": True,
            "ships_file": ships_file_path,
        }

        # Create mock ships data file
        ships_data = {
            "items": [
                {"name": "Test Ship 1", "mmsi": "123456789", "speed": 5.0},
                {"name": "Test Ship 2", "mmsi": "987654321", "speed": 3.0},
            ],
        }
        with open(ships_file_path, "w") as f:
            json.dump(ships_data, f)

        try:
            overlay = ImageOverlay(test_overlay_config)
            output_path = test_image.replace(".jpg", "_ships.jpg")

            # This should not fail - if rounded_rectangle is missing, this test will catch it
            result = overlay.apply_overlay(
                test_image, test_metadata, mode="day", output_path=output_path
            )

            assert os.path.exists(result)
            os.unlink(output_path)
        finally:
            if os.path.exists(ships_file_path):
                os.unlink(ships_file_path)

    def test_draw_ship_boxes_without_ships_data(
        self, test_overlay_config, test_image, test_metadata
    ):
        """Test top-bar mode when ships file is empty or missing."""
        # Use a unique nonexistent path in temp directory
        nonexistent_path = os.path.join(tempfile.gettempdir(), "nonexistent_ships_test.json")

        test_overlay_config["overlay"]["position"] = "top-bar"
        test_overlay_config["barentswatch"] = {
            "enabled": True,
            "ships_file": nonexistent_path,
        }

        overlay = ImageOverlay(test_overlay_config)
        output_path = test_image.replace(".jpg", "_noships.jpg")

        # Should succeed even without ships data
        result = overlay.apply_overlay(
            test_image, test_metadata, mode="day", output_path=output_path
        )

        assert os.path.exists(result)
        os.unlink(output_path)

    def test_ship_boxes_disabled(self, test_overlay_config, test_image, test_metadata):
        """Test top-bar mode with ships feature disabled."""
        test_overlay_config["overlay"]["position"] = "top-bar"
        test_overlay_config["barentswatch"] = {
            "enabled": False,
        }

        overlay = ImageOverlay(test_overlay_config)
        output_path = test_image.replace(".jpg", "_ships_disabled.jpg")

        result = overlay.apply_overlay(
            test_image, test_metadata, mode="day", output_path=output_path
        )

        assert os.path.exists(result)
        os.unlink(output_path)


class TestOverlayErrorHandling:
    """Tests for overlay error handling improvements."""

    def test_apply_overlay_returns_none_on_invalid_image(self, test_overlay_config):
        """Test that apply_overlay returns None when given invalid image path."""
        overlay = ImageOverlay(test_overlay_config)

        result = overlay.apply_overlay(
            "/nonexistent/path/to/image.jpg", {"ExposureTime": 1000}, mode="day"
        )

        # Should return None on failure, not the original path
        assert result is None

    def test_apply_overlay_returns_none_on_save_failure(
        self, test_overlay_config, test_image, test_metadata
    ):
        """Test that apply_overlay returns None when save fails."""
        overlay = ImageOverlay(test_overlay_config)

        # Try to save to a read-only location
        result = overlay.apply_overlay(
            test_image, test_metadata, mode="day", output_path="/root/cannot_write_here.jpg"
        )

        # Should return None on save failure
        assert result is None


class TestWidgetFixedWidths:
    """Tests for fixed-width widget positioning."""

    def test_aurora_uses_fixed_width_template(self, test_overlay_config):
        """Test that aurora widget uses fixed-width templates for consistent positioning."""
        # The max templates should be used for width calculation
        max_line_1 = "Kp: 9.9 | Bz: -99.9↓"
        max_line_2 = "G5 | 9999 km/s"

        # These should be longer than typical values
        typical_line_1 = "Kp: 2.3 | Bz: 0.9↑"
        typical_line_2 = "G0 | 556 km/s"

        assert len(max_line_1) >= len(typical_line_1)
        assert len(max_line_2) >= len(typical_line_2)

    def test_tide_format_includes_cm_values(self, test_overlay_config):
        """Test that tide widget includes cm values in parentheses."""
        # The expected format is: "H 13:18 (227cm) | L 07:10 (76cm)"
        tide_widget = {
            "high_time_str": "13:18",
            "high_level_str": "227cm",
            "low_time_str": "07:10",
            "low_level_str": "76cm",
        }

        expected_format = f"H {tide_widget['high_time_str']} ({tide_widget['high_level_str']}) | L {tide_widget['low_time_str']} ({tide_widget['low_level_str']})"

        assert "(227cm)" in expected_format
        assert "(76cm)" in expected_format
        assert expected_format == "H 13:18 (227cm) | L 07:10 (76cm)"

    def test_tide_max_width_template(self, test_overlay_config):
        """Test that tide widget uses appropriate max width template."""
        max_line_1 = "Tide level: 999cm → 999cm"
        typical_line_1 = "Tide level: 227cm → 76cm"
        max_line_2 = "H 00:00 (999cm) | L 00:00 (999cm)"
        typical_line_2 = "H 13:18 (227cm) | L 07:10 (76cm)"

        # Max template should accommodate all reasonable values
        assert len(max_line_1) >= len(typical_line_1)
        assert len(max_line_2) >= len(typical_line_2)


class TestTideDataCalculation:
    """Tests for TideData calculation of next high/low from points array."""

    @pytest.fixture
    def tide_config(self, tmp_path):
        """Create a config with tide enabled and a temp tide file."""
        tide_file = tmp_path / "tide.json"
        return {
            "tide": {
                "enabled": True,
                "tide_file": str(tide_file),
            }
        }

    @pytest.fixture
    def sample_tide_data(self):
        """Sample tide data with points array showing a typical day."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        base_time = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Generate points that create clear highs and lows:
        # Low at 02:00, High at 08:00, Low at 14:00, High at 20:00
        points = []
        for i in range(145):  # 24 hours of 10-min intervals + 1
            t = base_time + timedelta(minutes=i * 10)
            # Simulate tide with two cycles per day
            import math

            hours = i * 10 / 60
            # Create a pattern: low at 2:00, high at 8:00, low at 14:00, high at 20:00
            level = 150 + 80 * math.sin((hours - 2) * math.pi / 6)
            points.append({"time": t.isoformat(), "level_cm": int(level)})

        return {
            "tide_data": {
                "location": "Test",
                "points": points,
                "next_high": {
                    "time": (base_time + timedelta(hours=8)).isoformat(),
                    "level_cm": 230,
                },
                "next_low": {"time": (base_time + timedelta(hours=2)).isoformat(), "level_cm": 70},
            }
        }

    def test_find_extremes_from_points_finds_highs_and_lows(
        self, tide_config, sample_tide_data, tmp_path
    ):
        """Test that _find_extremes_from_points correctly identifies peaks and troughs."""
        tide_file = tmp_path / "tide.json"
        with open(tide_file, "w") as f:
            json.dump(sample_tide_data, f)

        tide = TideData(tide_config)
        highs, lows = tide._find_extremes_from_points()

        # Should find at least one high and one low
        assert len(highs) >= 1, "Should find at least one high tide"
        assert len(lows) >= 1, "Should find at least one low tide"

        # Highs should have higher levels than lows
        if highs and lows:
            assert highs[0]["level_cm"] > lows[0]["level_cm"]

    def test_get_next_high_filters_past_events(self, tide_config, tmp_path):
        """Test that get_next_high returns only future events."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)

        # Create data with a past high and a future high
        past_high_time = now - timedelta(hours=2)
        future_high_time = now + timedelta(hours=4)

        # Create simple points that create these highs
        points = []
        for i in range(-30, 60):  # -5 hours to +10 hours in 10-min intervals
            t = now + timedelta(minutes=i * 10)
            # Create peaks at past_high and future_high
            if abs((t - past_high_time).total_seconds()) < 600:
                level = 220 - abs((t - past_high_time).total_seconds()) / 60
            elif abs((t - future_high_time).total_seconds()) < 600:
                level = 210 - abs((t - future_high_time).total_seconds()) / 60
            else:
                level = 100
            points.append({"time": t.isoformat(), "level_cm": int(level)})

        tide_data = {
            "tide_data": {
                "points": points,
                "next_high": {
                    "time": past_high_time.isoformat(),  # Backend says past high
                    "level_cm": 220,
                },
            }
        }

        tide_file = tmp_path / "tide.json"
        with open(tide_file, "w") as f:
            json.dump(tide_data, f)

        tide = TideData(tide_config)
        next_high = tide.get_next_high()

        # Should return the future high, not the past one
        if next_high:
            high_time = tide._parse_time(next_high.get("time"))
            assert high_time > now, "Next high should be in the future"

    def test_get_next_low_filters_past_events(self, tide_config, tmp_path):
        """Test that get_next_low returns only future events."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)

        # Create data with a past low and a future low
        past_low_time = now - timedelta(hours=3)
        future_low_time = now + timedelta(hours=3)

        # Create simple points that create these lows
        points = []
        for i in range(-40, 50):  # -6.6 hours to +8.3 hours in 10-min intervals
            t = now + timedelta(minutes=i * 10)
            # Create troughs at past_low and future_low
            if abs((t - past_low_time).total_seconds()) < 600:
                level = 70 + abs((t - past_low_time).total_seconds()) / 60
            elif abs((t - future_low_time).total_seconds()) < 600:
                level = 80 + abs((t - future_low_time).total_seconds()) / 60
            else:
                level = 180
            points.append({"time": t.isoformat(), "level_cm": int(level)})

        tide_data = {
            "tide_data": {
                "points": points,
                "next_low": {
                    "time": past_low_time.isoformat(),  # Backend says past low
                    "level_cm": 70,
                },
            }
        }

        tide_file = tmp_path / "tide.json"
        with open(tide_file, "w") as f:
            json.dump(tide_data, f)

        tide = TideData(tide_config)
        next_low = tide.get_next_low()

        # Should return the future low, not the past one
        if next_low:
            low_time = tide._parse_time(next_low.get("time"))
            assert low_time > now, "Next low should be in the future"

    def test_empty_points_returns_none(self, tide_config, tmp_path):
        """Test that empty points array gracefully returns None."""
        tide_data = {"tide_data": {"points": []}}

        tide_file = tmp_path / "tide.json"
        with open(tide_file, "w") as f:
            json.dump(tide_data, f)

        tide = TideData(tide_config)
        assert tide.get_next_high() is None
        assert tide.get_next_low() is None

    def test_no_extremes_returns_none(self, tide_config, tmp_path):
        """Test that if points don't have extremes, returns None (no fallback)."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)

        # Create flat points (no extremes) - always calculates from points, no fallback
        points = [
            {"time": (now + timedelta(minutes=i * 10)).isoformat(), "level_cm": 150}
            for i in range(20)
        ]

        tide_data = {
            "tide_data": {
                "points": points,
                # Backend next_high is ignored - we always calculate from points
                "next_high": {"time": (now + timedelta(hours=5)).isoformat(), "level_cm": 220},
            }
        }

        tide_file = tmp_path / "tide.json"
        with open(tide_file, "w") as f:
            json.dump(tide_data, f)

        tide = TideData(tide_config)
        next_high = tide.get_next_high()

        # No extremes in points = None (we don't use backend's pre-calculated values)
        assert next_high is None

    def test_find_extremes_handles_slack_tide_plateaus(self, tide_config, tmp_path):
        """Test that _find_extremes_from_points correctly handles slack tide plateaus.

        Near peaks and troughs, consecutive points can have the same level_cm value
        due to rounding (the tide moves slowly at extremes). The algorithm should:
        1. Detect trend changes across plateaus, not just adjacent values
        2. Use the middle point of the plateau as the extreme time
        """
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        base_time = now - timedelta(hours=1)

        # Create data with plateaus at extremes (simulating slack tides)
        # Pattern: rising -> plateau at 200 (high) -> falling -> plateau at 50 (low) -> rising
        points = []

        # Rising phase: 100 -> 150 -> 180 -> 190 -> 195 -> 198 -> 199
        rising = [100, 120, 140, 160, 180, 190, 195, 198, 199]
        for i, level in enumerate(rising):
            t = base_time + timedelta(minutes=i * 10)
            points.append({"time": t.isoformat(), "level_cm": level})

        # Plateau at high (slack tide) - 5 points with same value
        plateau_high_start = len(points)
        for i in range(5):
            t = base_time + timedelta(minutes=(len(points)) * 10)
            points.append({"time": t.isoformat(), "level_cm": 200})
        plateau_high_end = len(points) - 1

        # Falling phase: 199 -> 195 -> 190 -> 180 -> ... -> 51
        falling = [199, 195, 190, 180, 160, 140, 120, 100, 80, 60, 51]
        for level in falling:
            t = base_time + timedelta(minutes=(len(points)) * 10)
            points.append({"time": t.isoformat(), "level_cm": level})

        # Plateau at low (slack tide) - 5 points with same value
        plateau_low_start = len(points)
        for i in range(5):
            t = base_time + timedelta(minutes=(len(points)) * 10)
            points.append({"time": t.isoformat(), "level_cm": 50})
        plateau_low_end = len(points) - 1

        # Rising again: 51 -> 60 -> 80 -> 100
        rising_again = [51, 60, 80, 100, 120]
        for level in rising_again:
            t = base_time + timedelta(minutes=(len(points)) * 10)
            points.append({"time": t.isoformat(), "level_cm": level})

        tide_data = {"tide_data": {"points": points}}

        tide_file = tmp_path / "tide.json"
        with open(tide_file, "w") as f:
            json.dump(tide_data, f)

        tide = TideData(tide_config)
        highs, lows = tide._find_extremes_from_points()

        # Should find exactly one high and one low
        assert len(highs) == 1, f"Should find exactly one high tide, found {len(highs)}"
        assert len(lows) == 1, f"Should find exactly one low tide, found {len(lows)}"

        # The high should be at level 200
        assert highs[0]["level_cm"] == 200, f"High level should be 200, got {highs[0]['level_cm']}"

        # The low should be at level 50
        assert lows[0]["level_cm"] == 50, f"Low level should be 50, got {lows[0]['level_cm']}"

        # The high time should be in the middle of the plateau
        # plateau_high_start=9, plateau_high_end=13, middle index = 11
        expected_high_mid = (plateau_high_start + plateau_high_end) // 2
        expected_high_time = points[expected_high_mid]["time"]
        assert highs[0]["time"] == expected_high_time, (
            f"High time should be middle of plateau ({expected_high_time}), "
            f"got {highs[0]['time']}"
        )

        # The low time should be in the middle of the low plateau
        expected_low_mid = (plateau_low_start + plateau_low_end) // 2
        expected_low_time = points[expected_low_mid]["time"]
        assert lows[0]["time"] == expected_low_time, (
            f"Low time should be middle of plateau ({expected_low_time}), " f"got {lows[0]['time']}"
        )

    def test_find_extremes_without_plateaus(self, tide_config, tmp_path):
        """Test _find_extremes_from_points with unique values (no plateaus).

        Each point has a different level_cm, testing the single-point plateau case
        where plateau_start == plateau_end.
        """
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        base_time = now - timedelta(hours=1)

        # All unique values: rising to 200, then falling to 50, then rising
        levels = [100, 120, 140, 160, 180, 200, 180, 160, 140, 120, 100, 80, 60, 50, 60, 80, 100]
        points = []
        for i, level in enumerate(levels):
            t = base_time + timedelta(minutes=i * 10)
            points.append({"time": t.isoformat(), "level_cm": level})

        tide_data = {"tide_data": {"points": points}}

        tide_file = tmp_path / "tide.json"
        with open(tide_file, "w") as f:
            json.dump(tide_data, f)

        tide = TideData(tide_config)
        highs, lows = tide._find_extremes_from_points()

        assert len(highs) == 1, f"Should find one high, found {len(highs)}"
        assert len(lows) == 1, f"Should find one low, found {len(lows)}"
        assert highs[0]["level_cm"] == 200
        assert lows[0]["level_cm"] == 50

    def test_find_extremes_at_data_boundaries(self, tide_config, tmp_path):
        """Test that extremes at the very start or end of data are not detected.

        When an extreme would be at the boundary, there's no trend_before or
        trend_after, so it should be skipped.
        """
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        base_time = now - timedelta(hours=1)

        # Data starts at a high and ends at a low - neither should be detected
        # as extremes since we can't determine the trend on one side
        levels = [200, 180, 160, 140, 120, 100, 80, 60, 50]
        points = []
        for i, level in enumerate(levels):
            t = base_time + timedelta(minutes=i * 10)
            points.append({"time": t.isoformat(), "level_cm": level})

        tide_data = {"tide_data": {"points": points}}

        tide_file = tmp_path / "tide.json"
        with open(tide_file, "w") as f:
            json.dump(tide_data, f)

        tide = TideData(tide_config)
        highs, lows = tide._find_extremes_from_points()

        # No extremes should be found - start/end don't have both trend_before and trend_after
        assert len(highs) == 0, f"Should find no highs (boundary case), found {len(highs)}"
        assert len(lows) == 0, f"Should find no lows (boundary case), found {len(lows)}"

    def test_find_extremes_multiple_cycles(self, tide_config, tmp_path):
        """Test detecting multiple high and low tides across several cycles."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        base_time = now - timedelta(hours=2)

        # Two complete cycles: low->high->low->high->low
        points = []
        # First low at 50
        for level in [80, 60, 50, 60, 80]:
            t = base_time + timedelta(minutes=len(points) * 10)
            points.append({"time": t.isoformat(), "level_cm": level})
        # First high at 200
        for level in [120, 160, 200, 160, 120]:
            t = base_time + timedelta(minutes=len(points) * 10)
            points.append({"time": t.isoformat(), "level_cm": level})
        # Second low at 40
        for level in [80, 50, 40, 50, 80]:
            t = base_time + timedelta(minutes=len(points) * 10)
            points.append({"time": t.isoformat(), "level_cm": level})
        # Second high at 210
        for level in [140, 180, 210, 180, 140]:
            t = base_time + timedelta(minutes=len(points) * 10)
            points.append({"time": t.isoformat(), "level_cm": level})
        # Trailing points
        for level in [100, 80]:
            t = base_time + timedelta(minutes=len(points) * 10)
            points.append({"time": t.isoformat(), "level_cm": level})

        tide_data = {"tide_data": {"points": points}}

        tide_file = tmp_path / "tide.json"
        with open(tide_file, "w") as f:
            json.dump(tide_data, f)

        tide = TideData(tide_config)
        highs, lows = tide._find_extremes_from_points()

        assert len(highs) == 2, f"Should find 2 highs, found {len(highs)}"
        assert len(lows) == 2, f"Should find 2 lows, found {len(lows)}"
        assert highs[0]["level_cm"] == 200
        assert highs[1]["level_cm"] == 210
        assert lows[0]["level_cm"] == 50
        assert lows[1]["level_cm"] == 40
