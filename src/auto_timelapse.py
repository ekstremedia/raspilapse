"""Adaptive timelapse module for Raspilapse.

Automatically adjusts exposure settings based on ambient light conditions.
Perfect for 24/7 timelapses that capture both daylight and nighttime scenes,
including stars and aurora activity.
"""

import os
import sys
import time
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import yaml

# Optional: Sun position calculation for polar regions
try:
    from astral import LocationInfo
    from astral.sun import elevation

    ASTRAL_AVAILABLE = True
except ImportError:
    ASTRAL_AVAILABLE = False

# Handle imports for both module and script execution
try:
    from src.logging_config import configure_logging, get_logger
    from src.capture_image import CameraConfig, ImageCapture
    from src.database import CaptureDatabase
    from src.system_monitor import SystemMonitor
except ImportError:
    from logging_config import configure_logging, get_logger
    from capture_image import CameraConfig, ImageCapture

    try:
        from database import CaptureDatabase
    except ImportError:
        CaptureDatabase = None  # Database module not available

    try:
        from system_monitor import SystemMonitor
    except ImportError:
        SystemMonitor = None  # System monitor not available

# Initialize logger
logger = get_logger("auto_timelapse")


class LightMode:
    """Light mode enumeration."""

    NIGHT = "night"
    DAY = "day"
    TRANSITION = "transition"


class BrightnessZones:
    """Brightness landmarks on the 0-255 scale.

    WARNING_HIGH and WARNING_LOW drive the hybrid mode override in
    determine_mode: when the lux reading and the measured brightness disagree
    badly, brightness wins.
    """

    EMERGENCY_HIGH = 180  # Severe overexposure
    WARNING_HIGH = 160  # Moderate overexposure
    TARGET = 120  # Ideal brightness
    WARNING_LOW = 80  # Moderate underexposure
    EMERGENCY_LOW = 60  # Severe underexposure
    CRITICAL_LOW = 40  # Critical underexposure, e.g. Arctic twilight


def highlight_factor(
    p95: Optional[float],
    *,
    safe: float = 200.0,
    warning: float = 220.0,
    critical: float = 240.0,
    floor: float = 0.70,
) -> float:
    """
    How much headroom the highlights still have, as a multiplier in [floor, 1.0].

    From the Raspberry Pi camera algorithm guide: the top few percent of pixels
    should stay at or below roughly 0.8 of full scale. Reducing exposure as p95
    approaches saturation prevents clipping rather than correcting it after the
    highlights are already gone.

    Pure: no logging, no state. It used to log at WARNING from inside the
    calculation, which is how one condition came to account for 95% of the log.

    Args:
        p95: 95th percentile brightness (0-255), or None
        safe: Below this, full headroom -- returns exactly 1.0
        warning: Gentle reduction between safe and here
        critical: Moderate reduction between warning and here; aggressive above
        floor: Hard lower bound on the returned factor

    Returns:
        1.0 when there is nothing to protect, down to floor when clipping
    """
    if p95 is None:
        return 1.0

    if p95 <= safe:
        return 1.0

    if p95 <= warning:
        # safe -> 1.00, warning -> 0.95
        return 1.0 - ((p95 - safe) / (warning - safe)) * 0.05

    if p95 <= critical:
        # warning -> 0.95, critical -> 0.85
        return 0.95 - ((p95 - warning) / (critical - warning)) * 0.10

    # critical -> 0.85, and downhill from there to the floor
    return max(floor, 0.85 - ((p95 - critical) / 15) * 0.15)


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

        # Transition smoothing state
        self._smoothed_lux: float = None  # Exponential moving average of lux
        self._last_mode: str = None  # Previous mode for hysteresis
        self._mode_hold_count: int = 0  # Counter for hysteresis
        self._day_wb_reference: tuple = None  # AWB gains from bright daylight
        self._last_colour_gains: tuple = None  # Previous frame's color gains for smooth transition
        self._last_analogue_gain: float = None  # Previous frame's analogue gain for smooth ISO
        self._last_exposure_time: float = (
            None  # Previous frame's exposure time for smooth transition
        )

        # Brightness feedback state for smooth transitions
        self._last_brightness: float = None  # Previous frame's mean brightness
        self._last_p95: float = None  # Previous frame's 95th percentile (highlight level)

        # Highlight protection: scales the brightness target down when the
        # top of the histogram approaches clipping. See _highlight_target_scale.
        hp = self.config.get("adaptive_timelapse", {}).get("highlight_protection", {})
        self._p95_enabled = hp.get("enabled", False)
        self._p95_safe = hp.get("safe_p95", 200)
        self._p95_warning = hp.get("warning_p95", 220)
        self._p95_critical = hp.get("critical_p95", 240)
        self._p95_floor = hp.get("min_scale", 0.70)
        self._p95_slew = hp.get("slew", 0.25)
        self._p95_apply_in_night = hp.get("apply_in_night", False)
        self._p95_scale: float = 1.0
        self._last_highlight_scale: float = 1.0

        # What get_camera_settings decided for the current frame, for the
        # metadata diagnostics. Written by the branch that ran, read by
        # _enrich_metadata_with_diagnostics -- no recomputation.
        self._last_decision: Dict = {}

        # Overexposure detection for fast ramp-down
        self._overexposure_detected: bool = False  # True when image is overexposed
        self._overexposure_severity: str = None  # "warning" or "critical"

        # Underexposure detection for fast recovery (symmetric to overexposure)
        self._underexposure_detected: bool = (
            False  # True when image is underexposed at min exposure
        )
        self._underexposure_severity: str = None  # "warning" or "critical"

        # Holy Grail transition state - seeded from actual camera metadata
        self._transition_seeded: bool = False  # True once we've seeded from metadata
        self._seed_exposure: float = None  # Actual exposure from last auto frame
        self._seed_gain: float = None  # Actual gain from last auto frame
        self._seed_wb_gains: tuple = None  # Actual WB gains from last auto frame
        self._previous_mode: str = None  # Track mode changes for seeding detection
        self._last_day_capture_metadata: Dict = None  # Metadata from last day mode capture

        # Load transition smoothing config with defaults
        transition_config = self.config.get("adaptive_timelapse", {}).get("transition_mode", {})
        self._lux_smoothing_factor = transition_config.get("lux_smoothing_factor", 0.3)
        self._hysteresis_frames = transition_config.get("hysteresis_frames", 3)
        self._wb_transition_speed = transition_config.get("wb_transition_speed", 0.15)
        self._gain_transition_speed = transition_config.get("gain_transition_speed", 0.15)
        self._exposure_transition_speed = transition_config.get("exposure_transition_speed", 0.15)

        # Brightness feedback config
        self._target_brightness = transition_config.get("target_brightness", 120)
        self._brightness_tolerance = transition_config.get("brightness_tolerance", 40)
        self._brightness_feedback_strength = transition_config.get(
            "brightness_feedback_strength", 0.3
        )

        # Contrast-aware brightness target config (overcast boost)
        adaptive_config = self.config.get("adaptive_timelapse", {})
        bt_config = adaptive_config.get("brightness_target", {})
        self._base_target_brightness = bt_config.get("base", 120)
        self._overcast_boost = bt_config.get("overcast_boost", 15)
        self._max_target_brightness = bt_config.get("max_target", 140)
        self._contrast_threshold_low = bt_config.get("contrast_threshold_low", 25)
        self._contrast_threshold_high = bt_config.get("contrast_threshold_high", 40)

        # HDR config
        hdr_config = adaptive_config.get("hdr", {})
        self._hdr_enabled = hdr_config.get("enabled", False)
        self._hdr_day_mode = hdr_config.get("day_mode", "SingleExposure")
        self._hdr_night_mode = hdr_config.get("night_mode", "Off")
        self._hdr_enum_available = False
        if self._hdr_enabled:
            try:
                import libcamera

                self._hdr_mode_enum = libcamera.controls.HdrModeEnum
                self._hdr_enum_available = True
                logger.info(
                    f"[HDR] Enabled: day={self._hdr_day_mode}, night={self._hdr_night_mode}"
                )
            except (ImportError, AttributeError):
                logger.info(
                    "[HDR] HdrModeEnum not available (Pi 4/vc4) - HDR controls will be no-op"
                )

        # Fast ramp-down speed for overexposure correction (default 0.30 = 3x normal speed)
        self._fast_rampdown_speed = transition_config.get("fast_rampdown_speed", 0.30)
        # Critical ramp-down speed for severe overexposure (default 0.70 = very aggressive)
        self._critical_rampdown_speed = transition_config.get("critical_rampdown_speed", 0.70)

        # Fast ramp-up speeds for underexposure correction (symmetric to ramp-down)
        self._fast_rampup_speed = transition_config.get("fast_rampup_speed", 0.50)
        self._critical_rampup_speed = transition_config.get("critical_rampup_speed", 0.70)

        # Polar awareness - sun position for high latitude locations (68°N)
        self._location = None
        self._sun_elevation: float = None  # Current sun elevation in degrees
        self._civil_twilight_threshold = -6.0  # Default: Civil twilight
        self._init_location()

        self._frame_interval = self.config.get("adaptive_timelapse", {}).get("interval", 30)

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

            seeded = False

            # Seed exposure time
            if exposure_us is not None and exposure_us > 0:
                self._last_exposure_time = exposure_us / 1_000_000  # Convert to seconds
                self._seed_exposure = self._last_exposure_time
                seeded = True

            # Seed analogue gain
            if analogue_gain is not None and analogue_gain > 0:
                self._last_analogue_gain = analogue_gain
                self._seed_gain = analogue_gain
                seeded = True

            # Seed white balance
            if colour_gains_r is not None and colour_gains_b is not None:
                self._last_colour_gains = (colour_gains_r, colour_gains_b)
                self._seed_wb_gains = (colour_gains_r, colour_gains_b)
                seeded = True

            # Seed brightness for feedback loop
            if last_brightness is not None:
                self._last_brightness = last_brightness

            # Seed lux for mode determination
            if last_lux is not None:
                self._smoothed_lux = last_lux

            # Seed mode
            if last_mode is not None:
                self._last_mode = last_mode

            if seeded:
                logger.info(
                    f"[Startup] Seeded from last capture: "
                    f"exposure={self._last_exposure_time:.4f}s, "
                    f"gain={self._last_analogue_gain:.2f}, "
                    f"WB=[{self._last_colour_gains[0]:.2f}, {self._last_colour_gains[1]:.2f}], "
                    f"mode={last_mode}, brightness={last_brightness:.1f}"
                )
            else:
                logger.debug("[Startup] Last capture had no usable exposure data")

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
                config = yaml.safe_load(f)
                logger.debug("Configuration loaded successfully")
                return config
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse configuration file: {e}")
            raise

    def _smooth_lux(self, raw_lux: float) -> float:
        """
        Apply exponential moving average smoothing to lux values.

        This prevents sudden jumps in lux readings from causing mode flips.

        Args:
            raw_lux: Raw calculated lux value

        Returns:
            Smoothed lux value
        """
        if self._smoothed_lux is None:
            # First reading - initialize
            self._smoothed_lux = raw_lux
        else:
            # Exponential moving average: new = alpha * raw + (1 - alpha) * old
            alpha = self._lux_smoothing_factor
            self._smoothed_lux = alpha * raw_lux + (1 - alpha) * self._smoothed_lux

        logger.debug(f"Lux smoothing: raw={raw_lux:.2f} → smoothed={self._smoothed_lux:.2f}")
        return self._smoothed_lux

    def _apply_hysteresis(self, new_mode: str) -> str:
        """
        Apply hysteresis to mode transitions to prevent rapid flipping.

        Mode only changes after N consecutive frames request the same new mode.

        Args:
            new_mode: The mode determined by current lux

        Returns:
            The actual mode to use (may be held at previous)
        """
        if self._last_mode is None:
            # First frame - accept the mode
            self._last_mode = new_mode
            self._mode_hold_count = 0
            return new_mode

        if new_mode == self._last_mode:
            # Same mode - reset counter
            self._mode_hold_count = 0
            return new_mode

        # Different mode requested
        self._mode_hold_count += 1

        if self._mode_hold_count >= self._hysteresis_frames:
            # Enough consecutive frames - accept the change
            logger.info(
                f"Mode transition: {self._last_mode} → {new_mode} "
                f"(after {self._mode_hold_count} frames)"
            )
            self._last_mode = new_mode
            self._mode_hold_count = 0
            return new_mode
        else:
            # Hold at previous mode
            logger.debug(
                f"Hysteresis: holding {self._last_mode}, "
                f"requested {new_mode} ({self._mode_hold_count}/{self._hysteresis_frames})"
            )
            return self._last_mode

    def _interpolate_colour_gains(self, target_gains: tuple, position: float = None) -> tuple:
        """
        Smoothly interpolate colour gains to prevent sudden white balance shifts.

        Uses gradual transition towards target gains rather than instant switching.

        Args:
            target_gains: Target (red, blue) colour gains
            position: Optional transition position (0.0=night, 1.0=day) for
                     calculating intermediate gains between night and day references

        Returns:
            Interpolated colour gains tuple
        """
        if target_gains is None:
            return self._last_colour_gains

        if self._last_colour_gains is None:
            # First frame - accept target gains
            self._last_colour_gains = target_gains
            return target_gains

        # Gradual transition towards target
        speed = self._wb_transition_speed
        new_red = self._last_colour_gains[0] + speed * (
            target_gains[0] - self._last_colour_gains[0]
        )
        new_blue = self._last_colour_gains[1] + speed * (
            target_gains[1] - self._last_colour_gains[1]
        )

        interpolated = (new_red, new_blue)
        self._last_colour_gains = interpolated

        logger.debug(
            f"WB interpolation: target=[{target_gains[0]:.2f}, {target_gains[1]:.2f}] "
            f"→ actual=[{new_red:.2f}, {new_blue:.2f}]"
        )
        return interpolated

    def _interpolate_gain(
        self, target_gain: float, speed_override: Optional[float] = None
    ) -> float:
        """
        Smoothly interpolate analogue gain to prevent sudden ISO jumps.

        Uses gradual transition towards target gain rather than instant switching.

        Args:
            target_gain: Target analogue gain value
            speed_override: Optional speed override (0.0-1.0) for faster transitions

        Returns:
            Interpolated gain value
        """
        if target_gain is None:
            return self._last_analogue_gain

        if self._last_analogue_gain is None:
            # First frame - accept target gain
            self._last_analogue_gain = target_gain
            return target_gain

        # Gradual transition towards target
        speed = speed_override if speed_override is not None else self._gain_transition_speed
        new_gain = self._last_analogue_gain + speed * (target_gain - self._last_analogue_gain)

        # Clamp to valid range
        new_gain = max(1.0, min(16.0, new_gain))

        self._last_analogue_gain = new_gain

        logger.debug(
            f"Gain interpolation: target={target_gain:.2f} → actual={new_gain:.2f}"
            + (f" (fast: {speed:.2f})" if speed_override is not None else "")
        )
        return new_gain

    def _interpolate_exposure(
        self, target_exposure_s: float, speed_override: float = None
    ) -> float:
        """
        Smoothly interpolate exposure time to prevent sudden brightness jumps.

        Uses gradual transition towards target exposure rather than instant switching.

        Args:
            target_exposure_s: Target exposure time in seconds
            speed_override: Optional speed override (0.0-1.0) for fast ramp-down

        Returns:
            Interpolated exposure time in seconds
        """
        if target_exposure_s is None:
            return self._last_exposure_time

        if self._last_exposure_time is None:
            # First frame - accept target exposure
            self._last_exposure_time = target_exposure_s
            return target_exposure_s

        # Gradual transition towards target (use logarithmic interpolation for exposure)
        # This gives smoother perceived brightness changes
        import math

        speed = speed_override if speed_override is not None else self._exposure_transition_speed

        # Log-space interpolation for more natural exposure transitions
        log_last = math.log10(max(0.0001, self._last_exposure_time))
        log_target = math.log10(max(0.0001, target_exposure_s))
        log_new = log_last + speed * (log_target - log_last)
        new_exposure = 10**log_new

        # Clamp to valid range (100µs to 20s)
        new_exposure = max(0.0001, min(20.0, new_exposure))

        self._last_exposure_time = new_exposure

        logger.debug(
            f"Exposure interpolation: target={target_exposure_s:.4f}s → actual={new_exposure:.4f}s"
            + (f" (fast: {speed:.2f})" if speed_override else "")
        )
        return new_exposure

    def _get_dynamic_target_brightness(self, std_brightness: float) -> int:
        """
        Calculate dynamic brightness target based on image contrast (std deviation).

        On overcast days, images have low contrast (low std_brightness) and look
        flat/dark at the normal target of 120. This method boosts the target up to
        max_target when contrast is low, making overcast images brighter.

        On sunny days (high contrast), the target stays at the base value.
        In NIGHT mode, always returns the base target to protect aurora/star captures.

        Args:
            std_brightness: Standard deviation of image brightness (0-255 scale).
                           High values (~50) = sunny/contrasty, low values (~20) = overcast/flat.

        Returns:
            Dynamic brightness target (base to max_target).
        """
        # Always use base target in night mode (protects aurora/star captures)
        if self._last_mode == LightMode.NIGHT:
            return self._base_target_brightness

        # If std_brightness is missing or invalid, return base target
        if std_brightness is None or std_brightness < 0:
            return self._base_target_brightness

        low = self._contrast_threshold_low  # Below this = full boost (overcast)
        high = self._contrast_threshold_high  # Above this = no boost (sunny)

        if std_brightness >= high:
            # High contrast (sunny) - no boost
            return self._base_target_brightness
        elif std_brightness <= low:
            # Low contrast (overcast) - full boost
            boosted = self._base_target_brightness + self._overcast_boost
            return min(boosted, self._max_target_brightness)
        else:
            # Linear interpolation between thresholds
            # At low threshold: full boost, at high threshold: no boost
            t = (std_brightness - low) / (high - low)
            boost = self._overcast_boost * (1.0 - t)
            boosted = self._base_target_brightness + boost
            return min(int(round(boosted)), self._max_target_brightness)

    def _get_rampdown_speed(self) -> float:
        """
        Get the appropriate ramp-down speed based on overexposure severity.

        Returns:
            Speed value for exposure/gain interpolation, or None for normal speed
        """
        if not self._overexposure_detected:
            return None

        if self._overexposure_severity == "critical":
            return self._critical_rampdown_speed
        else:
            return self._fast_rampdown_speed

    def _get_rampup_speed(self) -> float:
        """
        Get the appropriate ramp-up speed based on underexposure severity.

        Returns:
            Speed value for exposure/gain interpolation, or None for normal speed
        """
        if not self._underexposure_detected:
            return None

        if self._underexposure_severity == "critical":
            return self._critical_rampup_speed
        else:
            return self._fast_rampup_speed

    def _check_overexposure(self, brightness_metrics: Dict) -> bool:
        """
        Check if the image is overexposed and update fast ramp-down state.

        Uses two-tier detection:
        - WARNING level (brightness > 150): Moderate correction
        - CRITICAL level (brightness > 170): Aggressive correction

        Triggers fast ramp-down when:
        - Mean brightness > 150 (warning - early detection)
        - OR overexposed_percent > 5% (many clipped pixels)

        Clears fast ramp-down when:
        - Mean brightness < 130 (back to safe range)
        - AND overexposed_percent < 3%

        Args:
            brightness_metrics: Dictionary with brightness analysis results

        Returns:
            True if overexposure detected (fast ramp-down active)
        """
        if not brightness_metrics:
            return self._overexposure_detected

        mean_brightness = brightness_metrics.get("mean_brightness", 0)
        overexposed_pct = brightness_metrics.get("overexposed_percent", 0)

        # Thresholds - lowered for earlier detection
        brightness_warning = 150  # Early warning threshold
        brightness_critical = 170  # Critical overexposure
        brightness_safe = 130  # Clear fast ramp-down below this
        overexposed_warning = 5  # Trigger if >5% pixels clipped
        overexposed_safe = 3  # Clear if <3% pixels clipped

        was_overexposed = self._overexposure_detected

        if mean_brightness > brightness_critical or overexposed_pct > overexposed_warning * 2:
            # Critical overexposure - activate fast ramp-down
            self._overexposure_detected = True
            self._overexposure_severity = "critical"
            if not was_overexposed:
                logger.warning(
                    f"[FastRamp] CRITICAL OVEREXPOSURE: brightness={mean_brightness:.1f}, "
                    f"clipped={overexposed_pct:.1f}% - activating aggressive ramp-down"
                )
        elif mean_brightness > brightness_warning or overexposed_pct > overexposed_warning:
            # Warning level overexposure - activate moderate fast ramp-down
            self._overexposure_detected = True
            self._overexposure_severity = "warning"
            if not was_overexposed:
                logger.warning(
                    f"[FastRamp] OVEREXPOSURE WARNING: brightness={mean_brightness:.1f}, "
                    f"clipped={overexposed_pct:.1f}% - activating fast ramp-down"
                )
        elif mean_brightness < brightness_safe and overexposed_pct < overexposed_safe:
            # Back to safe range - deactivate fast ramp-down
            self._overexposure_detected = False
            self._overexposure_severity = None
            if was_overexposed:
                logger.info(
                    f"[FastRamp] Overexposure cleared: brightness={mean_brightness:.1f}, "
                    f"clipped={overexposed_pct:.1f}% - resuming normal interpolation"
                )

        return self._overexposure_detected

    def _check_underexposure(self, brightness_metrics: Dict) -> bool:
        """
        Check if the image is underexposed and trigger fast ramp-up.

        Uses two-tier detection symmetric to overexposure:
        - WARNING level (brightness < 90): Moderate recovery
        - CRITICAL level (brightness < 70): Aggressive recovery

        Unlike the previous version, this works in ANY mode - not just at
        minimum exposure. This is critical for smooth day-to-night transitions
        where the exposure is ramping UP but lagging behind the light drop.

        Args:
            brightness_metrics: Dictionary with brightness analysis results

        Returns:
            True if underexposure detected (fast recovery active)
        """
        if not brightness_metrics:
            return self._underexposure_detected

        mean_brightness = brightness_metrics.get("mean_brightness", 128)

        # Thresholds for underexposure detection (lowered for faster response)
        brightness_warning = 90  # Early warning (target is 120)
        brightness_critical = 70  # Critical underexposure
        brightness_safe = 105  # Clear underexposure above this

        was_underexposed = self._underexposure_detected

        if mean_brightness < brightness_critical:
            # Critical underexposure - activate aggressive fast recovery
            self._underexposure_detected = True
            self._underexposure_severity = "critical"
            if not was_underexposed:
                logger.warning(
                    f"[FastRecovery] CRITICAL UNDEREXPOSURE: brightness={mean_brightness:.1f} "
                    f"- activating aggressive ramp-up"
                )
        elif mean_brightness < brightness_warning:
            # Warning level underexposure - activate moderate fast recovery
            self._underexposure_detected = True
            self._underexposure_severity = "warning"
            if not was_underexposed:
                logger.warning(
                    f"[FastRecovery] UNDEREXPOSURE WARNING: brightness={mean_brightness:.1f} "
                    f"- activating fast ramp-up"
                )
        elif mean_brightness > brightness_safe:
            # Back to safe range - deactivate fast recovery
            self._underexposure_detected = False
            self._underexposure_severity = None
            if was_underexposed:
                logger.info(
                    f"[FastRecovery] Underexposure cleared: brightness={mean_brightness:.1f} "
                    f"- resuming normal interpolation"
                )

        return self._underexposure_detected

    def _highlight_target_scale(self, p95: Optional[float], mode: str) -> float:
        """
        Slew-limited highlight-protection scale for the brightness target.

        The scale multiplies the *target* brightness rather than the
        controller's output. Both settle -- p95 rises monotonically with
        exposure for a fixed scene, so pulling the target down lowers p95,
        which lets the scale relax back toward 1.0 -- but they settle
        differently.

        Scaling the target leaves the loop's own fixed point intact
        (mean == effective target), so the equilibrium depends only on the
        highlight_protection settings. Scaling the output instead makes the
        loop settle where ratio**damping * scale == 1, which ties how much
        protection you actually get to brightness_damping. Simulated across
        damping 0.3-1.0: target-scaling holds mean at 118.0 throughout, while
        output-scaling drifts 116.4 to 118.0. Highlight behaviour should not
        move when an unrelated tuning knob does.

        Args:
            p95: 95th percentile brightness of the previous frame (0-255)
            mode: Current light mode

        Returns:
            Multiplier for the brightness target, in [min_scale, 1.0]
        """
        if not self._p95_enabled:
            return 1.0

        # Night is off by default. Across 117k night frames here the mean
        # brightness is already 90 against a target of 120, while 11% of frames
        # exceed p95 200 -- streetlamps and the moon, not blown scenes. Cutting
        # exposure on those makes aurora frames worse, not better.
        if mode == LightMode.NIGHT and not self._p95_apply_in_night:
            return 1.0

        raw = highlight_factor(
            p95,
            safe=self._p95_safe,
            warning=self._p95_warning,
            critical=self._p95_critical,
            floor=self._p95_floor,
        )

        # Exponential slew, so one noisy p95 sample cannot step the target.
        previous = self._p95_scale
        self._p95_scale += self._p95_slew * (raw - self._p95_scale)

        # Edge-triggered logging only. Level-triggered logging of this exact
        # condition once produced 742 of 777 lines in the log file.
        engaged_before = previous < 0.995
        engaged_now = self._p95_scale < 0.995
        if engaged_now != engaged_before:
            if engaged_now:
                logger.info(
                    f"[Highlight] Protection engaged: p95={p95:.0f}, "
                    f"target scaled to {self._p95_scale:.2f}"
                )
            else:
                logger.info("[Highlight] Protection released")
        else:
            logger.debug(f"[Highlight] p95={p95}, scale={self._p95_scale:.3f}")

        return self._p95_scale

    def _calculate_exposure_from_brightness(
        self, actual_brightness: float, lux: Optional[float] = None, mode: str = LightMode.DAY
    ) -> float:
        """
        Direct proportional brightness control.

        Simple physics: exposure * brightness = constant (for fixed scene)
        Therefore: new_exposure = current_exposure * (target / actual) ^ damping

        This replaces the complex ML+formula+interpolation system with
        a simple, predictable feedback loop that converges in 3-5 frames
        instead of 10+ frames.

        Args:
            actual_brightness: Measured mean brightness (0-255)
            lux: Current lux (used for initial estimate on first frame)

        Returns:
            Target exposure in seconds
        """
        adaptive_config = self.config["adaptive_timelapse"]
        night_max = adaptive_config["night_mode"]["max_exposure_time"]

        # Handle missing or invalid brightness
        # If we have seeded exposure but no brightness yet (startup), use seeded exposure
        if (
            actual_brightness is None or actual_brightness < 1
        ) and self._last_exposure_time is not None:
            logger.warning(
                f"[DirectFB] No brightness data yet, using seeded exposure {self._last_exposure_time:.4f}s"
            )
            return self._last_exposure_time

        if actual_brightness is None or actual_brightness < 1:
            actual_brightness = 1  # Prevent division by zero

        # First frame - use lux-based estimate if available
        if self._last_exposure_time is None:
            if lux is not None and lux > 0:
                reference_lux = adaptive_config.get("reference_lux", 3.8)
                initial = (night_max * reference_lux) / lux
                initial = max(0.0001, min(night_max, initial))
                logger.info(
                    f"[DirectFB] First frame: using lux-based estimate {initial:.4f}s "
                    f"(lux={lux:.1f})"
                )
                return initial
            logger.info("[DirectFB] First frame: using default 20ms")
            return 0.02  # 20ms safe default

        # Get damping factor from config (0.5 = conservative)
        damping = adaptive_config.get("brightness_damping", 0.5)

        # Highlight protection pulls the *target* down when the top of the
        # histogram nears clipping. Scaling the target rather than the result
        # keeps the loop single-equilibrium; see _highlight_target_scale.
        scale = self._highlight_target_scale(self._last_p95, mode)
        effective_target = self._target_brightness * scale

        # Calculate correction ratio
        ratio = effective_target / actual_brightness

        # Clamp ratio to prevent extreme single-frame corrections
        # Max 4x change per frame (ratio^0.5 = 2x actual change with 0.5 damping)
        ratio = max(0.25, min(4.0, ratio))

        # Apply ratio with damping: new = current * ratio^damping
        new_exposure = self._last_exposure_time * (ratio**damping)

        # Clamp to valid range
        new_exposure = max(0.0001, min(night_max, new_exposure))

        self._last_highlight_scale = scale

        # Log significant corrections
        if abs(ratio - 1.0) > 0.1:
            actual_change = ratio**damping
            highlight_note = f", highlight_scale={scale:.2f}" if scale < 0.995 else ""
            logger.info(
                f"[DirectFB] brightness={actual_brightness:.0f}, "
                f"target={effective_target:.0f}, ratio={ratio:.2f}, "
                f"change={actual_change:.2f}x{highlight_note}, "
                f"exp: {self._last_exposure_time:.4f}s → {new_exposure:.4f}s"
            )

        return new_exposure

    def _update_day_wb_reference(self, metadata: Dict):
        """
        Update day white balance reference from camera's AWB in bright conditions.

        This captures what the camera considers correct WB for daylight,
        which we use to smoothly transition from/to night manual WB.

        Args:
            metadata: Camera metadata containing ColourGains
        """
        colour_gains = metadata.get("ColourGains")
        lux = metadata.get("Lux", 0)

        # Only update reference in bright daylight (>200 lux) with valid gains
        if colour_gains and lux > 200:
            # Validate gains are reasonable (not extreme values)
            if 1.0 < colour_gains[0] < 4.0 and 1.0 < colour_gains[1] < 4.0:
                self._day_wb_reference = tuple(colour_gains)
                logger.debug(
                    f"Updated day WB reference: [{colour_gains[0]:.2f}, {colour_gains[1]:.2f}] "
                    f"at {lux:.0f} lux"
                )

    def _seed_from_metadata(self, metadata: Dict, capture_metadata: Dict = None):
        """
        Seed interpolation state from actual camera metadata (Holy Grail technique).

        This captures the REAL camera settings and uses them as the starting point
        for manual control. This eliminates the "flash" that occurs when switching
        from auto to manual mode.

        For WB gains: Uses test shot metadata (AWB is enabled during test shots)
        For exposure/gain: Uses last actual capture metadata (if available) or
        calculates from current lux (already handled by interpolation init)

        Called when entering transition mode from day mode.

        Args:
            metadata: Test shot metadata (has AWB-chosen ColourGains)
            capture_metadata: Optional metadata from last actual capture
        """
        # AWB gains from test shot ARE useful - test shot has AWB enabled
        colour_gains = metadata.get("ColourGains")

        if colour_gains is not None:
            # Validate gains are reasonable
            if 1.0 < colour_gains[0] < 4.0 and 1.0 < colour_gains[1] < 4.0:
                self._seed_wb_gains = tuple(colour_gains)
                # Update day WB reference since this is what AWB chose at transition
                self._day_wb_reference = tuple(colour_gains)
                # DON'T set _last_colour_gains directly - let interpolation continue
                # smoothly from wherever it currently is. This prevents abrupt WB jumps.
                # Only initialize if we don't have any previous gains
                if self._last_colour_gains is None:
                    self._last_colour_gains = tuple(colour_gains)
                    logger.info(
                        f"[Holy Grail] Initialized WB from AWB: "
                        f"[{colour_gains[0]:.2f}, {colour_gains[1]:.2f}]"
                    )
                else:
                    logger.info(
                        f"[Holy Grail] Updated WB reference from AWB: "
                        f"[{colour_gains[0]:.2f}, {colour_gains[1]:.2f}] "
                        f"(interpolating from [{self._last_colour_gains[0]:.2f}, {self._last_colour_gains[1]:.2f}])"
                    )

        # If we have actual capture metadata (from last day mode frame), use its exposure/gain
        if capture_metadata:
            exposure_time_us = capture_metadata.get("ExposureTime")
            analogue_gain = capture_metadata.get("AnalogueGain")

            if exposure_time_us is not None:
                self._seed_exposure = exposure_time_us / 1_000_000
                self._last_exposure_time = self._seed_exposure
                logger.info(
                    f"[Holy Grail] Seeded exposure from last capture: {self._seed_exposure:.4f}s"
                )

            if analogue_gain is not None:
                self._seed_gain = analogue_gain
                self._last_analogue_gain = analogue_gain
                logger.info(f"[Holy Grail] Seeded gain from last capture: {self._seed_gain:.2f}")

        self._transition_seeded = True
        logger.info(
            "[Holy Grail] Transition seeded - AWB locked, "
            "smooth interpolation will prevent flash"
        )

    def _log_transition_progress(self, lux: float, position: float):
        """
        Log transition progress in Holy Grail format.

        Args:
            lux: Current smoothed lux value
            position: Transition position (0.0=night, 1.0=day)
        """
        progress_pct = (1.0 - position) * 100  # 0% at day threshold, 100% at night
        exposure_ms = (self._last_exposure_time or 0) * 1000
        gain = self._last_analogue_gain or 0
        wb_status = "Locked" if self._transition_seeded else "Learning"

        if exposure_ms >= 1000:
            shutter_str = f"{exposure_ms/1000:.1f}s"
        else:
            shutter_str = f"{exposure_ms:.0f}ms"

        logger.info(
            f"[Transition] Progress: {progress_pct:.0f}% | "
            f"Lux: {lux:.1f} | Shutter: {shutter_str} | "
            f"Gain: {gain:.2f} | AWB: {wb_status}"
        )

    def _get_target_colour_gains(self, mode: str, position: float = None) -> tuple:
        """
        Get target colour gains based on mode and transition position.

        For smooth transitions, interpolates between night manual gains
        and day AWB reference gains.

        Args:
            mode: Current light mode
            position: Transition position (0.0=night, 1.0=day), only for transition mode

        Returns:
            Target colour gains tuple (red, blue)
        """
        night_config = self.config["adaptive_timelapse"]["night_mode"]
        night_gains = tuple(night_config.get("colour_gains", [1.83, 2.02]))

        if mode == LightMode.NIGHT:
            return night_gains

        # For day and transition, we need day reference
        # Priority: 1) Fixed config gains, 2) Learned AWB reference, 3) Default
        day_config = self.config["adaptive_timelapse"].get("day_mode", {})
        fixed_gains = day_config.get("fixed_colour_gains")
        if fixed_gains:
            day_gains = tuple(fixed_gains)
        else:
            day_gains = self._day_wb_reference or (2.5, 1.6)

        if mode == LightMode.DAY:
            return day_gains

        # Transition mode - interpolate based on position
        if position is not None:
            # position: 0.0 = at night threshold, 1.0 = at day threshold
            red = night_gains[0] + position * (day_gains[0] - night_gains[0])
            blue = night_gains[1] + position * (day_gains[1] - night_gains[1])
            return (red, blue)

        # Default to midpoint
        return ((night_gains[0] + day_gains[0]) / 2, (night_gains[1] + day_gains[1]) / 2)

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
            from PIL import Image
            import numpy as np

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

    def determine_mode(self, lux: float) -> str:
        """
        Determine light mode based on lux value and sun position.

        Includes Polar Day override: In polar regions, force Day mode when
        sun elevation is above civil twilight threshold (-6°), even if lux
        readings suggest otherwise. This captures twilight colors with AWB.

        Args:
            lux: Calculated lux value

        Returns:
            Light mode (night, day, or transition)
        """
        thresholds = self.config["adaptive_timelapse"]["light_thresholds"]
        night_threshold = thresholds["night"]
        day_threshold = thresholds["day"]

        # === POLAR DAY OVERRIDE ===
        # In polar regions, force Day mode during civil twilight to capture
        # beautiful pink/blue twilight colors with AWB instead of locked night WB
        if self._is_polar_day(lux):
            sun_elev = self._sun_elevation  # Cached from _is_polar_day call
            logger.info(
                f"[Polar] Sun: {sun_elev:.1f}° | Lux: {lux:.1f} | Mode: Polar Day (override)"
            )
            return LightMode.DAY

        # Standard lux-based mode determination
        if lux < night_threshold:
            lux_mode = LightMode.NIGHT
        elif lux > day_threshold:
            lux_mode = LightMode.DAY
        else:
            lux_mode = LightMode.TRANSITION

        mode = lux_mode

        # === HYBRID BRIGHTNESS OVERRIDE ===
        # If brightness is severely off-target, force transition mode to start correction
        # This catches cases where lux suggests "night" but brightness is already 180+
        # (morning transition) or lux suggests "day" but brightness is <80 (clouds/evening)
        brightness = self._last_brightness
        brightness_override = False

        if brightness is not None:
            # Night mode but overexposed → force transition to reduce exposure
            if lux_mode == LightMode.NIGHT and brightness > BrightnessZones.WARNING_HIGH:
                mode = LightMode.TRANSITION
                brightness_override = True
                logger.info(
                    f"[Hybrid] Night mode override: brightness {brightness:.0f} > {BrightnessZones.WARNING_HIGH} "
                    f"→ forcing TRANSITION mode"
                )

            # Day mode but underexposed → force transition to increase exposure
            elif lux_mode == LightMode.DAY and brightness < BrightnessZones.WARNING_LOW:
                mode = LightMode.TRANSITION
                brightness_override = True
                logger.info(
                    f"[Hybrid] Day mode override: brightness {brightness:.0f} < {BrightnessZones.WARNING_LOW} "
                    f"→ forcing TRANSITION mode"
                )

        # Log with sun elevation if available
        sun_elev = self._sun_elevation
        override_note = " (brightness override)" if brightness_override else ""
        if sun_elev is not None:
            logger.info(
                f"[Status] Sun: {sun_elev:.1f}° | Lux: {lux:.1f} | "
                f"Brightness: {brightness if brightness is not None else 'N/A'} | Mode: {mode}{override_note}"
            )
        else:
            logger.info(
                f"Light level: {lux:.2f} lux | Brightness: {brightness if brightness is not None else 'N/A'} "
                f"→ Mode: {mode}{override_note}"
            )

        return mode

    def get_camera_settings(self, mode: str, lux: float = None) -> Dict:
        """
        Get camera settings for the specified light mode.

        Args:
            mode: Light mode (night, day, or transition)
            lux: Current lux value (used for transition mode)

        Returns:
            Dictionary of camera control settings
        """
        adaptive_config = self.config["adaptive_timelapse"]
        settings = {}

        if mode == LightMode.NIGHT:
            night = adaptive_config["night_mode"]
            # Disable auto-exposure, auto-gain, and auto-white-balance for manual control
            settings["AeEnable"] = 0

            night_max = night["max_exposure_time"]
            night_gain = night["analogue_gain"]

            # Night mode runs wide open by default and only pulls back when the
            # scene is measurably too bright.
            target_gain = night_gain
            target_exposure = night_max

            # Dawn: brightness climbing past 140 while still in night mode means
            # the sky is brightening faster than the mode boundary moved.
            if self._last_brightness is not None and self._last_brightness > 140:
                # Calculate ideal exposure from brightness ratio (same as transition mode)
                target_exposure = self._calculate_exposure_from_brightness(
                    self._last_brightness, lux=None, mode=LightMode.NIGHT
                )
                # Enforce night mode minimums: 60% max exposure, gain 2.0
                # This prevents over-reduction in actual dark scenes
                exposure_floor = night_max * 0.6
                target_exposure = max(exposure_floor, min(night_max, target_exposure))

                # FIX 1b: When exposure is near floor AND brightness still high, reduce gain
                # This prevents brightness climbing when exposure can't go lower
                exposure_near_floor = target_exposure <= exposure_floor * 1.1
                if exposure_near_floor and self._last_brightness > 150:
                    # Reduce gain proportionally to bring brightness toward 120
                    # Use sqrt for gentler reduction (since brightness ~ gain * exposure)
                    brightness_ratio = 120.0 / self._last_brightness
                    target_gain = max(2.0, self._last_analogue_gain * brightness_ratio**0.5)
                    logger.debug(
                        f"Night mode gain reduction: brightness={self._last_brightness:.0f}, "
                        f"exposure at floor ({target_exposure:.2f}s), reducing gain to {target_gain:.2f}"
                    )
                else:
                    target_gain = max(2.0, min(night_gain, target_gain))

                logger.debug(
                    f"Night mode brightness feedback: brightness={self._last_brightness:.0f}, "
                    f"target_exposure={target_exposure:.2f}s, target_gain={target_gain:.2f}"
                )

            # FIX 2: Coordinated ramps when entering night mode
            # Detect entry: current gain is < 50% of target (coming from day/transition)
            entering_night = (
                self._last_analogue_gain is not None
                and self._last_analogue_gain < target_gain * 0.5
            )

            if entering_night:
                # FIX 2b: Even slower base ramps to spread over ~20-30 minutes
                # At ~30s/frame: gain 0.04 = ~50 frames, exposure 0.03 = ~66 frames
                base_gain_speed = 0.04  # 4% per frame (was 8%)
                base_exposure_speed = 0.03  # 3% per frame (was 5%)

                # FIX 2c: Throttle when brightness is approaching target
                # Night target brightness is ~80 (lower than day's 120)
                night_brightness_target = 80
                if (
                    self._last_brightness is not None
                    and self._last_brightness > night_brightness_target * 0.8
                ):
                    # Approaching or exceeding target, slow down further
                    proximity = self._last_brightness / night_brightness_target
                    # Throttle from 100% speed at 64 brightness to 30% at 80+
                    throttle = max(0.3, 1.0 - (proximity - 0.8) * 2)
                    base_gain_speed *= throttle
                    base_exposure_speed *= throttle
                    logger.debug(
                        f"Entering night throttle: brightness={self._last_brightness:.0f}, "
                        f"throttle={throttle:.0%}, gain_speed={base_gain_speed:.3f}, exp_speed={base_exposure_speed:.3f}"
                    )

                gain_speed = base_gain_speed
                exposure_speed = base_exposure_speed
                logger.debug(
                    f"Entering night mode: gain={self._last_analogue_gain:.2f} → {target_gain:.2f}, "
                    f"using coordinated ramps (gain={gain_speed:.3f}, exp={exposure_speed:.3f})"
                )
            else:
                # Normal operation - use standard ramps with over/underexposure adjustments
                gain_speed = None
                if self._underexposure_detected:
                    exposure_speed = self._get_rampup_speed()
                elif self._overexposure_detected:
                    exposure_speed = self._get_rampdown_speed()
                else:
                    exposure_speed = None

            smooth_gain = self._interpolate_gain(target_gain, gain_speed)
            smooth_exposure = self._interpolate_exposure(target_exposure, exposure_speed)

            settings["ExposureTime"] = int(smooth_exposure * 1_000_000)
            settings["AnalogueGain"] = smooth_gain

            # Lock AWB for long exposures - AWB causes 5x slowdown!
            settings["AwbEnable"] = 0

            # Use smooth WB interpolation even in night mode for seamless transitions
            target_gains = self._get_target_colour_gains(mode)
            smooth_gains = self._interpolate_colour_gains(target_gains)
            settings["ColourGains"] = smooth_gains

            logger.info(
                f"Night mode: exposure={smooth_exposure:.2f}s, gain={smooth_gain:.2f}, "
                f"WB=[{smooth_gains[0]:.2f}, {smooth_gains[1]:.2f}]"
            )

            self._last_decision = {
                "target_exposure_s": round(target_exposure, 6),
                "target_exposure_ms": round(target_exposure * 1000, 2),
                "target_gain": round(target_gain, 3),
                "applied_exposure_s": round(smooth_exposure, 6),
                "applied_gain": round(smooth_gain, 3),
            }

        elif mode == LightMode.DAY:
            day = adaptive_config["day_mode"]
            transition_config = adaptive_config.get("transition_mode", {})

            # Check if direct brightness control is enabled (new simple approach)
            # Direct brightness feedback: exposure follows the measured
            # brightness of the previous frame. No interpolation -- the
            # controller's own damping already limits how fast it moves.
            settings["AeEnable"] = 0

            target_exposure = self._calculate_exposure_from_brightness(
                self._last_brightness, lux, mode=LightMode.DAY
            )

            # Gain stays at its floor in daylight; the shutter does the work.
            target_gain = self._interpolate_gain(1.0)

            settings["ExposureTime"] = int(target_exposure * 1_000_000)
            settings["AnalogueGain"] = target_gain

            # Feeds the next frame's ratio calculation.
            self._last_exposure_time = target_exposure

            self._last_decision = {
                "target_exposure_s": round(target_exposure, 6),
                "target_exposure_ms": round(target_exposure * 1000, 2),
                "target_gain": round(target_gain, 3),
                "applied_exposure_s": round(target_exposure, 6),
                "applied_gain": round(target_gain, 3),
            }

            # For smooth transitions, use manual WB with interpolated gains
            # AWB is only used internally to learn good daylight WB values
            # (captured via _update_day_wb_reference from actual capture metadata)
            if transition_config.get("smooth_wb_in_day_mode", True):
                settings["AwbEnable"] = 0
                target_gains = self._get_target_colour_gains(mode)
                smooth_gains = self._interpolate_colour_gains(target_gains)
                settings["ColourGains"] = smooth_gains
            else:
                # Legacy behavior: use AWB in day mode
                settings["AwbEnable"] = 1 if day.get("awb_enable", True) else 0

            # Apply brightness adjustment if specified
            if "brightness" in day:
                settings["Brightness"] = day["brightness"]

            wb_info = (
                f"WB=[{settings.get('ColourGains', ('auto', 'auto'))[0]:.2f}, {settings.get('ColourGains', ('auto', 'auto'))[1]:.2f}]"
                if "ColourGains" in settings
                else "WB=auto"
            )
            exposure_info = (
                f"exposure={settings.get('ExposureTime', 'auto')/1_000_000:.4f}s"
                if "ExposureTime" in settings
                else "exposure=auto"
            )
            gain_info = (
                f"gain={settings.get('AnalogueGain', 'auto'):.2f}"
                if "AnalogueGain" in settings
                else "gain=auto"
            )
            logger.info(f"Day mode: {exposure_info}, {gain_info}, {wb_info}")

        elif mode == LightMode.TRANSITION:
            transition = adaptive_config["transition_mode"]
            thresholds = adaptive_config["light_thresholds"]

            # Disable auto-exposure for manual control
            settings["AeEnable"] = 0

            # Direct brightness feedback for transition mode.
            night_max = adaptive_config["night_mode"]["max_exposure_time"]
            night_gain = adaptive_config["night_mode"]["analogue_gain"]

            target_exposure = self._calculate_exposure_from_brightness(
                self._last_brightness, lux, mode=LightMode.TRANSITION
            )

            # Shutter first, then gain: hold gain at 1.0 until the shutter is
            # within 20% of its ceiling, then trade the remainder for gain.
            if target_exposure >= night_max * 0.8:
                exposure_shortfall = target_exposure / (night_max * 0.8)
                target_gain = min(night_gain, exposure_shortfall)
                target_exposure = night_max * 0.8
            else:
                target_gain = 1.0

            # Gain is interpolated; exposure is not, since the controller's own
            # damping already limits per-frame movement.
            smooth_gain = self._interpolate_gain(target_gain)

            settings["ExposureTime"] = int(target_exposure * 1_000_000)
            settings["AnalogueGain"] = smooth_gain
            self._last_exposure_time = target_exposure

            self._last_decision = {
                "target_exposure_s": round(target_exposure, 6),
                "target_exposure_ms": round(target_exposure * 1000, 2),
                "target_gain": round(target_gain, 3),
                "applied_exposure_s": round(target_exposure, 6),
                "applied_gain": round(smooth_gain, 3),
            }

            # Position within the transition, for white-balance interpolation.
            # lux is None until the first test shot succeeds; mid-transition is
            # the least wrong assumption, and is far better than the hardcoded
            # 5-second exposure this used to fall through to -- which could fire
            # in broad daylight.
            if lux is not None:
                lux_range = thresholds["day"] - thresholds["night"]
                position = max(0.0, min(1.0, (lux - thresholds["night"]) / lux_range))
            else:
                position = 0.5

            # Always manual WB during transitions, to prevent flickering.
            settings["AwbEnable"] = 0
            target_gains = self._get_target_colour_gains(mode, position)
            smooth_gains = self._interpolate_colour_gains(target_gains, position)
            settings["ColourGains"] = smooth_gains

            brightness_str = (
                f"{self._last_brightness:.1f}" if self._last_brightness is not None else "N/A"
            )
            logger.info(
                f"Transition mode: lux={lux:.2f}" if lux is not None else "Transition mode: lux=N/A"
            )
            logger.debug(
                f"Transition: brightness={brightness_str}, position={position:.2f}, "
                f"exposure={target_exposure:.4f}s, gain={smooth_gain:.2f}, "
                f"WB=[{smooth_gains[0]:.2f}, {smooth_gains[1]:.2f}]"
            )

        # Add HDR mode control if enabled
        if self._hdr_enabled and self._hdr_enum_available:
            try:
                if mode == LightMode.NIGHT:
                    hdr_mode_value = getattr(self._hdr_mode_enum, self._hdr_night_mode, None)
                else:
                    # DAY and TRANSITION use day HDR mode
                    hdr_mode_value = getattr(self._hdr_mode_enum, self._hdr_day_mode, None)

                if hdr_mode_value is not None:
                    settings["HdrMode"] = hdr_mode_value
                    logger.debug(f"[HDR] Set HdrMode={hdr_mode_value} for {mode} mode")
            except Exception as e:
                logger.debug(f"[HDR] Could not set HdrMode: {e}")

        return settings

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
            from PIL import Image
            import numpy as np

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

            # What the controller decided for this frame. Recorded by
            # get_camera_settings rather than recomputed here -- the old code
            # re-ran the whole exposure formula purely to fill in these fields,
            # which meant a pure calculation ran twice per frame and emitted
            # duplicate log lines from a path whose result was thrown away.
            diagnostics.update(self._last_decision)

            # Add current interpolated values (what we actually sent to camera)
            if self._last_exposure_time is not None:
                diagnostics["interpolated_exposure_s"] = round(self._last_exposure_time, 6)
                diagnostics["interpolated_exposure_ms"] = round(self._last_exposure_time * 1000, 2)
            if self._last_analogue_gain is not None:
                diagnostics["interpolated_gain"] = round(self._last_analogue_gain, 2)

            # Add hysteresis state
            diagnostics["hysteresis_hold_count"] = getattr(self, "_mode_hold_count", 0)
            diagnostics["hysteresis_last_mode"] = getattr(self, "_last_mode", None)

            # Add brightness feedback state
            diagnostics["target_brightness"] = self._target_brightness
            diagnostics["base_target_brightness"] = self._base_target_brightness
            diagnostics["overcast_boost_active"] = (
                self._target_brightness > self._base_target_brightness
            )
            if self._last_brightness is not None:
                diagnostics["last_brightness"] = round(self._last_brightness, 2)
            if self._last_p95 is not None:
                diagnostics["last_p95"] = round(self._last_p95, 2)
            if self._last_highlight_scale < 1.0:
                diagnostics["highlight_scale"] = round(self._last_highlight_scale, 3)
                diagnostics["effective_target_brightness"] = round(
                    self._target_brightness * self._last_highlight_scale, 1
                )

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

        # Initialize camera once at the start
        capture = None
        last_mode = None

        try:
            while self.running:
                loop_start = time.time()

                # Check if we've reached the frame limit
                if num_frames > 0 and self.frame_count >= num_frames:
                    logger.info(f"Reached frame limit: {num_frames}")
                    break

                # Determine if we should take a test shot based on frequency
                test_shot_frequency = adaptive_config["test_shot"].get("frequency", 1)
                should_take_test_shot = adaptive_config["test_shot"]["enabled"] and (
                    self.frame_count % test_shot_frequency == 0
                )

                # CRITICAL: Close camera before taking test shot to avoid "Camera in Running state" error
                # Test shot uses its own context-managed camera instance
                if capture is not None and should_take_test_shot:
                    logger.debug("Closing camera before test shot...")
                    self._close_camera_fast(capture, last_mode)
                    capture = None
                    last_mode = None

                # Initialize diagnostic tracking variables
                raw_lux = None
                lux = None
                transition_position = None

                # Take test shot if enabled and frequency allows
                if should_take_test_shot:
                    try:
                        test_image_path, test_metadata = self.take_test_shot()

                        # Calculate lux from test shot image brightness
                        # This is more reliable than camera's metadata lux estimate
                        raw_lux = self.calculate_lux(test_image_path, test_metadata)

                        # === STARTUP SATURATED TEST SHOT DETECTION ===
                        # On first frame after reboot/restart, the camera ISP may not apply
                        # settings correctly, resulting in a saturated test shot.
                        # If we have seeded values from the database, use those instead.
                        if self.frame_count == 0:
                            test_brightness = self._analyze_image_brightness(test_image_path)
                            if test_brightness:
                                test_mean = test_brightness.get("mean_brightness", 128)
                                if test_mean > 250 and self._seed_exposure is not None:
                                    # Test shot is saturated AND we have seeded values
                                    # Use seeded lux instead of calculated lux
                                    if self._smoothed_lux is not None:
                                        logger.warning(
                                            f"[Startup] First test shot saturated ({test_mean:.1f}/255) - "
                                            f"using seeded lux={self._smoothed_lux:.1f} instead of calculated={raw_lux:.1f}"
                                        )
                                        raw_lux = self._smoothed_lux

                        # Apply exponential moving average smoothing
                        lux = self._smooth_lux(raw_lux)

                        # Determine raw mode from smoothed lux
                        raw_mode = self.determine_mode(lux)

                        # Apply hysteresis to prevent rapid mode flipping
                        mode = self._apply_hysteresis(raw_mode)

                        # Calculate transition position for diagnostics
                        if mode == LightMode.TRANSITION:
                            night_threshold = adaptive_config["light_thresholds"]["night"]
                            day_threshold = adaptive_config["light_thresholds"]["day"]
                            transition_position = (lux - night_threshold) / (
                                day_threshold - night_threshold
                            )
                            transition_position = max(0.0, min(1.0, transition_position))

                        # === HOLY GRAIL: Seed from metadata when entering transition ===
                        # Detect mode change: Day → Transition or Day → Night
                        entering_manual_mode = self._previous_mode == LightMode.DAY and mode in (
                            LightMode.TRANSITION,
                            LightMode.NIGHT,
                        )

                        if entering_manual_mode and not self._transition_seeded:
                            # Seed interpolation state from actual camera metadata
                            # This makes first manual frame identical to last auto frame
                            self._seed_from_metadata(test_metadata, self._last_day_capture_metadata)

                        # Reset seed state when returning to day mode
                        if mode == LightMode.DAY and self._previous_mode != LightMode.DAY:
                            self._transition_seeded = False
                            logger.info("[Holy Grail] Returned to Day mode - seed state reset")

                        # Log transition progress
                        if mode == LightMode.TRANSITION and transition_position is not None:
                            self._log_transition_progress(lux, transition_position)

                        # Track mode for next iteration
                        self._previous_mode = mode

                        # Get settings for this mode (with smooth WB interpolation)
                        settings = self.get_camera_settings(mode, lux)

                    except Exception as e:
                        logger.error(f"Test shot failed: {e}")
                        # Fall back to last mode or day mode
                        mode = self._last_mode or LightMode.DAY
                        lux = self._smoothed_lux
                        settings = self.get_camera_settings(mode, lux)
                else:
                    # Test shot skipped (frequency > 1) - reuse last known values
                    # This keeps camera running and applies interpolation
                    mode = self._last_mode or LightMode.DAY
                    lux = self._smoothed_lux  # Use last smoothed lux
                    settings = self.get_camera_settings(mode, lux)
                    lux_str = f"{lux:.2f}" if lux is not None else "N/A"
                    logger.debug(
                        f"Skipping test shot (frame {self.frame_count}), "
                        f"reusing mode={mode}, lux={lux_str}"
                    )

                # Initialize camera on first frame or if it was closed
                if capture is None:
                    logger.debug("Initializing camera for timelapse...")
                    capture = ImageCapture(self.camera_config)
                    capture.initialize_camera(manual_controls=settings)
                    last_mode = mode

                # Capture actual frame
                try:
                    image_path, metadata_path = self.capture_frame(capture, mode, lux)
                    logger.info(f"Frame captured: {image_path}")

                    # Enrich metadata with diagnostic information (if enabled)
                    diagnostics_enabled = (
                        self.config.get("adaptive_timelapse", {})
                        .get("diagnostics", {})
                        .get("enabled", False)
                    )
                    if metadata_path and diagnostics_enabled:
                        self._enrich_metadata_with_diagnostics(
                            metadata_path=metadata_path,
                            image_path=image_path,
                            mode=mode,
                            lux=lux,
                            raw_lux=raw_lux,
                            transition_position=transition_position,
                        )

                    # Apply brightness feedback for butter-smooth transitions
                    # Uses lores stream brightness (from capture.last_brightness_metrics)
                    # which avoids disk I/O and overlay contamination
                    # Initialize so it's defined when feedback is disabled — otherwise
                    # the later store_capture() call would NameError and be swallowed.
                    brightness_metrics = None
                    brightness_feedback_enabled = (
                        self.config.get("adaptive_timelapse", {})
                        .get("transition_mode", {})
                        .get("brightness_feedback_enabled", True)
                    )
                    if brightness_feedback_enabled:
                        try:
                            # Prefer lores brightness (fast, no overlay contamination)
                            # Fall back to disk analysis if lores not available
                            brightness_metrics = capture.last_brightness_metrics
                            if not brightness_metrics:
                                brightness_metrics = self._analyze_image_brightness(image_path)
                            if brightness_metrics:
                                actual_brightness = brightness_metrics.get("mean_brightness")

                                # Dynamic target: boost brightness on overcast days
                                std_brightness = brightness_metrics.get("std_brightness")
                                old_target = self._target_brightness
                                self._target_brightness = self._get_dynamic_target_brightness(
                                    std_brightness
                                )
                                if self._target_brightness != old_target:
                                    # Only log when the adjustment is meaningful — most
                                    # ticks move the target by ±1 and just spam INFO.
                                    if abs(self._target_brightness - old_target) >= 5:
                                        logger.info(
                                            f"[Overcast] Dynamic target: {old_target} → {self._target_brightness} "
                                            f"(std_brightness={std_brightness:.1f})"
                                        )
                                    else:
                                        logger.debug(
                                            f"[Overcast] Dynamic target: {old_target} → {self._target_brightness} "
                                            f"(std_brightness={std_brightness:.1f})"
                                        )

                                # These two are the entire input to the exposure
                                # controller. _apply_brightness_feedback used to write
                                # _last_brightness as a side effect while computing a
                                # correction factor nothing read.
                                self._last_brightness = actual_brightness
                                self._last_p95 = brightness_metrics.get("percentile_95")
                                # Check for overexposure and enable fast ramp-down if needed
                                self._check_overexposure(brightness_metrics)
                                # Check for underexposure at min exposure and enable fast recovery
                                self._check_underexposure(brightness_metrics)
                        except Exception as e:
                            logger.debug(f"Could not apply brightness feedback: {e}")

                    # Update day WB reference from actual capture metadata
                    # This allows us to learn good daylight WB values for smooth transitions
                    # Read the per-frame metadata JSON once and reuse for both the
                    # WB-reference update and the DB store below.
                    capture_metadata = None
                    if metadata_path:
                        try:
                            import json

                            with open(metadata_path, "r") as f:
                                capture_metadata = json.load(f)
                        except Exception as e:
                            logger.debug(f"Could not read capture metadata: {e}")

                    # Also store for Holy Grail seeding when entering transition
                    if capture_metadata is not None and mode == LightMode.DAY:
                        try:
                            self._update_day_wb_reference(capture_metadata)
                            self._last_day_capture_metadata = capture_metadata
                        except Exception as e:
                            logger.debug(f"Could not apply WB reference: {e}")

                    # Store capture in database for historical analysis
                    if self._database is not None:
                        try:
                            db_metadata = capture_metadata if capture_metadata is not None else {}

                            # Get weather data from overlay (if available)
                            weather_data = None
                            if capture and capture.overlay and capture.overlay.weather:
                                weather_data = capture.overlay.weather.get_weather_data()

                            # Get system metrics (CPU temp, load)
                            system_metrics = None
                            if self._system_monitor:
                                system_metrics = self._system_monitor.get_all_metrics()

                            self._database.store_capture(
                                image_path=image_path,
                                metadata=db_metadata,
                                mode=mode,
                                lux=lux,
                                brightness_metrics=brightness_metrics,
                                weather_data=weather_data,
                                sun_elevation=self._sun_elevation,
                                system_metrics=system_metrics,
                            )
                        except Exception as e:
                            logger.debug(f"[DB] Failed to store capture: {e}")

                except Exception as e:
                    logger.error(f"Frame capture failed: {e}", exc_info=True)

                # Calculate time to sleep
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
            # Close camera if it was initialized
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
