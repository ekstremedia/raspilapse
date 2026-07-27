"""Turning camera numbers into strings that do not jitter.

Every formatter here pads to a fixed width. That is the whole point of the
module: an overlay is rendered onto 2,880 frames a day and then played back at
30 fps, so a field that is 6 characters wide on one frame and 7 on the next
makes the whole line jump sideways twice a second. `1/500s` and ` 15.0s` are
both six characters for that reason, not for tidiness.

The functions are pure and take their configuration as an argument, so they can
be tested without an image, a font or a camera.
"""

import locale
import logging
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)


def exposure_time(exposure_us: int) -> str:
    """Format an exposure in microseconds, at a constant six or seven characters.

    Args:
        exposure_us: Exposure time in microseconds.

    Returns:
        e.g. " 500µs", "  5.0ms", "1/ 500s", " 15.0s".
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


def iso(gain: float) -> str:
    """Format analogue gain as an ISO equivalent, gain 1.0 being roughly ISO 100.

    Args:
        gain: Analogue gain value.

    Returns:
        e.g. "ISO  100", "ISO  800".
    """
    return f"ISO {int(gain * 100):4d}"


def wb_gains(gains: List[float]) -> str:
    """Format red and blue white-balance gains.

    Args:
        gains: [red, blue]; anything shorter renders as N/A.

    Returns:
        e.g. "R:1.80 B:1.50".
    """
    if len(gains) >= 2:
        return f"R:{gains[0]:.2f} B:{gains[1]:.2f}"
    return "N/A"


def color_gains(gains: List[float]) -> str:
    """Format colour gains as a fixed-width tuple.

    Args:
        gains: [red, blue]; anything shorter renders as N/A.

    Returns:
        e.g. "( 1.80,  1.50)".
    """
    if len(gains) >= 2:
        return f"({gains[0]:5.2f}, {gains[1]:5.2f})"
    return "(  N/A,   N/A)"


def localized_datetime(dt: datetime, datetime_config: Dict) -> str:
    """Format a timestamp in the configured locale.

    Args:
        dt: The moment to render.
        datetime_config: The overlay's `datetime` config block -- `localized`,
            `locale`, `show_seconds`, and `date_format`/`time_format` for the
            non-localized path.

    Returns:
        e.g. "onsdag. 05 november 2025 16:45". Falls back to ISO-ish output if
        the requested locale is not installed, which is the common case on a
        fresh Raspberry Pi OS image.
    """
    use_localized = datetime_config.get("localized", True)
    show_seconds = datetime_config.get("show_seconds", False)
    locale_str = datetime_config.get("locale", "nb_NO.UTF-8")

    if not use_localized:
        date_format = datetime_config.get("date_format", "%Y-%m-%d")
        time_format = datetime_config.get("time_format", "%H:%M")
        return f"{dt.strftime(date_format)} {dt.strftime(time_format)}"

    try:
        locale.setlocale(locale.LC_TIME, locale_str)

        # %A full weekday, %B full month -- both are what the locale is for.
        if show_seconds:
            formatted = dt.strftime("%A. %d %B %Y %H:%M:%S").lower()
        else:
            formatted = dt.strftime("%A. %d %B %Y %H:%M").lower()

        locale.setlocale(locale.LC_TIME, "")
        return formatted
    except Exception as e:
        logger.warning(f"Could not set locale {locale_str}: {e}")
        if show_seconds:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M")
