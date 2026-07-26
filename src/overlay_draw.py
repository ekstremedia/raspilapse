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


def measure_widest(draw, texts, font, font_size: int = 20) -> int:
    """
    Width of the widest of several strings.

    Used to reserve a fixed slot from template maxima, so a section does not
    jitter as its content changes between frames.
    """
    return max((text_width(draw, t, font, font_size) for t in texts), default=0)


def draw_gradient_bar(draw, img_width: int, bar_height: int, base_color) -> None:
    """
    Draw the top bar as a vertical gradient, fading toward the image.

    A hard-edged bar draws the eye to itself; fading the bottom 30% lets the
    scene show through where the text isn't.

    Args:
        draw: PIL ImageDraw target
        img_width: Full image width
        bar_height: Height of the bar in pixels
        base_color: RGBA sequence; the A is the alpha at the very top
    """
    r, g, b, max_alpha = base_color
    for y in range(bar_height):
        alpha = int(max_alpha * (1.0 - (y / bar_height) * 0.3))
        draw.rectangle([0, y, img_width, y + 1], fill=(r, g, b, alpha))
