"""Adaptive timelapse module for Raspilapse.

Automatically adjusts exposure settings based on ambient light conditions.
Perfect for 24/7 timelapses that capture both daylight and nighttime scenes,
including stars and aurora activity.
"""

import json
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

# Optional: Sun position calculation for polar regions
try:
    from astral import LocationInfo
    from astral.sun import elevation

    ASTRAL_AVAILABLE = True
except ImportError:
    ASTRAL_AVAILABLE = False


from raspilapse.camera.capture import CameraConfig, ImageCapture

# BrightnessZones and highlight_factor are re-exported for callers and
# tests that still import them from here.
from raspilapse.camera.exposure import (  # noqa: F401
    BrightnessZones,
    ExposureController,
    LightMode,
    highlight_factor,
)
from raspilapse.config import merge_defaults
from raspilapse.logging_setup import configure_logging, get_logger
from raspilapse.overlay import build_overlay
from raspilapse.overlay.sources.weather import WeatherData
from raspilapse.storage.database import CaptureDatabase
from raspilapse.system import SystemMonitor

# Initialize logger
logger = get_logger("auto_timelapse")


@dataclass
class Decision:
    """What the controller decided for one frame, and what it decided it from.

    The loop used to carry these as six separate locals threaded through 290
    lines, initialised to None at the top so the later diagnostics call would
    not NameError when a branch skipped them.
    """

    mode: str
    lux: Optional[float]
    raw_lux: Optional[float]
    transition_position: Optional[float]
    settings: Dict[str, Any]


class AdaptiveTimelapse:
    """Handles adaptive timelapse capture with automatic exposure adjustment."""

    def __init__(self, config_path: str = "config/config.yml"):
        """
        Initialize adaptive timelapse.

        Args:
            config_path: Path to configuration file
        """
        self.config_path = config_path
        self.config = self._load_config()
        self.camera_config = CameraConfig(config_path)
        self.running = True
        self.frame_count = 0

        # All exposure decisions and their state live here.
        self.exposure = ExposureController(self.config)

        self._previous_mode: str = None  # Track mode changes for seeding detection
        self._last_day_capture_metadata: Dict = None  # Metadata from last day mode capture

        # Polar awareness - sun position for high latitude locations (68°N)
        self._location = None
        self._sun_elevation: float = None  # Current sun elevation in degrees
        self._civil_twilight_threshold = -6.0  # Default: Civil twilight
        self._init_location()

        # Database storage for capture history
        self._database = None
        self._init_database()

        # System monitor for CPU temp and load (for database storage)
        self._system_monitor = None
        if SystemMonitor is not None:
            try:
                self._system_monitor = SystemMonitor()
            except Exception as e:
                logger.debug(f"[System] Failed to initialize monitor: {e}")

        # Weather is a data source, not an overlay feature: the database has
        # columns for it whether or not anything is drawn. This used to be read
        # through capture.overlay.weather, which tied the weather columns to a
        # setting that has nothing to do with them.
        #
        # The cache in the weather module is process-wide and keyed by
        # endpoint, so this instance and the overlay's share it -- two
        # instances, one HTTP request.
        self._weather = WeatherData(self.config)

        # None unless the overlay is switched on, and Pillow is only imported
        # in the case where it is.
        self._overlay = build_overlay(self.config)

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False

    def _init_location(self):
        """Initialize location for sun position calculations (Polar awareness)."""
        if not ASTRAL_AVAILABLE:
            logger.debug("Astral not available - sun position features disabled")
            return

        location_config = self.config.get("location", {})
        if not location_config:
            logger.debug("No location configured - sun position features disabled")
            return

        try:
            lat = location_config.get("latitude", 68.7)
            lon = location_config.get("longitude", 15.4)
            tz = location_config.get("timezone", "Europe/Oslo")
            self._civil_twilight_threshold = location_config.get("civil_twilight_threshold", -6.0)

            self._location = LocationInfo(
                name="Timelapse Location",
                region="",
                timezone=tz,
                latitude=lat,
                longitude=lon,
            )
            logger.info(
                f"[Polar] Location initialized: {lat}°N, {lon}°E "
                f"(Civil twilight threshold: {self._civil_twilight_threshold}°)"
            )
        except Exception as e:
            logger.warning(f"Could not initialize location: {e}")
            self._location = None

    def _init_database(self):
        """Initialize database storage for capture history."""
        if CaptureDatabase is None:
            logger.debug("[DB] CaptureDatabase not available")
            return

        db_config = self.config.get("database", {})
        if not db_config.get("enabled", False):
            logger.debug("[DB] Database storage disabled in config")
            return

        try:
            self._database = CaptureDatabase(self.config)
            stats = self._database.get_statistics()
            if stats.get("enabled"):
                logger.info(
                    f"[DB] Initialized: {stats.get('db_path', 'unknown')}, "
                    f"captures={stats.get('total_captures', 0)}"
                )
                # Seed exposure settings from last capture to prevent brightness flash on restart
                self._seed_from_last_capture()
        except Exception as e:
            logger.warning(f"[DB] Failed to initialize database: {e}")
            self._database = None

    def _seed_from_last_capture(self):
        """
        Seed exposure settings from the last database capture.

        This prevents the "brightness flash" that occurs after a reboot or service restart
        where the first few frames are severely over/underexposed because the system
        starts with no knowledge of the previous exposure settings.

        Called during initialization after the database is ready.
        """
        if self._database is None:
            return

        try:
            last_capture = self._database.get_last_capture()
            if last_capture is None:
                logger.debug("[Startup] No previous capture found in database")
                return

            # Extract exposure settings from last capture
            exposure_us = last_capture.get("exposure_time_us")
            analogue_gain = last_capture.get("analogue_gain")
            colour_gains_r = last_capture.get("colour_gains_r")
            colour_gains_b = last_capture.get("colour_gains_b")
            last_mode = last_capture.get("mode")
            last_brightness = last_capture.get("brightness_mean")
            last_lux = last_capture.get("lux")

            has_exposure = exposure_us is not None and exposure_us > 0
            has_gain = analogue_gain is not None and analogue_gain > 0
            has_wb = colour_gains_r is not None and colour_gains_b is not None

            if not (has_exposure or has_gain or has_wb):
                logger.debug("[Startup] Last capture had no usable exposure data")
                return

            seed_exposure = exposure_us / 1_000_000 if has_exposure else None
            seed_gains = (colour_gains_r, colour_gains_b) if has_wb else None

            self.exposure.seed_from_capture(
                exposure_time=seed_exposure,
                analogue_gain=analogue_gain if has_gain else None,
                colour_gains=seed_gains,
                brightness=last_brightness,
                lux=last_lux,
                mode=last_mode,
            )

            parts = []
            if seed_exposure:
                parts.append(f"exposure={seed_exposure:.4f}s")
            if has_gain:
                parts.append(f"gain={analogue_gain:.2f}")
            if seed_gains:
                parts.append(f"WB=[{seed_gains[0]:.2f}, {seed_gains[1]:.2f}]")
            if last_mode:
                parts.append(f"mode={last_mode}")
            if last_brightness is not None:
                parts.append(f"brightness={last_brightness:.1f}")
            logger.info(f"[Startup] Seeded from last capture: {', '.join(parts)}")

        except Exception as e:
            logger.warning(f"[Startup] Failed to seed from last capture: {e}")

    def _get_sun_elevation(self) -> Optional[float]:
        """
        Calculate current sun elevation angle in degrees.

        Returns:
            Sun elevation in degrees (positive = above horizon, negative = below)
            None if location not configured or calculation fails
        """
        if not ASTRAL_AVAILABLE or self._location is None:
            return None

        try:
            now = datetime.now(timezone.utc)
            self._sun_elevation = elevation(self._location.observer, now)
            return self._sun_elevation
        except Exception as e:
            logger.debug(f"Could not calculate sun elevation: {e}")
            return None

    def _is_polar_day(self, lux: float = None) -> bool:
        """
        Check if we're in Polar Day conditions (Civil Twilight override).

        In polar regions, even when lux is low, we should stay in Day mode
        if the sun is above the civil twilight threshold (-6°) to capture
        beautiful twilight colors with AWB instead of locked night settings.

        Args:
            lux: Current measured lux (for logging)

        Returns:
            True if sun elevation indicates Polar Day (civil twilight or brighter)
        """
        sun_elev = self._get_sun_elevation()
        if sun_elev is None:
            return False

        is_polar_day = sun_elev > self._civil_twilight_threshold
        if is_polar_day:
            logger.debug(
                f"[Polar] Civil twilight override: Sun={sun_elev:.1f}° > {self._civil_twilight_threshold}° "
                f"(forcing Day mode despite lux={f'{lux:.1f}' if lux is not None else 'N/A'})"
            )
        return is_polar_day

    def _load_config(self) -> Dict:
        """Load configuration from YAML file."""
        config_file = Path(self.config_path)
        if not config_file.exists():
            logger.error(f"Configuration file not found: {self.config_path}")
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        try:
            with open(config_file, "r") as f:
                config = yaml.safe_load(f) or {}
                logger.debug("Configuration loaded successfully")
                return merge_defaults(config)
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse configuration file: {e}")
            raise

    def calculate_lux(self, test_image_path: str, metadata: Dict) -> float:
        """
        Calculate approximate lux from camera metadata and image brightness.

        This method analyzes the actual image brightness rather than just
        relying on camera metadata, which can be misleading due to auto-exposure.

        Args:
            test_image_path: Path to test shot image
            metadata: Camera metadata from capture

        Returns:
            Estimated lux value
        """
        exposure_time = metadata.get("ExposureTime", 10000)  # microseconds
        analogue_gain = metadata.get("AnalogueGain", 1.0)

        # Convert exposure time to seconds
        exposure_seconds = exposure_time / 1_000_000

        # Analyze image brightness
        try:
            import numpy as np
            from PIL import Image

            # Open image and convert to grayscale
            img = Image.open(test_image_path)
            img_gray = img.convert("L")  # Convert to grayscale
            img_array = np.array(img_gray)

            # Calculate mean brightness (0-255)
            mean_brightness = np.mean(img_array)

            # Calculate lux based on brightness and camera settings
            # The brighter the image with less exposure time/gain, the more ambient light
            # Formula: lux = (mean_brightness / 128) * (1 / exposure_seconds) * (1 / gain) * calibration_factor
            calibration_factor = 100.0

            if exposure_seconds > 0 and analogue_gain > 0:
                # Normalized brightness (0.0 to 2.0, where 1.0 is mid-gray)
                brightness_factor = mean_brightness / 128.0

                lux = (
                    brightness_factor
                    * (1.0 / exposure_seconds)
                    * (1.0 / analogue_gain)
                    * calibration_factor
                )
            else:
                lux = 1000.0  # Very bright

            logger.debug(
                f"Image analysis: brightness={mean_brightness:.1f}/255, "
                f"exposure={exposure_time}µs, gain={analogue_gain:.2f} → lux={lux:.2f}"
            )

        except ImportError:
            # Fall back to metadata-only calculation if PIL not available
            logger.warning("PIL not available, using metadata-only lux calculation")
            if exposure_seconds > 0:
                lux = (100.0 / exposure_seconds) / analogue_gain
            else:
                lux = 1000.0

            logger.debug(
                f"Metadata-only lux: {lux:.2f} (exposure: {exposure_time}µs, gain: {analogue_gain})"
            )

        except Exception as e:
            logger.error(f"Error analyzing image brightness: {e}")
            # Fallback calculation
            lux = 50.0

        return lux

    def take_test_shot(self) -> Tuple[str, Dict]:
        """
        Take a quick test shot to measure light levels.

        Returns:
            Tuple of (image_path, metadata)
        """
        logger.debug("Taking test shot to measure light levels...")

        test_config = self.config["adaptive_timelapse"]["test_shot"]

        # Temporarily modify camera config for test shot
        original_controls = self.camera_config.config["camera"].get("controls", {})
        original_save_metadata = self.camera_config.config["system"]["save_metadata"]

        # Set test shot controls
        self.camera_config.config["camera"]["controls"] = {
            "exposure_time": int(test_config["exposure_time"] * 1_000_000),
            "analogue_gain": test_config["analogue_gain"],
            "awb_enable": True,
        }

        # CRITICAL: Disable metadata saving for test shots to prevent timestamped
        # metadata files from accumulating in metadata/ folder
        # Test shots are only for measuring light levels, not part of timelapse
        self.camera_config.config["system"]["save_metadata"] = False

        # Create metadata directory (files get overwritten, not accumulated)
        metadata_dir = Path(self.config.get("system", {}).get("metadata_folder", "metadata"))
        metadata_dir.mkdir(exist_ok=True)

        # Capture test image (overwritten each time - no timestamps)
        # Since save_metadata=False, this won't create timestamped metadata files
        metadata = {}
        with ImageCapture(self.camera_config) as capture:
            test_path = metadata_dir / "test_shot.jpg"

            # Capture test image using capture_request to get metadata directly
            import json

            try:
                request = capture.picam2.capture_request()
                try:
                    # Save image
                    request.save("main", str(test_path))
                    # Get metadata from request
                    metadata = request.get_metadata()
                    # Save test shot metadata manually with fixed filename (overwritten each time)
                    test_metadata_path = metadata_dir / "test_shot_metadata.json"
                    with open(test_metadata_path, "w") as f:
                        json.dump(metadata, f, indent=2, default=str)
                    logger.debug(f"Test shot metadata saved: {test_metadata_path}")
                finally:
                    request.release()
            except Exception as e:
                logger.warning(f"Could not capture test shot with metadata: {e}")
                metadata = {}

            # Set image_path for return value
            image_path = str(test_path)

        # Restore original settings
        self.camera_config.config["camera"]["controls"] = original_controls
        self.camera_config.config["system"]["save_metadata"] = original_save_metadata

        logger.debug(f"Test shot saved: {image_path}")
        return image_path, metadata

    def _analyze_image_brightness(self, image_path: str) -> Dict:
        """
        Analyze brightness characteristics of a captured image.

        Calculates histogram statistics to help diagnose exposure issues.

        Args:
            image_path: Path to the image file

        Returns:
            Dictionary with brightness metrics
        """
        try:
            import numpy as np
            from PIL import Image

            with Image.open(image_path) as img:
                # Convert to grayscale for brightness analysis
                gray = img.convert("L")
                pixels = np.array(gray)

                # Calculate statistics
                mean_brightness = float(np.mean(pixels))
                median_brightness = float(np.median(pixels))
                std_brightness = float(np.std(pixels))

                # Percentiles for exposure analysis
                p5 = float(np.percentile(pixels, 5))
                p25 = float(np.percentile(pixels, 25))
                p75 = float(np.percentile(pixels, 75))
                p95 = float(np.percentile(pixels, 95))

                # Calculate under/overexposure percentages
                total_pixels = pixels.size
                underexposed = float(np.sum(pixels < 10) / total_pixels * 100)
                overexposed = float(np.sum(pixels > 245) / total_pixels * 100)

                return {
                    "mean_brightness": round(mean_brightness, 2),
                    "median_brightness": round(median_brightness, 2),
                    "std_brightness": round(std_brightness, 2),
                    "percentile_5": round(p5, 2),
                    "percentile_25": round(p25, 2),
                    "percentile_75": round(p75, 2),
                    "percentile_95": round(p95, 2),
                    "underexposed_percent": round(underexposed, 2),
                    "overexposed_percent": round(overexposed, 2),
                }

        except Exception as e:
            logger.warning(f"Could not analyze image brightness: {e}")
            return {}

    def _enrich_metadata_with_diagnostics(
        self,
        metadata_path: str,
        image_path: str,
        mode: str,
        lux: float = None,
        raw_lux: float = None,
        transition_position: float = None,
    ) -> bool:
        """
        Enrich saved metadata with diagnostic information.

        Adds brightness analysis, exposure calculation details, and mode state
        to help with future tuning and debugging.

        Args:
            metadata_path: Path to the metadata JSON file
            image_path: Path to the captured image
            mode: Current light mode
            lux: Smoothed lux value
            raw_lux: Raw lux value before smoothing
            transition_position: Position in transition (0-1), None if not transition

        Returns:
            True if successful, False otherwise
        """
        import json

        try:
            # Load existing metadata
            with open(metadata_path, "r") as f:
                metadata = json.load(f)

            # Add diagnostics section
            diagnostics = {
                "mode": mode,
                "smoothed_lux": round(lux, 4) if lux is not None else None,
                "raw_lux": round(raw_lux, 4) if raw_lux is not None else None,
                "transition_position": (
                    round(transition_position, 4) if transition_position is not None else None
                ),
                "sun_elevation": (
                    round(self._sun_elevation, 2) if self._sun_elevation is not None else None
                ),
            }

            # Everything the controller decided, recorded when it decided it.
            # This used to re-run the whole exposure calculation from scratch.
            diagnostics.update(self.exposure.diagnostics())

            # Analyze image brightness
            brightness_analysis = self._analyze_image_brightness(image_path)
            if brightness_analysis:
                diagnostics["brightness"] = brightness_analysis

            # Add diagnostics to metadata
            metadata["diagnostics"] = diagnostics

            # Save enriched metadata
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=2, default=str)

            logger.debug(f"Enriched metadata with diagnostics: {metadata_path}")
            return True

        except Exception as e:
            logger.warning(f"Could not enrich metadata with diagnostics: {e}")
            return False

    def _create_latest_symlink(self, image_path: str):
        """
        Create a symlink to the latest captured image.

        Args:
            image_path: Path to the latest image
        """
        symlink_config = self.config.get("output", {}).get("symlink_latest", {})
        if not symlink_config.get("enabled", False):
            return

        symlink_path = symlink_config.get("path")
        if not symlink_path:
            logger.warning("Symlink enabled but no path specified")
            return

        try:
            symlink_path = Path(symlink_path)
            image_path = Path(image_path).resolve()  # Get absolute path

            # Remove existing symlink/file if it exists
            if symlink_path.exists() or symlink_path.is_symlink():
                symlink_path.unlink()

            # Create new symlink
            symlink_path.symlink_to(image_path)
            logger.debug(f"Created symlink: {symlink_path} -> {image_path}")

        except PermissionError:
            logger.error(
                f"Permission denied creating symlink at {symlink_path}. "
                f"You may need to run with sudo or adjust permissions."
            )
        except Exception as e:
            logger.error(f"Failed to create symlink: {e}")

    def capture_frame(
        self, capture: ImageCapture, mode: str, calculated_lux: float = None
    ) -> Tuple[str, Optional[str]]:
        """
        Capture a single frame with the camera's current settings.

        Args:
            capture: ImageCapture instance with initialized camera
            mode: Light mode
            calculated_lux: Calculated lux value to use in overlay (overrides camera's estimate)

        Returns:
            Tuple of (image_path, metadata_path)
        """
        logger.info(f"Capturing frame #{self.frame_count} in {mode} mode...")

        # Prepare extra metadata with calculated lux (overrides camera's unreliable estimate)
        extra_metadata = {}
        if calculated_lux is not None:
            extra_metadata["Lux"] = calculated_lux

        # Capture the image (controls were set during initialization)
        # Pass mode so overlay knows the light mode, and calculated lux for accurate display
        image_path, metadata_path = capture.capture(
            mode=mode, extra_metadata=extra_metadata if extra_metadata else None
        )

        # Create symlink to latest image if enabled
        self._create_latest_symlink(image_path)

        self.frame_count += 1
        return image_path, metadata_path

    def _close_camera_fast(self, capture: ImageCapture, last_mode: str):
        """
        Close camera properly.

        Args:
            capture: ImageCapture instance to close
            last_mode: Last light mode used (for logging)
        """
        if capture is None or capture.picam2 is None:
            return

        try:
            # Close the camera
            capture.close()
            logger.debug("Camera closed successfully")

        except Exception as e:
            logger.error(f"Error during close: {e}")

    def _wants_test_shot(self) -> bool:
        """Whether this frame should be preceded by a metering shot."""
        test_shot = self.config["adaptive_timelapse"]["test_shot"]
        frequency = test_shot.get("frequency", 1)
        return bool(test_shot["enabled"]) and self.frame_count % frequency == 0

    def _meter(self) -> Decision:
        """Measure the light and decide what the camera should do.

        Takes the fixed-settings test shot, turns it into a lux figure, picks a
        mode and asks the controller for settings. Everything here is decision;
        nothing touches the frame that gets kept.
        """
        test_image_path, test_metadata = self.take_test_shot()
        raw_lux = self.calculate_lux(test_image_path, test_metadata)

        # On the first frame after a restart the ISP may not have applied the
        # test shot's settings yet, and the result comes back saturated. A
        # seeded lux from the database is a better estimate than one measured
        # from a blown frame.
        if self.frame_count == 0:
            raw_lux = self._prefer_seeded_lux_if_saturated(test_image_path, raw_lux)

        lux = self.exposure.smooth_lux(raw_lux)

        # Keep this on its own line. It is what populates self._sun_elevation,
        # and Python evaluates arguments left to right: inlined into the call
        # below, the attribute was read before the call that fills it, and the
        # first frame after every restart passed None.
        is_polar_day = self._is_polar_day(lux)
        raw_mode = self.exposure.determine_mode(lux, self._sun_elevation, is_polar_day)
        mode = self.exposure.apply_hysteresis(raw_mode)

        position = self.exposure.transition_position(lux) if mode == LightMode.TRANSITION else None

        self._seed_across_mode_change(mode, test_metadata)
        if mode == LightMode.TRANSITION and position is not None:
            self.exposure.log_transition_progress(lux, position)
        self._previous_mode = mode

        return Decision(
            mode=mode,
            lux=lux,
            raw_lux=raw_lux,
            transition_position=position,
            settings=self.exposure.get_camera_settings(mode, lux),
        )

    def _prefer_seeded_lux_if_saturated(self, test_image_path: str, raw_lux: float) -> float:
        """Fall back to the seeded lux when the first test shot comes back blown."""
        test_brightness = self._analyze_image_brightness(test_image_path)
        if not test_brightness:
            return raw_lux

        test_mean = test_brightness.get("mean_brightness", 128)
        if (
            test_mean > 250
            and self.exposure.seed_exposure is not None
            and self.exposure.smoothed_lux is not None
        ):
            logger.warning(
                f"[Startup] First test shot saturated ({test_mean:.1f}/255) - "
                f"using seeded lux={self.exposure.smoothed_lux:.1f} "
                f"instead of calculated={raw_lux:.1f}"
            )
            return self.exposure.smoothed_lux
        return raw_lux

    def _seed_across_mode_change(self, mode: str, test_metadata: Dict) -> None:
        """Hand exposure state across the day/night boundary.

        Leaving day means leaving the only frames taken with AWB on, so the
        controller is primed from the test shot's metadata to make the first
        manual frame match the last automatic one.
        """
        entering_manual = self._previous_mode == LightMode.DAY and mode in (
            LightMode.TRANSITION,
            LightMode.NIGHT,
        )
        if entering_manual and not self.exposure.transition_seeded:
            self.exposure.seed_from_metadata(test_metadata, self._last_day_capture_metadata)

        if mode == LightMode.DAY and self._previous_mode != LightMode.DAY:
            self.exposure.reset_seed_state()
            logger.info("[Holy Grail] Returned to Day mode - seed state reset")

    def _reuse_last_decision(self) -> Decision:
        """What to shoot when no test shot was taken this frame."""
        mode = self.exposure.last_mode or LightMode.DAY
        lux = self.exposure.smoothed_lux
        return Decision(
            mode=mode,
            lux=lux,
            raw_lux=None,
            transition_position=None,
            settings=self.exposure.get_camera_settings(mode, lux),
        )

    def _observe(self, capture: ImageCapture, image_path: str) -> Optional[Dict]:
        """Feed the frame the camera just produced back to the controller.

        This is the whole input to the next frame's exposure: measured
        brightness, highlight level, the dynamic target and the over/under
        flags. Lores first -- it costs no disk read and carries no overlay.
        """
        feedback = (
            self.config.get("adaptive_timelapse", {})
            .get("transition_mode", {})
            .get("brightness_feedback_enabled", True)
        )
        if not feedback:
            return None

        try:
            metrics = capture.last_brightness_metrics or self._analyze_image_brightness(image_path)
            if metrics:
                self.exposure.observe_frame(metrics)
            return metrics
        except Exception as e:
            # Losing this means the controller reacts to a stale measurement
            # forever, so it is not a debug-level event.
            logger.warning(f"Could not apply brightness feedback: {e}")
            return None

    def _read_capture_metadata(self, metadata_path: Optional[str]) -> Optional[Dict]:
        """Read the frame's metadata JSON once; two callers below want it."""
        if not metadata_path:
            return None
        try:
            with open(metadata_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.debug(f"Could not read capture metadata: {e}")
            return None

    def _record(
        self,
        decision: Decision,
        image_path: str,
        metadata_path: Optional[str],
        brightness_metrics: Optional[Dict],
    ) -> None:
        """Everything that happens to a frame after it has been taken."""
        diagnostics = (
            self.config.get("adaptive_timelapse", {}).get("diagnostics", {}).get("enabled", False)
        )
        if metadata_path and diagnostics:
            self._enrich_metadata_with_diagnostics(
                metadata_path=metadata_path,
                image_path=image_path,
                mode=decision.mode,
                lux=decision.lux,
                raw_lux=decision.raw_lux,
                transition_position=decision.transition_position,
            )

        capture_metadata = self._read_capture_metadata(metadata_path)

        # Daylight is where the camera learns what neutral looks like, and that
        # reference is what the night handover interpolates away from.
        if capture_metadata is not None and decision.mode == LightMode.DAY:
            try:
                self.exposure.update_day_wb_reference(capture_metadata)
                self._last_day_capture_metadata = capture_metadata
            except Exception as e:
                logger.debug(f"Could not apply WB reference: {e}")

        if self._database is None:
            return

        try:
            self._database.store_capture(
                image_path=image_path,
                metadata=capture_metadata if capture_metadata is not None else {},
                mode=decision.mode,
                lux=decision.lux,
                brightness_metrics=brightness_metrics,
                weather_data=self._weather.get_weather_data(),
                sun_elevation=self._sun_elevation,
                system_metrics=(
                    self._system_monitor.get_all_metrics() if self._system_monitor else None
                ),
            )
        except Exception as e:
            # Not debug: this silently lost every row for anyone who turned the
            # overlay off, because the failure was an AttributeError nobody saw.
            logger.warning(f"[DB] Failed to store capture: {e}")

    def run(self, test_mode: bool = False):
        """Run the adaptive timelapse capture loop.

        Args:
            test_mode: If True, capture one image then exit
        """
        adaptive_config = self.config["adaptive_timelapse"]

        if not adaptive_config.get("enabled", True):
            logger.warning("Adaptive timelapse is disabled in configuration")
            return

        interval = adaptive_config["interval"]
        num_frames = 1 if test_mode else adaptive_config["num_frames"]

        logger.info("=== Adaptive Timelapse Started ===")
        logger.info(f"Interval: {interval} seconds")
        logger.info(f"Frames: {'unlimited' if num_frames == 0 else num_frames}")

        capture = None
        last_mode = None

        try:
            while self.running:
                loop_start = time.time()

                if num_frames > 0 and self.frame_count >= num_frames:
                    logger.info(f"Reached frame limit: {num_frames}")
                    break

                metering = self._wants_test_shot()

                # The test shot opens its own context-managed camera, and
                # libcamera refuses a second one while this is running.
                if capture is not None and metering:
                    logger.debug("Closing camera before test shot...")
                    self._close_camera_fast(capture, last_mode)
                    capture = None
                    last_mode = None

                if metering:
                    try:
                        decision = self._meter()
                    except Exception as e:
                        # exc_info: _meter spans lux, mode, hysteresis, WB
                        # seeding and settings. Without a traceback the message
                        # alone cannot say which of them failed, and the frame
                        # silently falls back to the last mode.
                        logger.error(f"Test shot failed: {e}", exc_info=True)
                        decision = self._reuse_last_decision()
                else:
                    decision = self._reuse_last_decision()
                    lux_str = f"{decision.lux:.2f}" if decision.lux is not None else "N/A"
                    logger.debug(
                        f"Skipping test shot (frame {self.frame_count}), "
                        f"reusing mode={decision.mode}, lux={lux_str}"
                    )

                if capture is None:
                    logger.debug("Initializing camera for timelapse...")
                    capture = ImageCapture(self.camera_config, post_process=self._overlay)
                    capture.initialize_camera(manual_controls=decision.settings)
                    last_mode = decision.mode

                try:
                    image_path, metadata_path = self.capture_frame(
                        capture, decision.mode, decision.lux
                    )
                    logger.info(f"Frame captured: {image_path}")

                    brightness_metrics = self._observe(capture, image_path)
                    self._record(decision, image_path, metadata_path, brightness_metrics)
                except Exception as e:
                    logger.error(f"Frame capture failed: {e}", exc_info=True)

                elapsed = time.time() - loop_start
                sleep_time = max(0, interval - elapsed)
                if sleep_time > 0:
                    logger.debug(f"Sleeping for {sleep_time:.1f} seconds...")
                    time.sleep(sleep_time)
                else:
                    logger.warning(
                        f"Capture took longer than interval ({elapsed:.1f}s > {interval}s)"
                    )

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
        finally:
            if capture is not None:
                logger.info("Closing camera...")
                self._close_camera_fast(capture, last_mode)

            logger.info(f"=== Adaptive Timelapse Stopped ({self.frame_count} frames) ===")


def main():
    """CLI entry point for adaptive timelapse."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Adaptive timelapse for Raspberry Pi Camera - automatically adjusts exposure for day/night"
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config/config.yml",
        help="Path to configuration file (default: config/config.yml)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test mode: capture one image then exit",
    )

    args = parser.parse_args()
    configure_logging(args.config)

    logger.info("Starting Raspilapse Adaptive Timelapse")

    try:
        timelapse = AdaptiveTimelapse(args.config)
        if args.test:
            logger.info("TEST MODE: Capturing single image then exiting")
            timelapse.run(test_mode=True)
        else:
            timelapse.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
