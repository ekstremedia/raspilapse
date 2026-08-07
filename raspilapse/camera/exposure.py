"""Deciding what the camera should do, from what the last frame looked like.

One feedback loop and one ladder. The loop asks for more or less light; the
ladder decides whether that means a longer shutter or more gain.

    required = last * (target / measured) ** damping
    shutter, gain = allocate(required, ceiling, max_gain)

There are no modes in that. There used to be three, chosen by comparing an
uncalibrated lux figure against absolute thresholds -- `night: 3`, `day: 80` --
which had to be retuned per camera and per site, and were overridden at high
latitude by sun elevation because the thresholds could not survive being moved.
`_settings_day` and `_settings_transition` turned out to be the same function
with different log strings, and `_settings_night` differed only in where it
started. See ladder.py.

The mode name survives as a label on the output -- the database column, the
overlay, the graph scripts -- derived from the settings rather than deciding
them.

There is no handover across that label either, and there deliberately is not
one. `seed_from_metadata` used to reseed the loop at the day-to-transition
crossing from the last daylight frame's *camera* metadata. Its colour half was
removed first, for bypassing the wb_speed cross-fade it was meant to feed; its
exposure half was removed for the same class of reason. The metadata reports
what the sensor *did*, and this sensor's analogue gain floor is 1.1228 while
the ladder commands 1.0 -- so reseeding multiplied `_required` by 1.12 at every
dusk, a 0.17-stop step measured on nine consecutive nights. Nothing needed
seeding: `_required` already holds the last commanded value, which is the
correct continuation. A boundary that no decision consults cannot need state
carried across it.

This module deliberately knows nothing about the camera, the clock, or where
on Earth it is running.
"""

import math
from typing import Any, Dict, Optional, Tuple

from raspilapse.camera import ladder
from raspilapse.camera.ladder import LightMode
from raspilapse.camera.metering import BrightnessZones, Meter, highlight_factor  # noqa: F401
from raspilapse.logging_setup import get_logger

logger = get_logger("exposure")

# Exposure to start from with no measurement and no seed: short enough not to
# blow out a daylight first frame, long enough to see something indoors.
COLD_START_EXPOSURE_S = 0.02

# Largest single-frame correction the loop will ask for, as a ratio of the
# current exposure. With damping 0.5 this is a 2x change per frame.
MAX_CORRECTION = 4.0
MIN_CORRECTION = 0.25


class ExposureController:
    """Decides camera settings from measured light.

    Owns every piece of per-frame exposure state. Nothing else writes it: the
    capture loop reports what it measured via observe_frame() and asks for
    settings via decide().
    """

    def __init__(self, config: Dict):
        """
        Args:
            config: Full configuration dictionary
        """
        self.config = config
        adaptive = config.get("adaptive_timelapse", {})
        transition = adaptive.get("transition_mode", {})

        self.meter = Meter(config)

        # The ladder's limits, which are the camera's limits.
        night = adaptive.get("night_mode", {})
        self._max_shutter = night.get("max_exposure_time", 20.0)
        self._max_gain = night.get("analogue_gain", 6)

        # The single quantity the loop controls: shutter seconds times gain.
        self._required: Optional[float] = None
        self._shutter: Optional[float] = None
        self._gain: Optional[float] = None
        self._position: float = 0.0
        self._mode: Optional[str] = None

        self._damping = adaptive.get("brightness_damping", 0.5)

        # White balance
        self._day_wb_reference: Optional[Tuple[float, float]] = None
        self._last_colour_gains: Optional[Tuple[float, float]] = None
        self._wb_speed = transition.get("wb_transition_speed", 0.15)

        # Lux is no longer an input to anything. It is measured, smoothed and
        # recorded because the overlay shows it and the graphs plot it.
        self._smoothed_lux: Optional[float] = None
        self._lux_smoothing = transition.get("lux_smoothing_factor", 0.3)

        # What decide() decided, for the metadata diagnostics -- so the
        # calculation never runs twice per frame.
        self._last_decision: Dict = {}

        hdr = adaptive.get("hdr", {})
        self._hdr_enabled = hdr.get("enabled", False)
        self._hdr_bright = hdr.get("day_mode", "SingleExposure")
        self._hdr_dark = hdr.get("night_mode", "Off")
        self._hdr_enum = None
        if self._hdr_enabled:
            try:
                import libcamera

                self._hdr_enum = libcamera.controls.HdrModeEnum
                logger.info(f"[HDR] Enabled: bright={self._hdr_bright}, dark={self._hdr_dark}")
            except (ImportError, AttributeError):
                logger.info("[HDR] HdrModeEnum not available (Pi 4/vc4) - HDR controls are no-op")

    # ---------------------------------------------------------------- state --

    def observe_frame(self, brightness_metrics: Dict) -> None:
        """Record what the camera actually produced."""
        self.meter.set_dark_end(self._mode == LightMode.NIGHT)
        self.meter.observe(brightness_metrics)

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
        of the light and is blown out or black.

        Only non-None values are applied, so a partial row still helps.
        """
        if exposure_time is not None:
            self._shutter = exposure_time
        if analogue_gain is not None:
            self._gain = analogue_gain
        if exposure_time is not None:
            # The ladder's state is the product. A row with an exposure but no
            # gain is still worth having; assume the gain floor.
            self._required = exposure_time * (analogue_gain or 1.0)
            self._position = ladder.position(self._required, self._max_shutter, self._max_gain)
        if colour_gains is not None:
            self._last_colour_gains = tuple(colour_gains)
        if brightness is not None:
            self.meter.seed_brightness(brightness)
        if lux is not None:
            self._smoothed_lux = lux
        if mode is not None:
            self._mode = mode

    # Read-only views for the capture loop and the metadata diagnostics.
    @property
    def smoothed_lux(self) -> Optional[float]:
        return self._smoothed_lux

    @property
    def last_mode(self) -> Optional[str]:
        return self._mode

    @property
    def last_brightness(self) -> Optional[float]:
        return self.meter.brightness

    @property
    def ladder_position(self) -> float:
        """Where the last decision sat, 0 (bright) to 1 (dark)."""
        return self._position

    def diagnostics(self) -> Dict:
        """Everything worth writing into a frame's metadata JSON."""
        data = dict(self._last_decision)
        data.update(self.meter.diagnostics())
        data["ladder_position"] = round(self._position, 4)
        if self._shutter is not None:
            data["applied_exposure_s"] = round(self._shutter, 6)
            data["applied_exposure_ms"] = round(self._shutter * 1000, 2)
        if self._gain is not None:
            data["applied_gain"] = round(self._gain, 3)
        return data

    def smooth_lux(self, raw_lux: Optional[float]) -> Optional[float]:
        """Exponential moving average of the measured lux.

        Cosmetic. Nothing decides anything from this any more -- it is written
        to the database and shown in the overlay, and smoothing keeps the
        displayed figure from jittering frame to frame.
        """
        if raw_lux is None:
            return self._smoothed_lux
        if self._smoothed_lux is None:
            self._smoothed_lux = raw_lux
        else:
            alpha = self._lux_smoothing
            self._smoothed_lux = alpha * raw_lux + (1 - alpha) * self._smoothed_lux
        return self._smoothed_lux

    # ------------------------------------------------------------- decision --

    def decide(self) -> Dict[str, Any]:
        """Choose camera settings for the next frame.

        This replaced determine_mode() + apply_hysteresis() + a three-way
        dispatch on the result. There is nothing to flip between now, so there
        is nothing to apply hysteresis to.
        """
        required = self._required_exposure()
        required = self._rate_limit(required)

        shutter, gain = ladder.allocate(required, self._max_shutter, self._max_gain)
        position = ladder.position(required, self._max_shutter, self._max_gain)
        mode = ladder.label(shutter, gain, self._max_shutter)

        self._required = required
        self._shutter = shutter
        self._gain = gain
        self._position = position
        self._mode = mode

        settings: Dict[str, Any] = {
            "AeEnable": 0,
            "ExposureTime": int(shutter * 1_000_000),
            "AnalogueGain": gain,
        }
        colour_gains = self._apply_wb(settings, position)
        self._apply_hdr(settings, mode)

        self._last_decision = {
            "required_exposure": round(required, 6),
            "target_exposure_s": round(shutter, 6),
            "target_exposure_ms": round(shutter * 1000, 2),
            "target_gain": round(gain, 3),
            "mode": mode,
        }

        logger.info(
            f"{mode.capitalize()}: exposure={shutter:.4f}s, gain={gain:.2f}, "
            f"ladder={position:.3f}, WB=[{colour_gains[0]:.2f}, {colour_gains[1]:.2f}]"
        )
        return settings

    def _required_exposure(self) -> float:
        """How much light the next frame should gather, from how the last looked.

        Direct proportional feedback: no lookup table, no per-camera
        calibration. Converges in three to five frames from anywhere.
        """
        measured = self.meter.brightness

        # Only a missing measurement is missing. A frame that came back almost
        # black is a measurement, and an emphatic one -- it means open up, hard.
        #
        # The old guard was `measured is None or measured < 1`, which held the
        # exposure where it was whenever the frame was very dark. Under the
        # three modes that never mattered, because night pinned the shutter at
        # its ceiling regardless. On the ladder it is the difference between
        # recovering from darkness and sitting in it: a simulated polar night
        # left the loop stuck at its cold-start 20 ms for 300 frames.
        if measured is None:
            if self._required is not None:
                logger.warning(
                    f"[Feedback] No measurement, holding {self._required:.4f} second-gain"
                )
                return self._required
            logger.info(f"[Feedback] Cold start at {COLD_START_EXPOSURE_S}s")
            return COLD_START_EXPOSURE_S

        if self._required is None:
            logger.info(f"[Feedback] First frame: starting at {COLD_START_EXPOSURE_S}s")
            return COLD_START_EXPOSURE_S

        target = self.meter.target(dark=self._mode == LightMode.NIGHT)

        # A floor rather than a bail-out: at brightness 0 the ratio is
        # unbounded. Its exact value is not a tuning knob -- any floor below
        # target/MAX_CORRECTION, which is 30 at the default target, produces
        # the same clamped ratio. It exists only to keep the division defined.
        ratio = max(MIN_CORRECTION, min(MAX_CORRECTION, target / max(1.0, measured)))

        required = self._required * (ratio**self._damping)
        ceiling = self._max_shutter * self._max_gain
        return max(ladder.MIN_SHUTTER_S, min(ceiling, required))

    def _rate_limit(self, target: float) -> float:
        """Cap how far along the ladder one frame may move.

        Log-space, because a fixed fraction of a stop looks the same whether
        the exposure is a millisecond or ten seconds. The damping above already
        limits ordinary movement; this is what stops a cloud crossing the sun
        from producing a visible step, and what the recovery rates override
        when a frame comes back genuinely wrong.
        """
        if self._required is None:
            return target

        speed = self.meter.speed(self._position)
        log_last = math.log10(max(ladder.MIN_SHUTTER_S, self._required))
        log_target = math.log10(max(ladder.MIN_SHUTTER_S, target))
        moved = 10 ** (log_last + speed * (log_target - log_last))

        ceiling = self._max_shutter * self._max_gain
        return max(ladder.MIN_SHUTTER_S, min(ceiling, moved))

    # ------------------------------------------------------- white balance --

    def update_day_wb_reference(self, metadata: Dict):
        """Learn what neutral looks like, from a frame the ISP metered itself.

        Only worth doing at the bright end, where AWB has enough signal, and
        only useful to a camera with no `fixed_colour_gains` -- see
        _target_colour_gains, where the configured value wins.

        The metadata has to come from the AWB reference shot. Feeding it an
        ordinary capture instead reads back the manual gains the controller
        just applied, which makes the reference its own input: it holds
        whatever it last was, and any step in colour is adopted permanently
        rather than corrected.
        """
        if self._mode != LightMode.DAY:
            return
        gains = metadata.get("ColourGains")
        if not gains or len(gains) < 2:
            return
        self._day_wb_reference = (float(gains[0]), float(gains[1]))
        logger.debug(
            f"[WB] Daylight reference: R={gains[0]:.2f} B={gains[1]:.2f}",
        )

    def _wb_position(self, position: float) -> float:
        """Where in the day-to-night colour cross-fade this ladder position sits.

        The cross-fade spans the transition band -- from the day knee to the
        night knee -- rather than the whole ladder, so daylight frames get
        daylight colour rather than something 10% of the way to night.

        It used to be the lux figure's position between two configured
        thresholds, which made a twilight frame's colour depend on numbers
        tuned for one camera at one latitude.
        """
        day_edge = ladder.position(
            self._max_shutter * ladder.DAY_KNEE, self._max_shutter, self._max_gain
        )
        night_edge = ladder.position(
            self._max_shutter * ladder.NIGHT_KNEE, self._max_shutter, self._max_gain
        )
        if night_edge <= day_edge:
            return 0.0
        return max(0.0, min(1.0, (position - day_edge) / (night_edge - day_edge)))

    def _target_colour_gains(self, position: float) -> Tuple[float, float]:
        """Cross-fade between the daylight white point and the configured night gains.

        `fixed_colour_gains` wins over the learned reference, and the learned
        reference is the fallback for a camera that has not configured one.
        That order was inverted for a while: the config value was read and then
        unconditionally overwritten, so a configured white point silently did
        nothing and the daylight colour wandered instead of staying put.
        """
        adaptive = self.config.get("adaptive_timelapse", {})
        night = adaptive.get("night_mode", {}).get("colour_gains") or [1.83, 2.02]

        fixed = adaptive.get("day_mode", {}).get("fixed_colour_gains")
        day = list(fixed) if fixed else list(self._day_wb_reference or (2.5, 1.6))

        into = self._wb_position(position)
        return (
            day[0] + into * (night[0] - day[0]),
            day[1] + into * (night[1] - day[1]),
        )

    def _apply_wb(self, settings: Dict, position: float) -> Tuple[float, float]:
        """Set manual white balance and return the gains applied.

        Manual in every mode: AWB drifting between frames is the main source of
        colour flicker in a timelapse, and at the dark end it also costs about
        a 5x exposure penalty while the ISP hunts.
        """
        target = self._target_colour_gains(position)

        if self._last_colour_gains is None:
            applied = target
        else:
            applied = (
                self._last_colour_gains[0]
                + self._wb_speed * (target[0] - self._last_colour_gains[0]),
                self._last_colour_gains[1]
                + self._wb_speed * (target[1] - self._last_colour_gains[1]),
            )

        self._last_colour_gains = applied
        settings["AwbEnable"] = 0
        settings["ColourGains"] = applied
        return applied

    def _apply_hdr(self, settings: Dict, mode: str) -> None:
        """Set HdrMode where libcamera exposes it. No-op on Pi 4 / vc4."""
        if not self._hdr_enabled or self._hdr_enum is None:
            return
        name = self._hdr_dark if mode == LightMode.NIGHT else self._hdr_bright
        try:
            settings["HdrMode"] = getattr(self._hdr_enum, name)
        except AttributeError:
            logger.debug(f"[HDR] Unknown mode {name!r}, leaving HDR unset")
