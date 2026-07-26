"""Drawing helpers for the overlay.

Pure functions, no state, no config. Extracted because the same three
idioms -- measure a string, format a template slot, draw a right-aligned
section -- appeared ten, eight and four times respectively inside one
500-line method.
"""

from typing import Dict

try:
    from src.logging_config import get_logger
except ImportError:
    from logging_config import get_logger

logger = get_logger("overlay")


def text_width(draw, text: str, font, font_size: int = 20) -> int:
    """
    Width of `text` in pixels.

    Falls back to a character-count estimate when the font cannot be measured,
    which happens with some bitmap fallbacks. An overlay that is slightly
    misaligned beats an overlay that is not drawn.
    """
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return int(bbox[2] - bbox[0])
    except Exception:
        return int(len(text) * font_size * 0.6)


def text_height(draw, font, reference: str = "Ayg", fallback: int = 20) -> int:
    """
    Height of a line in pixels, measured from a reference string.

    The reference has an ascender, a descender and a cap so every line comes
    out the same height regardless of its own characters.
    """
    try:
        bbox = draw.textbbox((0, 0), reference, font=font)
        return int(bbox[3] - bbox[1])
    except Exception:
        return fallback


def format_slot(template: str, data: Dict, slot_name: str = "overlay") -> str:
    """
    Fill a template with overlay data, surviving an unknown placeholder.

    A typo in one config template should leave that slot showing its raw text,
    not abort the whole overlay.
    """
    try:
        return template.format(**data)
    except KeyError as e:
        logger.warning(f"Unknown variable in {slot_name}: {e}")
        return template
    except (IndexError, ValueError) as e:
        logger.warning(f"Malformed template in {slot_name}: {e}")
        return template


def draw_divider(draw, x: int, y_top: int, y_bottom: int, color, width: int = 1) -> None:
    """Vertical rule between two sections of the top bar."""
    draw.line([(x, y_top), (x, y_bottom)], fill=color, width=width)


def draw_right_aligned(
    draw,
    text: str,
    *,
    right_edge: int,
    y: int,
    font,
    fill,
    font_size: int = 20,
) -> int:
    """
    Draw `text` ending at `right_edge`.

    Returns:
        The x coordinate the text starts at, so a caller can place a divider
        or the next section beside it.
    """
    x = right_edge - text_width(draw, text, font, font_size)
    draw.text((x, y), text, font=font, fill=fill)
    return x


def measure_widest(draw, texts, font, font_size: int = 20) -> int:
    """
    Width of the widest of several strings.

    Used to reserve a fixed slot from template maxima, so a section does not
    jitter as its content changes between frames.
    """
    return max((text_width(draw, t, font, font_size) for t in texts), default=0)


def draw_gradient_bar(draw, img_width: int, bar_height: int, base_color, steps: int = 40) -> None:
    """
    Draw the top bar as a vertical gradient fading to transparent.

    A hard-edged bar draws the eye; a fade lets the image show through.
    """
    r, g, b, a = base_color
    for i in range(steps):
        y_start = int(i * bar_height / steps)
        y_end = int((i + 1) * bar_height / steps)
        alpha = int(a * (1 - i / steps))
        draw.rectangle([(0, y_start), (img_width, y_end)], fill=(r, g, b, alpha))
