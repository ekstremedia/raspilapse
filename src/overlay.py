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

        logger.warning(
            f"Could not load font '{font_family}', falling back to default font"
        )
        return None

    def _format_exposure_time(self, exposure_us: int) -> str:
        """
        Format exposure time in human-readable form.

        Args:
            exposure_us: Exposure time in microseconds

        Returns:
            Formatted string (e.g., "1/500s", "2.5s", "15s")
        """
        if exposure_us < 1000:
            return f"{exposure_us}µs"
        elif exposure_us < 1_000_000:
            ms = exposure_us / 1000
            return f"{ms:.1f}ms"
        else:
            seconds = exposure_us / 1_000_000
            if seconds < 1:
                # Show as fraction for fast speeds
                fraction = int(1 / seconds)
                return f"1/{fraction}s"
            else:
                return f"{seconds:.1f}s"

    def _format_iso(self, gain: float) -> str:
        """
        Format analogue gain as ISO equivalent.

        Args:
            gain: Analogue gain value

        Returns:
            Formatted ISO string (e.g., "ISO 100", "ISO 800")
        """
        # Rough ISO equivalent (gain 1.0 ≈ ISO 100)
        iso = int(gain * 100)
        return f"ISO {iso}"

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
        Format color correction gains as tuple.

        Args:
            gains: List of color gains

        Returns:
            Formatted string (e.g., "(1.80, 1.50)")
        """
        if len(gains) >= 2:
            return f"({gains[0]:.2f}, {gains[1]:.2f})"
        return "N/A"

    def _prepare_overlay_data(
        self, metadata: Dict, mode: Optional[str] = None
    ) -> Dict[str, str]:
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
            "gain": f"{gain:.2f}",
            "wb": wb_mode,
            "wb_gains": self._format_wb_gains(wb_gains),
            "color_gains": self._format_color_gains(wb_gains),
            "lux": f"{lux:.1f}",
            "resolution": f"{resolution[0]}x{resolution[1]}",
            "temperature": f"{temp:.1f}",
        }

        return data

    def _get_text_lines(self, data: Dict[str, str]) -> List[str]:
        """
        Get all text lines to display based on configuration.

        Args:
            data: Formatted data dictionary

        Returns:
            List of text lines
        """
        lines = []
        content_config = self.overlay_config.get("content", {})
        layout_config = self.overlay_config.get("layout", {})
        section_spacing = layout_config.get("section_spacing", True)

        # Main content (always shown)
        main_lines = content_config.get("main", [])
        for line_template in main_lines:
            try:
                line = line_template.format(**data)
                lines.append(line)
            except KeyError as e:
                logger.warning(f"Unknown variable in overlay template: {e}")
                lines.append(line_template)

        # Camera settings (optional)
        camera_settings = content_config.get("camera_settings", {})
        if camera_settings.get("enabled", False):
            if section_spacing and lines:
                lines.append("")  # Blank line separator
            for line_template in camera_settings.get("lines", []):
                try:
                    line = line_template.format(**data)
                    lines.append(line)
                except KeyError as e:
                    logger.warning(f"Unknown variable in overlay template: {e}")
                    lines.append(line_template)

        # Debug info (optional)
        debug = content_config.get("debug", {})
        if debug.get("enabled", False):
            if section_spacing and lines:
                lines.append("")  # Blank line separator
            for line_template in debug.get("lines", []):
                try:
                    line = line_template.format(**data)
                    lines.append(line)
                except KeyError as e:
                    logger.warning(f"Unknown variable in overlay template: {e}")
                    lines.append(line_template)

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

    def _draw_gradient_bar(
        self, draw, img_width: int, bar_height: int, base_color: List[int]
    ):
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
                    regular_font_path = self.font.replace("-Bold", "").replace(
                        "-bold", ""
                    )
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

                # Line 1 - Left: Camera name (bold), Right: Mode + Exposure + ISO
                # Line 2 - Left: Date Time, Right: WB + Lux

                # Calculate line height
                try:
                    line_height = int(font_bold.size * 1.2)
                except AttributeError:
                    line_height = int(font_size * 1.2)

                # Get bottom padding multiplier for extra spacing
                layout_config = self.overlay_config.get("layout", {})
                bottom_padding_mult = layout_config.get(
                    "bottom_padding_multiplier", 1.3
                )

                # Total bar height for 2 lines with extra bottom spacing
                bar_height = (
                    (line_height * 2)
                    + (padding * 2)
                    + int(padding * bottom_padding_mult)
                )

                # Draw gradient background
                bg_config = self.overlay_config.get("background", {})
                if bg_config.get("enabled", True):
                    bg_color = bg_config.get("color", [0, 0, 0, 140])
                    self._draw_gradient_bar(draw, img_width, bar_height, bg_color)

                # Font color
                font_color = tuple(font_config.get("color", [255, 255, 255, 255]))

                # Line 1 positions
                y1 = margin + padding
                # Line 2 positions
                y2 = y1 + line_height

                # LEFT SIDE (bold camera name + date/time)
                left_x = margin + padding

                # Line 1 Left: Camera name (bold)
                camera_name = data.get("camera_name", "Camera")
                draw.text((left_x, y1), camera_name, fill=font_color, font=font_bold)

                # Line 2 Left: Date and time (regular, localized if enabled)
                datetime_text = data.get(
                    "datetime_localized", f"{data['date']} {data['time']}"
                )
                draw.text(
                    (left_x, y2), datetime_text, fill=font_color, font=font_regular
                )

                # RIGHT SIDE (use config content, regular font)
                content_config = self.overlay_config.get("content", {})

                # Line 1 Right: Camera settings (if enabled)
                camera_settings = content_config.get("camera_settings", {})
                if camera_settings.get("enabled", False):
                    lines = camera_settings.get("lines", [])
                    if lines:
                        # Use first line for line 1 right
                        line1_template = lines[0]
                        try:
                            line1_right = line1_template.format(**data)
                        except KeyError as e:
                            logger.warning(f"Unknown variable in overlay template: {e}")
                            line1_right = line1_template

                        # Calculate width to position from right
                        try:
                            bbox = draw.textbbox((0, 0), line1_right, font=font_regular)
                            text_width = bbox[2] - bbox[0]
                        except Exception:
                            text_width = len(line1_right) * font_size * 0.6

                        right_x = img_width - text_width - margin - padding
                        draw.text(
                            (right_x, y1),
                            line1_right,
                            fill=font_color,
                            font=font_regular,
                        )

                # Line 2 Right: Debug info (if enabled)
                debug = content_config.get("debug", {})
                if debug.get("enabled", False):
                    lines = debug.get("lines", [])
                    if lines:
                        # Use first line for line 2 right
                        line2_template = lines[0]
                        try:
                            line2_right = line2_template.format(**data)
                        except KeyError as e:
                            logger.warning(f"Unknown variable in overlay template: {e}")
                            line2_right = line2_template

                        try:
                            bbox = draw.textbbox((0, 0), line2_right, font=font_regular)
                            text_width = bbox[2] - bbox[0]
                        except Exception:
                            text_width = len(line2_right) * font_size * 0.6

                        right_x = img_width - text_width - margin - padding
                        draw.text(
                            (right_x, y2),
                            line2_right,
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
