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
from typing import Dict, Optional, Tuple
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
    from src.logging_config import get_logger
    from src.capture_image import CameraConfig, ImageCapture
    from src.ml_exposure_v2 import MLExposurePredictorV2
    from src.database import CaptureDatabase
    from src.system_monitor import SystemMonitor
except ImportError:
    from logging_config import get_logger
    from capture_image import CameraConfig, ImageCapture

    try:
        from ml_exposure_v2 import MLExposurePredictorV2
    except ImportError:
        MLExposurePredictorV2 = None  # ML v2 module not available

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


# Emergency brightness zones for fast correction
# When brightness is severely off-target, apply immediate corrections
class BrightnessZones:
    """Brightness thresholds for emergency exposure correction."""

    EMERGENCY_HIGH = 180  # Severe overexposure - immediate 30% reduction
    WARNING_HIGH = 160  # Moderate overexposure - 15% reduction
    TARGET = 120  # Ideal brightness
    WARNING_LOW = 80  # Moderate underexposure - 20% increase
    EMERGENCY_LOW = 60  # Severe underexposure - 40% increase

    # Emergency correction multipliers (applied directly to exposure)
    EMERGENCY_HIGH_FACTOR = 0.7  # Reduce exposure by 30%
    WARNING_HIGH_FACTOR = 0.85  # Reduce by 15%
    WARNING_LOW_FACTOR = 1.2  # Increase by 20%
    EMERGENCY_LOW_FACTOR = 1.4  # Increase by 40%


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
        self._lux_history: list = []  # Rolling history for EMA
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
        self._brightness_correction_factor: float = 1.0  # Multiplier for exposure adjustment

        # Overexposure detection for fast ramp-down
        self._overexposure_detected: bool = False  # True when image is overexposed
        self._overexposure_severity: str = None  # "warning" or "critical"

        # Underexposure detection for fast recovery (symmetric to overexposure)
        self._underexposure_detected: bool = (
            False  # True when image is underexposed at min exposure
        )
        self._underexposure_severity: str = None  # "warning" or "critical"

        # Smoothed emergency factor to prevent oscillation
        # Instead of hard on/off switching, this gradually moves towards target factor
        self._smoothed_emergency_factor: float = 1.0
        self._emergency_factor_speed: float = 0.15  # How fast to adjust (0.0-1.0)

        # Holy Grail transition state - seeded from actual camera metadata
        self._transition_seeded: bool = False  # True once we've seeded from metadata
        self._seed_exposure: float = None  # Actual exposure from last auto frame
        self._seed_gain: float = None  # Actual gain from last auto frame
        self._seed_wb_gains: tuple = None  # Actual WB gains from last auto frame
        self._previous_mode: str = None  # Track mode changes for seeding detection
        self._last_day_capture_metadata: Dict = None  # Metadata from last day mode capture
        self._ev_clamp_applied: bool = False  # True after EV clamp applied on first frame

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

        # Fast ramp-down speed for overexposure correction (default 0.30 = 3x normal speed)
        self._fast_rampdown_speed = transition_config.get("fast_rampdown_speed", 0.30)
        # Critical ramp-down speed for severe overexposure (default 0.70 = very aggressive)
        self._critical_rampdown_speed = transition_config.get("critical_rampdown_speed", 0.70)

        # Fast ramp-up speeds for underexposure correction (symmetric to ramp-down)
        self._fast_rampup_speed = transition_config.get("fast_rampup_speed", 0.50)
        self._critical_rampup_speed = transition_config.get("critical_rampup_speed", 0.70)

        # Rapid lux change detection
        self._previous_raw_lux: float = None  # For detecting rapid changes
        self._lux_change_threshold = transition_config.get(
            "lux_change_threshold", 3.0
        )  # 3x change = rapid

        # Polar awareness - sun position for high latitude locations (68°N)
        self._location = None
        self._sun_elevation: float = None  # Current sun elevation in degrees
        self._civil_twilight_threshold = -6.0  # Default: Civil twilight
        self._init_location()

        # ML-based exposure prediction
        self._ml_predictor = None
        self._ml_enabled = False
        self._init_ml_predictor()

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

    def _init_ml_predictor(self):
        """Initialize ML v2 exposure predictor if enabled.

        ML v2 is database-driven and Arctic-aware:
        - Trains only on good frames (brightness 100-140) from database
        - Uses sun elevation for time periods (not clock hours)
        - Doesn't learn from bad frames, avoiding reinforced mistakes
        """
        if MLExposurePredictorV2 is None:
            logger.debug("[ML v2] MLExposurePredictorV2 not available")
            return

        ml_config = self.config.get("adaptive_timelapse", {}).get("ml_exposure", {})
        self._ml_enabled = ml_config.get("enabled", False)

        if not self._ml_enabled:
            logger.debug("[ML v2] ML exposure prediction disabled in config")
            return

        # Get database path for ML v2 (it trains from database)
        db_config = self.config.get("database", {})
        db_path = db_config.get("path", "data/timelapse.db")

        if not db_config.get("enabled", False):
            logger.warning("[ML v2] Database disabled - ML v2 requires database for training")
            self._ml_enabled = False
            return

        try:
            self._ml_predictor = MLExposurePredictorV2(
                db_path=db_path, config=ml_config, state_dir="ml_state"
            )
            stats = self._ml_predictor.get_statistics()
            logger.info(
                f"[ML v2] Initialized: trust={self._ml_predictor.get_trust_level():.2f}, "
                f"buckets={stats.get('lux_exposure_buckets', 0)}, "
                f"trained={stats.get('last_trained', 'never')}"
            )
        except Exception as e:
            logger.warning(f"[ML v2] Failed to initialize predictor: {e}")
            self._ml_predictor = None
            self._ml_enabled = False

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
        except Exception as e:
            logger.warning(f"[DB] Failed to initialize database: {e}")
            self._database = None

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
                f"(forcing Day mode despite lux={lux:.1f if lux else 'N/A'})"
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

    def _interpolate_gain(self, target_gain: float) -> float:
        """
        Smoothly interpolate analogue gain to prevent sudden ISO jumps.

        Uses gradual transition towards target gain rather than instant switching.

        Args:
            target_gain: Target analogue gain value

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
        speed = self._gain_transition_speed
        new_gain = self._last_analogue_gain + speed * (target_gain - self._last_analogue_gain)

        # Clamp to valid range
        new_gain = max(1.0, min(16.0, new_gain))

        self._last_analogue_gain = new_gain

        logger.debug(f"Gain interpolation: target={target_gain:.2f} → actual={new_gain:.2f}")
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

    def _apply_brightness_feedback(self, actual_brightness: float) -> float:
        """
        Apply gradual brightness feedback to correct exposure errors.

        This method maintains a slow-moving correction factor based on the
        difference between actual and target brightness. The correction is
        applied VERY gradually over multiple frames to ensure butter-smooth
        transitions with no visible jumps.

        The correction factor is a multiplier for exposure:
        - If images are consistently too bright, factor decreases below 1.0
        - If images are consistently too dark, factor increases above 1.0

        Args:
            actual_brightness: Mean brightness of the captured image (0-255)

        Returns:
            Updated correction factor to apply to target exposure
        """
        if actual_brightness is None:
            return self._brightness_correction_factor

        # Store for tracking
        self._last_brightness = actual_brightness

        # Calculate brightness error (positive = too bright, negative = too dark)
        error = actual_brightness - self._target_brightness

        # Check if we're within acceptable tolerance
        if abs(error) <= self._brightness_tolerance:
            # Within tolerance - slowly decay correction back to 1.0
            # This prevents over-correction after reaching target
            decay_rate = 0.05  # Very slow decay
            if self._brightness_correction_factor > 1.0:
                self._brightness_correction_factor = max(
                    1.0, self._brightness_correction_factor - decay_rate
                )
            elif self._brightness_correction_factor < 1.0:
                self._brightness_correction_factor = min(
                    1.0, self._brightness_correction_factor + decay_rate
                )
            logger.debug(
                f"Brightness within tolerance ({actual_brightness:.1f}), "
                f"correction decaying to {self._brightness_correction_factor:.3f}"
            )
            return self._brightness_correction_factor

        # Check for underexposure at minimum exposure (fast recovery needed)
        # Get minimum exposure from config
        adaptive_config = self.config.get("adaptive_timelapse", {})
        min_exposure = adaptive_config.get("day_mode", {}).get("exposure_time", 0.02)

        # If we're at or near minimum exposure AND the image is significantly dark,
        # apply faster recovery to prevent prolonged dark periods
        at_min_exposure = (
            self._last_exposure_time is not None
            and self._last_exposure_time <= min_exposure * 1.5  # Within 50% of minimum
        )
        significantly_dark = actual_brightness < 90  # Well below target of 120

        if at_min_exposure and significantly_dark:
            # Fast underexposure recovery - boost correction factor
            boost = 1.2  # 20% increase per frame
            self._brightness_correction_factor *= boost
            self._brightness_correction_factor = min(4.0, self._brightness_correction_factor)
            logger.info(
                f"[Underexposure] Fast recovery: brightness={actual_brightness:.1f}, "
                f"at min exposure - boosting correction to {self._brightness_correction_factor:.3f}"
            )
            return self._brightness_correction_factor

        # Outside tolerance - apply correction with urgency scaling
        # Convert error to a correction percentage
        # error of 40 (e.g., brightness 160 vs target 120) = 33% too bright
        error_percent = error / self._target_brightness
        abs_error = abs(error)

        # === URGENCY MULTIPLIER ===
        # Scale feedback strength based on how far off-target we are
        # Small errors: normal slow correction
        # Large errors: faster correction to catch up
        base_strength = self._brightness_feedback_strength

        if abs_error > 60:
            # Severe deviation (e.g., brightness 60 or 180) - 3x speed
            urgency_multiplier = 3.0
            urgency_level = "URGENT"
        elif abs_error > 40:
            # Moderate deviation (e.g., brightness 80 or 160) - 2x speed
            urgency_multiplier = 2.0
            urgency_level = "elevated"
        elif abs_error > 25:
            # Mild deviation - 1.5x speed
            urgency_multiplier = 1.5
            urgency_level = "mild"
        else:
            # Normal
            urgency_multiplier = 1.0
            urgency_level = "normal"

        effective_strength = min(0.7, base_strength * urgency_multiplier)
        adjustment = error_percent * effective_strength

        # Update correction factor (reducing it if too bright, increasing if too dark)
        # Too bright (positive error) → reduce correction factor (less exposure)
        # Too dark (negative error) → increase correction factor (more exposure)
        self._brightness_correction_factor *= 1.0 - adjustment

        # Clamp to reasonable range (0.3x to 4x correction)
        self._brightness_correction_factor = max(0.3, min(4.0, self._brightness_correction_factor))

        # Log with urgency level if not normal
        if urgency_multiplier > 1.0:
            logger.info(
                f"[Feedback] {urgency_level.upper()}: brightness={actual_brightness:.1f}, "
                f"error={error:.0f}, urgency={urgency_multiplier}x, "
                f"correction={self._brightness_correction_factor:.3f}"
            )
        else:
            logger.debug(
                f"Brightness feedback: actual={actual_brightness:.1f}, "
                f"target={self._target_brightness}, error={error:.1f}, "
                f"correction={self._brightness_correction_factor:.3f}"
            )

        return self._brightness_correction_factor

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

    def _apply_proactive_exposure_correction(self, test_image_path: str, raw_lux: float) -> None:
        """
        Proactively adjust exposure correction based on test shot brightness.

        This is called BEFORE calculating exposure for the actual capture.
        If the test shot (with fixed short exposure) is bright, it means the
        scene is getting brighter and we should proactively reduce exposure
        to prevent overexposure in the actual long-exposure capture.

        Args:
            test_image_path: Path to the test shot image
            raw_lux: Raw calculated lux from test shot
        """
        try:
            brightness_metrics = self._analyze_image_brightness(test_image_path)
            if not brightness_metrics:
                return

            test_brightness = brightness_metrics.get("mean_brightness", 128)

            # Test shot uses fixed short exposure (0.1s, gain 1.0)
            # If it's bright, the scene has lots of light
            # We need to proactively reduce exposure for the actual capture

            # Thresholds for proactive correction (softened to prevent dark dips)
            bright_threshold = 160  # Test shot brightness indicating bright scene
            very_bright_threshold = 200  # Very bright scene

            if test_brightness > very_bright_threshold:
                # Scene is very bright - reduce correction factor (softened)
                # This helps when transitioning from night to day
                reduction = 0.8  # 20% reduction (was 30%)
                self._brightness_correction_factor *= reduction
                self._brightness_correction_factor = max(0.3, self._brightness_correction_factor)
                logger.info(
                    f"[Proactive] Very bright test shot ({test_brightness:.1f}) - "
                    f"reducing exposure correction to {self._brightness_correction_factor:.3f}"
                )
            elif test_brightness > bright_threshold:
                # Scene is moderately bright - gentle reduction (softened)
                reduction = 0.9  # 10% reduction (was 15%)
                self._brightness_correction_factor *= reduction
                self._brightness_correction_factor = max(0.3, self._brightness_correction_factor)
                logger.debug(
                    f"[Proactive] Bright test shot ({test_brightness:.1f}) - "
                    f"reducing exposure correction to {self._brightness_correction_factor:.3f}"
                )

            # Also detect rapid brightening (lux increasing quickly)
            if self._previous_raw_lux is not None and raw_lux > 0:
                lux_ratio = raw_lux / max(0.01, self._previous_raw_lux)
                if lux_ratio > 2.0:  # Lux more than doubled
                    # Scene getting much brighter - reduce exposure proactively
                    reduction = min(0.85, 1.0 / (lux_ratio * 0.5))  # Softened from 0.8
                    self._brightness_correction_factor *= reduction
                    self._brightness_correction_factor = max(
                        0.3, self._brightness_correction_factor  # Lowered floor for faster recovery
                    )
                    logger.info(
                        f"[Proactive] Rapid brightening ({lux_ratio:.1f}x) - "
                        f"reducing exposure correction to {self._brightness_correction_factor:.3f}"
                    )

        except Exception as e:
            logger.debug(f"Could not apply proactive exposure correction: {e}")

    def _detect_rapid_lux_change(self, raw_lux: float) -> bool:
        """
        Detect if lux is changing rapidly (e.g., at dawn/dusk).

        Rapid changes trigger faster transition speeds to keep up.

        Args:
            raw_lux: Current raw lux value (before smoothing)

        Returns:
            True if rapid change detected
        """
        if self._previous_raw_lux is None:
            self._previous_raw_lux = raw_lux
            return False

        # Calculate ratio of change
        if self._previous_raw_lux > 0 and raw_lux > 0:
            ratio = max(raw_lux / self._previous_raw_lux, self._previous_raw_lux / raw_lux)
            is_rapid = ratio > self._lux_change_threshold

            if is_rapid:
                logger.info(
                    f"[RapidLux] Rapid light change detected: "
                    f"{self._previous_raw_lux:.1f} → {raw_lux:.1f} (ratio: {ratio:.1f}x)"
                )

            self._previous_raw_lux = raw_lux
            return is_rapid

        self._previous_raw_lux = raw_lux
        return False

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

    def _get_emergency_brightness_factor(self, brightness: float) -> float:
        """
        Get smoothed emergency correction factor based on brightness zones.

        This provides correction when brightness is off-target, but uses
        smooth transitions to prevent oscillation. The factor gradually
        moves towards the ideal value rather than jumping instantly.

        Args:
            brightness: Current mean brightness (0-255)

        Returns:
            Smoothed correction factor (1.0 = no change, <1.0 = reduce, >1.0 = increase)
        """
        if brightness is None:
            return self._smoothed_emergency_factor

        # Calculate the ideal (target) factor based on current brightness
        target_factor = 1.0
        zone_name = None

        if brightness > BrightnessZones.EMERGENCY_HIGH:
            target_factor = BrightnessZones.EMERGENCY_HIGH_FACTOR
            zone_name = "SEVERE OVEREXPOSURE"
        elif brightness > BrightnessZones.WARNING_HIGH:
            target_factor = BrightnessZones.WARNING_HIGH_FACTOR
            zone_name = "Overexposure warning"
        elif brightness < BrightnessZones.EMERGENCY_LOW:
            target_factor = BrightnessZones.EMERGENCY_LOW_FACTOR
            zone_name = "SEVERE UNDEREXPOSURE"
        elif brightness < BrightnessZones.WARNING_LOW:
            target_factor = BrightnessZones.WARNING_LOW_FACTOR
            zone_name = "Underexposure warning"

        # Smoothly move towards target factor to prevent oscillation
        # Use faster speed when moving away from 1.0 (applying correction)
        # Use slower speed when returning to 1.0 (relaxing correction)
        if abs(target_factor - 1.0) > abs(self._smoothed_emergency_factor - 1.0):
            # Getting worse - apply correction faster
            speed = self._emergency_factor_speed * 2.0
        else:
            # Getting better - relax slowly to prevent oscillation
            speed = self._emergency_factor_speed * 0.5

        # Interpolate towards target
        old_factor = self._smoothed_emergency_factor
        self._smoothed_emergency_factor += speed * (target_factor - self._smoothed_emergency_factor)

        # Clamp to valid range
        self._smoothed_emergency_factor = max(0.5, min(1.5, self._smoothed_emergency_factor))

        # Only log when factor is significantly different from 1.0
        if abs(self._smoothed_emergency_factor - 1.0) > 0.02:
            if self._smoothed_emergency_factor < 1.0:
                reduction_pct = (1 - self._smoothed_emergency_factor) * 100
                logger.info(
                    f"[Emergency] {zone_name or 'Correction'}: brightness={brightness:.1f} "
                    f"→ smoothed factor {self._smoothed_emergency_factor:.2f} ({reduction_pct:.0f}% reduction)"
                )
            else:
                increase_pct = (self._smoothed_emergency_factor - 1) * 100
                logger.info(
                    f"[Emergency] {zone_name or 'Correction'}: brightness={brightness:.1f} "
                    f"→ smoothed factor {self._smoothed_emergency_factor:.2f} ({increase_pct:.0f}% increase)"
                )

        return self._smoothed_emergency_factor

    def _calculate_target_gain_from_lux(self, lux: float) -> float:
        """
        Calculate target analogue gain based on current lux level.

        Uses a continuous relationship across the entire lux range.
        Higher lux = lower gain needed (less amplification for bright scenes).

        The gain adjusts to complement exposure:
        - In bright light (high lux): low gain (1.0) since exposure provides enough light
        - In dim light (low lux): high gain to amplify the signal

        Args:
            lux: Current light level in lux

        Returns:
            Target analogue gain value
        """
        import math

        adaptive_config = self.config["adaptive_timelapse"]

        # Get gain limits from config
        night_gain = adaptive_config["night_mode"]["analogue_gain"]
        day_gain = adaptive_config.get("day_mode", {}).get("analogue_gain", 1.0)

        # Clamp lux to reasonable range
        lux = max(0.01, min(10000, lux))

        # Use logarithmic interpolation for smooth gain transitions
        # Map lux range to gain range using log scale
        # At lux=1, gain should be near night_gain
        # At lux=500+, gain should be near day_gain

        # Calculate position in log space (0 to 1)
        # lux=1 → position=0 (night), lux=500 → position=1 (day)
        lux_low = 1.0  # Lux level for max gain
        lux_high = 500.0  # Lux level for min gain

        if lux <= lux_low:
            return night_gain
        elif lux >= lux_high:
            return day_gain
        else:
            # Logarithmic interpolation
            log_position = (math.log10(lux) - math.log10(lux_low)) / (
                math.log10(lux_high) - math.log10(lux_low)
            )
            log_position = max(0.0, min(1.0, log_position))

            # Interpolate gain (higher position = lower gain)
            target_gain = night_gain - log_position * (night_gain - day_gain)

            logger.debug(
                f"Lux-based gain: lux={lux:.2f} → position={log_position:.2f} → gain={target_gain:.2f}"
            )

            return target_gain

    def _calculate_sequential_ramping(self, lux: float, position: float) -> Tuple[float, float]:
        """
        Calculate exposure and gain using Sequential Ramping for noise reduction.

        This prioritizes shutter speed to keep ISO (gain) low:
        - Phase 1 (Shutter Priority): Ramp exposure from seed to max, keep gain locked
        - Phase 2 (Gain Priority): Only after exposure is maxed, ramp gain up

        This produces cleaner images by minimizing sensor noise from high gain.

        Args:
            lux: Current light level in lux
            position: Transition position (0.0=at night threshold, 1.0=at day threshold)

        Returns:
            Tuple of (target_exposure_seconds, target_gain)
        """
        import math

        adaptive_config = self.config["adaptive_timelapse"]
        night_config = adaptive_config["night_mode"]

        # Get limits
        max_exposure = night_config["max_exposure_time"]  # e.g., 20s
        max_gain = night_config["analogue_gain"]  # e.g., 8.0

        # Get seed values (from last day mode capture) or reasonable defaults
        seed_exposure = self._seed_exposure if self._seed_exposure else 0.01  # 10ms default
        seed_gain = self._seed_gain if self._seed_gain else 1.0

        # Transition goes from position=1.0 (day) to position=0.0 (night)
        # So we invert to get progress towards night (0=start, 1=end)
        night_progress = 1.0 - position

        # Calculate the total EV range we need to cover
        # EV_night = max_exposure * max_gain
        # EV_seed = seed_exposure * seed_gain
        # Total EV increase needed = log2(EV_night / EV_seed)

        ev_seed = seed_exposure * seed_gain
        ev_night = max_exposure * max_gain

        if ev_seed <= 0 or ev_night <= 0:
            # Fallback to simple calculation
            return self._calculate_target_exposure_from_lux(
                lux
            ), self._calculate_target_gain_from_lux(lux)

        # Phase boundary: when does exposure hit max?
        # Exposure range: seed_exposure → max_exposure
        # This represents a portion of total EV range
        exposure_ev_range = math.log2(max_exposure / seed_exposure) if seed_exposure > 0 else 10
        total_ev_range = math.log2(ev_night / ev_seed) if ev_seed > 0 else 12

        # Phase 1 ends when exposure is maxed
        phase1_end = exposure_ev_range / total_ev_range if total_ev_range > 0 else 0.5
        phase1_end = max(0.1, min(0.9, phase1_end))  # Clamp to reasonable range

        if night_progress <= phase1_end:
            # === PHASE 1: Shutter Priority ===
            # Ramp exposure from seed to max, keep gain locked at seed
            phase1_progress = night_progress / phase1_end  # 0 to 1 within phase 1

            # Logarithmic interpolation for exposure
            log_seed = math.log10(max(0.0001, seed_exposure))
            log_max = math.log10(max_exposure)
            log_target = log_seed + phase1_progress * (log_max - log_seed)
            target_exposure = 10**log_target

            # Keep gain locked at seed value
            target_gain = seed_gain

            logger.debug(
                f"[Sequential] Phase 1 (Shutter): progress={night_progress:.2f}/{phase1_end:.2f}, "
                f"exposure={target_exposure:.4f}s, gain={target_gain:.2f} (locked)"
            )
        else:
            # === PHASE 2: Gain Priority ===
            # Exposure is maxed, now ramp gain from seed to night target
            phase2_progress = (night_progress - phase1_end) / (
                1.0 - phase1_end
            )  # 0 to 1 within phase 2
            phase2_progress = max(0.0, min(1.0, phase2_progress))

            # Exposure stays at max
            target_exposure = max_exposure

            # Logarithmic interpolation for gain
            log_seed = math.log10(max(0.5, seed_gain))
            log_max = math.log10(max_gain)
            log_target = log_seed + phase2_progress * (log_max - log_seed)
            target_gain = 10**log_target

            logger.debug(
                f"[Sequential] Phase 2 (Gain): progress={night_progress:.2f}, "
                f"exposure={target_exposure:.4f}s (maxed), gain={target_gain:.2f}"
            )

        return target_exposure, target_gain

    def _calculate_target_exposure_from_lux(self, lux: float) -> float:
        """
        Calculate target exposure time based on current lux level.

        Uses a continuous logarithmic relationship across the entire lux range,
        not just thresholds. This ensures exposure adjusts smoothly even within
        "day mode" as clouds pass or light changes.

        The formula: exposure = k / lux (inverse relationship)
        In log space: log(exposure) = log(k) - log(lux)

        Additionally applies brightness feedback correction to compensate for
        any consistent over/under exposure detected in previous frames.

        Args:
            lux: Current light level in lux

        Returns:
            Target exposure time in seconds (with brightness correction applied)
        """
        import math

        adaptive_config = self.config["adaptive_timelapse"]

        # Get exposure limits from config
        night_exposure = adaptive_config["night_mode"]["max_exposure_time"]
        min_exposure = adaptive_config.get("day_mode", {}).get("exposure_time", 0.01)

        # Clamp lux to reasonable range to avoid extreme values
        lux = max(0.01, min(10000, lux))

        # Use inverse relationship: exposure = calibration_constant / lux
        # Calibrate so that:
        #   - At low lux, exposure approaches night_exposure (20s)
        #   - At high lux, exposure gives mid-tone brightness (~120)
        #
        # Formula: exposure = (night_exposure * reference_lux) / lux
        # where reference_lux controls the overall brightness level
        #
        # Reference lux controls overall image brightness
        # Higher = brighter images, Lower = darker images
        # Can be configured per-camera in config.yml under adaptive_timelapse.reference_lux
        # Default 3.8 - slightly brighter than 3.5 which was "good but could be a bit brighter"
        reference_lux = adaptive_config.get("reference_lux", 3.8)

        # Calculate base target exposure using inverse relationship
        base_exposure = (night_exposure * reference_lux) / lux

        # Apply brightness feedback correction
        # This gradually adjusts exposure based on actual image brightness
        # to maintain consistent brightness even when lux formula is imperfect
        formula_exposure = base_exposure * self._brightness_correction_factor

        # === ML v2 EXPOSURE PREDICTION ===
        # If ML v2 is enabled, blend ML prediction with formula-based exposure
        # ML v2 uses sun_elevation for Arctic-aware predictions
        target_exposure = formula_exposure
        if self._ml_enabled and self._ml_predictor is not None:
            ml_exposure, ml_confidence = self._ml_predictor.predict_optimal_exposure(
                lux=lux, timestamp=time.time(), sun_elevation=self._sun_elevation
            )
            if ml_exposure is not None:
                target_exposure = self._ml_predictor.blend_with_formula(
                    ml_exposure, formula_exposure
                )
                logger.debug(
                    f"[ML v2] Blending: formula={formula_exposure:.4f}s, "
                    f"ML={ml_exposure:.4f}s (conf={ml_confidence:.2f}) "
                    f"→ blended={target_exposure:.4f}s"
                )

        # === EMERGENCY BRIGHTNESS CORRECTION ===
        # Apply immediate correction when brightness is severely off-target
        # This bypasses the slow gradual correction to catch up during rapid transitions
        emergency_factor = self._get_emergency_brightness_factor(self._last_brightness)
        if emergency_factor != 1.0:
            target_exposure *= emergency_factor
            logger.debug(
                f"[Emergency] Applied factor {emergency_factor:.2f} → "
                f"exposure now {target_exposure:.4f}s"
            )

        # Clamp to valid range
        target_exposure = max(min_exposure, min(night_exposure, target_exposure))

        logger.debug(
            f"Lux-based exposure: lux={lux:.2f} → base={base_exposure:.4f}s "
            f"× correction={self._brightness_correction_factor:.3f} "
            f"× emergency={emergency_factor:.2f} → target={target_exposure:.4f}s"
        )

        return target_exposure

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

    def _apply_ev_safety_clamp(
        self, target_exposure: float, target_gain: float
    ) -> Tuple[float, float]:
        """
        Apply EV Safety Clamp to ensure seamless auto-to-manual handover.

        Compares proposed manual EV to the seeded auto EV. If they differ by >5%,
        forces the manual values to match the auto EV exactly.

        This guarantees the first manual frame is mathematically identical to
        the last auto frame, preventing any visible "flash" or brightness jump.

        Args:
            target_exposure: Proposed exposure time in seconds
            target_gain: Proposed analogue gain

        Returns:
            Tuple of (clamped_exposure, clamped_gain)
        """
        # Check if EV safety clamp is disabled in config
        transition = self.config["adaptive_timelapse"].get("transition_mode", {})
        if not transition.get("ev_safety_clamp_enabled", True):
            return target_exposure, target_gain

        # Only apply clamp on first manual frame (when we have seed values)
        # Bug fix: only apply ONCE, not every frame
        if not self._transition_seeded or self._seed_exposure is None or self._seed_gain is None:
            return target_exposure, target_gain

        # Skip if clamp was already applied (only apply on first frame)
        if self._ev_clamp_applied:
            return target_exposure, target_gain

        # Calculate EVs (EV = exposure * gain, proportional to light captured)
        seed_ev = self._seed_exposure * self._seed_gain
        proposed_ev = target_exposure * target_gain

        if seed_ev <= 0 or proposed_ev <= 0:
            return target_exposure, target_gain

        # Calculate percentage difference
        ev_ratio = proposed_ev / seed_ev
        ev_diff_percent = abs(ev_ratio - 1.0) * 100

        if ev_diff_percent > 5.0:
            # Clamp: adjust exposure to match seed EV while keeping proposed gain
            # EV_seed = exposure_new * gain_proposed
            # exposure_new = EV_seed / gain_proposed
            clamped_exposure = seed_ev / target_gain

            # Ensure within valid range
            night_config = self.config["adaptive_timelapse"]["night_mode"]
            max_exposure = night_config["max_exposure_time"]
            min_exposure = 0.0001  # 100µs

            clamped_exposure = max(min_exposure, min(max_exposure, clamped_exposure))

            logger.info(
                f"[Safety] EV clamp applied: proposed EV differs by {ev_diff_percent:.1f}%. "
                f"Adjusted exposure {target_exposure:.4f}s → {clamped_exposure:.4f}s "
                f"to match auto EV={seed_ev:.4f}"
            )
            # Mark clamp as applied so it only runs once
            self._ev_clamp_applied = True
            return clamped_exposure, target_gain

        return target_exposure, target_gain

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
                self._last_colour_gains = tuple(colour_gains)
                # Update day WB reference since this is what AWB chose at transition
                self._day_wb_reference = tuple(colour_gains)
                logger.info(
                    f"[Holy Grail] Seeded WB from AWB: "
                    f"[{colour_gains[0]:.2f}, {colour_gains[1]:.2f}]"
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
                f"Brightness: {brightness if brightness else 'N/A'} | Mode: {mode}{override_note}"
            )
        else:
            logger.info(
                f"Light level: {lux:.2f} lux | Brightness: {brightness if brightness else 'N/A'} "
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

            # Calculate target values for night mode
            target_gain = night["analogue_gain"]
            target_exposure = night["max_exposure_time"]

            # Apply smooth interpolation even in night mode for seamless transitions
            # Use fast ramp-up for underexposure or fast ramp-down for overexposure
            if self._underexposure_detected:
                exposure_speed = self._get_rampup_speed()
            elif self._overexposure_detected:
                exposure_speed = self._get_rampdown_speed()
            else:
                exposure_speed = None
            smooth_gain = self._interpolate_gain(target_gain)
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

        elif mode == LightMode.DAY:
            day = adaptive_config["day_mode"]
            transition_config = adaptive_config.get("transition_mode", {})

            # Check if smooth exposure/gain transitions are enabled
            smooth_exposure_enabled = transition_config.get("smooth_exposure_in_day_mode", True)

            if smooth_exposure_enabled and lux is not None:
                # SMOOTH TRANSITION MODE: Use calculated exposure/gain based on lux
                # This prevents ISO jumps by gradually adjusting values
                settings["AeEnable"] = 0

                # Calculate target values based on current lux
                target_gain = self._calculate_target_gain_from_lux(lux)
                target_exposure = self._calculate_target_exposure_from_lux(lux)

                # Apply smooth interpolation to prevent jumps
                # Use fast ramp-up for underexposure or fast ramp-down for overexposure
                if self._underexposure_detected:
                    exposure_speed = self._get_rampup_speed()
                elif self._overexposure_detected:
                    exposure_speed = self._get_rampdown_speed()
                else:
                    exposure_speed = None
                smooth_gain = self._interpolate_gain(target_gain)
                smooth_exposure = self._interpolate_exposure(target_exposure, exposure_speed)

                settings["AnalogueGain"] = smooth_gain
                settings["ExposureTime"] = int(smooth_exposure * 1_000_000)

            elif "exposure_time" in day:
                # Manual exposure mode (fixed values from config)
                settings["AeEnable"] = 0
                settings["ExposureTime"] = int(day["exposure_time"] * 1_000_000)
                if "analogue_gain" in day:
                    settings["AnalogueGain"] = day["analogue_gain"]
            else:
                # Legacy auto exposure mode (may cause ISO jumps)
                settings["AeEnable"] = 1

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

            if transition.get("smooth_transition", True) and lux is not None:
                # Calculate position in transition range for WB interpolation
                night_threshold = thresholds["night"]
                day_threshold = thresholds["day"]
                lux_range = day_threshold - night_threshold
                position = (lux - night_threshold) / lux_range
                position = max(0.0, min(1.0, position))

                # === SEQUENTIAL RAMPING ===
                # Use shutter-first ramping when transition is seeded (Holy Grail mode)
                # This keeps ISO low for cleaner images
                use_sequential = (
                    transition.get("sequential_ramping", True) and self._transition_seeded
                )

                if use_sequential:
                    # Sequential: Shutter first, then gain
                    target_exposure, target_gain = self._calculate_sequential_ramping(lux, position)
                else:
                    # Legacy: Simultaneous ramping based on lux
                    target_gain = self._calculate_target_gain_from_lux(lux)
                    target_exposure = self._calculate_target_exposure_from_lux(lux)

                # === BRIGHTNESS CORRECTION FOR TRANSITION MODE ===
                # Apply brightness correction factor to sequential ramping results
                # This compensates for sensor differences and scene variations
                # that the seed-based ramping doesn't account for
                if self._brightness_correction_factor != 1.0:
                    corrected_exposure = target_exposure * self._brightness_correction_factor
                    # Clamp to valid range
                    max_exp = self.config["adaptive_timelapse"]["night_mode"]["max_exposure_time"]
                    min_exp = (
                        self.config["adaptive_timelapse"]
                        .get("day_mode", {})
                        .get("exposure_time", 0.01)
                    )
                    corrected_exposure = max(min_exp, min(max_exp, corrected_exposure))
                    logger.debug(
                        f"[Transition] Brightness correction: {target_exposure:.4f}s × "
                        f"{self._brightness_correction_factor:.3f} = {corrected_exposure:.4f}s"
                    )
                    target_exposure = corrected_exposure

                # === EMERGENCY BRIGHTNESS CORRECTION ===
                # Apply immediate correction when brightness is severely off-target
                emergency_factor = self._get_emergency_brightness_factor(self._last_brightness)
                if emergency_factor != 1.0:
                    target_exposure *= emergency_factor
                    max_exp = self.config["adaptive_timelapse"]["night_mode"]["max_exposure_time"]
                    target_exposure = min(max_exp, target_exposure)
                    logger.debug(
                        f"[Transition] Emergency factor {emergency_factor:.2f} → "
                        f"exposure now {target_exposure:.4f}s"
                    )

                # === EV SAFETY CLAMP ===
                # Ensure first manual frame matches last auto frame exactly
                target_exposure, target_gain = self._apply_ev_safety_clamp(
                    target_exposure, target_gain
                )

                # Apply smooth interpolation to prevent jumps
                # Use fast ramp-up for underexposure or fast ramp-down for overexposure
                if self._underexposure_detected:
                    exposure_speed = self._get_rampup_speed()
                elif self._overexposure_detected:
                    exposure_speed = self._get_rampdown_speed()
                else:
                    exposure_speed = None
                smooth_gain = self._interpolate_gain(target_gain)
                smooth_exposure = self._interpolate_exposure(target_exposure, exposure_speed)

                settings["ExposureTime"] = int(smooth_exposure * 1_000_000)
                settings["AnalogueGain"] = smooth_gain

                # ALWAYS use manual WB during transitions to prevent flickering
                # AWB causes sudden color shifts - instead we smoothly interpolate
                settings["AwbEnable"] = 0

                # Get smoothly interpolated colour gains
                target_gains = self._get_target_colour_gains(mode, position)
                smooth_gains = self._interpolate_colour_gains(target_gains, position)
                settings["ColourGains"] = smooth_gains

                logger.info(
                    f"Transition mode: lux={lux:.2f}, position={position:.2f}, "
                    f"exposure={smooth_exposure:.2f}s, gain={smooth_gain:.2f}, "
                    f"WB=[{smooth_gains[0]:.2f}, {smooth_gains[1]:.2f}]"
                )
            else:
                # Legacy fallback when smooth_transition is disabled
                # Use middle values between day and night
                exposure_seconds = 5.0
                settings["ExposureTime"] = int(exposure_seconds * 1_000_000)  # 5 seconds
                settings["AnalogueGain"] = 2.5  # Sensible middle value
                settings["AwbEnable"] = 0
                # Use interpolated colour gains
                target_gains = self._get_target_colour_gains(mode, 0.5)
                smooth_gains = self._interpolate_colour_gains(target_gains)
                settings["ColourGains"] = smooth_gains

        return settings

    def take_test_shot(self) -> Tuple[str, Dict]:
        """
        Take a quick test shot to measure light levels.

        Returns:
            Tuple of (image_path, metadata)
        """
        logger.info("Taking test shot to measure light levels...")

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

            # Add exposure calculation targets (what we calculated, before interpolation)
            if lux is not None:
                target_exposure = self._calculate_target_exposure_from_lux(lux)
                target_gain = self._calculate_target_gain_from_lux(lux)
                diagnostics["target_exposure_s"] = round(target_exposure, 6)
                diagnostics["target_exposure_ms"] = round(target_exposure * 1000, 2)
                diagnostics["target_gain"] = round(target_gain, 2)

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
            diagnostics["brightness_correction_factor"] = round(
                self._brightness_correction_factor, 4
            )
            diagnostics["target_brightness"] = self._target_brightness
            if self._last_brightness is not None:
                diagnostics["last_brightness"] = round(self._last_brightness, 2)

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

                        # Apply proactive exposure correction based on test shot brightness
                        # This helps prevent overexposure during rapid light changes
                        self._apply_proactive_exposure_correction(test_image_path, raw_lux)

                        # Detect rapid lux changes for faster transition speeds
                        self._detect_rapid_lux_change(raw_lux)

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
                            self._ev_clamp_applied = False
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
                    logger.debug(
                        f"Skipping test shot (frame {self.frame_count}), "
                        f"reusing mode={mode}, lux={lux:.2f if lux else 'N/A'}"
                    )

                # Initialize camera on first frame or if it was closed
                if capture is None:
                    logger.info("Initializing camera for timelapse...")
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
                                self._apply_brightness_feedback(actual_brightness)
                                # Check for overexposure and enable fast ramp-down if needed
                                self._check_overexposure(brightness_metrics)
                                # Check for underexposure at min exposure and enable fast recovery
                                self._check_underexposure(brightness_metrics)
                        except Exception as e:
                            logger.debug(f"Could not apply brightness feedback: {e}")

                    # Note: ML v2 does not learn frame-by-frame like v1
                    # It trains from the database on initialization (daily retrain)
                    # This avoids reinforcing bad exposures during problematic transitions

                    # Update day WB reference from actual capture metadata
                    # This allows us to learn good daylight WB values for smooth transitions
                    # Also store for Holy Grail seeding when entering transition
                    if metadata_path and mode == LightMode.DAY:
                        try:
                            import json

                            with open(metadata_path, "r") as f:
                                capture_metadata = json.load(f)
                            self._update_day_wb_reference(capture_metadata)
                            # Store for Holy Grail seeding
                            self._last_day_capture_metadata = capture_metadata
                        except Exception as e:
                            logger.debug(f"Could not read capture metadata for WB reference: {e}")

                    # Store capture in database for historical analysis
                    if self._database is not None:
                        try:
                            # Load metadata if not already loaded
                            import json

                            if metadata_path:
                                with open(metadata_path, "r") as f:
                                    db_metadata = json.load(f)
                            else:
                                db_metadata = {}

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
