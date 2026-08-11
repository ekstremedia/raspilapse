"""Burned-in overlay. Entirely optional; nothing in the capture path needs it.

`build_overlay` is the seam. Import it from anywhere -- it costs nothing --
and it imports the renderer, and therefore Pillow, only when the overlay is
actually switched on. The capture path used to import the renderer at module
level, which made Pillow a hard requirement of taking a photo even with
`overlay.enabled: false`.
"""

from typing import Callable, Dict, Optional

from raspilapse.logging_setup import get_logger

logger = get_logger("overlay")

__all__ = ["build_overlay"]


def build_overlay(config: Dict) -> Optional[Callable[..., object]]:
    """Return a callable that burns the overlay into a frame, or None.

    None means "do not draw" -- either the overlay is switched off, or Pillow
    is missing. Both are ordinary states, not errors, so the caller writes

        if self._overlay is not None:
            self._overlay(path, metadata, mode)

    and nothing else in the capture path has to know the overlay exists.

    Args:
        config: Full configuration dictionary

    Returns:
        ImageOverlay.apply_overlay, bound to a configured instance, or None
    """
    if not config.get("overlay", {}).get("enabled", False):
        return None

    try:
        from raspilapse.overlay.render import ImageOverlay
    except ImportError as e:
        # Pillow is the only way this fails. Say so once, at a level someone
        # running in production will actually see, and carry on capturing --
        # a missing overlay is not a reason to stop taking photographs.
        logger.warning(f"Overlay is enabled but cannot be drawn: {e}")
        return None

    return ImageOverlay(config).apply_overlay
