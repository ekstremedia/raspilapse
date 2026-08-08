"""Tests for the exposure controller.

What survives here after the ladder replaced mode selection: the feedback loop,
the rate limit, white balance, seeding across restarts, and the cosmetic lux.
The metering half moved to test_metering.py; allocation itself is in
test_ladder.py.

Deleted along with the code they covered: hysteresis (nothing discrete is left
to flip between), the three per-mode settings builders, the separate gain and
exposure interpolators, the hybrid brightness override, the entering-night
coordinated ramp, and the night gain-reduction path.
"""

import pytest

from raspilapse.camera.exposure import COLD_START_EXPOSURE_S, ExposureController
from raspilapse.camera.ladder import LightMode

MAX_SHUTTER = 20.0
MAX_GAIN = 6
WB_SPEED = 0.15  # matches make_config's wb_transition_speed


def make_config(**overrides):
    config = {
        "adaptive_timelapse": {
            "brightness_damping": 0.5,
            "night_mode": {
                "max_exposure_time": MAX_SHUTTER,
                "analogue_gain": MAX_GAIN,
                "colour_gains": [1.83, 2.02],
            },
            "day_mode": {"fixed_colour_gains": [2.5, 1.6]},
            "transition_mode": {
                "target_brightness": 120,
                "exposure_transition_speed": 0.08,
                "wb_transition_speed": 0.15,
                "lux_smoothing_factor": 0.3,
                "fast_rampdown_speed": 0.2,
                "critical_rampdown_speed": 0.7,
                "fast_rampup_speed": 0.2,
                "critical_rampup_speed": 0.7,
            },
            "brightness_target": {"base": 120, "overcast_boost": 15, "max_target": 140},
            "highlight_protection": {"enabled": False},
            "hdr": {"enabled": False},
        }
    }
    config["adaptive_timelapse"].update(overrides)
    return config


@pytest.fixture
def controller():
    return ExposureController(make_config())


def product(settings):
    """The exposure the settings actually deliver: seconds times gain."""
    return (settings["ExposureTime"] / 1e6) * settings["AnalogueGain"]


def converge(controller, luminance, frames=80):
    """Run a closed loop against a fixed scene and return the final settings.

    Closed, not open: the brightness fed back is what the controller's own
    choice would have produced. Feeding it recorded brightness instead lets it
    chase a measurement that never responds, which is not a thing a camera
    does -- and is exactly how the first version of the comparison tool
    produced a runaway that could not happen in reality.
    """
    settings = None
    for _ in range(frames):
        settings = controller.decide()
        brightness = max(0.0, min(255.0, luminance * product(settings)))
        controller.observe_frame({"mean_brightness": brightness, "std_brightness": 50.0})
    return settings


class TestColdStart:
    def test_first_frame_has_no_measurement_to_go_on(self, controller):
        assert product(controller.decide()) == pytest.approx(COLD_START_EXPOSURE_S)

    def test_it_still_produces_usable_settings(self, controller):
        settings = controller.decide()
        assert settings["AeEnable"] == 0
        assert settings["AwbEnable"] == 0
        assert settings["ExposureTime"] > 0
        assert settings["AnalogueGain"] >= 1.0

    def test_a_seed_beats_the_cold_start(self, controller):
        controller.seed_from_capture(exposure_time=2.0, analogue_gain=3.0)
        assert product(controller.decide()) == pytest.approx(6.0, rel=0.01)


class TestFeedback:
    # Six decades of light. The dark end stops at 2.0 because that is where
    # this camera runs out: 20 s at gain 6 is an exposure product of 120, so a
    # scene below luminance 1.0 cannot be brought to a brightness of 120 by any
    # setting. Darker scenes are covered by test_it_cannot_exceed_the_camera.
    @pytest.mark.parametrize("luminance", [2.0, 5.0, 50.0, 5000.0, 500_000.0])
    def test_it_converges_on_the_target_from_anywhere(self, controller, luminance):
        """No lookup table and no per-camera calibration: the loop finds it."""
        settings = converge(controller, luminance)
        assert luminance * product(settings) == pytest.approx(120, rel=0.1)

    def test_a_darker_scene_gets_more_exposure(self):
        bright = converge(ExposureController(make_config()), 5000.0)
        dark = converge(ExposureController(make_config()), 50.0)
        assert product(dark) > product(bright)

    def test_it_cannot_exceed_the_camera(self, controller):
        """A scene darker than the camera can reach pins at the ceiling."""
        settings = converge(controller, 0.01)
        assert settings["ExposureTime"] == pytest.approx(MAX_SHUTTER * 1e6, rel=0.01)
        assert settings["AnalogueGain"] == pytest.approx(MAX_GAIN, rel=0.01)

    def test_an_almost_black_frame_opens_up_rather_than_holding(self, controller):
        """A measurement of 0.03 is a measurement, and an emphatic one.

        The guard used to be `brightness is None or brightness < 1`, which held
        the exposure wherever it was whenever a frame came back nearly black.
        Under the old night mode that never showed, because night pinned the
        shutter at its ceiling anyway. On the ladder it left a simulated polar
        night stuck at the cold-start 20 ms for three hundred frames.
        """
        controller.decide()
        controller.observe_frame({"mean_brightness": 0.03, "std_brightness": 5.0})
        first = product(controller.decide())

        controller.observe_frame({"mean_brightness": 0.05, "std_brightness": 5.0})
        second = product(controller.decide())

        assert first > COLD_START_EXPOSURE_S
        assert second > first

    def test_a_genuinely_missing_measurement_holds(self, controller):
        controller.decide()
        controller.observe_frame({"mean_brightness": 100.0, "std_brightness": 50.0})
        held = product(controller.decide())

        controller.observe_frame({"mean_brightness": None, "std_brightness": 50.0})
        assert product(controller.decide()) == pytest.approx(held, rel=0.05)

    def test_single_frame_correction_is_bounded(self, controller):
        """Without a clamp, one bad measurement swings the whole sequence."""
        controller.seed_from_capture(exposure_time=1.0, analogue_gain=1.0)
        controller.observe_frame({"mean_brightness": 1.0, "std_brightness": 50.0})
        assert product(controller.decide()) <= 1.0 * 2.0 + 1e-9


class TestLadderIntegration:
    def test_shutter_carries_the_load_before_gain(self, controller):
        settings = converge(controller, 500.0)
        assert settings["AnalogueGain"] == pytest.approx(
            1.0
        ), "gain must not rise while the shutter still has room"

    def test_gain_rises_only_at_the_ceiling(self, controller):
        settings = converge(controller, 0.5)
        assert settings["ExposureTime"] == pytest.approx(MAX_SHUTTER * 1e6, rel=0.01)
        assert settings["AnalogueGain"] > 1.0

    def test_the_mode_label_follows_the_settings(self, controller):
        converge(controller, 500_000.0)
        assert controller.last_mode == LightMode.DAY

        dark = ExposureController(make_config())
        converge(dark, 0.5)
        assert dark.last_mode == LightMode.NIGHT

    def test_ladder_position_moves_with_the_light(self, controller):
        converge(controller, 500_000.0)
        bright = controller.ladder_position

        dark = ExposureController(make_config())
        converge(dark, 0.5)
        assert dark.ladder_position > bright


class TestRateLimit:
    @staticmethod
    def _response(exposure, gain, measured):
        """Fraction of the damped correction that one frame actually applies."""
        controller = ExposureController(make_config())
        controller.seed_from_capture(exposure_time=exposure, analogue_gain=gain)
        controller.observe_frame({"mean_brightness": measured, "std_brightness": 50.0})

        before = exposure * gain
        after = product(controller.decide())
        wanted = before * (120 / measured) ** 0.5
        return (after - before) / (wanted - before)

    def test_the_bright_end_responds_faster_than_the_dark_end(self):
        """The old code applied no interpolation in day mode, and 8% in night.

        Making the rate uniform across the ladder was measurably worse: the
        closed-loop comparison against the old controller showed the brightness
        error rising on nearly every recorded sequence, because daylight became
        sluggish. The rate is a straight line between the two ends instead.
        """
        bright = self._response(0.001, 1.0, 60.0)
        dark = self._response(MAX_SHUTTER, MAX_GAIN, 60.0)

        assert bright > 0.7, "daylight should go most of the way in one frame"
        assert dark < 0.4, "a 20-second exposure of an aurora should not"
        assert bright > dark * 2

    def test_it_always_moves_towards_the_target_and_never_past_it(self):
        for exposure, gain, measured in (
            (0.001, 1.0, 60.0),
            (1.0, 1.0, 60.0),
            (MAX_SHUTTER, MAX_GAIN, 200.0),
            (0.5, 1.0, 200.0),
        ):
            applied = self._response(exposure, gain, measured)
            assert 0.0 < applied <= 1.0 + 1e-9, f"{exposure=} {gain=} {measured=}: {applied}"

    def test_the_dark_end_does_not_step(self, controller):
        controller.seed_from_capture(exposure_time=MAX_SHUTTER, analogue_gain=MAX_GAIN)
        controller.decide()
        before = product(controller.decide())

        controller.observe_frame({"mean_brightness": 200.0, "std_brightness": 50.0})
        after = product(controller.decide())

        assert after < before, "it should be coming down"
        assert after > before * 0.5, "but not in one step"


class TestWhiteBalance:
    @pytest.mark.parametrize("luminance", [500_000.0, 500.0, 0.5])
    def test_manual_in_every_condition(self, luminance):
        """AWB drifting between frames is the main cause of colour flicker."""
        settings = converge(ExposureController(make_config()), luminance)
        assert settings["AwbEnable"] == 0
        assert "ColourGains" in settings

    def test_daylight_gets_the_daylight_gains(self, controller):
        settings = converge(controller, 500_000.0)
        assert settings["ColourGains"][0] == pytest.approx(2.5, rel=0.05)
        assert settings["ColourGains"][1] == pytest.approx(1.6, rel=0.05)

    def test_the_dark_end_gets_the_night_gains(self, controller):
        settings = converge(controller, 0.5, frames=200)
        assert settings["ColourGains"][0] == pytest.approx(1.83, rel=0.1)
        assert settings["ColourGains"][1] == pytest.approx(2.02, rel=0.1)

    def test_it_cross_fades_rather_than_stepping(self, controller):
        """A step in colour between two frames is the flicker this prevents."""
        gains = []
        for luminance in (500_000.0, 5000.0, 500.0, 50.0, 5.0, 0.5):
            converge(controller, luminance, frames=40)
            gains.append(controller._last_colour_gains)

        for before, after in zip(gains, gains[1:]):
            assert abs(after[0] - before[0]) < 0.5, f"colour jumped: {before} -> {after}"

    def test_it_learns_the_daylight_reference(self, controller):
        converge(controller, 500_000.0)
        controller.update_day_wb_reference({"ColourGains": [2.1, 1.9]})
        assert controller._day_wb_reference == (2.1, 1.9)

    def test_configured_gains_beat_the_learned_reference(self, controller):
        """`fixed_colour_gains` means fixed. The learned reference was allowed
        to override it for a while, so the configured value was read and then
        thrown away, and daylight colour wandered from 2.500 to 2.547 over nine
        days on the camera this was found on."""
        converge(controller, 500_000.0)
        controller.update_day_wb_reference({"ColourGains": [2.1, 1.9]})

        settings = converge(controller, 500_000.0)
        assert settings["ColourGains"][0] == pytest.approx(2.5, rel=0.01)
        assert settings["ColourGains"][1] == pytest.approx(1.6, rel=0.01)

    def test_the_reference_is_the_fallback_without_configured_gains(self):
        """It is still what a camera that has not configured a white point
        cross-fades away from at dusk."""
        config = make_config(day_mode={})
        controller = ExposureController(config)
        converge(controller, 500_000.0)
        controller.update_day_wb_reference({"ColourGains": [2.1, 1.9]})

        settings = converge(controller, 500_000.0)
        assert settings["ColourGains"][0] == pytest.approx(2.1, rel=0.01)
        assert settings["ColourGains"][1] == pytest.approx(1.9, rel=0.01)

    def test_it_only_learns_at_the_bright_end(self, controller):
        converge(controller, 0.5)
        controller.update_day_wb_reference({"ColourGains": [2.1, 1.9]})
        assert controller._day_wb_reference is None, "AWB has no signal at night"

    def test_malformed_metadata_is_ignored(self, controller):
        converge(controller, 500_000.0)
        controller.update_day_wb_reference({"ColourGains": [2.1]})
        controller.update_day_wb_reference({})
        assert controller._day_wb_reference is None


class TestSeeding:
    def test_a_full_row_primes_everything(self, controller):
        controller.seed_from_capture(
            exposure_time=5.0,
            analogue_gain=2.0,
            colour_gains=(1.9, 2.0),
            brightness=118.0,
            lux=12.0,
            mode=LightMode.NIGHT,
        )
        assert controller.last_brightness == 118.0
        assert controller.smoothed_lux == 12.0
        assert controller.last_mode == LightMode.NIGHT
        # The ladder's state is the product, so that is what a row primes.
        assert product(controller.decide()) == pytest.approx(10.0, rel=0.01)

    def test_a_partial_row_still_helps(self, controller):
        """seed_from_capture applies only the fields the database row had."""
        controller.seed_from_capture(exposure_time=5.0)
        assert product(controller.decide()) == pytest.approx(5.0, rel=0.01)

    def test_seeding_nothing_is_harmless(self, controller):
        controller.seed_from_capture()
        assert product(controller.decide()) == pytest.approx(COLD_START_EXPOSURE_S)

    def test_there_is_no_handover_to_seed(self, controller):
        """`seed_from_metadata` is gone, and nothing may put it back.

        It reseeded the loop at the day-to-transition crossing from the last
        daylight frame's *camera* metadata. Its colour half went first, for
        bypassing the wb_speed cross-fade it was meant to feed. Its exposure
        half survived that and kept stepping: metadata reports what the sensor
        did, and this sensor's analogue gain floor is 1.1228 where the ladder
        commands 1.0, so every dusk multiplied the loop's state by 1.12.

        This is a shape assertion and it knows it -- it catches the symbol
        coming back, not a differently-spelled reseed. The behaviour is pinned
        by test_crossing_the_day_knee_is_not_an_event below and, on real
        recorded dusks, by the golden replay suite.
        """
        assert not hasattr(controller, "seed_from_metadata")
        assert not hasattr(controller, "reset_seed_state")
        assert not hasattr(controller, "transition_seeded")


class TestTheDayTransitionBoundary:
    """Crossing the label must cost nothing, because the label decides nothing.

    `ladder.label()` names a region after the settings are chosen; its own
    docstring says "No exposure decision consults it." So an exposure walking
    across the knee must walk, not jump.
    """

    def test_crossing_the_day_knee_is_not_an_event(self, controller):
        """Walk the light across the knee and watch every step.

        The knee is at max_shutter * DAY_KNEE = 0.2 s exactly. The light is
        moved by only 2%, so every honest frame-to-frame step is about 2% --
        which leaves the 12% the old seeding produced nowhere to hide. A wider
        light change would blur the two together and let the bug through.
        """
        knee = MAX_SHUTTER * 0.01
        converge(controller, 120.0 / (knee * 0.99))  # settle just below it

        products = []
        for _ in range(40):
            settings = controller.decide()
            p = product(settings)
            products.append(p)
            brightness = max(0.0, min(255.0, (120.0 / (knee * 1.01)) * p))
            controller.observe_frame({"mean_brightness": brightness, "std_brightness": 50.0})

        assert min(products) < knee < max(products), "the walk never crossed the knee"

        steps = [products[i] / products[i - 1] for i in range(1, len(products))]
        worst = max(steps, key=lambda r: abs(r - 1.0))
        assert abs(worst - 1.0) < 0.05, f"a single frame moved the exposure by {worst:.3f}x"


class TestCosmeticLux:
    def test_the_first_reading_is_taken_whole(self, controller):
        assert controller.smooth_lux(100.0) == 100.0

    def test_later_readings_are_averaged_in(self, controller):
        controller.smooth_lux(100.0)
        assert controller.smooth_lux(200.0) == pytest.approx(130.0)

    def test_none_does_not_disturb_it(self, controller):
        controller.smooth_lux(100.0)
        assert controller.smooth_lux(None) == 100.0

    def test_nothing_decides_anything_from_it(self):
        """Lux is recorded and displayed. It is not an input any more.

        Two controllers given wildly different lux and identical brightness
        must choose identical settings. This is the property that makes the
        camera work at any latitude: the old mode selection compared this
        figure against thresholds tuned for one site.
        """
        bright_lux = ExposureController(make_config())
        dark_lux = ExposureController(make_config())

        for lux, instance in ((100_000.0, bright_lux), (0.001, dark_lux)):
            instance.smooth_lux(lux)
            instance.observe_frame({"mean_brightness": 90.0, "std_brightness": 50.0})

        assert bright_lux.decide() == dark_lux.decide()


class TestDiagnostics:
    def test_it_reports_what_was_decided(self, controller):
        converge(controller, 5000.0, frames=10)
        data = controller.diagnostics()
        assert data["mode"] in (LightMode.DAY, LightMode.TRANSITION, LightMode.NIGHT)
        assert "ladder_position" in data
        assert "applied_exposure_s" in data
        assert "applied_gain" in data

    def test_it_includes_the_metering(self, controller):
        converge(controller, 5000.0, frames=10)
        assert "last_brightness" in controller.diagnostics()

    def test_it_does_not_recompute_the_decision(self, controller):
        """The diagnostics used to re-run the whole exposure calculation."""
        converge(controller, 5000.0, frames=10)
        assert controller.diagnostics() == controller.diagnostics()


class TestHdr:
    def test_absent_when_disabled(self, controller):
        assert "HdrMode" not in controller.decide()

    def test_absent_when_libcamera_does_not_expose_it(self):
        """Pi 4 and vc4 have no HdrModeEnum; the setting must be a no-op."""
        controller = ExposureController(make_config(hdr={"enabled": True}))
        controller._hdr_enum = None
        assert "HdrMode" not in controller.decide()
