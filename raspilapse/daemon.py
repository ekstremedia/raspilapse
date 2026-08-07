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

# How far along the exposure ladder the light must move before the white
# balance is worth reading again. 0.05 of the ladder is roughly a third of a
# stop; a dusk transition crosses most of the range and so fires this a couple
# of dozen times, against the 1800-odd frames it spans.
REFERENCE_LADDER_STEP = 0.05

# ...and a floor on how stale the reading may get when the light is not moving,
# for slow changes the ladder does not register: seasons, a dirty lens, a
# streetlight coming on.
REFERENCE_MAX_INTERVAL_FRAMES = 120

# Turns brightness and exposure into the lux figure the overlay shows and the
# graphs plot. Approximate: it puts full daylight in the right order of
# magnitude, around twenty thousand, which is roughly what a light meter reads
# under bright overcast.
#
# This is not the value the old code used, and the scale of the recorded column
# changes here. That is unavoidable and no loss. The figure used to be measured
# from a dedicated shot pinned at 0.2 s, which saturates in daylight, so 368
# thousand rows of this database carry the identical value 887.190349001447 --
# continuity with a constant is not worth preserving. It is now measured from
# the frame the camera actually took, and varies with the light.
LUX_CALIBRATION = 5.0


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
    ladder_position: Optional[float]
    settings: Dict[str, Any]
    # Read the instant decide() returns, so they describe the frame that was
    # about to be taken. That used to be load-bearing: the handover seeding ran
    # a few lines later and overwrote the controller's shutter, gain and ladder
    # position from the last daylight frame's metadata, so diagnostics read
    # afterwards described the seed instead. The seeding is gone -- see
    # exposure.py's module docstring -- and nothing may reintroduce a writer
    # between decide() and here.
    diagnostics: Dict[str, Any]


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

        # Where on the ladder the last white-balance reading was taken, and
        # when. The reading itself goes straight to the controller. See
        # _wants_reference_shot.
        self._reference_position: Optional[float] = None
        self._reference_frame: int = 0
        self._last_raw_lux: Optional[float] = None

        # Polar awareness - sun position for high latitude locations (68°N)
        self._location = None
        self._sun_elevation: float = None  # Current sun elevation in degrees
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

            self._location = LocationInfo(
                name="Timelapse Location",
                region="",
                timezone=tz,
                latitude=lat,
                longitude=lon,
            )
            logger.info(f"[Sun] Location: {lat}°N, {lon}°E - elevation will be recorded")
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

    # _is_polar_day used to live here. It forced Day mode whenever the sun was
    # above civil twilight, overriding the lux-based mode decision entirely.
    #
    # Its stated purpose -- "capture twilight colours with AWB instead of
    # locked night settings" -- had not been true for a long time: Day mode
    # sets AwbEnable to 0 like every other mode. What it actually did was pin
    # gain at its floor and skip the night exposure floor, and it existed
    # because absolute lux thresholds do not survive being moved to another
    # latitude. The ladder has no thresholds to override.

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
        controller_diagnostics: Dict = None,
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
            controller_diagnostics: What the controller decided, as captured
                when it decided it rather than read back afterwards

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
                "sun_elevation": (
                    round(self._sun_elevation, 2) if self._sun_elevation is not None else None
                ),
            }

            # Everything the controller decided, as it stood when it decided
            # it. This used to re-run the whole exposure calculation from
            # scratch, and then -- worse -- to read the controller's live state
            # after the handover seeding had already moved it.
            diagnostics.update(controller_diagnostics or {})

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

    def _wants_reference_shot(self) -> bool:
        """Whether to interrupt the loop for a white-balance reference frame.

        This used to fire on every frame, and it is expensive: the running
        camera has to be torn down and a second one opened, because libcamera
        will not have two. Two extra open/close cycles, thirty times a minute,
        for the whole life of the installation.

        It fired that often because the shot did two jobs. One of them -- a lux
        figure -- is now taken from the frame the camera just captured, which
        already carries everything the calculation needs. What is left is the
        white balance: this is the only frame taken with AWB enabled, so its
        ColourGains are the only reading of what the scene's white actually is,
        and there is no way to get one without asking the ISP.

        Colour changes when the light changes, so that is what triggers it:
        movement along the exposure ladder since the last reading. Over a dusk
        transition the ladder travels most of its range and this fires perhaps
        twenty times; through a stable afternoon, not at all until the refresh
        interval comes round.

        Nothing the reference shot reads reaches a delivered frame directly.
        The controller keeps it as the daylight white point for a camera that
        has not configured `fixed_colour_gains`, and cross-fades towards it at
        wb_transition_speed like any other target. Where the gains are
        configured, this whole path is dead weight and `test_shot.enabled: false`
        turns it off.
        """
        reference = self.config["adaptive_timelapse"]["test_shot"]
        if not reference.get("enabled", True):
            return False

        if self._reference_position is None:
            return True

        moved = abs(self.exposure.ladder_position - self._reference_position)
        if moved >= REFERENCE_LADDER_STEP:
            return True

        return self.frame_count - self._reference_frame >= REFERENCE_MAX_INTERVAL_FRAMES

    def _take_reference_shot(self) -> None:
        """Read the scene's white balance, the one thing only the ISP knows."""
        try:
            _, metadata = self.take_test_shot()
        except Exception as e:
            # Not fatal: without a fresh reading the controller keeps the last
            # one, and the colour drifts slowly rather than stopping.
            logger.warning(f"White-balance reference shot failed: {e}")
            # Record the attempt anyway. Leaving the position untouched keeps
            # _wants_reference_shot() true, so a persistent failure -- a busy
            # camera, a permissions problem -- would tear the live camera down
            # and fail to open a second one on every frame, forever.
            self._reference_position = self.exposure.ladder_position
            self._reference_frame = self.frame_count
            return

        # This is the AWB frame, so this is the only place the daylight
        # reference can honestly be learned. The controller ignores readings
        # taken away from the bright end, where AWB has nothing to go on.
        #
        # Guarded because this runs inside the capture loop, whose only
        # handler is the one that ends it. A malformed reading is worth a line
        # in the log, not the end of the timelapse.
        try:
            self.exposure.update_day_wb_reference(metadata)
        except Exception as e:
            logger.debug(f"Could not apply WB reference: {e}")

        self._reference_position = self.exposure.ladder_position
        self._reference_frame = self.frame_count
        logger.debug(
            f"[WB] Reference at ladder {self._reference_position:.3f}: "
            f"{metadata.get('ColourGains')}"
        )

    def _decide(self) -> Decision:
        """Choose settings for the next frame."""
        # Recorded, not consulted. Sun elevation is an interesting thing to have
        # alongside a frame at 68°N, and it is what graph_solar_patterns.py
        # plots against, but nothing decides anything from it any more.
        self._get_sun_elevation()

        settings = self.exposure.decide()
        mode = self.exposure.last_mode

        # Read straight after decide(), so the diagnostics describe the frame
        # they were recorded with. That used to be an ordering constraint
        # rather than a fact: _seed_across_mode_change ran between these two
        # and overwrote the shutter, gain and ladder position the diagnostics
        # report, so every handover frame recorded the seed instead of its own
        # exposure. The seeding is gone (see exposure.py's module docstring),
        # so there is nothing left to race -- but keep the read here anyway.
        decision = Decision(
            mode=mode,
            lux=self.exposure.smoothed_lux,
            raw_lux=self._last_raw_lux,
            ladder_position=self.exposure.ladder_position,
            settings=settings,
            diagnostics=self.exposure.diagnostics(),
        )

        return decision

    def _measure_lux(self, brightness: Optional[float], settings: Dict) -> Optional[float]:
        """Estimate ambient light from the frame that was just taken.

        Same arithmetic the metering shot used, on a frame the camera was
        taking anyway:

            lux = (brightness / 128) * (1 / seconds) * (1 / gain) * calibration

        Nothing decides from this. It is written to the database, shown in the
        overlay and plotted by the graph scripts, which is why it survives at
        all -- and why taking it from a dedicated shot, at the cost of two
        camera restarts a frame, stopped being worth it.

        A caveat this inherits honestly: the figure is now read off a frame the
        controller exposed to hit a brightness target, so once converged it
        varies with the settings rather than independently of them. The old
        dedicated shot was no better. Pinned at 0.2 s it saturated in daylight,
        which is why 368k rows of this database carry the identical value
        887.190349001447.
        """
        exposure_us = settings.get("ExposureTime")
        gain = settings.get("AnalogueGain")

        # `is None`, not falsy: a frame measuring 0.0 is a real reading of a
        # very dark scene and should record a lux near zero, not no lux at all.
        if brightness is None or exposure_us is None or gain is None:
            return None

        seconds = exposure_us / 1_000_000
        if seconds <= 0 or gain <= 0:
            return None

        return (brightness / 128.0) * (1.0 / seconds) * (1.0 / gain) * LUX_CALIBRATION

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
                controller_diagnostics=decision.diagnostics,
            )

        capture_metadata = self._read_capture_metadata(metadata_path)

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

                # The reference shot opens its own context-managed camera, and
                # libcamera will not have two, so the running one has to go
                # first. This is why it is worth firing rarely.
                if self._wants_reference_shot():
                    if capture is not None:
                        self._close_camera_fast(capture, last_mode)
                        capture = None
                        last_mode = None
                    self._take_reference_shot()

                try:
                    decision = self._decide()
                except Exception as e:
                    # exc_info: _decide spans the feedback loop, the ladder, the
                    # handover seeding and white balance. Without a traceback
                    # the message alone cannot say which of them failed.
                    logger.error(f"Exposure decision failed: {e}", exc_info=True)
                    break

                # New settings mean a new camera. They can only be applied as
                # it starts: pushing them to a running one with set_controls
                # looks like it should work and does not. Measured on this
                # hardware, a commanded exposure took eight frames to appear in
                # the returned metadata, and four consecutive captures after a
                # change all came back carrying the value from two commands
                # earlier. At a 20-second night exposure, waiting that out
                # would be 160 seconds against a teardown costing about two.
                #
                # Skipping the teardown when the settings barely move was tried
                # and removed. The sensor quantises exposure to whole lines --
                # it delivers 210us for a commanded 217us -- so the command and
                # the delivery differ by more than any useful tolerance, on
                # every frame, and the branch never fired. The saving here is
                # the metering shot, which was the other camera cycle.
                if capture is not None:
                    self._close_camera_fast(capture, last_mode)
                    capture = None

                if capture is None:
                    try:
                        logger.debug("Initializing camera for timelapse...")
                        capture = ImageCapture(self.camera_config, post_process=self._overlay)
                        capture.initialize_camera(manual_controls=decision.settings)
                        last_mode = decision.mode
                    except Exception as e:
                        # Tolerated like a failed frame rather than fatal. The
                        # camera is opened and closed once per frame now, so a
                        # device that is briefly still busy after the previous
                        # teardown is an ordinary event -- and outside this try
                        # it would reach the outer handler and stop the daemon.
                        logger.error(f"Camera initialisation failed: {e}", exc_info=True)
                        capture = None
                        time.sleep(min(interval, 5))
                        continue

                try:
                    image_path, metadata_path = self.capture_frame(
                        capture, decision.mode, decision.lux
                    )
                    logger.info(f"Frame captured: {image_path}")

                    brightness_metrics = self._observe(capture, image_path)

                    # Lux comes off the frame that was just taken, not off a
                    # shot of its own. From the settings the sensor actually
                    # used, not the ones it was asked for: it quantises
                    # exposure to whole lines, and pairing a measured
                    # brightness with an exposure that was never applied put
                    # the figure out by an order of magnitude.
                    applied = self._read_capture_metadata(metadata_path) or decision.settings
                    measured = (brightness_metrics or {}).get("mean_brightness")
                    self._last_raw_lux = self._measure_lux(measured, applied)
                    if self._last_raw_lux is not None:
                        decision.lux = self.exposure.smooth_lux(self._last_raw_lux)
                        decision.raw_lux = self._last_raw_lux

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
