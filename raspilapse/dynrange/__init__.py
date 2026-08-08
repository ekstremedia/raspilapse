"""Selectable dynamic-range methods for the capture pipeline.

A single exposure cannot hold both ends of a high-contrast sky, so the
metering loop compensates by underexposing (highlight protection) -- which is
what crushes the shadows. Each method here gives the pipeline more range than
one exposure carries:

    fusion      -- bracket the exposure 2-3 times per slot, merge with
                   exposure fusion; converges to a plain single shot as
                   exposures lengthen toward night
    sensor_hdr  -- the imx708's on-chip HDR (quarter resolution, day only)
    raw         -- develop the sensor's DNG instead of keeping the ISP's JPEG
    tone_map    -- redistribute what the single JPEG already holds

``DynamicRange`` is the seam, mirroring ``build_overlay``: the daemon builds
one instance and asks it for the post-process chain and per-frame capture
behaviour, and ``method: off`` reproduces the plain pipeline exactly.
Optional dependencies (OpenCV, rawpy) are detected without importing them,
and a method whose dependency is missing degrades to ``off`` with a single
warning -- a camera must keep photographing whatever its config asks for.

This block replaces ``adaptive_timelapse.hdr``, which set libcamera's
``HdrMode`` control -- a control the Pi 4's imx708 never acts on (its on-chip
HDR is switched through V4L2, before the camera opens).
"""

import importlib.util
from typing import Callable, Dict, Optional

from raspilapse.logging_setup import get_logger
from raspilapse.overlay import build_overlay

logger = get_logger("dynrange")

__all__ = ["DynamicRange", "METHODS"]

METHODS = ("off", "fusion", "sensor_hdr", "raw", "tone_map")

# Import-level requirements per method, checked with find_spec so the check is
# free. The real imports stay inside the functions that use them: cv2 alone
# takes over a second to import on a Pi, and CI has neither package.
_REQUIRES = {
    "fusion": ("cv2",),
    "tone_map": ("cv2",),
    "raw": ("rawpy", "cv2"),
}

_APT_PACKAGES = {"cv2": "python3-opencv", "rawpy": "python3-rawpy"}


class DynamicRange:
    """The parsed ``adaptive_timelapse.dynamic_range`` block, plus dispatch.

    Construction never raises on bad configuration: an unknown method or a
    missing dependency logs one warning and runs as ``off``.
    """

    def __init__(self, config: Dict):
        adaptive = config.get("adaptive_timelapse", {}) or {}
        if "hdr" in adaptive:
            logger.warning(
                "adaptive_timelapse.hdr is gone -- it set a control this "
                "sensor never acted on. Use adaptive_timelapse.dynamic_range."
            )

        block = adaptive.get("dynamic_range", {}) or {}
        method = str(block.get("method", "off")).lower()
        if method not in METHODS:
            logger.warning(
                f"Unknown dynamic_range.method {method!r} "
                f"(one of: {', '.join(METHODS)}); running with 'off'"
            )
            method = "off"

        missing = [d for d in _REQUIRES.get(method, ()) if importlib.util.find_spec(d) is None]
        if missing:
            packages = " ".join(_APT_PACKAGES[d] for d in missing)
            logger.warning(
                f"dynamic_range.method '{method}' needs "
                f"{' and '.join(missing)} (sudo apt install {packages}); "
                "running with 'off'"
            )
            method = "off"

        self.method = method
        self._block = block

    @classmethod
    def from_config(cls, config: Dict) -> "DynamicRange":
        """Build from the full configuration dictionary."""
        return cls(config)

    def label(self) -> str:
        """Short name of what is actually running, for metadata and overlay.

        Reports the degraded reality, not the configured wish: a camera whose
        config asks for fusion without OpenCV installed labels its frames
        ``off``, so the trial's records stay honest.
        """
        return self.method

    def pre_open(self, mode: Optional[str]) -> Dict:
        """Adjustments to apply before the camera opens for this frame.

        Returns keyword arguments for ``ImageCapture.initialize_camera``.
        Methods that reconfigure the sensor (sensor_hdr) or need extra
        streams (raw) contribute here; the rest return nothing.
        """
        return {}

    def build_post_process(self, config: Dict) -> Optional[Callable[..., object]]:
        """Return the frame's post-process chain, or None for nothing to do.

        With every stage off this is exactly ``build_overlay(config)`` --
        the same callable the daemon used before this seam existed, so
        ``method: off`` is provably the old pipeline.
        """
        return build_overlay(config)
