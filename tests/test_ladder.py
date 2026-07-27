"""Tests for the exposure ladder.

The ladder replaced mode selection by absolute lux thresholds, so the property
that matters most is the one these tests spend the most effort on: it must
behave identically wherever the camera is. There is nothing in it that knows
about latitude, time of day, or what a lux is.
"""

import math

import pytest

from raspilapse.camera.ladder import (
    DAY_KNEE,
    MIN_SHUTTER_S,
    NIGHT_KNEE,
    LightMode,
    allocate,
    label,
    position,
)

MAX_SHUTTER = 20.0
MAX_GAIN = 6.0


def allocate_default(required):
    return allocate(required, MAX_SHUTTER, MAX_GAIN)


class TestAllocate:
    def test_shutter_carries_everything_it_can(self):
        """Gain stays at its floor until the shutter runs out."""
        for required in (0.001, 0.1, 1.0, 10.0, 19.9):
            shutter, gain = allocate_default(required)
            assert gain == 1.0, f"gain rose at {required}s with shutter still available"
            assert shutter == pytest.approx(required)

    def test_gain_covers_the_remainder_once_the_shutter_is_full(self):
        shutter, gain = allocate_default(60.0)
        assert shutter == MAX_SHUTTER
        assert gain == pytest.approx(3.0)

    def test_the_product_is_preserved_within_the_limits(self):
        """The whole contract: shutter times gain is what was asked for."""
        for required in (0.001, 0.5, 20.0, 45.0, 119.0):
            shutter, gain = allocate_default(required)
            assert shutter * gain == pytest.approx(required, rel=1e-9)

    def test_it_cannot_go_past_the_dark_end(self):
        shutter, gain = allocate_default(10_000.0)
        assert shutter == MAX_SHUTTER
        assert gain == MAX_GAIN

    def test_it_cannot_go_past_the_bright_end(self):
        shutter, gain = allocate_default(1e-9)
        assert shutter == MIN_SHUTTER_S
        assert gain == 1.0

    def test_allocation_is_monotonic(self):
        """More light required must never mean less of either knob.

        A non-monotonic ladder would oscillate: the loop would ask for more
        exposure and get a darker frame.
        """
        previous = (0.0, 0.0)
        for step in range(200):
            required = 10 ** (-4 + step * 0.03)
            shutter, gain = allocate_default(required)
            assert shutter >= previous[0] - 1e-12, f"shutter went backwards at {required}"
            assert gain >= previous[1] - 1e-12, f"gain went backwards at {required}"
            previous = (shutter, gain)

    def test_gain_never_drops_below_one(self):
        """Analogue gain below 1.0 is not a thing a sensor can do."""
        for required in (1e-9, 1e-6, MIN_SHUTTER_S / 2):
            _, gain = allocate_default(required)
            assert gain >= 1.0

    @pytest.mark.parametrize("max_shutter,max_gain", [(1.0, 2.0), (20.0, 6.0), (120.0, 16.0)])
    def test_limits_come_from_the_camera_not_from_constants(self, max_shutter, max_gain):
        shutter, gain = allocate(1e9, max_shutter, max_gain)
        assert shutter == max_shutter
        assert gain == max_gain


class TestPosition:
    def test_ends_of_the_ladder(self):
        assert position(MIN_SHUTTER_S, MAX_SHUTTER, MAX_GAIN) == pytest.approx(0.0)
        assert position(MAX_SHUTTER * MAX_GAIN, MAX_SHUTTER, MAX_GAIN) == pytest.approx(1.0)

    def test_clamped_outside_the_ladder(self):
        assert position(1e-12, MAX_SHUTTER, MAX_GAIN) == 0.0
        assert position(1e12, MAX_SHUTTER, MAX_GAIN) == 1.0

    def test_monotonic(self):
        previous = -1.0
        for step in range(200):
            current = position(10 ** (-4 + step * 0.03), MAX_SHUTTER, MAX_GAIN)
            assert current >= previous
            previous = current

    def test_logarithmic_not_linear(self):
        """Doubling the light must move the same distance wherever you are.

        Linear position would put daylight and deep dusk within a rounding
        error of each other, and spend the entire scale on the last stop.
        """
        near_bright = position(0.002, MAX_SHUTTER, MAX_GAIN) - position(
            0.001, MAX_SHUTTER, MAX_GAIN
        )
        near_dark = position(20.0, MAX_SHUTTER, MAX_GAIN) - position(10.0, MAX_SHUTTER, MAX_GAIN)
        assert near_bright == pytest.approx(near_dark, rel=1e-6)

    def test_a_degenerate_range_does_not_divide_by_zero(self):
        assert position(1.0, MIN_SHUTTER_S, 1.0, min_shutter=MIN_SHUTTER_S) == 0.0


class TestLabel:
    def test_daylight(self):
        assert label(0.0004, 1.0, MAX_SHUTTER) == LightMode.DAY

    def test_full_night(self):
        assert label(MAX_SHUTTER, 6.0, MAX_SHUTTER) == LightMode.NIGHT

    def test_any_gain_above_the_floor_is_night(self):
        """Gain only rises once the shutter has run out, which is night's defining act."""
        assert label(MAX_SHUTTER, 1.5, MAX_SHUTTER) == LightMode.NIGHT

    def test_between_the_knees_is_transition(self):
        assert label(1.0, 1.0, MAX_SHUTTER) == LightMode.TRANSITION

    def test_knees_are_fractions_of_the_camera_ceiling(self):
        assert label(MAX_SHUTTER * NIGHT_KNEE, 1.0, MAX_SHUTTER) == LightMode.NIGHT
        assert label(MAX_SHUTTER * DAY_KNEE, 1.0, MAX_SHUTTER) == LightMode.DAY

    def test_label_agrees_with_the_settings_it_describes(self):
        """The failure the old mode had: a "day" label on a 20-second exposure.

        368k frames were labelled day, and some of them were at gain 5.5 with
        the shutter wide open, because a sun-elevation override set the label
        without reference to what the camera was doing.
        """
        for step in range(300):
            required = 10 ** (-4 + step * 0.02)
            shutter, gain = allocate_default(required)
            name = label(shutter, gain, MAX_SHUTTER)

            if name == LightMode.DAY:
                assert gain == 1.0 and shutter <= MAX_SHUTTER * DAY_KNEE
            elif name == LightMode.NIGHT:
                assert gain > 1.0 or shutter >= MAX_SHUTTER * NIGHT_KNEE
            else:
                assert gain == 1.0


class TestLocationIndependence:
    """The property the whole change exists for."""

    def test_nothing_in_the_module_mentions_location(self):
        """No latitude, no clock, no lux. The code, not the prose about it.

        Tokenised rather than filtered by line prefix: the docstrings here
        explain at length what was removed and why, and naming those things is
        the point of them.
        """
        import io
        import tokenize
        from pathlib import Path

        from raspilapse.camera import ladder

        source = Path(ladder.__file__).read_text()
        code = " ".join(
            token.string.lower()
            for token in tokenize.generate_tokens(io.StringIO(source).readline)
            if token.type not in (tokenize.COMMENT, tokenize.STRING)
        )

        for word in ("latitude", "longitude", "sun", "elevation", "twilight", "timezone", "lux"):
            assert word not in code, f"the ladder's code refers to {word!r}"

    def test_identical_inputs_give_identical_output_regardless_of_anything_else(self):
        """There is no hidden state and no clock. Same in, same out, always."""
        first = [allocate_default(10 ** (-4 + n * 0.1)) for n in range(60)]
        second = [allocate_default(10 ** (-4 + n * 0.1)) for n in range(60)]
        assert first == second

    def test_the_ladder_spans_the_full_dynamic_range(self):
        """Daylight to a 20-second night exposure, with no gaps."""
        daylight = position(0.0004, MAX_SHUTTER, MAX_GAIN)
        deep_night = position(MAX_SHUTTER * MAX_GAIN, MAX_SHUTTER, MAX_GAIN)

        assert daylight < 0.25, "daylight should sit near the bright end"
        assert deep_night == pytest.approx(1.0)
        assert math.isfinite(daylight)
