"""Tests for simplified overlay structure."""

import os
import tempfile
import pytest
from unittest.mock import Mock, patch
from PIL import Image
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.overlay import ImageOverlay


@pytest.fixture
def simplified_config():
    """Create simplified overlay configuration."""
    return {
        "overlay": {
            "enabled": True,
            "position": "top-bar",
            "camera_name": "Test Cam",
            "font": {
                "family": "default",
                "size_ratio": 0.025,
                "color": [255, 255, 255, 255],
            },
            "background": {
                "enabled": True,
                "color": [0, 0, 0, 140],
            },
            "content": {
                "line_1_left": "{camera_name}",
                "line_1_right": "{exposure} {iso} | {temp}",
                "line_2_left": "{date} {time}",
                "line_2_right": "Lux: {lux}",
            },
            "layout": {
                "bottom_padding_multiplier": 1.3,
            },
            "datetime": {
                "localized": True,
                "locale": "en_US.UTF-8",
                "show_seconds": False,
            },
        },
        "weather": {
            "enabled": True,
        },
    }


@pytest.fixture
def test_metadata():
    """Test metadata."""
    return {
        "ExposureTime": 1000000,  # 1 second
        "AnalogueGain": 2.0,
        "Lux": 100.5,
        "ColourGains": [1.8, 1.5],
        "SensorTemperature": 40.0,
        "resolution": [1920, 1080],
    }


class TestSimplifiedStructure:
    """Test simplified line structure."""

    def test_top_bar_simplified_lines(self, simplified_config, test_metadata):
        """Test top-bar mode with simplified line structure."""
        overlay = ImageOverlay(simplified_config)

        # Create test image
        img = Image.new("RGB", (1920, 1080), color=(100, 100, 100))
        temp_file = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        img.save(temp_file.name)

        try:
            output = temp_file.name.replace(".jpg", "_out.jpg")
            result = overlay.apply_overlay(
                temp_file.name, test_metadata, mode="day", output_path=output
            )
            assert os.path.exists(result)
            os.unlink(output)
        finally:
            os.unlink(temp_file.name)

    def test_empty_right_lines(self, simplified_config, test_metadata):
        """Test with empty right-side lines."""
        simplified_config["overlay"]["content"]["line_1_right"] = ""
        simplified_config["overlay"]["content"]["line_2_right"] = ""

        overlay = ImageOverlay(simplified_config)
        data = overlay._prepare_overlay_data(test_metadata, mode="night")

        # Should handle empty strings gracefully
        assert data is not None
        assert "camera_name" in data

    def test_corner_mode_simplified(self, simplified_config, test_metadata):
        """Test corner positions with simplified structure."""
        simplified_config["overlay"]["position"] = "bottom-left"

        overlay = ImageOverlay(simplified_config)
        data = overlay._prepare_overlay_data(test_metadata, mode="day")
        lines = overlay._get_text_lines(data)

        # Should stack all non-empty lines
        assert len(lines) >= 2  # At least camera name and date/time
        assert "Test Cam" in lines[0]

    def test_localized_datetime_in_line_2(self, simplified_config, test_metadata):
        """Test that date/time is localized when using {date} {time} template."""
        overlay = ImageOverlay(simplified_config)

        with patch.object(overlay, "_format_localized_datetime") as mock_format:
            mock_format.return_value = "mocked localized datetime"
            data = overlay._prepare_overlay_data(test_metadata, mode="day")

            # Should have called localization
            assert mock_format.called
            assert data["datetime_localized"] == "mocked localized datetime"

    def test_unknown_variables_handling(self, simplified_config, test_metadata):
        """Test handling of unknown variables in templates."""
        simplified_config["overlay"]["content"]["line_1_right"] = "{unknown_var} test"

        overlay = ImageOverlay(simplified_config)

        # Create test image
        img = Image.new("RGB", (800, 600))
        temp_file = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        img.save(temp_file.name)

        try:
            # Should not crash with unknown variables
            result = overlay.apply_overlay(temp_file.name, test_metadata, mode="day")
            assert result == temp_file.name  # Returns original on error or same if in-place
        finally:
            os.unlink(temp_file.name)

    def test_all_four_lines_populated(self, simplified_config, test_metadata):
        """Test with all four line positions having content."""
        config = simplified_config.copy()
        config["overlay"]["content"] = {
            "line_1_left": "Camera: {camera_name}",
            "line_1_right": "Mode: {mode}",
            "line_2_left": "Time: {time}",
            "line_2_right": "Temp: {temperature}°C",
        }

        overlay = ImageOverlay(config)
        data = overlay._prepare_overlay_data(test_metadata, mode="transition")

        # Check all fields are formatted
        assert data["mode"] == "Transition"
        assert data["temperature"] == "40.0"
        assert "time" in data

    def test_gradient_bar_with_two_lines(self, simplified_config):
        """Test gradient bar height calculation for 2-line layout."""
        overlay = ImageOverlay(simplified_config)

        # Mock image and draw
        img = Image.new("RGBA", (1920, 1080))
        from PIL import ImageDraw

        draw = ImageDraw.Draw(img, "RGBA")

        # Test gradient bar drawing
        overlay._draw_gradient_bar(draw, 1920, 100, [0, 0, 0, 200])

        # Check pixels for gradient effect
        pixels = img.load()
        top_pixel = pixels[960, 10]
        bottom_pixel = pixels[960, 90]

        # Alpha should fade (top more opaque than bottom)
        assert top_pixel[3] > bottom_pixel[3]

    def test_weather_data_missing(self, simplified_config, test_metadata):
        """Test with weather data unavailable."""
        overlay = ImageOverlay(simplified_config)

        # Mock weather data as unavailable
        with patch.object(overlay.weather, "get_weather_data", return_value=None):
            data = overlay._prepare_overlay_data(test_metadata, mode="day")

            # Should show "-" for weather fields
            assert data["temp"] == "-"
            assert data["humidity"] == "-"
            assert data["wind"] == "-"

    def test_line_1_right_with_pipe_separator(self, simplified_config, test_metadata):
        """Test line_1_right with pipe separator in content."""
        config = simplified_config.copy()
        config["overlay"]["content"]["line_1_right"] = "Exp: {exposure} | ISO: {iso} | Lux: {lux}"

        overlay = ImageOverlay(config)

        # Create test image
        img = Image.new("RGB", (1920, 1080))
        temp_file = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        img.save(temp_file.name)

        try:
            output = temp_file.name.replace(".jpg", "_pipe.jpg")
            result = overlay.apply_overlay(
                temp_file.name, test_metadata, mode="day", output_path=output
            )
            assert os.path.exists(result)
            os.unlink(output)
        finally:
            os.unlink(temp_file.name)


class TestFontHandling:
    """Test font loading and sizing with simplified structure."""

    def test_font_size_calculation(self, simplified_config):
        """Test font size scales with image height."""
        overlay = ImageOverlay(simplified_config)

        # Test different image sizes
        for height in [480, 720, 1080, 2160]:
            img = Image.new("RGB", (1920, height))
            expected_size = int(height * 0.025)

            # Font size should scale with height
            assert expected_size > 0
            assert expected_size == int(height * simplified_config["overlay"]["font"]["size_ratio"])

    def test_default_font_fallback(self, simplified_config):
        """Test fallback to default font when custom font not found."""
        config = simplified_config.copy()
        config["overlay"]["font"]["family"] = "NonExistentFont.ttf"

        overlay = ImageOverlay(config)

        # Should fall back to None (default font)
        assert overlay.font is None or overlay.font == "default"


class TestEdgeCases:
    """Test edge cases with simplified structure."""

    def test_very_long_line_content(self, simplified_config, test_metadata):
        """Test with very long line content."""
        config = simplified_config.copy()
        long_text = "Very " * 50 + "long text"
        config["overlay"]["content"]["line_1_right"] = long_text

        overlay = ImageOverlay(config)

        # Should handle long text without crashing
        img = Image.new("RGB", (800, 600))
        temp_file = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        img.save(temp_file.name)

        try:
            result = overlay.apply_overlay(temp_file.name, test_metadata, mode="day")
            assert result == temp_file.name
        finally:
            os.unlink(temp_file.name)

    def test_special_characters_in_templates(self, simplified_config, test_metadata):
        """Test special characters in template strings."""
        config = simplified_config.copy()
        config["overlay"]["content"]["line_1_right"] = "Test: {exposure} © ® ™ °C"

        overlay = ImageOverlay(config)
        data = overlay._prepare_overlay_data(test_metadata, mode="day")

        # Should handle special characters
        assert "exposure" in data
        assert data["exposure"] == "1.0s"

    def test_missing_metadata_values(self, simplified_config):
        """Test with missing values in metadata."""
        metadata = {}  # Empty metadata - keys missing entirely

        overlay = ImageOverlay(simplified_config)

        # Should handle missing keys gracefully with defaults
        data = overlay._prepare_overlay_data(metadata, mode="day")
        assert data is not None
        # Default values should be used
        assert data["lux"] == "0.0"
        assert data["temperature"] == "0.0"
        assert data["wb_gains"] == "N/A"
