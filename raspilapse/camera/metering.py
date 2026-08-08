"""Reading the frame the camera just produced, and deciding what to aim for.

Everything here answers one of two questions:

    how bright should the next frame be?    -> target(), highlight_factor()
    how fast may we move towards it?        -> speed()

Both used to live inside ExposureController, mixed in with the exposure
arithmetic and the three per-mode settings builders. They are separable
because they never look at shutter speed or gain: they take measured
brightness in and produce a target and a rate out.
"""

from typing import Dict, Optional

from raspilapse.logging_setup import get_logger

logger = get_logger("exposure")


class BrightnessZones:
    """Mean-brightness bands, on the 0-255 scale the metering reports.

    WARNING_HIGH and WARNING_LOW are the points at which the measurement is
    trusted over anything else. They are the only zone constants read from
    outside this module.
    """

    CRITICAL_HIGH = 170
    WARNING_HIGH = 160
    TARGET_HIGH = 140
    TARGET_LOW = 100
    WARNING_LOW = 80
    CRITICAL_LOW = 60


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

    Note that `floor` cannot bind below 0.70 for 8-bit input: the last segment
    reaches exactly 0.70 at p95 255, which is the highest value there is. A
    configured min_scale under 0.70 therefore does nothing.

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


class Meter:
    """What the last frame looked like, and what to do about it.

    Owns every brightness-derived piece of state: the measurement itself, the
    over- and under-exposure flags, the contrast-adaptive target, and the
    slew-limited highlight protection scale.
    """

    def __init__(self, config: Dict):
        """
        Args:
            config: Full configuration dictionary. Everything read here lives
                under adaptive_timelapse -- the brightness target and its
                overcast boost, the highlight-protection curve, and the rates
                the over/under flags select between.
        """
        adaptive = config.get("adaptive_timelapse", {})
        transition = adaptive.get("transition_mode", {})

        self._brightness: Optional[float] = None
        self._p95: Optional[float] = None

        # Contrast-adaptive target. Overcast scenes are flat and read as dark
        # at a fixed target, so the target rises as contrast falls.
        target_config = adaptive.get("brightness_target", {})
        self._base_target = target_config.get("base", 120)
        # Seed the working target from the same knob the dynamic target uses.
        # It used to seed from transition_mode.target_brightness -- a dead
        # key, so anyone lowering brightness_target.base still got 120 aimed
        # at for the first frame after every restart.
        self._target = self._base_target
        self._overcast_boost = target_config.get("overcast_boost", 15)
        self._max_target = target_config.get("max_target", 140)
        self._contrast_low = target_config.get("contrast_threshold_low", 25)
        self._contrast_high = target_config.get("contrast_threshold_high", 40)

        # Highlight protection. See highlight_factor.
        protection = adaptive.get("highlight_protection", {})
        self._p95_enabled = protection.get("enabled", True)
        self._p95_safe = protection.get("safe_p95", 200)
        self._p95_warning = protection.get("warning_p95", 220)
        self._p95_critical = protection.get("critical_p95", 240)
        self._p95_floor = protection.get("min_scale", 0.70)
        self._p95_slew = protection.get("slew", 0.25)
        self._p95_apply_in_dark = protection.get("apply_in_night", False)
        self._p95_scale: float = 1.0
        self._last_highlight_scale: float = 1.0

        # Two-tier over/under exposure, which picks the rate limit.
        self._over = False
        self._over_severity: Optional[str] = None
        self._under = False
        self._under_severity: Optional[str] = None

        self._normal_speed = transition.get("exposure_transition_speed", 0.15)
        self._fast_down = transition.get("fast_rampdown_speed", 0.30)
        self._critical_down = transition.get("critical_rampdown_speed", 0.70)
        self._fast_up = transition.get("fast_rampup_speed", 0.50)
        self._critical_up = transition.get("critical_rampup_speed", 0.70)

    # ------------------------------------------------------------- reading --

    @property
    def brightness(self) -> Optional[float]:
        """The last observed mean brightness, or None before the first frame."""
        return self._brightness

    @property
    def p95(self) -> Optional[float]:
        """The last observed 95th-percentile brightness (the highlight level)."""
        return self._p95

    def observe(self, metrics: Dict) -> None:
        """Record what the camera actually produced.

        The only per-frame writer of the metering state. Everything the next
        decision reacts to arrives through here.
        """
        if not metrics:
            return

        std = metrics.get("std_brightness")
        if std is not None:
            old = self._target
            self._target = self._dynamic_target(std)
            if self._target != old:
                message = (
                    f"[Overcast] Dynamic target: {old} -> {self._target} "
                    f"(std_brightness={std:.1f})"
                )
                # Most ticks move the target by a point or two; only say so out
                # loud when the change is meaningful.
                logger.info(message) if abs(self._target - old) >= 5 else logger.debug(message)

        self._brightness = metrics.get("mean_brightness")
        self._p95 = metrics.get("percentile_95")
        self._check_overexposure(metrics)
        self._check_underexposure(metrics)

    def seed_brightness(self, brightness: Optional[float]) -> None:
        """Prime the measurement from a previous run's last capture."""
        if brightness is not None:
            self._brightness = brightness

    # ------------------------------------------------------------- aiming --

    def target(self, dark: bool = False) -> float:
        """The brightness to aim the next frame at, after highlight protection.

        Args:
            dark: True at the dark end of the ladder, where highlight
                protection is off by default -- see _highlight_scale.
        """
        return self._target * self._highlight_scale(dark)

    @property
    def base_target(self) -> int:
        """The configured brightness_target.base, before any boost or scaling."""
        return self._base_target

    @property
    def raw_target(self) -> int:
        """The target before highlight protection scales it."""
        return self._target

    def _dynamic_target(self, std: Optional[float]) -> int:
        """Raise the target on flat, low-contrast scenes.

        An overcast sky has little contrast and reads as dark at a fixed
        target. Sunny scenes keep the base target; so does the dark end of the
        ladder, where raising it would wash out aurora and stars.
        """
        if self._at_dark_end:
            return self._base_target

        if std is None or std < 0:
            return self._base_target

        if std >= self._contrast_high:
            return self._base_target
        if std <= self._contrast_low:
            return min(int(round(self._base_target + self._overcast_boost)), self._max_target)

        into = (std - self._contrast_low) / (self._contrast_high - self._contrast_low)
        boosted = self._base_target + self._overcast_boost * (1.0 - into)
        return min(int(round(boosted)), self._max_target)

    # Set by the controller before observe(), because the overcast boost and
    # highlight protection are both suppressed at the dark end of the ladder.
    _at_dark_end: bool = False

    def set_dark_end(self, dark: bool) -> None:
        """Tell the meter it is near the dark end of the ladder.

        Two things are suppressed there: the overcast boost, which would wash
        out aurora and stars, and highlight protection, because a streetlamp in
        an otherwise dark frame is not a blown scene. The controller sets this
        before each observation rather than the meter working it out, since the
        ladder position is the controller's to know.
        """
        self._at_dark_end = bool(dark)

    def _highlight_scale(self, dark: bool) -> float:
        """Slew-limited highlight-protection scale for the brightness target.

        Scaling the target rather than the result keeps the loop
        single-equilibrium: the controller still converges on one exposure,
        just a slightly darker one.
        """
        if not self._p95_enabled:
            self._last_highlight_scale = 1.0
            return 1.0

        # Off at the dark end by default. Across 117k night frames here the
        # mean brightness is already 90 against a target of 120, while 11% of
        # frames exceed p95 200 -- streetlamps and the moon, not blown scenes.
        # Cutting exposure on those makes aurora frames worse, not better.
        if dark and not self._p95_apply_in_dark:
            self._last_highlight_scale = 1.0
            return 1.0

        raw = highlight_factor(
            self._p95,
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
        was_engaged = previous < 0.995
        is_engaged = self._p95_scale < 0.995
        if is_engaged != was_engaged:
            if is_engaged:
                logger.info(
                    f"[Highlight] Protection engaged: p95={self._p95:.0f}, "
                    f"target scaled to {self._p95_scale:.2f}"
                )
            else:
                logger.info("[Highlight] Protection released")
        else:
            logger.debug(f"[Highlight] p95={self._p95}, scale={self._p95_scale:.3f}")

        self._last_highlight_scale = self._p95_scale
        return self._p95_scale

    @property
    def highlight_scale(self) -> float:
        """The scale applied on the last call to target()."""
        return self._last_highlight_scale

    # -------------------------------------------------------------- pacing --

    def speed(self, position: float = 0.0) -> float:
        """How much of the gap to the target to close this frame, in log space.

        Scaled by where on the ladder we are. At the bright end the measurement
        is trustworthy and the scene changes slowly, so the frame goes straight
        to what the feedback asked for. At the dark end a 20-second exposure of
        an aurora is a noisy thing to meter, and moving a fraction of the way
        each frame is what keeps a timelapse from strobing.

        This reproduces what the old code did without the mode switch that did
        it: `_settings_day` applied no interpolation at all -- "the controller's
        own damping already limits how fast it moves" -- while `_settings_night`
        interpolated at 8% per frame. Making the rate uniform made the bright
        end sluggish and measurably worse; making it a straight line between
        the two ends does not.

        A frame that came back genuinely wrong overrides this, but only
        upwards: recovery may hurry, never dawdle.
        """
        smooth = position * self._normal_speed + (1.0 - position) * 1.0

        if self._under:
            recovery = self._critical_up if self._under_severity == "critical" else self._fast_up
            return max(smooth, recovery)
        if self._over:
            recovery = self._critical_down if self._over_severity == "critical" else self._fast_down
            return max(smooth, recovery)
        return smooth

    @property
    def overexposed(self) -> bool:
        """Whether the last frame tripped the overexposure flag."""
        return self._over

    @property
    def underexposed(self) -> bool:
        """Whether the last frame tripped the underexposure flag."""
        return self._under

    def _check_overexposure(self, metrics: Dict) -> bool:
        """Two-tier overexposure detection, driving the ramp-down rate.

        Sticky between the trigger and the release thresholds, so a frame
        sitting between them does not flap the rate every 30 seconds.
        """
        if not metrics:
            return self._over

        # `or` rather than a .get default: a failed measurement sets the key
        # to None, which a default does not cover.
        brightness = metrics.get("mean_brightness") or 0
        clipped = metrics.get("overexposed_percent") or 0

        warning, critical, safe = 150, 170, 130
        clipped_warning, clipped_safe = 5, 3

        was = self._over

        if brightness > critical or clipped > clipped_warning * 2:
            self._over = True
            self._over_severity = "critical"
            if not was:
                logger.warning(
                    f"[FastRamp] CRITICAL OVEREXPOSURE: brightness={brightness:.1f}, "
                    f"clipped={clipped:.1f}% - activating aggressive ramp-down"
                )
        elif brightness > warning or clipped > clipped_warning:
            self._over = True
            self._over_severity = "warning"
            if not was:
                logger.warning(
                    f"[FastRamp] OVEREXPOSURE WARNING: brightness={brightness:.1f}, "
                    f"clipped={clipped:.1f}% - activating fast ramp-down"
                )
        elif brightness < safe and clipped < clipped_safe:
            self._over = False
            self._over_severity = None
            if was:
                logger.info(
                    f"[FastRamp] Overexposure cleared: brightness={brightness:.1f}, "
                    f"clipped={clipped:.1f}% - resuming normal interpolation"
                )

        return self._over

    def _check_underexposure(self, metrics: Dict) -> bool:
        """Two-tier underexposure detection, driving the ramp-up rate.

        Applies everywhere on the ladder, not only at minimum exposure. That
        matters through dusk, where the exposure is climbing but lagging the
        light.
        """
        if not metrics:
            return self._under

        # A missing measurement reads as 128, which is above the release
        # threshold and therefore clears the flag rather than holding it --
        # dropping back to the ordinary rate beats hurrying on a stale
        # reading. Missing means None, though: a measured 0.0 is a real and
        # emphatic reading (lens cap, blackout), and the old `or 128` treated
        # it as absent -- clearing the underexposure flag at the exact moment
        # it mattered most and recovering 4.7x slower for it.
        brightness = metrics.get("mean_brightness")
        if brightness is None:
            brightness = 128

        warning, critical, safe = 90, 70, 105

        was = self._under

        if brightness < critical:
            self._under = True
            self._under_severity = "critical"
            if not was:
                logger.warning(
                    f"[FastRecovery] CRITICAL UNDEREXPOSURE: brightness={brightness:.1f} "
                    f"- activating aggressive ramp-up"
                )
        elif brightness < warning:
            self._under = True
            self._under_severity = "warning"
            if not was:
                logger.warning(
                    f"[FastRecovery] UNDEREXPOSURE WARNING: brightness={brightness:.1f} "
                    f"- activating fast ramp-up"
                )
        elif brightness > safe:
            self._under = False
            self._under_severity = None
            if was:
                logger.info(
                    f"[FastRecovery] Underexposure cleared: brightness={brightness:.1f} "
                    f"- resuming normal interpolation"
                )

        return self._under

    # --------------------------------------------------------- diagnostics --

    def diagnostics(self) -> Dict:
        """The metering half of a frame's diagnostics block."""
        data: Dict = {
            "target_brightness": self._target,
            "base_target_brightness": self._base_target,
            "overcast_boost_active": self._target > self._base_target,
        }
        if self._brightness is not None:
            data["last_brightness"] = round(self._brightness, 2)
        if self._p95 is not None:
            data["last_p95"] = round(self._p95, 2)
        if self._last_highlight_scale < 1.0:
            data["highlight_scale"] = round(self._last_highlight_scale, 3)
            data["effective_target_brightness"] = round(
                self._target * self._last_highlight_scale, 1
            )
        return data
