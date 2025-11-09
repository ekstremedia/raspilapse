"""Image overlay module for Raspilapse.

Adds configurable text overlays to captured images with camera settings,
timestamps, and debug information.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import json

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise ImportError(
        "Pillow is required for overlay functionality. Install with: pip3 install Pillow"
    )

try:
    from src.logging_config import get_logger
except ImportError:
    from logging_config import get_logger

try:
    from src.weather import WeatherData
except ImportError:
    from weather import WeatherData

logger = get_logger("overlay")


class ImageOverlay:
    """Handles adding text overlays to images."""

    def __init__(self, config: Dict):
        """
        Initialize overlay handler.

        Args:
            config: Full configuration dictionary
        """
        self.config = config
        self.overlay_config = config.get("overlay", {})
        self.enabled = self.overlay_config.get("enabled", False)

        if not self.enabled:
            logger.debug("Overlay disabled in configuration")
            return

        # Load font
        self.font = self._load_font()

        # Initialize weather data fetcher
        self.weather = WeatherData(config)

        logger.info("Overlay initialized")

    def _load_font(self) -> Optional[ImageFont.FreeTypeFont]:
        """
        Load font with fallback options.

        Returns:
            Font object or None for default font
        """
        font_config = self.overlay_config.get("font", {})
        font_family = font_config.get("family", "default")

        if font_family == "default":
            logger.debug("Using default PIL font")
            return None

        # Try to load the specified font
        font_paths = [
            font_family,  # Direct path
            f"/usr/share/fonts/truetype/dejavu/{font_family}",  # Debian/Ubuntu
            f"/usr/share/fonts/truetype/{font_family}",
            f"/usr/share/fonts/TTF/{font_family}",  # Arch
            f"/System/Library/Fonts/{font_family}",  # macOS
        ]

        # If bold requested but not found, try regular as fallback
        if "Bold" in font_family or "bold" in font_family:
            fallback_regular = font_family.replace("-Bold", "").replace("-bold", "")
            font_paths.extend(
                [
                    fallback_regular,
                    f"/usr/share/fonts/truetype/dejavu/{fallback_regular}",
                    f"/usr/share/fonts/truetype/{fallback_regular}",
                ]
            )

        for font_path in font_paths:
            try:
                # Try with a test size (will be resized later based on image)
                test_font = ImageFont.truetype(font_path, 20)
                logger.debug(f"Loaded font: {font_path}")
                return font_path  # Return path, will load with proper size later
            except (OSError, IOError):
                continue

        logger.warning(f"Could not load font '{font_family}', falling back to default font")
        return None

    def _format_exposure_time(self, exposure_us: int) -> str:
        """
        Format exposure time in human-readable form with fixed width.

        Args:
            exposure_us: Exposure time in microseconds

        Returns:
            Formatted string with consistent width (e.g., "1/500s  ", "  2.5s  ", " 15.0s  ")
        """
        if exposure_us < 1000:
            # Microseconds: XXXXµs (6 chars)
            return f"{exposure_us:4d}µs"
        elif exposure_us < 1_000_000:
            ms = exposure_us / 1000
            # Milliseconds: XXX.Xms (7 chars)
            return f"{ms:5.1f}ms"
        else:
            seconds = exposure_us / 1_000_000
            if seconds < 1:
                # Fraction format: 1/XXXX (7 chars)
                fraction = int(1 / seconds)
                return f"1/{fraction:4d}s"
            else:
                # Seconds: XX.Xs (6 chars, right-aligned)
                return f"{seconds:5.1f}s"

    def _format_iso(self, gain: float) -> str:
        """
        Format analogue gain as ISO equivalent with fixed width.

        Args:
            gain: Analogue gain value

        Returns:
            Formatted ISO string (e.g., "ISO  100", "ISO  800")
        """
        # Rough ISO equivalent (gain 1.0 ≈ ISO 100)
        iso = int(gain * 100)
        # Fixed width: ISO XXXX (4 digits, right-aligned)
        return f"ISO {iso:4d}"

    def _format_wb_gains(self, gains: List[float]) -> str:
        """
        Format white balance gains.

        Args:
            gains: List of [red, blue] gains

        Returns:
            Formatted string (e.g., "R:1.8 B:1.5")
        """
        if len(gains) >= 2:
            return f"R:{gains[0]:.2f} B:{gains[1]:.2f}"
        return "N/A"

    def _format_localized_datetime(self, dt: datetime) -> str:
        """
        Format datetime according to locale settings.

        Args:
            dt: datetime object

        Returns:
            Localized datetime string (e.g., "onsdag. 05 november 2025 16:45")
        """
        import locale

        datetime_config = self.overlay_config.get("datetime", {})
        use_localized = datetime_config.get("localized", True)
        show_seconds = datetime_config.get("show_seconds", False)
        locale_str = datetime_config.get("locale", "nb_NO.UTF-8")

        if use_localized:
            try:
                # Set locale for datetime formatting
                locale.setlocale(locale.LC_TIME, locale_str)

                # Format: "onsdag. 05 november 2025 16:45"
                # %A = full weekday name, %d = day, %B = full month name, %Y = year
                if show_seconds:
                    formatted = dt.strftime("%A. %d %B %Y %H:%M:%S").lower()
                else:
                    formatted = dt.strftime("%A. %d %B %Y %H:%M").lower()

                # Reset locale to default
                locale.setlocale(locale.LC_TIME, "")

                return formatted
            except Exception as e:
                logger.warning(f"Could not set locale {locale_str}: {e}")
                # Fallback to non-localized
                if show_seconds:
                    return dt.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    return dt.strftime("%Y-%m-%d %H:%M")
        else:
            # Use custom format from config
            date_format = datetime_config.get("date_format", "%Y-%m-%d")
            time_format = datetime_config.get("time_format", "%H:%M")
            return f"{dt.strftime(date_format)} {dt.strftime(time_format)}"

    def _format_color_gains(self, gains: List[float]) -> str:
        """
        Format color correction gains as tuple with fixed width.

        Args:
            gains: List of color gains

        Returns:
            Formatted string with fixed width (e.g., "( 1.80,  1.50)")
        """
        if len(gains) >= 2:
            return f"({gains[0]:5.2f}, {gains[1]:5.2f})"
        return "(  N/A,   N/A)"

    def _prepare_overlay_data(self, metadata: Dict, mode: Optional[str] = None) -> Dict[str, str]:
        """
        Prepare data dictionary for overlay formatting.

        Args:
            metadata: Image metadata from capture
            mode: Light mode (day/night/transition)

        Returns:
            Dictionary of formatted values
        """
        now = datetime.now()
        exposure_us = metadata.get("ExposureTime", 0)
        gain = metadata.get("AnalogueGain", 1.0)
        lux = metadata.get("Lux", 0.0)
        wb_gains = metadata.get("ColourGains", [])
        temp = metadata.get("SensorTemperature", 0)
        resolution = metadata.get("resolution", [0, 0])

        # Determine white balance mode
        # Note: metadata doesn't always contain control states, infer from config
        wb_mode = "Auto"  # Default assumption
        if mode == "night":
            wb_mode = "Manual"

        # Get datetime config for show_seconds
        datetime_config = self.overlay_config.get("datetime", {})
        show_seconds = datetime_config.get("show_seconds", False)

        # Format time based on show_seconds setting
        if show_seconds:
            time_str = now.strftime("%H:%M:%S")
        else:
            time_str = now.strftime("%H:%M")

        data = {
            "date": now.strftime("%Y-%m-%d"),
            "time": time_str,
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "datetime_localized": self._format_localized_datetime(now),
            "camera_name": self.overlay_config.get("camera_name", "Camera"),
            "mode": mode.title() if mode else "Unknown",
            "exposure": self._format_exposure_time(exposure_us),
            "exposure_ms": f"{exposure_us / 1000:.2f}",
            "exposure_us": str(exposure_us),
            "iso": self._format_iso(gain),
            "gain": f"{gain:4.2f}",
            "wb": wb_mode,
            "wb_gains": self._format_wb_gains(wb_gains),
            "color_gains": self._format_color_gains(wb_gains),
            "lux": f"{lux:6.1f}",
            "resolution": f"{resolution[0]}x{resolution[1]}",
            "temperature": f"{temp:5.1f}",
        }

        # Add weather data if available
        weather_data = self.weather.get_weather_data()
        if weather_data:
            data.update(
                {
                    "temp": self.weather._format_temperature(weather_data.get("temperature")),
                    "temperature_outdoor": self.weather._format_temperature(
                        weather_data.get("temperature")
                    ),
                    "humidity": self.weather._format_humidity(weather_data.get("humidity")),
                    "wind": self.weather._format_wind(
                        weather_data.get("wind_speed"), weather_data.get("wind_gust")
                    ),
                    "wind_speed": self.weather._format_wind_speed(weather_data.get("wind_speed")),
                    "wind_gust": self.weather._format_wind_speed(weather_data.get("wind_gust")),
                    "wind_dir": self.weather._format_wind_direction(weather_data.get("wind_angle")),
                    "rain": self.weather._format_rain(weather_data.get("rain")),
                    "rain_1h": self.weather._format_rain(weather_data.get("rain_1h")),
                    "rain_24h": self.weather._format_rain(weather_data.get("rain_24h")),
                    "pressure": self.weather._format_pressure(weather_data.get("pressure")),
                }
            )
        else:
            # Show "-" for stale/unavailable weather data
            data.update(
                {
                    "temp": "-",
                    "temperature_outdoor": "-",
                    "humidity": "-",
                    "wind": "-",
                    "wind_speed": "-",
                    "wind_gust": "-",
                    "wind_dir": "-",
                    "rain": "-",
                    "rain_1h": "-",
                    "rain_24h": "-",
                    "pressure": "-",
                }
            )

        return data

    def _get_text_lines(self, data: Dict[str, str]) -> List[str]:
        """
        Get all text lines to display based on configuration.
        Used for corner positions (non-bar modes).

        Args:
            data: Formatted data dictionary

        Returns:
            List of text lines
        """
        lines = []
        content_config = self.overlay_config.get("content", {})

        # For corner modes, stack all configured lines
        # Line 1 left
        if content_config.get("line_1_left"):
            try:
                line = content_config["line_1_left"].format(**data)
                lines.append(line)
            except KeyError as e:
                logger.warning(f"Unknown variable in line_1_left: {e}")
                lines.append(content_config["line_1_left"])

        # Line 1 right (if you want it in corner mode)
        if content_config.get("line_1_right"):
            try:
                line = content_config["line_1_right"].format(**data)
                lines.append(line)
            except KeyError as e:
                logger.warning(f"Unknown variable in line_1_right: {e}")
                lines.append(content_config["line_1_right"])

        # Line 2 left
        if content_config.get("line_2_left"):
            # Check if it's date/time to use localized version
            if content_config["line_2_left"] == "{date} {time}":
                lines.append(data.get("datetime_localized", f"{data['date']} {data['time']}"))
            else:
                try:
                    line = content_config["line_2_left"].format(**data)
                    lines.append(line)
                except KeyError as e:
                    logger.warning(f"Unknown variable in line_2_left: {e}")
                    lines.append(content_config["line_2_left"])

        # Line 2 right (if you want it in corner mode)
        if content_config.get("line_2_right"):
            try:
                line = content_config["line_2_right"].format(**data)
                lines.append(line)
            except KeyError as e:
                logger.warning(f"Unknown variable in line_2_right: {e}")
                lines.append(content_config["line_2_right"])

        return lines

    def _get_position(
        self, img_width: int, img_height: int, text_bbox: Tuple[int, int, int, int]
    ) -> Tuple[int, int]:
        """
        Calculate text position based on configuration.

        Args:
            img_width: Image width
            img_height: Image height
            text_bbox: Text bounding box (left, top, right, bottom)

        Returns:
            (x, y) position for top-left corner of text
        """
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        margin = self.overlay_config.get("margin", 20)

        position_preset = self.overlay_config.get("position", "bottom-left")

        # Check for bar mode
        if position_preset == "top-bar":
            # Center horizontally, small margin from top
            x = (img_width - text_width) // 2
            y = margin
            return (x, y)
        elif position_preset == "top-left":
            return (margin, margin)
        elif position_preset == "top-right":
            return (img_width - text_width - margin, margin)
        elif position_preset == "bottom-left":
            return (margin, img_height - text_height - margin)
        elif position_preset == "bottom-right":
            return (
                img_width - text_width - margin,
                img_height - text_height - margin,
            )
        elif position_preset == "custom":
            custom_pos = self.overlay_config.get("custom_position", {})
            x_percent = custom_pos.get("x", 5)
            y_percent = custom_pos.get("y", 95)
            x = int(img_width * x_percent / 100)
            y = int(img_height * y_percent / 100)
            return (x, y)
        else:
            # Default to bottom-left
            return (margin, img_height - text_height - margin)

    def _draw_gradient_bar(self, draw, img_width: int, bar_height: int, base_color: List[int]):
        """
        Draw a gradient background bar that fades from solid to transparent.

        Args:
            draw: ImageDraw object
            img_width: Image width
            bar_height: Height of the bar
            base_color: Base RGBA color [R, G, B, A]
        """
        # Create gradient from top to bottom
        r, g, b, max_alpha = base_color

        for y in range(bar_height):
            # Calculate alpha based on position (fade out towards bottom)
            alpha_ratio = 1.0 - (y / bar_height) * 0.3  # Fade 30% at bottom
            alpha = int(max_alpha * alpha_ratio)

            # Draw horizontal line with calculated alpha
            color = (r, g, b, alpha)
            draw.rectangle([0, y, img_width, y + 1], fill=color)

    def apply_overlay(
        self,
        image_path: str,
        metadata: Dict,
        mode: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> str:
        """
        Apply overlay to an image.

        Args:
            image_path: Path to source image
            metadata: Image metadata dictionary
            mode: Light mode (day/night/transition)
            output_path: Optional output path (if None, overwrites source)

        Returns:
            Path to output image
        """
        if not self.enabled:
            logger.debug("Overlay disabled, skipping")
            return image_path

        try:
            # Load image
            img = Image.open(image_path)
            img_width, img_height = img.size

            # Calculate font size based on image height
            font_config = self.overlay_config.get("font", {})
            size_ratio = font_config.get("size_ratio", 0.025)
            font_size = int(img_height * size_ratio)

            # Load fonts with calculated size (bold and regular)
            font_bold = None
            font_regular = None

            if self.font:
                try:
                    # Load bold font
                    font_bold = ImageFont.truetype(self.font, font_size)

                    # Load regular font (for details)
                    regular_font_path = self.font.replace("-Bold", "").replace("-bold", "")
                    try:
                        font_regular = ImageFont.truetype(regular_font_path, font_size)
                    except Exception:
                        font_regular = font_bold  # Fallback to bold

                except Exception as e:
                    logger.warning(f"Could not load font with size {font_size}: {e}")
                    font_bold = ImageFont.load_default()
                    font_regular = ImageFont.load_default()
            else:
                font_bold = ImageFont.load_default()
                font_regular = ImageFont.load_default()

            # Prepare overlay data
            data = self._prepare_overlay_data(metadata, mode)

            # Create drawing context
            draw = ImageDraw.Draw(img, "RGBA")

            position_preset = self.overlay_config.get("position", "bottom-left")

            # Check if we're in top-bar mode (special 2-line layout)
            if position_preset == "top-bar":
                # Two-line layout with left/right alignment
                margin = self.overlay_config.get("margin", 10)
                padding = int(font_size * 0.6)

                # Get content config
                content_config = self.overlay_config.get("content", {})

                # Calculate line height
                try:
                    line_height = int(font_bold.size * 1.2)
                except AttributeError:
                    line_height = int(font_size * 1.2)

                # Get bottom padding multiplier for extra spacing
                layout_config = self.overlay_config.get("layout", {})
                bottom_padding_mult = layout_config.get("bottom_padding_multiplier", 1.3)

                # Fixed 2 lines for compact bar
                num_lines = 2

                # Total bar height with extra bottom spacing
                bar_height = (
                    (line_height * num_lines) + (padding * 2) + int(padding * bottom_padding_mult)
                )

                # Draw gradient background
                bg_config = self.overlay_config.get("background", {})
                if bg_config.get("enabled", True):
                    bg_color = bg_config.get("color", [0, 0, 0, 140])
                    self._draw_gradient_bar(draw, img_width, bar_height, bg_color)

                # Font color
                font_color = tuple(font_config.get("color", [255, 255, 255, 255]))

                # Line positions
                y1 = margin + padding
                y2 = y1 + line_height

                # LEFT SIDE
                left_x = margin + padding

                # Line 1 Left
                line_1_left_template = content_config.get("line_1_left", "{camera_name}")
                try:
                    line_1_left = line_1_left_template.format(**data)
                except KeyError as e:
                    logger.warning(f"Unknown variable in line_1_left: {e}")
                    line_1_left = line_1_left_template
                draw.text((left_x, y1), line_1_left, fill=font_color, font=font_bold)

                # Line 2 Left (use localized datetime if it contains date/time variables)
                line_2_left_template = content_config.get("line_2_left", "{date} {time}")

                # Check if it's the default date/time template
                if line_2_left_template == "{date} {time}":
                    line_2_left = data.get("datetime_localized", f"{data['date']} {data['time']}")
                else:
                    try:
                        line_2_left = line_2_left_template.format(**data)
                    except KeyError as e:
                        logger.warning(f"Unknown variable in line_2_left: {e}")
                        line_2_left = line_2_left_template
                draw.text((left_x, y2), line_2_left, fill=font_color, font=font_regular)

                # RIGHT SIDE

                # Line 1 Right
                line_1_right_template = content_config.get("line_1_right", "")
                if line_1_right_template:
                    try:
                        line_1_right = line_1_right_template.format(**data)
                    except KeyError as e:
                        logger.warning(f"Unknown variable in line_1_right: {e}")
                        line_1_right = line_1_right_template

                    # Calculate width to position from right
                    try:
                        bbox = draw.textbbox((0, 0), line_1_right, font=font_regular)
                        text_width = bbox[2] - bbox[0]
                    except Exception:
                        text_width = len(line_1_right) * font_size * 0.6

                    right_x = img_width - text_width - margin - padding
                    draw.text(
                        (right_x, y1),
                        line_1_right,
                        fill=font_color,
                        font=font_regular,
                    )

                # Line 2 Right
                line_2_right_template = content_config.get("line_2_right", "")
                if line_2_right_template:
                    try:
                        line_2_right = line_2_right_template.format(**data)
                    except KeyError as e:
                        logger.warning(f"Unknown variable in line_2_right: {e}")
                        line_2_right = line_2_right_template

                    try:
                        bbox = draw.textbbox((0, 0), line_2_right, font=font_regular)
                        text_width = bbox[2] - bbox[0]
                    except Exception:
                        text_width = len(line_2_right) * font_size * 0.6

                    right_x = img_width - text_width - margin - padding
                    draw.text(
                        (right_x, y2),
                        line_2_right,
                        fill=font_color,
                        font=font_regular,
                    )

            else:
                # Original box layout for non-bar modes
                lines = self._get_text_lines(data)

                if not lines:
                    logger.debug("No overlay content configured")
                    return image_path

                # Calculate text dimensions
                layout_config = self.overlay_config.get("layout", {})
                line_spacing = layout_config.get("line_spacing", 1.3)

                # Get line height from font
                try:
                    line_height = int(font_bold.size * line_spacing)
                except AttributeError:
                    line_height = int(font_size * line_spacing)

                # Calculate max text width and total height
                max_width = 0
                for line in lines:
                    try:
                        bbox = draw.textbbox((0, 0), line, font=font_bold)
                        line_width = bbox[2] - bbox[0]
                        max_width = max(max_width, line_width)
                    except Exception:
                        line_width = len(line) * font_size * 0.6
                        max_width = max(max_width, int(line_width))

                total_height = len(lines) * line_height

                # Get position
                text_bbox = (0, 0, max_width, total_height)
                x, y = self._get_position(img_width, img_height, text_bbox)

                # Draw background
                bg_config = self.overlay_config.get("background", {})
                if bg_config.get("enabled", True):
                    bg_color = tuple(bg_config.get("color", [0, 0, 0, 180]))
                    padding_ratio = bg_config.get("padding", 0.3)
                    padding = int(font_size * padding_ratio)

                    bg_box = [
                        x - padding,
                        y - padding,
                        x + max_width + padding,
                        y + total_height + padding,
                    ]
                    draw.rectangle(bg_box, fill=bg_color)

                # Draw text lines
                font_color = tuple(font_config.get("color", [255, 255, 255, 255]))
                current_y = y
                for line in lines:
                    if line:
                        draw.text((x, current_y), line, fill=font_color, font=font_bold)
                    current_y += line_height

            # Save image
            if output_path is None:
                output_path = image_path

            img.save(output_path, quality=95)
            logger.debug(f"Overlay applied to {output_path}")

            return output_path

        except Exception as e:
            logger.error(f"Failed to apply overlay: {e}", exc_info=True)
            return image_path


def apply_overlay_to_image(
    image_path: str,
    metadata_path: Optional[str] = None,
    metadata: Optional[Dict] = None,
    config_path: str = "config/config.yml",
    mode: Optional[str] = None,
    output_path: Optional[str] = None,
) -> str:
    """
    Convenience function to apply overlay to an image.

    Args:
        image_path: Path to image file
        metadata_path: Path to metadata JSON file (optional if metadata provided)
        metadata: Metadata dictionary (optional if metadata_path provided)
        config_path: Path to configuration file
        mode: Light mode (day/night/transition)
        output_path: Optional output path

    Returns:
        Path to output image
    """
    import yaml

    # Load config
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Load metadata if not provided
    if metadata is None:
        if metadata_path and Path(metadata_path).exists():
            with open(metadata_path, "r") as f:
                metadata = json.load(f)
        else:
            metadata = {}

    # Apply overlay
    overlay = ImageOverlay(config)
    return overlay.apply_overlay(image_path, metadata, mode, output_path)
