"""Tone mapping: redistribute what the captured JPEG already holds.

Luminance-only CLAHE, blended into the original at a configurable strength.
One pass gives both shadow lift and local contrast with a single meaningful
parameter; working on the L channel alone leaves chroma untouched, so colours
do not shift. The 50% default blend keeps the result subtle and close to
idempotent -- a second application moves pixels far less than the first.

Timelapse frames must not flicker, so nothing here makes per-frame choices
from thresholds: CLAHE's parameters are fixed, and the night guard is a
smooth fade (full strength above L=45, zero below L=35) rather than an
on/off skip that would visibly toggle as a scene hovers around the line.
Lifting the shadows of a 20-second night exposure amplifies sensor noise
into something worse than the crushed shadows were.

cv2 is imported inside the function: it costs over a second on a Pi, CI does
not have it, and the config seam has already verified it exists before any
frame gets here.
"""

import os
import tempfile
from typing import Optional

from raspilapse.logging_setup import get_logger

logger = get_logger("dynrange")

# CLAHE is deliberately not configurable: clipLimit 2.0 on an 8x8 tile grid
# is a gentle setting, and the one exposed knob (strength) already scales the
# whole effect. More sliders would just be more ways to overdo it.
_CLIP_LIMIT = 2.0
_TILE_GRID = (8, 8)

# The night fade. Below L=35 the frame is a long-exposure night sky and tone
# mapping would amplify noise; above L=45 it gets the configured strength;
# between the two it ramps linearly so consecutive twilight frames never
# straddle an on/off edge.
_FADE_FLOOR = 35.0
_FADE_CEILING = 45.0


def effective_strength(strength: float, mean_luminance: float) -> float:
    """The strength actually applied to a frame of this brightness."""
    if strength <= 0:
        return 0.0
    span = _FADE_CEILING - _FADE_FLOOR
    fade = (mean_luminance - _FADE_FLOOR) / span
    return float(strength) * min(max(fade, 0.0), 1.0)


def tone_map_file(image_path: str, strength: float, quality: int = 85) -> bool:
    """Tone-map a JPEG in place. Returns True when the file was processed.

    "Processed" includes the deliberate no-ops (zero strength, a frame dark
    enough to fade to nothing): the frame is in its intended state. False
    means the frame could not be read or written; the original file is left
    untouched in that case, so a failure costs the polish, never the photo.
    """
    if strength <= 0:
        return True

    try:
        import cv2
    except ImportError as e:  # pragma: no cover - config seam checks first
        logger.warning(f"Tone mapping needs OpenCV: {e}")
        return False

    tmp_path: Optional[str] = None
    try:
        image = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if image is None:
            logger.warning(f"Tone mapping could not decode {image_path}")
            return False

        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        luminance = lab[:, :, 0]

        applied = effective_strength(strength, float(luminance.mean()))
        if applied <= 0:
            logger.debug(f"Tone mapping faded to zero (night frame): {image_path}")
            return True

        clahe = cv2.createCLAHE(clipLimit=_CLIP_LIMIT, tileGridSize=_TILE_GRID)
        lab[:, :, 0] = clahe.apply(luminance)
        mapped = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

        blended = cv2.addWeighted(mapped, applied, image, 1.0 - applied, 0.0)
        del image, lab, mapped

        # Same atomic dance as the overlay's save: a crash mid-write must not
        # leave a half-encoded frame, and mkstemp's 0600 would carry through
        # os.replace onto a file a webserver answers 403 for.
        out_dir = os.path.dirname(os.path.abspath(image_path))
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".tonemap-", suffix=".jpg", dir=out_dir)
        os.fchmod(tmp_fd, 0o644)
        os.close(tmp_fd)
        if not cv2.imwrite(tmp_path, blended, [cv2.IMWRITE_JPEG_QUALITY, int(quality)]):
            logger.warning(f"Tone mapping could not encode {image_path}")
            return False
        os.replace(tmp_path, image_path)
        tmp_path = None
        logger.debug(f"Tone mapped {image_path} at strength {applied:.2f}")
        return True
    except Exception as e:
        logger.warning(f"Tone mapping failed for {image_path}: {e}")
        return False
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
