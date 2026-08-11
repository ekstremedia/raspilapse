"""Tests for the metering half of the exposure controller.

Split out of test_exposure.py when Meter was: these cover what the last frame
looked like and what to do about it, with no reference to shutter speed or
gain.
"""

import pytest

from raspilapse.camera.metering import BrightnessZones, Meter, highlight_factor


def config(**adaptive):
    return {"adaptive_timelapse": adaptive}


@pytest.fixture
def meter():
    return Meter(
        config(
            transition_mode={
                "target_brightness": 120,
                "exposure_transition_speed": 0.08,
                "fast_rampdown_speed": 0.2,
                "critical_rampdown_speed": 0.7,
                "fast_rampup_speed": 0.2,
                "critical_rampup_speed": 0.7,
            },
            brightness_target={
                "base": 120,
                "overcast_boost": 15,
                "max_target": 140,
                "contrast_threshold_low": 25,
                "contrast_threshold_high": 40,
            },
            highlight_protection={
                "enabled": True,
                "safe_p95": 200,
                "warning_p95": 220,
                "critical_p95": 240,
                "min_scale": 0.7,
                "slew": 0.25,
                "apply_in_night": False,
            },
        )
    )


def frame(mean, **extra):
    return {"mean_brightness": mean, "std_brightness": 50.0, **extra}


class TestObserve:
    def test_records_what_it_was_given(self, meter):
        meter.observe(frame(118.0, percentile_95=190.0))
        assert meter.brightness == 118.0
        assert meter.p95 == 190.0

    def test_an_empty_measurement_changes_nothing(self, meter):
        meter.observe(frame(118.0))
        meter.observe({})
        assert meter.brightness == 118.0

    def test_seeding_primes_the_measurement(self, meter):
        meter.seed_brightness(95.0)
        assert meter.brightness == 95.0

    def test_seeding_none_is_ignored(self, meter):
        meter.seed_brightness(95.0)
        meter.seed_brightness(None)
        assert meter.brightness == 95.0


class TestDynamicTarget:
    def test_high_contrast_gets_the_base_target(self, meter):
        meter.observe(frame(120.0, std_brightness=60.0))
        assert meter.raw_target == 120

    def test_low_contrast_gets_the_full_boost(self, meter):
        """An overcast sky is flat, and reads as dark at a fixed target."""
        meter.observe(frame(120.0, std_brightness=10.0))
        assert meter.raw_target == 135

    def test_the_boost_is_capped(self, meter):
        meter._overcast_boost = 100
        meter.observe(frame(120.0, std_brightness=10.0))
        assert meter.raw_target == 140

    def test_between_the_thresholds_it_interpolates(self, meter):
        meter.observe(frame(120.0, std_brightness=32.5))
        assert 120 < meter.raw_target < 135

    def test_the_dark_end_keeps_the_base_target(self, meter):
        """Raising the target at night washes out aurora and stars."""
        meter.set_dark_end(True)
        meter.observe(frame(120.0, std_brightness=10.0))
        assert meter.raw_target == 120

    def test_a_missing_contrast_measure_is_not_a_boost(self, meter):
        meter.observe({"mean_brightness": 120.0})
        assert meter.raw_target == 120


class TestOverexposure:
    def test_clean_frame_is_not_flagged(self, meter):
        meter.observe(frame(120.0, overexposed_percent=0.0))
        assert not meter.overexposed

    def test_warning_level(self, meter):
        meter.observe(frame(155.0, overexposed_percent=0.0))
        assert meter.overexposed

    def test_critical_level(self, meter):
        meter.observe(frame(180.0, overexposed_percent=0.0))
        assert meter.overexposed
        assert meter.speed(1.0) == pytest.approx(0.7)

    def test_clipped_pixels_trigger_it_independently_of_the_mean(self, meter):
        """A dark scene with a streetlamp in it: mean low, highlights gone."""
        meter.observe(frame(100.0, overexposed_percent=8.0))
        assert meter.overexposed

    def test_the_clipped_warning_threshold_is_exactly_five_percent(self, meter):
        meter.observe(frame(100.0, overexposed_percent=4.9))
        assert not meter.overexposed

        meter.observe(frame(100.0, overexposed_percent=5.5))
        assert meter.overexposed

    def test_it_is_sticky_between_the_thresholds(self, meter):
        """Flapping the rate every 30 seconds would show up as flicker."""
        meter.observe(frame(180.0, overexposed_percent=0.0))
        meter.observe(frame(140.0, overexposed_percent=0.0))
        assert meter.overexposed, "should hold until the release threshold"

    def test_it_clears_below_the_release_threshold(self, meter):
        meter.observe(frame(180.0, overexposed_percent=0.0))
        meter.observe(frame(125.0, overexposed_percent=0.0))
        assert not meter.overexposed

    def test_clipped_pixels_hold_it_even_when_the_mean_is_safe(self, meter):
        """The release needs both the mean and the clipped fraction to be calm.

        3.5 rather than 4.0 on purpose: at 4.0 this passes whether the release
        threshold is 3 or 4, which is how the first version of this test came
        to cover nothing. The value has to sit strictly between them.
        """
        meter.observe(frame(180.0, overexposed_percent=0.0))
        meter.observe(frame(120.0, overexposed_percent=3.5))
        assert meter.overexposed

    def test_the_clipped_release_threshold_is_exactly_three_percent(self, meter):
        meter.observe(frame(180.0, overexposed_percent=0.0))
        meter.observe(frame(120.0, overexposed_percent=2.9))
        assert not meter.overexposed, "below 3% it should release"

        meter.observe(frame(180.0, overexposed_percent=0.0))
        meter.observe(frame(120.0, overexposed_percent=3.1))
        assert meter.overexposed, "above 3% it should hold"


class TestUnderexposure:
    def test_clean_frame_is_not_flagged(self, meter):
        meter.observe(frame(120.0))
        assert not meter.underexposed

    def test_warning_level(self, meter):
        meter.observe(frame(85.0))
        assert meter.underexposed

    def test_critical_level(self, meter):
        meter.observe(frame(60.0))
        assert meter.underexposed
        assert meter.speed(1.0) == pytest.approx(0.7)

    def test_it_is_sticky_between_the_thresholds(self, meter):
        meter.observe(frame(60.0))
        meter.observe(frame(95.0))
        assert meter.underexposed

    def test_it_clears_above_the_release_threshold(self, meter):
        meter.observe(frame(60.0))
        meter.observe(frame(110.0))
        assert not meter.underexposed

    def test_a_missing_measurement_clears_the_flag(self, meter):
        """`or 128` reads a missing measurement as mid-grey, which is above the
        release threshold -- so the flag clears rather than holding.

        The comment in the code claimed the opposite for a long time. Clearing
        is the safer reading: it drops back to the ordinary rate instead of
        hurrying on the strength of a measurement that did not arrive.
        """
        meter.observe(frame(60.0))
        assert meter.underexposed

        meter.observe({"mean_brightness": None, "std_brightness": 50.0})
        assert not meter.underexposed


class TestSpeed:
    def test_the_bright_end_goes_straight_to_the_target(self, meter):
        """Where the old code applied no interpolation at all."""
        assert meter.speed(0.0) == pytest.approx(1.0)

    def test_the_dark_end_moves_a_fraction_at_a_time(self, meter):
        """Where the old code interpolated at 8% per frame."""
        assert meter.speed(1.0) == pytest.approx(0.08)

    def test_it_is_a_straight_line_between_them(self, meter):
        assert meter.speed(0.5) == pytest.approx(0.54)

    def test_recovery_only_ever_hurries(self, meter):
        """A wrong frame must not slow the loop down.

        At the bright end the ordinary speed is already 1.0, and the recovery
        rates are fractions -- taking them unconditionally would make an
        overexposed daylight frame recover *slower* than a correct one.
        """
        meter.observe(frame(180.0, overexposed_percent=0.0))
        assert meter.speed(0.0) == pytest.approx(1.0)
        assert meter.speed(1.0) == pytest.approx(0.7), "but it does hurry the dark end"

    def test_underexposure_wins_over_overexposure(self, meter):
        meter._under = True
        meter._under_severity = "critical"
        meter._over = True
        meter._over_severity = "warning"
        assert meter.speed(1.0) == pytest.approx(0.7)


class TestHighlightFactor:
    def test_below_safe_is_full_headroom(self):
        assert highlight_factor(150.0) == 1.0
        assert highlight_factor(200.0) == 1.0

    def test_none_is_full_headroom(self):
        assert highlight_factor(None) == 1.0

    def test_monotonically_decreasing(self):
        previous = 1.1
        for p95 in range(150, 256):
            current = highlight_factor(float(p95))
            assert current <= previous + 1e-12
            previous = current

    def test_never_below_the_floor(self):
        for p95 in range(200, 256):
            assert highlight_factor(float(p95), floor=0.7) >= 0.7

    def test_the_floor_cannot_bind_below_070_on_8_bit_input(self):
        """The last segment reaches exactly 0.70 at p95 255, the highest there is.

        So a configured min_scale under 0.70 does nothing at all. Worth
        pinning: the setting reads as if it does.
        """
        assert highlight_factor(255.0, floor=0.3) == pytest.approx(0.70)
        assert highlight_factor(255.0, floor=0.7) == pytest.approx(0.70)

    def test_equal_thresholds_do_not_divide_by_zero(self):
        assert highlight_factor(210.0, safe=200, warning=200, critical=200) >= 0.0


class TestFusionRelaxesHighlightProtection:
    """Fusion's under-bracket protects highlights better than underexposing
    the whole frame ever could, so the protection default flips off with it."""

    def test_default_flips_off_under_fusion(self):
        meter = Meter(config(dynamic_range={"method": "fusion"}))
        assert meter._p95_enabled is False

    def test_default_stays_on_without_fusion(self):
        assert Meter(config())._p95_enabled is True
        assert Meter(config(dynamic_range={"method": "tone_map"}))._p95_enabled is True

    def test_explicit_enabled_still_wins(self):
        meter = Meter(
            config(
                dynamic_range={"method": "fusion"},
                highlight_protection={"enabled": True},
            )
        )
        assert meter._p95_enabled is True


class TestHighlightProtection:
    def test_disabled_means_no_scaling(self):
        meter = Meter(config(highlight_protection={"enabled": False}))
        meter.observe(frame(120.0, percentile_95=255.0))
        assert meter.target() == pytest.approx(meter.raw_target)

    def test_it_slews_rather_than_stepping(self, meter):
        """One noisy p95 sample must not move the target by a whole stop."""
        meter.observe(frame(120.0, percentile_95=255.0))
        first = meter.target()
        assert first > meter.raw_target * 0.9, "one frame should barely move it"

        for _ in range(30):
            meter.observe(frame(120.0, percentile_95=255.0))
            meter.target()
        assert meter.target() == pytest.approx(meter.raw_target * 0.7, rel=0.02)

    def test_off_at_the_dark_end_by_default(self, meter):
        """Streetlamps and the moon are not blown scenes."""
        meter.observe(frame(90.0, percentile_95=255.0))
        assert meter.target(dark=True) == pytest.approx(meter.raw_target)

    def test_on_at_the_dark_end_when_asked(self):
        meter = Meter(
            config(highlight_protection={"enabled": True, "apply_in_night": True, "slew": 1.0})
        )
        meter.observe(frame(90.0, percentile_95=255.0))
        assert meter.target(dark=True) < meter.raw_target


class TestBrightnessZones:
    def test_the_bands_are_ordered(self):
        assert (
            BrightnessZones.CRITICAL_LOW
            < BrightnessZones.WARNING_LOW
            < BrightnessZones.TARGET_LOW
            < BrightnessZones.TARGET_HIGH
            < BrightnessZones.WARNING_HIGH
            < BrightnessZones.CRITICAL_HIGH
        )


class TestDiagnostics:
    def test_reports_what_it_measured(self, meter):
        meter.observe(frame(118.0, percentile_95=190.0))
        data = meter.diagnostics()
        assert data["last_brightness"] == 118.0
        assert data["last_p95"] == 190.0
        assert data["target_brightness"] == 120

    def test_reports_the_highlight_scale_only_when_it_is_doing_something(self, meter):
        meter.observe(frame(118.0, percentile_95=150.0))
        meter.target()
        assert "highlight_scale" not in meter.diagnostics()

        meter.observe(frame(118.0, percentile_95=255.0))
        meter.target()
        assert "highlight_scale" in meter.diagnostics()

    def test_reports_the_overcast_boost(self, meter):
        meter.observe(frame(120.0, std_brightness=10.0))
        assert meter.diagnostics()["overcast_boost_active"] is True
