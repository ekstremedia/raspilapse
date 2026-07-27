"""Exposure control for the adaptive timelapse.

Everything that decides *what the camera should do* lives here: mode selection
and hysteresis, the interpolators, the brightness feedback controller, highlight
protection, and white-balance seeding. It owns all the per-frame exposure state.

Deliberately knows nothing about the camera, the filesystem or the database.
AdaptiveTimelapse holds one of these and feeds it measurements; the solar
position it needs is passed in rather than computed here.
"""

from typing import Any, Dict, Optional

from raspilapse.logging_setup import get_logger

logger = get_logger("auto_timelapse")


class LightMode:
    """Light mode constants."""

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
        # safe -> 1.00, warning -> 0.95.
        # No divide-by-zero guard is needed for safe == warning: the
        # `p95 <= safe` return above already covers every p95 that could reach
        # this branch. Verified exhaustively over all threshold/p95 combinations.
        return 1.0 - ((p95 - safe) / (warning - safe)) * 0.05

    if p95 <= critical:
        # warning -> 0.95, critical -> 0.85. Same reasoning for warning == critical.
        return 0.95 - ((p95 - warning) / (critical - warning)) * 0.10

    # critical -> 0.85, and downhill from there to the floor
    return max(floor, 0.85 - ((p95 - critical) / 15) * 0.15)


class ExposureController:
    """Decides camera settings from measured light.

    Owns every piece of per-frame exposure state. Nothing else writes it: the
    capture loop reports what it measured via observe_frame() and asks for
    settings via get_camera_settings().
    """

    def __init__(self, config: Dict):
        """
        Args:
            config: Full configuration dictionary
        """
        self.config = config
        adaptive_config = config.get("adaptive_timelapse", {})
        transition_config = adaptive_config.get("transition_mode", {})

        # Transition smoothing state
        self._smoothed_lux: float = None  # Exponential moving average of lux
        self._last_mode: str = None  # Previous mode for hysteresis
        self._mode_hold_count: int = 0  # Counter for hysteresis
        self._day_wb_reference: tuple = None  # AWB gains from bright daylight
        self._last_colour_gains: tuple = None  # Previous frame's colour gains
        self._last_analogue_gain: float = None  # Previous frame's analogue gain
        self._last_exposure_time: float = None  # Previous frame's exposure time

        # Brightness feedback state
        self._last_brightness: float = None  # Previous frame's mean brightness
        self._last_p95: float = None  # Previous frame's 95th percentile

        # Highlight protection. See _highlight_target_scale.
        hp = adaptive_config.get("highlight_protection", {})
        self._p95_enabled = hp.get("enabled", False)
        self._p95_safe = hp.get("safe_p95", 200)
        self._p95_warning = hp.get("warning_p95", 220)
        self._p95_critical = hp.get("critical_p95", 240)
        self._p95_floor = hp.get("min_scale", 0.70)
        self._p95_slew = hp.get("slew", 0.25)
        self._p95_apply_in_night = hp.get("apply_in_night", False)
        self._p95_scale: float = 1.0
        self._last_highlight_scale: float = 1.0

        # What get_camera_settings decided for the current frame. Written by
        # the branch that ran, read by the metadata diagnostics -- so the
        # exposure calculation never runs twice per frame.
        self._last_decision: Dict = {}

        # Over/underexposure detection, for the fast ramp speeds
        self._overexposure_detected: bool = False
        self._overexposure_severity: str = None  # "warning" or "critical"
        self._underexposure_detected: bool = False
        self._underexposure_severity: str = None

        # Holy Grail transition state, seeded from actual camera metadata
        self._transition_seeded: bool = False
        self._seed_exposure: float = None
        self._seed_gain: float = None
        self._seed_wb_gains: tuple = None

        # Interpolation speeds
        self._lux_smoothing_factor = transition_config.get("lux_smoothing_factor", 0.3)
        self._hysteresis_frames = transition_config.get("hysteresis_frames", 3)
        self._wb_transition_speed = transition_config.get("wb_transition_speed", 0.15)
        self._gain_transition_speed = transition_config.get("gain_transition_speed", 0.15)
        self._exposure_transition_speed = transition_config.get("exposure_transition_speed", 0.15)

        # Brightness feedback config
        self._target_brightness = transition_config.get("target_brightness", 120)

        # Contrast-aware brightness target (overcast boost)
        bt_config = adaptive_config.get("brightness_target", {})
        self._base_target_brightness = bt_config.get("base", 120)
        self._overcast_boost = bt_config.get("overcast_boost", 15)
        self._max_target_brightness = bt_config.get("max_target", 140)
        self._contrast_threshold_low = bt_config.get("contrast_threshold_low", 25)
        self._contrast_threshold_high = bt_config.get("contrast_threshold_high", 40)

        # Ramp speeds for over/underexposure recovery
        self._fast_rampdown_speed = transition_config.get("fast_rampdown_speed", 0.30)
        self._critical_rampdown_speed = transition_config.get("critical_rampdown_speed", 0.70)
        self._fast_rampup_speed = transition_config.get("fast_rampup_speed", 0.50)
        self._critical_rampup_speed = transition_config.get("critical_rampup_speed", 0.70)

        # HDR
        hdr_config = adaptive_config.get("hdr", {})
        self._hdr_enabled = hdr_config.get("enabled", False)
        self._hdr_day_mode = hdr_config.get("day_mode", "SingleExposure")
        self._hdr_night_mode = hdr_config.get("night_mode", "Off")
        self._hdr_enum_available = False
        self._hdr_mode_enum = None
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

    # ---------------------------------------------------------------- state --

    def observe_frame(self, brightness_metrics: Dict) -> None:
        """
        Record what the camera actually produced.

        The only per-frame writer of the controller's inputs. Everything the
        next get_camera_settings() call reacts to arrives through here.

        Args:
            brightness_metrics: Lores-stream metrics from the capture
        """
        if not brightness_metrics:
            return

        std_brightness = brightness_metrics.get("std_brightness")
        if std_brightness is not None:
            old_target = self._target_brightness
            self._target_brightness = self._get_dynamic_target_brightness(std_brightness)
            if self._target_brightness != old_target:
                delta = abs(self._target_brightness - old_target)
                message = (
                    f"[Overcast] Dynamic target: {old_target} -> {self._target_brightness} "
                    f"(std_brightness={std_brightness:.1f})"
                )
                # Most ticks move the target by a point or two; only say so out
                # loud when the change is meaningful.
                logger.info(message) if delta >= 5 else logger.debug(message)

        self._last_brightness = brightness_metrics.get("mean_brightness")
        self._last_p95 = brightness_metrics.get("percentile_95")
        self._check_overexposure(brightness_metrics)
        self._check_underexposure(brightness_metrics)

    def seed_from_capture(
        self,
        exposure_time: Optional[float] = None,
        analogue_gain: Optional[float] = None,
        colour_gains: Optional[tuple] = None,
        brightness: Optional[float] = None,
        lux: Optional[float] = None,
        mode: Optional[str] = None,
    ) -> None:
        """
        Prime the controller from a previous run's last good capture.

        Without this, the first frame after a restart is taken with no memory
        of the light: the test shot comes back saturated on a cold ISP, the
        calculated lux is wrong, and the frame is blown out.

        Only non-None values are applied, so a partial row still helps.
        """
        if exposure_time is not None:
            self._last_exposure_time = exposure_time
            self._seed_exposure = exposure_time
        if analogue_gain is not None:
            self._last_analogue_gain = analogue_gain
            self._seed_gain = analogue_gain
        if colour_gains is not None:
            self._last_colour_gains = colour_gains
            self._seed_wb_gains = colour_gains
        if brightness is not None:
            self._last_brightness = brightness
        if lux is not None:
            self._smoothed_lux = lux
        if mode is not None:
            self._last_mode = mode

    def reset_seed_state(self) -> None:
        """Forget the Holy Grail seed, e.g. on returning to day mode."""
        self._transition_seeded = False

    def transition_position(self, lux: Optional[float]) -> Optional[float]:
        """Where in the night-to-day range this lux sits, clamped to 0..1."""
        if lux is None:
            return None
        thresholds = self.config["adaptive_timelapse"]["light_thresholds"]
        span = thresholds["day"] - thresholds["night"]
        if span <= 0:
            return None
        return max(0.0, min(1.0, (lux - thresholds["night"]) / span))

    # Read-only views for the capture loop and the metadata diagnostics.
    @property
    def smoothed_lux(self) -> Optional[float]:
        return self._smoothed_lux

    @property
    def last_mode(self) -> Optional[str]:
        return self._last_mode

    @property
    def last_brightness(self) -> Optional[float]:
        return self._last_brightness

    @property
    def transition_seeded(self) -> bool:
        return self._transition_seeded

    @property
    def seed_exposure(self) -> Optional[float]:
        return self._seed_exposure

    def diagnostics(self) -> Dict:
        """Everything worth writing into a frame's metadata JSON."""
        data = dict(self._last_decision)
        if self._last_exposure_time is not None:
            data["interpolated_exposure_s"] = round(self._last_exposure_time, 6)
            data["interpolated_exposure_ms"] = round(self._last_exposure_time * 1000, 2)
        if self._last_analogue_gain is not None:
            data["interpolated_gain"] = round(self._last_analogue_gain, 2)
        data["hysteresis_hold_count"] = self._mode_hold_count
        data["hysteresis_last_mode"] = self._last_mode
        data["target_brightness"] = self._target_brightness
        data["base_target_brightness"] = self._base_target_brightness
        data["overcast_boost_active"] = self._target_brightness > self._base_target_brightness
        if self._last_brightness is not None:
            data["last_brightness"] = round(self._last_brightness, 2)
        if self._last_p95 is not None:
            data["last_p95"] = round(self._last_p95, 2)
        if self._last_highlight_scale < 1.0:
            data["highlight_scale"] = round(self._last_highlight_scale, 3)
            data["effective_target_brightness"] = round(
                self._target_brightness * self._last_highlight_scale, 1
            )
        return data

    # ------------------------------------------------------------- decisions --

    def smooth_lux(self, raw_lux: float) -> float:
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

    def apply_hysteresis(self, new_mode: str) -> str:
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

        # `or` rather than a .get default: a failed measurement sets the key
        # to None, which a default does not cover.
        mean_brightness = brightness_metrics.get("mean_brightness") or 0
        overexposed_pct = brightness_metrics.get("overexposed_percent") or 0

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

        # 128 is "unremarkable" -- neither over nor under -- so a missing or
        # None measurement leaves the flags where they are.
        mean_brightness = brightness_metrics.get("mean_brightness") or 128

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

    def update_day_wb_reference(self, metadata: Dict):
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

    def seed_from_metadata(self, metadata: Dict, capture_metadata: Dict = None):
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

    def log_transition_progress(self, lux: float, position: float):
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

    def determine_mode(
        self, lux: float, sun_elevation: float = None, is_polar_day: bool = False
    ) -> str:
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
        if is_polar_day:
            # The caller decides polar day from its own elevation reading, so
            # this one can be absent without the override being wrong. Losing
            # the whole mode decision to a log line would be worse.
            elev = f"{sun_elevation:.1f}°" if sun_elevation is not None else "unknown"
            logger.info(f"[Polar] Sun: {elev} | Lux: {lux:.1f} | Mode: Polar Day (override)")
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
        sun_elev = sun_elevation
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
            lux: Current lux value; None until the first test shot succeeds

        Returns:
            Dictionary of camera control settings
        """
        if mode == LightMode.NIGHT:
            settings = self._settings_night(lux)
        elif mode == LightMode.DAY:
            settings = self._settings_day(lux)
        else:
            settings = self._settings_transition(lux)

        self._apply_hdr(settings, mode)
        return settings

    def _settings_night(self, lux: float = None) -> Dict:
        """Night: long exposure at high gain, pulled back only if the scene is bright."""
        adaptive_config = self.config["adaptive_timelapse"]
        settings: Dict[str, Any] = {}
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
                # seed_from_capture applies only the fields the database row
                # actually had, so a partial row can leave this None while
                # brightness is set. Fall back to the configured night gain.
                current_gain = (
                    night_gain if self._last_analogue_gain is None else self._last_analogue_gain
                )
                target_gain = max(2.0, current_gain * brightness_ratio**0.5)
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
            self._last_analogue_gain is not None and self._last_analogue_gain < target_gain * 0.5
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

        smooth_gains = self._apply_wb(settings, LightMode.NIGHT)

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

        return settings

    def _settings_day(self, lux: float = None) -> Dict:
        """Day: shutter does the work, gain pinned at its floor."""
        adaptive_config = self.config["adaptive_timelapse"]
        settings: Dict[str, Any] = {}
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
            self._apply_wb(settings, LightMode.DAY)
        else:
            # Opt-out: let the ISP run AWB. Expect colour flicker between frames.
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

        return settings

    def _settings_transition(self, lux: float = None) -> Dict:
        """Transition: shutter first, then gain once the shutter nears its ceiling."""
        adaptive_config = self.config["adaptive_timelapse"]
        settings: Dict[str, Any] = {}
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

        smooth_gains = self._apply_wb(settings, LightMode.TRANSITION, position)

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

        return settings

    def _apply_wb(self, settings: Dict, mode: str, position: float = None) -> tuple:
        """
        Set manual white balance on `settings` and return the applied gains.

        Manual WB in every mode: AWB drifting between frames is the main source
        of colour flicker in a timelapse, and at night it also costs about a 5x
        slowdown on long exposures.
        """
        settings["AwbEnable"] = 0
        target_gains = self._get_target_colour_gains(mode, position)
        smooth_gains = self._interpolate_colour_gains(target_gains, position)
        settings["ColourGains"] = smooth_gains
        return smooth_gains

    def _apply_hdr(self, settings: Dict, mode: str) -> None:
        """Add the HDR control in place, if the sensor exposes one."""
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
