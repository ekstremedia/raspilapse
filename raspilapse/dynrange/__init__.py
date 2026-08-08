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

# Import-level requirements per capture method, checked with find_spec so the
# check is free. The real imports stay inside the functions that use them: cv2
# alone takes over a second to import on a Pi, and CI has neither package.
# tone_map is absent here because it is not a capture method -- it normalises
# to a post-process stage below and its cv2 check happens there.
_REQUIRES = {
    "fusion": ("cv2",),
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

        # Tone mapping is a post-process stage, not a capture method, so it
        # combines with any of the others. `method: tone_map` is sugar for
        # `method: off` plus `tone_map.enabled: true`.
        tone_map = block.get("tone_map", {}) or {}
        tone_map_enabled = bool(tone_map.get("enabled", False)) or method == "tone_map"
        if method == "tone_map":
            method = "off"
        strength = tone_map.get("strength", 0.5)
        self._tone_map_strength = min(max(float(strength), 0.0), 1.0)

        fusion_cfg = block.get("fusion", {}) or {}
        self._fusion_brackets = min(max(int(fusion_cfg.get("brackets", 3)), 2), 3)
        self._fusion_ev_spread = min(max(float(fusion_cfg.get("ev_spread", 2.0)), 0.0), 4.0)
        self._fusion_single_shot_above_s = max(
            float(fusion_cfg.get("single_shot_above_s", 0.5)), 0.01
        )
        self._interval_s = float(adaptive.get("interval", 30))
        self._quality = int((config.get("output", {}) or {}).get("quality", 85))

        # Frames a bracket discards while its controls land, seeded with the
        # figure measured on a running camera and refined from what the
        # brackets actually need. Only the budget guard reads it.
        self._settle_ema = 8.0

        missing = [d for d in _REQUIRES.get(method, ()) if importlib.util.find_spec(d) is None]
        if missing:
            packages = " ".join(_APT_PACKAGES[d] for d in missing)
            logger.warning(
                f"dynamic_range.method '{method}' needs "
                f"{' and '.join(missing)} (sudo apt install {packages}); "
                "running with 'off'"
            )
            method = "off"

        if tone_map_enabled and importlib.util.find_spec("cv2") is None:
            logger.warning(
                "dynamic_range.tone_map needs cv2 "
                "(sudo apt install python3-opencv); running without it"
            )
            tone_map_enabled = False

        self.method = method
        self.tone_map_enabled = tone_map_enabled
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
        if self.tone_map_enabled:
            return "tone_map" if self.method == "off" else f"{self.method}+tm"
        return self.method

    def pre_open(self, mode: Optional[str]) -> Dict:
        """Adjustments to apply before the camera opens for this frame.

        Returns keyword arguments for ``ImageCapture.initialize_camera``.
        Methods that reconfigure the sensor (sensor_hdr) or need extra
        streams (raw) contribute here; the rest return nothing.
        """
        return {}

    def capture_frame(
        self,
        capture,
        mode: Optional[str] = None,
        settings: Optional[Dict] = None,
        extra_metadata: Optional[Dict] = None,
    ):
        """Capture one frame the way the active method dictates.

        Everything except an actively-bracketing fusion frame falls through
        to ``ImageCapture.capture``: off, tone_map, sensor_hdr, raw and a
        converged fusion all share the plain, battle-tested path. That is
        also what makes fusion's night behaviour smooth -- once the plan
        collapses to one exposure there is no fusion code left running.
        """
        if self.method == "fusion" and settings:
            plan = self._fusion_plan(settings)
            if len(plan) > 1:
                from raspilapse.dynrange import fusion

                merged = dict(extra_metadata or {})
                merged["fusion_exposures_us"] = [int(t * 1_000_000) for t in plan]
                result = capture.capture_bracketed(
                    merged["fusion_exposures_us"],
                    fusion.build_fuse_fn(self._quality),
                    mode=mode,
                    extra_metadata=merged,
                )
                self._observe_settle(capture.last_settle_frames)
                return result
        return capture.capture(mode=mode, extra_metadata=extra_metadata)

    def _fusion_plan(self, settings: Dict) -> list:
        """The bracket exposures (seconds, base first) for this decision."""
        from raspilapse.dynrange import fusion

        base_s = settings.get("ExposureTime", 0) / 1_000_000
        if base_s <= 0:
            return [base_s]
        return fusion.plan_brackets(
            base_s,
            self._fusion_brackets,
            self._fusion_ev_spread,
            self._fusion_single_shot_above_s,
            self._interval_s,
            round(self._settle_ema),
        )

    def _observe_settle(self, settle_frames) -> None:
        """Fold what the last bracket run measured into the settle estimate."""
        if settle_frames:
            self._settle_ema += 0.3 * (max(settle_frames) - self._settle_ema)

    def build_post_process(self, config: Dict) -> Optional[Callable[..., object]]:
        """Return the frame's post-process chain, or None for nothing to do.

        With every stage off this is exactly ``build_overlay(config)`` --
        the same callable the daemon used before this seam existed, so
        ``method: off`` is provably the old pipeline. With tone mapping on,
        the chain tone-maps the saved frame first and then hands the same
        arguments to the overlay: the overlay bar must be drawn on the
        mapped image, never mapped itself.
        """
        overlay = build_overlay(config)
        if not self.tone_map_enabled:
            return overlay

        from raspilapse.dynrange import tonemap

        strength = self._tone_map_strength
        quality = config.get("output", {}).get("quality", 85)

        def chain(image_path, metadata, mode, output_path=None):
            processed = tonemap.tone_map_file(str(image_path), strength, quality)
            if overlay is not None:
                return overlay(image_path, metadata, mode, output_path=output_path)
            return processed

        return chain
