"""The right-hand sections of the top bar: aurora and tide.

Both follow the same contract. Each draws itself hard against the right edge,
inset by whatever earlier sections already claimed, and returns the width it
used so the next one can move further left. A section that is disabled, has no
data, or fails outright returns 0 and the bar closes up around it.

Two things here look like over-engineering and are not:

**Fixed text widths.** Each section measures a worst-case template ("Kp: 9.9 |
Bz: -99.9↓") rather than the string it is about to draw, and positions itself
from that. The real text is narrower most of the time, so there is dead space
on the left of each section. That is deliberate: these frames become a video,
and sizing to the actual text makes the whole block slide left and right as the
Kp index gains a digit or the arrow flips between ↑ and ↓.

**Partial widths survive a failure.** If drawing throws halfway, the width
measured so far is still returned, so the sections to the left do not slide
under the half-drawn one. Preserved deliberately from the original.
"""

import logging
import math
from dataclasses import dataclass

from .layout import draw_divider, measure_widest

logger = logging.getLogger(__name__)

# Worst-case strings, used for width only -- never drawn. Widening a real
# format string means widening its template here too, or the section will
# start drifting between frames.
#
# Only the widest line of each pair actually sets the section width; at the
# sizes this renders at, that is the first line in both cases, and the second
# has roughly 30px of slack. So shortening a second line changes nothing today
# -- but lengthening one past its partner does, and silently, which is why both
# are listed rather than only the one that currently wins.
AURORA_WIDEST = ("Kp: 9.9 | Bz: -99.9↓", "G5 | 9999 km/s")
TIDE_WIDEST = ("Tide level: 999cm → 999cm", "H 00:00 (999cm) | L 00:00 (999cm)")

MARKER_COLOR = (255, 200, 100, 255)  # Orange/gold, against the white-ish text
DIVIDER_ALPHA = 60
WAVE_ALPHA = 180
WAVE_POINTS = 40


@dataclass
class BarGeometry:
    """Where the top bar's two text lines sit, and how much room they have."""

    img_width: int
    y1: int
    y2: int
    line_height: int
    margin: int
    padding: int
    section_gap: int
    font_size: int

    def divider_bottom(self) -> int:
        """The y a section divider stops at: the bottom of line 2, less a hair."""
        return self.y2 + self.line_height - int(self.padding * 0.3)


def draw_aurora_section(draw, aurora, geometry: BarGeometry, font, font_color) -> int:
    """Draw Kp/Bz and storm level/solar wind speed at the far right of the bar.

    Args:
        draw: The `ImageDraw` to render onto.
        aurora: The aurora source; `get_widget_data()` supplies the reading.
        geometry: Bar layout.
        font: Regular-weight font.
        font_color: RGBA text colour.

    Returns:
        Width consumed including the trailing gap, or 0 if nothing was drawn.
    """
    width = 0
    try:
        widget = aurora.get_widget_data()
        if not widget:
            return 0

        line_1 = f"Kp: {widget['kp_str']} | Bz: {widget['bz_str']}{widget['bz_arrow']}"
        line_2 = f"{widget['storm']} | {widget['speed_str']} km/s"

        try:
            width = measure_widest(draw, AURORA_WIDEST, font, geometry.font_size)
        except Exception:
            width = 0

        x = geometry.img_width - width - geometry.margin - geometry.padding
        draw.text((x, geometry.y1), line_1, fill=font_color, font=font)
        draw.text((x, geometry.y2), line_2, fill=font_color, font=font)

        width += geometry.section_gap
        draw_divider(
            draw,
            x=x - int(geometry.section_gap * 0.5),
            y_top=geometry.y1,
            y_bottom=geometry.divider_bottom(),
            color=font_color[:3] + (DIVIDER_ALPHA,),
        )
    except Exception as err:
        logger.error(f"Failed to draw aurora widget: {err}", exc_info=True)
    return width


def draw_tide_section(draw, tide, geometry: BarGeometry, font, font_color, occupied: int) -> int:
    """Draw tide level, the next high/low, and a scrolling wave, left of aurora.

    Args:
        draw: The `ImageDraw` to render onto.
        tide: The tide source; `get_widget_data()` supplies the reading.
        geometry: Bar layout.
        font: Regular-weight font.
        font_color: RGBA text colour.
        occupied: Width already taken by sections to the right.

    Returns:
        Width consumed including the trailing gap, or 0 if nothing was drawn.
    """
    width = 0
    try:
        widget = tide.get_widget_data()
        if not widget:
            return 0

        wave_width = int(geometry.font_size * 4)
        wave_height = int(geometry.line_height * 1.6)
        wave_margin = int(geometry.padding * 0.5)

        line_1 = f"Tide level: {widget['level_str']} {widget['arrow']} {widget['target_level_str']}"
        line_2 = _tide_events_line(widget)

        try:
            text_w = measure_widest(draw, TIDE_WIDEST, font, geometry.font_size)
        except Exception:
            text_w = 0

        width = text_w + wave_margin + wave_width
        x = geometry.img_width - width - occupied - geometry.margin - geometry.padding
        wave_x = x + text_w + wave_margin

        draw.text((x, geometry.y1), line_1, fill=font_color, font=font)
        draw.text((x, geometry.y2), line_2, fill=font_color, font=font)

        _draw_tide_wave(
            draw,
            widget,
            x=wave_x,
            y=geometry.y1 + int(geometry.line_height * 0.1),
            wave_width=wave_width,
            wave_height=wave_height,
            font_size=geometry.font_size,
            font_color=font_color,
        )

        width += geometry.section_gap
        draw_divider(
            draw,
            x=x - int(geometry.section_gap * 0.5),
            y_top=geometry.y1,
            y_bottom=geometry.divider_bottom(),
            color=font_color[:3] + (DIVIDER_ALPHA,),
        )
    except Exception as err:
        logger.error(f"Failed to draw tide widget: {err}", exc_info=True)
    return width


def _tide_events_line(widget) -> str:
    """The high/low line, earlier event first.

    Chronological rather than always high-then-low, so the pair reads as
    "what happens next, and then what" instead of needing to be decoded.
    """
    high_time = widget.get("high_time")
    low_time = widget.get("low_time")
    high = f"H {widget['high_time_str']} ({widget['high_level_str']})"
    low = f"L {widget['low_time_str']} ({widget['low_level_str']})"
    if high_time and low_time and low_time < high_time:
        return f"{low} | {high}"
    return f"{high} | {low}"


def _draw_tide_wave(draw, widget, *, x, y, wave_width, wave_height, font_size, font_color) -> None:
    """A sine wave with a marker pinned to its centre.

    The marker does not travel: it holds the horizontal middle and moves only
    up and down with the water, while the wave scrolls underneath it. Over a
    timelapse that reads as the tide flowing past a fixed point, which is what
    is actually happening. A marker that slid left to right instead would go
    back to the start twice a day and look like a glitch.
    """
    level = widget["level"]
    # Defaults for a station that reports a level but no upcoming extremes;
    # they only set the scale the marker moves within.
    high = widget["high_level"] if widget["high_level"] is not None else 2.0
    low = widget["low_level"] if widget["low_level"] is not None else 0.5

    level_range = high - low
    if level_range > 0:
        normalized = max(0.0, min(1.0, (level - low) / level_range))
    else:
        normalized = 0.5

    # Phase 0.0 = low and rising, 0.5 = high, 1.0 = low and falling.
    if widget["next_event_type"] == "high":
        phase = normalized * 0.5
    else:
        phase = 0.5 + (1.0 - normalized) * 0.5

    amplitude = wave_height / 2 * 0.8
    center_y = y + int(wave_height / 2)
    marker_x = x + int(wave_width / 2)
    marker_y = int(center_y - (normalized - 0.5) * 2 * amplitude)

    points = []
    for i in range(WAVE_POINTS + 1):
        t = i / WAVE_POINTS
        # Offset so the current phase lands at the centre, t = 0.5.
        wave_t = (t - 0.5) + phase
        y_offset = math.sin(wave_t * 2 * math.pi - math.pi / 2)
        points.append((x + int(t * wave_width), int(center_y - y_offset * amplitude)))

    if len(points) > 1:
        draw.line(points, fill=font_color[:3] + (WAVE_ALPHA,), width=2)

    radius = int(font_size * 0.25)
    draw.ellipse(
        [marker_x - radius, marker_y - radius, marker_x + radius, marker_y + radius],
        fill=MARKER_COLOR,
        outline=(255, 255, 255, 255),
        width=1,
    )

    # A dropped line to the waterline, so the marker's height is readable
    # against something rather than only in comparison with earlier frames.
    bottom = y + wave_height
    if marker_y + radius < bottom:
        draw.line(
            [(marker_x, marker_y + radius), (marker_x, bottom)],
            fill=(255, 255, 255, 100),
            width=1,
        )
