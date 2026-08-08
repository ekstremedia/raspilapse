"""Image overlay module for Raspilapse.

Adds configurable text overlays to captured images with camera settings,
timestamps, and debug information.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as e:
    raise ImportError(
        "Pillow is required for overlay functionality. Install with: pip3 install Pillow"
    ) from e

from raspilapse.config import PROJECT_ROOT
from raspilapse.logging_setup import get_logger
from raspilapse.overlay import formats
from raspilapse.overlay.layout import (
    draw_gradient_bar,
    format_slot,
    text_height,
    text_width,
)

# Re-exported: these used to live here, and callers and tests still import
# them from this module.
from raspilapse.overlay.sources.json_sources import (  # noqa: F401
    AuroraData,
    CachedJsonSource,
    ShipsData,
    TideData,
)
from raspilapse.overlay.sources.weather import WeatherData
from raspilapse.overlay.widgets import BarGeometry, draw_aurora_section, draw_tide_section
from raspilapse.system import SystemMonitor

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
        self._last_weather_data: Optional[Dict] = None

        # The data sources are built whether or not the overlay draws. They are
        # pure attribute assignment -- every one of them fetches lazily, gated
        # by its own `enabled` flag -- so there is nothing to save by skipping
        # them, and the capture loop reads weather off this object to fill the
        # database columns.
        #
        # This used to return early when disabled, leaving self.weather unset.
        # The loop's read of capture.overlay.weather then raised AttributeError
        # into an `except Exception` that logged at DEBUG, so `overlay.enabled:
        # false` silently disabled all database logging as well.
        self.weather = WeatherData(config)
        self.system_monitor = SystemMonitor()
        self.ships = ShipsData(config)
        self.tide = TideData(config)
        self.aurora = AuroraData(config)

        if not self.enabled:
            logger.debug("Overlay disabled in configuration - data sources still available")
            self.font = None
            self._ship_icon = None
            return

        self.font = self._load_font()

        # Pre-sized ship icon for the header box. PROJECT_ROOT rather than
        # walking up from __file__: this module has already moved once, and the
        # walk silently resolved to a directory that does not exist, which the
        # exists() check below turned into a missing icon rather than an error.
        self._ship_icon = None
        icon_path = PROJECT_ROOT / "icons" / "ship2_small.png"
        if icon_path.exists():
            try:
                self._ship_icon = Image.open(icon_path).convert("RGBA")
                logger.debug(f"Loaded ship icon from {icon_path} ({self._ship_icon.size})")
            except Exception as e:
                logger.warning(f"Could not load ship icon: {e}")
        else:
            logger.warning(f"Ship icon not found at {icon_path}")

        logger.debug("Overlay initialized")

    def _load_font(self) -> Optional[str]:
        """
        Load font with fallback options.

        Returns:
            Path to a loadable TrueType font, or None for PIL's built-in
            bitmap font. The built-in ignores the computed size -- 10px text
            on a 4K frame -- so "default" is the last resort, not the default.
        """
        font_config = self.overlay_config.get("font", {})
        font_family = font_config.get("family", "DejaVuSans-Bold.ttf")

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
                ImageFont.truetype(font_path, 20)
                logger.debug(f"Loaded font: {font_path}")
                return font_path  # Return path, will load with proper size later
            except (OSError, IOError):
                continue

        logger.warning(f"Could not load font '{font_family}', falling back to default font")
        return None

    # The formatters live in formats.py, which is where the fixed-width
    # reasoning is written down. They stay as methods because the config for
    # the datetime one is held here, and because tests patch them per instance.

    def _format_exposure_time(self, exposure_us: int) -> str:
        """Format an exposure in microseconds. See `formats.exposure_time`."""
        return formats.exposure_time(exposure_us)

    def _format_iso(self, gain: float) -> str:
        """Format analogue gain as an ISO equivalent. See `formats.iso`."""
        return formats.iso(gain)

    def _format_wb_gains(self, gains: List[float]) -> str:
        """Format white-balance gains. See `formats.wb_gains`."""
        return formats.wb_gains(gains)

    def _format_color_gains(self, gains: List[float]) -> str:
        """Format colour gains. See `formats.color_gains`."""
        return formats.color_gains(gains)

    def _format_localized_datetime(self, dt: datetime) -> str:
        """Format a timestamp in the configured locale. See `formats.localized_datetime`."""
        return formats.localized_datetime(dt, self.overlay_config.get("datetime", {}))

    def _prepare_overlay_data(self, metadata: Dict, mode: Optional[str] = None) -> Dict[str, str]:
        """Build the substitution table the content templates draw from.

        Every key a template can reference is filled here, including the ones
        whose source is switched off -- a disabled source contributes "-" or ""
        rather than nothing, so a template naming it renders a gap instead of
        raising KeyError halfway through a frame.

        Args:
            metadata: Image metadata from capture.
            mode: Light mode (day/night/transition).

        Returns:
            Dictionary of formatted values, keyed by template placeholder.
        """
        data = self._camera_fields(metadata, mode)
        data.update(self._system_fields())
        data.update(self._weather_fields())
        data.update(self._ship_fields())
        data.update(self._tide_fields())
        return data

    def _camera_fields(self, metadata: Dict, mode: Optional[str]) -> Dict[str, str]:
        """Exposure, white balance, focus, resolution and the timestamp."""
        now = datetime.now()
        exposure_us = metadata.get("ExposureTime", 0)
        gain = metadata.get("AnalogueGain", 1.0)
        lux = metadata.get("Lux", 0.0)
        wb_gains = metadata.get("ColourGains", [])
        temp = metadata.get("SensorTemperature", 0)
        resolution = metadata.get("resolution", [0, 0])
        lens_position = metadata.get("LensPosition", None)
        af_mode = metadata.get("AfMode", None)

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

        # Format autofocus mode
        af_mode_str = "N/A"
        if af_mode is not None:
            af_modes = {0: "Manual", 1: "Auto", 2: "Continuous"}
            af_mode_str = af_modes.get(af_mode, f"Mode {af_mode}")

        # Format lens position
        lens_position_str = "N/A"
        focus_distance_str = "N/A"
        if lens_position is not None:
            lens_position_str = f"{lens_position:.2f}"
            # Calculate approximate focus distance (1 / dioptres)
            if lens_position > 0:
                focus_distance = 1.0 / lens_position
                if focus_distance < 1.0:
                    focus_distance_str = f"{focus_distance * 100:.0f}cm"
                elif focus_distance < 10.0:
                    focus_distance_str = f"{focus_distance:.1f}m"
                else:
                    focus_distance_str = f"{focus_distance:.0f}m"
            else:
                focus_distance_str = "∞"  # Infinity

        return {
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
            "af_mode": af_mode_str,
            "lens_position": lens_position_str,
            "focus_distance": focus_distance_str,
        }

    def _system_fields(self) -> Dict[str, str]:
        """CPU temperature, disk, memory, load and uptime, or N/A for each."""
        data: Dict[str, str] = {}
        # Add system monitoring data
        system_metrics = self.system_monitor.get_all_metrics(
            disk_path=self.config.get("output", {}).get("directory", "/")
        )
        if system_metrics.get("cpu_temp") is not None:
            data["cpu_temp"] = SystemMonitor.format_cpu_temp(system_metrics["cpu_temp"])
            data["cpu_temp_raw"] = f"{system_metrics['cpu_temp']:.1f}"
        else:
            data["cpu_temp"] = "N/A"
            data["cpu_temp_raw"] = "N/A"

        if system_metrics.get("disk") is not None:
            disk = system_metrics["disk"]
            data["disk_free"] = f"{disk['free']:.1f} GB"
            data["disk_used"] = f"{disk['used']:.1f} GB"
            data["disk_total"] = f"{disk['total']:.1f} GB"
            data["disk_percent"] = f"{disk['percent']:.0f}%"
            data["disk"] = SystemMonitor.format_disk_space(disk)
        else:
            data["disk_free"] = "N/A"
            data["disk_used"] = "N/A"
            data["disk_total"] = "N/A"
            data["disk_percent"] = "N/A"
            data["disk"] = "N/A"

        if system_metrics.get("memory") is not None:
            mem = system_metrics["memory"]
            data["memory_used"] = f"{mem['used']/1024:.1f} GB"
            data["memory_free"] = f"{mem['free']/1024:.1f} GB"
            data["memory_total"] = f"{mem['total']/1024:.1f} GB"
            data["memory_percent"] = f"{mem['percent']:.0f}%"
            data["memory"] = SystemMonitor.format_memory(mem)
        else:
            data["memory_used"] = "N/A"
            data["memory_free"] = "N/A"
            data["memory_total"] = "N/A"
            data["memory_percent"] = "N/A"
            data["memory"] = "N/A"

        if system_metrics.get("load") is not None:
            load = system_metrics["load"]
            data["load_1min"] = f"{load['1min']:.2f}"
            data["load_5min"] = f"{load['5min']:.2f}"
            data["load_15min"] = f"{load['15min']:.2f}"
            data["load"] = SystemMonitor.format_cpu_load(load)
        else:
            data["load_1min"] = "N/A"
            data["load_5min"] = "N/A"
            data["load_15min"] = "N/A"
            data["load"] = "N/A"

        if system_metrics.get("uptime") is not None:
            data["uptime"] = SystemMonitor.format_uptime(system_metrics["uptime"])
        else:
            data["uptime"] = "N/A"
        return data

    def _weather_fields(self) -> Dict[str, str]:
        """The current observation, falling back to the last one that arrived.

        Weather comes over the network and the network is not always there. The
        previous reading is better than a row of dashes for the minutes it takes
        to come back, and format_fields fills in dashes when there has never
        been a reading at all.
        """
        data: Dict[str, str] = {}
        # Add weather data if available
        weather_data = self.weather.get_weather_data()

        # If no fresh data, use our cached fallback
        if weather_data is None and self._last_weather_data is not None:
            logger.debug("Using overlay's cached weather data as fallback")
            weather_data = self._last_weather_data
        elif weather_data is not None:
            # Update our fallback cache with fresh data
            self._last_weather_data = weather_data

        # format_fields fills every placeholder with "-" when there is no data
        # at all (first run, never succeeded).
        data.update(self.weather.format_fields(weather_data))
        return data

    def _ship_fields(self) -> Dict[str, str]:
        """Vessel names and counts, with five line slots always defined."""
        data: Dict[str, str] = {}
        # Add ships data if available
        if self.ships.enabled:
            ships_lines = self.ships.format_ships_lines(ships_per_line=6)
            data["ships"] = ships_lines[0] if ships_lines else ""
            data["ships_count"] = str(self.ships.get_ships_count())
            data["ships_moving"] = str(self.ships.get_moving_ships_count())
            # Add individual line variables for multi-line display
            for i, line in enumerate(ships_lines, 1):
                data[f"ships_line_{i}"] = line
            # Ensure at least 5 line variables exist (empty if not needed)
            for i in range(len(ships_lines) + 1, 6):
                data[f"ships_line_{i}"] = ""
        else:
            data["ships"] = ""
            data["ships_count"] = "0"
            data["ships_moving"] = "0"
            for i in range(1, 6):
                data[f"ships_line_{i}"] = ""
        return data

    def _tide_fields(self) -> Dict[str, str]:
        """Water level and the next high and low.

        Disabled and enabled-but-no-data produce the same placeholders, so they
        share one branch here; they were written out twice identically before.
        """
        widget = self.tide.get_widget_data() if self.tide.enabled else None
        if not widget:
            return {
                "tide": "",
                "tide_level": "-",
                "tide_arrow": "",
                "tide_trend": "-",
                "tide_target": "-",
                "tide_high_time": "-",
                "tide_high_level": "-",
                "tide_low_time": "-",
                "tide_low_level": "-",
            }
        return {
            "tide": self.tide.format_tide_compact(),
            "tide_level": widget["level_str"],
            "tide_arrow": widget["arrow"],
            "tide_trend": widget["trend"],
            "tide_target": widget["target_level_str"],
            "tide_high_time": widget["high_time_str"],
            "tide_high_level": widget["high_level_str"],
            "tide_low_time": widget["low_time_str"],
            "tide_low_level": widget["low_level_str"],
        }

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
            lines.append(format_slot(content_config["line_1_left"], data, "line_1_left"))

        # Line 1 right (if you want it in corner mode)
        if content_config.get("line_1_right"):
            lines.append(format_slot(content_config["line_1_right"], data, "line_1_right"))

        # Line 2 left
        if content_config.get("line_2_left"):
            # Check if it's date/time to use localized version
            if content_config["line_2_left"] == "{date} {time}":
                lines.append(data.get("datetime_localized", f"{data['date']} {data['time']}"))
            else:
                lines.append(format_slot(content_config["line_2_left"], data, "line_2_left"))

        # Line 2 right (if you want it in corner mode)
        if content_config.get("line_2_right"):
            lines.append(format_slot(content_config["line_2_right"], data, "line_2_right"))

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
        # Named to avoid shadowing overlay_draw.text_width / text_height.
        bbox_width = text_bbox[2] - text_bbox[0]
        bbox_height = text_bbox[3] - text_bbox[1]
        margin = self.overlay_config.get("margin", 20)

        position_preset = self.overlay_config.get("position", "top-bar")

        # Check for bar mode
        if position_preset == "top-bar":
            # Center horizontally, small margin from top
            x = (img_width - bbox_width) // 2
            y = margin
            return (x, y)
        elif position_preset == "top-left":
            return (margin, margin)
        elif position_preset == "top-right":
            return (img_width - bbox_width - margin, margin)
        elif position_preset == "bottom-left":
            return (margin, img_height - bbox_height - margin)
        elif position_preset == "bottom-right":
            return (
                img_width - bbox_width - margin,
                img_height - bbox_height - margin,
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
            return (margin, img_height - bbox_height - margin)

    def _draw_gradient_bar(self, draw, img_width: int, bar_height: int, base_color: List[int]):
        """Draw the gradient background bar. See overlay_draw.draw_gradient_bar."""
        draw_gradient_bar(draw, img_width, bar_height, base_color)

    def _draw_ship_boxes(
        self,
        img: Image.Image,
        bar_height: int,
        font: ImageFont.FreeTypeFont,
        font_color: Tuple[int, int, int, int],
        bg_color: List[int],
        margin: int,
        padding: int,
    ) -> None:
        """
        Draw individual ship boxes below the overlay bar.

        Each ship appears in its own rounded box with a ship icon prefix,
        arranged horizontally from left to right.

        Args:
            img: PIL Image to draw on
            bar_height: Height of the main overlay bar (boxes start below this)
            font: Font to use for text
            font_color: RGBA tuple for text color
            bg_color: RGBA list for box background [R, G, B, A]
            margin: Margin from edges
            padding: Padding inside boxes
        """
        if not self.ships.enabled:
            return

        ship_texts = self.ships.get_ship_boxes_data()
        if not ship_texts:
            return

        # Create drawing context with alpha support
        draw = ImageDraw.Draw(img, "RGBA")

        # Box styling
        box_bg = tuple(bg_color)  # Same as overlay background
        corner_radius = int(padding * 0.8)  # Rounded corners
        box_gap = int(padding * 0.6)  # Gap between boxes
        box_padding_h = int(padding * 0.8)  # Horizontal padding inside box
        box_padding_v = int(padding * 0.7)  # Vertical padding inside box
        box_margin = int(padding * 0.5)  # Margin from bar and left edge

        # Starting position (below the bar, same gap on top and left)
        x = box_margin
        y = bar_height + box_margin

        img_width = img.size[0]

        # Calculate consistent text height using reference characters (covers ascenders/descenders)
        consistent_text_height = text_height(draw, font)

        # Use icon height for box height if icon is taller than text
        ship_icon = self._ship_icon
        if ship_icon and ship_icon.height > consistent_text_height:
            box_content_height = ship_icon.height
        else:
            box_content_height = consistent_text_height

        # Consistent box height for all ship boxes
        consistent_box_height = box_content_height + (box_padding_v * 2)

        # Draw header box with ship icon and count
        ship_count = len(ship_texts)
        count_text = str(ship_count)
        icon_spacing = int(padding * 0.4)

        if ship_icon:
            # Calculate header box width: icon + spacing + count
            count_width = text_width(draw, count_text, font)

            header_content_width = ship_icon.width + icon_spacing + count_width
            header_box_width = header_content_width + (box_padding_h * 2)

            # Draw header box background
            header_box_coords = [x, y, x + header_box_width, y + consistent_box_height]
            draw.rounded_rectangle(header_box_coords, radius=corner_radius, fill=box_bg)

            # Paste icon vertically centered
            icon_x = x + box_padding_h
            icon_y = y + (consistent_box_height - ship_icon.height) // 2
            img.paste(ship_icon, (int(icon_x), int(icon_y)), ship_icon)

            # Draw count text vertically centered
            text_x = icon_x + ship_icon.width + icon_spacing
            text_y = y + (consistent_box_height // 2)
            draw.text((text_x, text_y), count_text, fill=font_color, font=font, anchor="lm")

            # Move to next box position
            x += header_box_width + box_gap

        # Draw ship name boxes (no icons)
        for ship_text in ship_texts:
            # Calculate text width
            ship_text_width = text_width(draw, ship_text, font)

            box_width = ship_text_width + (box_padding_h * 2)
            box_height = consistent_box_height

            # Check if box fits on current line
            if x + box_width > img_width - box_margin:
                # Wrap to next line
                x = box_margin
                y += box_height + box_gap

            # Draw rounded rectangle background
            box_coords = [x, y, x + box_width, y + box_height]
            draw.rounded_rectangle(box_coords, radius=corner_radius, fill=box_bg)

            # Draw text vertically centered
            text_x = x + box_padding_h
            text_y = y + (box_height // 2)
            draw.text((text_x, text_y), ship_text, fill=font_color, font=font, anchor="lm")

            # Move to next box position
            x += box_width + box_gap

    def _load_sized_fonts(self, font_size: int):
        """Load the bold and regular faces at a size derived from the image.

        Returns:
            (bold, regular). Regular falls back to bold when only a bold face is
            installed, and both fall back to PIL's built-in font when the
            configured family will not load at all -- an overlay in the wrong
            font is worth more than no overlay.
        """
        if not self.font:
            return ImageFont.load_default(), ImageFont.load_default()

        try:
            bold = ImageFont.truetype(self.font, font_size)
        except Exception as e:
            logger.warning(f"Could not load font with size {font_size}: {e}")
            return ImageFont.load_default(), ImageFont.load_default()

        regular_path = self.font.replace("-Bold", "").replace("-bold", "")
        try:
            regular = ImageFont.truetype(regular_path, font_size)
        except Exception:
            regular = bold
        return bold, regular

    def _draw_top_bar(self, img, draw, data: Dict, font_bold, font_regular, font_size: int) -> None:
        """Draw the two-line bar across the top of the frame.

        Left side is camera identity and time; right side is the widget
        sections, laid out from the right edge inwards so each one keeps a
        fixed position from frame to frame. Ship boxes float below the bar.
        """
        img_width = img.size[0]
        font_config = self.overlay_config.get("font", {})

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

        # Fixed 2 lines for top bar (ships are rendered as separate floating boxes)
        num_lines = 2

        # Total bar height with extra bottom spacing
        bar_height = (line_height * num_lines) + (padding * 2) + int(padding * bottom_padding_mult)

        # Get background config and color (used for bar and ship boxes)
        bg_config = self.overlay_config.get("background", {})
        bg_color = bg_config.get("color", [0, 0, 0, 140])

        # Draw gradient background bar
        if bg_config.get("enabled", True):
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
        line_1_left = format_slot(line_1_left_template, data, "line_1_left")
        draw.text((left_x, y1), line_1_left, fill=font_color, font=font_bold)

        # Line 2 Left (use localized datetime if it contains date/time variables)
        line_2_left_template = content_config.get("line_2_left", "{date} {time}")

        # Check if it's the default date/time template
        if line_2_left_template == "{date} {time}":
            line_2_left = data.get("datetime_localized", f"{data['date']} {data['time']}")
        else:
            line_2_left = format_slot(line_2_left_template, data, "line_2_left")
        draw.text((left_x, y2), line_2_left, fill=font_color, font=font_regular)

        # RIGHT SIDE. Each section draws itself against the right
        # edge, inset by what the sections to its right already took,
        # and reports the width it used. See widgets.py.
        geometry = BarGeometry(
            img_width=img_width,
            y1=y1,
            y2=y2,
            line_height=line_height,
            margin=margin,
            padding=padding,
            section_gap=int(padding * 2),
            font_size=font_size,
        )

        aurora_section_width = 0
        if self.aurora.enabled:
            aurora_section_width = draw_aurora_section(
                draw, self.aurora, geometry, font_regular, font_color
            )

        tide_section_width = 0
        if self.tide.enabled:
            tide_section_width = draw_tide_section(
                draw,
                self.tide,
                geometry,
                font_regular,
                font_color,
                occupied=aurora_section_width,
            )

        # Line 1 Right (positioned to left of tide section)
        line_1_right_template = content_config.get("line_1_right", "")
        if line_1_right_template:
            line_1_right = format_slot(line_1_right_template, data, "line_1_right")

            # Calculate width to position from right (accounting for tide section)
            line_1_width = text_width(draw, line_1_right, font_regular, font_size)

            right_x = (
                img_width
                - line_1_width
                - margin
                - padding
                - tide_section_width
                - aurora_section_width
            )
            draw.text(
                (right_x, y1),
                line_1_right,
                fill=font_color,
                font=font_regular,
            )

        # Line 2 Right (positioned to left of tide section)
        line_2_right_template = content_config.get("line_2_right", "")
        if line_2_right_template:
            line_2_right = format_slot(line_2_right_template, data, "line_2_right")

            line_2_width = text_width(draw, line_2_right, font_regular, font_size)

            right_x = (
                img_width
                - line_2_width
                - margin
                - padding
                - tide_section_width
                - aurora_section_width
            )
            draw.text(
                (right_x, y2),
                line_2_right,
                fill=font_color,
                font=font_regular,
            )

        # Draw ship boxes below the bar (floating boxes with rounded corners)
        try:
            self._draw_ship_boxes(
                img=img,
                bar_height=bar_height,
                font=font_regular,
                font_color=font_color,
                bg_color=bg_color,
                margin=margin,
                padding=padding,
            )
        except Exception as ships_err:
            logger.error(f"Failed to draw ship boxes: {ships_err}", exc_info=True)

    def _draw_corner_box(self, img, draw, data: Dict, font_bold, font_size: int) -> bool:
        """Draw the boxed layout used by every position preset except top-bar.

        Returns:
            False if no content is configured, so the caller can leave the image
            untouched rather than writing an identical copy of it.
        """
        img_width, img_height = img.size
        font_config = self.overlay_config.get("font", {})

        # Original box layout for non-bar modes
        lines = self._get_text_lines(data)

        if not lines:
            logger.warning(
                "Overlay is enabled but this corner layout has no content "
                "configured; nothing drawn. Use overlay.position: top-bar "
                "(which has built-in content) or set overlay.content lines."
            )
            return False

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
            max_width = max(max_width, text_width(draw, line, font_bold, font_size))

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

        return True

    def apply_overlay(
        self,
        image_path: str,
        metadata: Dict,
        mode: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> Optional[str]:
        """
        Apply overlay to an image.

        Args:
            image_path: Path to source image
            metadata: Image metadata dictionary
            mode: Light mode (day/night/transition)
            output_path: Optional output path (if None, overwrites source)

        Returns:
            Path to output image, or None on failure
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

            font_bold, font_regular = self._load_sized_fonts(font_size)

            # Prepare overlay data
            data = self._prepare_overlay_data(metadata, mode)

            # Create drawing context
            draw = ImageDraw.Draw(img, "RGBA")

            position_preset = self.overlay_config.get("position", "top-bar")

            # Check if we're in top-bar mode (special 2-line layout)
            if position_preset == "top-bar":
                self._draw_top_bar(img, draw, data, font_bold, font_regular, font_size)
            elif not self._draw_corner_box(img, draw, data, font_bold, font_size):
                return image_path

            # Save image
            if output_path is None:
                output_path = image_path

            # 85 matches output.quality's real default; this fallback was 95,
            # so the one path that bypasses merge_defaults re-encoded at a
            # different quality from every other frame.
            output_quality = self.config.get("output", {}).get("quality", 85)
            # Write beside the target and rename into place. Image.save opens
            # its destination "w+b", so saving in place truncates the good
            # capture first -- a failure mid-encode (ENOSPC, power loss) left
            # a partial JPEG that was then symlinked, recorded and uploaded
            # as if it were a frame.
            tmp_path = f"{output_path}.tmp"
            try:
                img.save(tmp_path, format="JPEG", quality=output_quality)
                os.replace(tmp_path, output_path)
                logger.debug(f"Overlay saved to {output_path}")
            except Exception as save_error:
                logger.error(f"Failed to save overlay image: {save_error}", exc_info=True)
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                return None  # Return None to indicate failure

            return output_path

        except Exception as e:
            logger.error(f"Failed to apply overlay: {e}", exc_info=True)
            return None  # Return None to indicate failure


def apply_overlay_to_image(
    image_path: str,
    metadata_path: Optional[str] = None,
    metadata: Optional[Dict] = None,
    config_path: str = "config/config.yml",
    mode: Optional[str] = None,
    output_path: Optional[str] = None,
) -> Optional[str]:
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
        Path to output image, or None on failure
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
