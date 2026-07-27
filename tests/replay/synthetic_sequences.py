"""Replay fixtures for branches the recorded light never reaches.

Six months of captures from one camera do not exercise everything the
controller can do. Mutating constants one at a time showed which: the feedback
ratio clamps, the underexposure thresholds, the night gain floor's else branch
and the low-side hybrid override all survived being changed, because no real
frame ever drove them.

Some of those are config-dependent rather than rare -- with `min_scale: 0.7`
the ratio can never fall below 84/255, so the 0.25 lower clamp is unreachable
by construction. Those get a sequence with a config that does reach them.

The rest sweep brightness through the boundary in single steps rather than
sitting on one value, so a threshold that moves in either direction is caught
regardless of which way it moved.

    python3 tests/replay/synthetic_sequences.py
"""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.replay.extract_sequences import OUT_DIR, REPLAY_CONFIG  # noqa: E402
from tests.replay.harness import dump_frames  # noqa: E402

DAY_GAINS = [2.5, 1.6]


def frame(lux, brightness, *, p95=None, over_pct=0.0, under_pct=0.0, exposure_us=10000, gain=1.0):
    """One frame's worth of controller input.

    p95 defaults to a plausible distance above the mean rather than a constant,
    so highlight protection sees something that moves with the exposure.
    """
    if p95 is None:
        p95 = min(255.0, brightness * 1.5)
    return {
        "raw_lux": lux,
        "sun_elevation": None,
        "brightness": {
            "mean_brightness": round(float(brightness), 2),
            "median_brightness": round(float(brightness), 2),
            "std_brightness": 40.0,
            "percentile_5": round(max(0.0, brightness * 0.3), 2),
            "percentile_25": round(max(0.0, brightness * 0.6), 2),
            "percentile_75": round(min(255.0, brightness * 1.25), 2),
            "percentile_95": round(float(p95), 2),
            "underexposed_percent": under_pct,
            "overexposed_percent": over_pct,
        },
        "capture_metadata": {
            "ExposureTime": exposure_us,
            "AnalogueGain": gain,
            "ColourGains": DAY_GAINS,
            "Lux": lux,
        },
        "test_metadata": {
            "ExposureTime": 200000,
            "AnalogueGain": 1,
            "ColourGains": DAY_GAINS,
            "Lux": lux,
        },
    }


def sweep(lo, hi, step=1):
    """Inclusive integer sweep, in whichever direction lo -> hi points."""
    if hi >= lo:
        return list(range(lo, hi + 1, step))
    return list(range(lo, hi - 1, -step))


def config_with(**overrides):
    """REPLAY_CONFIG with a nested override applied, e.g. night_mode={'analogue_gain': 1.5}."""
    config = copy.deepcopy(REPLAY_CONFIG)
    adaptive = config["adaptive_timelapse"]
    for section, values in overrides.items():
        adaptive[section] = {**adaptive.get(section, {}), **values}
    return config


def build():
    sequences = {}

    # --- feedback ratio, upper clamp -------------------------------------
    # The clamp is easy to reach and easy to hide. Reaching it needs a very
    # dark frame; *observing* it needs the resulting exposure to stay away from
    # its own limits, because both the clamped and unclamped values pin to the
    # same number once exposure saturates. So this starts from a seeded 1 ms
    # and crashes the light in one step: the ratio saturates while the exposure
    # is still nowhere near its ceiling, and the two differ for a dozen frames.
    sequences["synthetic_starved_light"] = {
        "description": (
            "a one-step light crash from a short seeded exposure, which "
            "saturates the brightness feedback ratio against its 4.0 upper "
            "clamp while the exposure itself is still far from its ceiling"
        ),
        "config": REPLAY_CONFIG,
        "seed": {"exposure_time": 0.001, "analogue_gain": 1.0, "brightness": 120.0},
        "frames": (
            [frame(100, 120, exposure_us=1000)]
            + [frame(100, 6, exposure_us=1000) for _ in range(14)]
            + [frame(100, b, exposure_us=1000) for b in sweep(6, 120, 4)]
        ),
    }

    # --- feedback ratio, lower clamp -------------------------------------
    # ratio is effective_target/measured, so binding the 0.25 lower clamp needs
    # effective_target below a quarter of the measured brightness.
    #
    # Lowering min_scale cannot do it, and not because 0.25 is far away:
    # highlight_factor's last segment is 0.85 - ((p95-critical)/15)*0.15, which
    # at the highest p95 an 8-bit frame can have -- 255, exactly 15 above the
    # default critical -- evaluates to 0.70 before the floor is consulted. Any
    # min_scale below 0.70 is therefore dead configuration.
    #
    # The brightness target is the lever that does work: at base 50 and full
    # highlight protection the ratio reaches 50*0.7/250, well under the clamp.
    sequences["synthetic_clamped_highlights"] = {
        "description": (
            "a blown frame on a camera targeting an unusually dark image, the "
            "only way the 0.25 lower ratio clamp can bind, oscillating so the "
            "exposure never parks on its own floor and hides the difference"
        ),
        "config": config_with(
            transition_mode={"target_brightness": 50},
            brightness_target={"base": 50, "max_target": 50, "overcast_boost": 0},
        ),
        "seed": {"exposure_time": 0.05, "analogue_gain": 1.0, "brightness": 250.0},
        "frames": [frame(500, 250, p95=255, over_pct=40.0) for _ in range(25)]
        + [
            frame(500, 250 if i % 2 == 0 else 110, p95=255 if i % 2 == 0 else 180)
            for i in range(60)
        ],
    }

    # --- gain interpolation clamp ----------------------------------------
    # _interpolate_gain clamps at 16.0, which no sane night gain approaches.
    # A camera configured past it is the only way to see the clamp work.
    sequences["synthetic_extreme_gain"] = {
        "description": (
            "a night gain configured above the interpolator's 16.0 ceiling, "
            "which is the only thing that exercises that clamp"
        ),
        "config": config_with(night_mode={"analogue_gain": 20}),
        "seed": {"analogue_gain": 1.0, "exposure_time": 0.5},
        "frames": [frame(1, 60, exposure_us=500000, gain=1.0) for _ in range(80)],
    }

    # --- mode threshold comparisons --------------------------------------
    # determine_mode compares lux against the thresholds with < and >, so the
    # only input that tells < from <= is one sitting exactly on the boundary.
    #
    # There is exactly one frame per sequence where that can happen. smooth_lux
    # is an EMA, and an EMA only ever approaches a constant input
    # asymptotically -- it lands on it just once, on the first frame, where
    # _smoothed_lux is still None and the raw value is adopted whole. Hence one
    # sequence per boundary, each opening on the value it needs to test.
    for edge_name, edge_lux in (("night", 3), ("day", 80)):
        sequences[f"synthetic_threshold_edge_{edge_name}"] = {
            "description": (
                f"smoothed lux opening exactly on the {edge_name} threshold, "
                f"where the difference between < and <= decides the mode"
            ),
            "config": REPLAY_CONFIG,
            "frames": [frame(edge_lux, 100) for _ in range(20)],
        }

    # --- night gain floor, else branch -----------------------------------
    # max(2.0, min(night_gain, target_gain)) only binds when the configured
    # night gain is itself below 2.0.
    sequences["synthetic_low_night_gain"] = {
        "description": (
            "night mode on a camera configured with a night gain below the "
            "2.0 floor, on a scene bright enough to enter brightness feedback"
        ),
        "config": config_with(night_mode={"analogue_gain": 1.5}),
        "seed": {"analogue_gain": 1.5, "exposure_time": 12.0},
        "frames": [frame(1, b, exposure_us=12_000_000, gain=1.5) for b in sweep(141, 149)] * 6,
    }

    # --- under/overexposure thresholds -----------------------------------
    # These flags do exactly one thing: pick the exposure interpolation speed
    # in night mode. Getting that to show up in the output takes three
    # conditions at once, and missing any one of them hides the thresholds
    # completely:
    #
    #   1. The exposure must be away from its target. Converged at 20 s, every
    #      speed produces 20 s. So the sequence opens bright enough (>140) to
    #      put night mode into brightness feedback and pull the exposure down.
    #   2. entering_night must be false, or its coordinated ramp overrides the
    #      speeds. That means the gain must stay above half the night gain,
    #      which is why the sequence is seeded at 6.0 and never lets it fall.
    #   3. The mode must stay night. Brightness above 160 trips the hybrid
    #      override into transition, where gain collapses toward 1.0 and
    #      condition 2 fails for a long time afterwards -- so the bright phase
    #      is capped at 158, just under the override and just over the
    #      feedback gate.
    #
    # Within that window the sweep crosses 150, 130, 105, 90 and 70 one step at
    # a time, and the clipped-pixel percentage tracks brightness so the
    # percentage thresholds (5 and 3) are crossed too.
    def night_frame(b):
        return frame(
            1,
            b,
            over_pct=round(max(0.0, (b - 110) / 8.0), 2),
            exposure_us=20_000_000,
            gain=6.0,
        )

    sequences["synthetic_night_brightness_sweep"] = {
        "description": (
            "night mode held off its exposure ceiling by a scene just under "
            "the hybrid-override brightness, then swept down to 40 and back, "
            "crossing every over- and underexposure threshold while the "
            "exposure is still ramping and the speed still matters"
        ),
        "config": REPLAY_CONFIG,
        "seed": {"analogue_gain": 6.0, "exposure_time": 20.0},
        "frames": (
            [night_frame(158) for _ in range(40)]
            + [night_frame(b) for b in sweep(158, 40)]
            + [night_frame(b) for b in sweep(40, 158)]
            + [night_frame(158) for _ in range(20)]
            + [night_frame(b) for b in sweep(158, 60)]
        ),
    }

    # --- underexposure "safe" threshold ----------------------------------
    # The clear at 105 is crossed on the way up out of a dark scene -- by which
    # point the exposure has long since converged on 20 s, and one frame's
    # difference in ramp speed changes nothing. Interleaving bright frames
    # keeps pulling the exposure back down, so every crossing happens while it
    # is still in flight.
    def under_cycle(edge):
        return (
            [night_frame(158), night_frame(158), night_frame(158)]  # pull exposure down
            + [night_frame(60)]  # set underexposure
            + [night_frame(edge)]  # cross the clear boundary, mid-ramp
        )

    sequences["synthetic_underexposure_release"] = {
        "description": (
            "the underexposure flag repeatedly set and released while the "
            "exposure is still ramping, which is the only state in which the "
            "release threshold changes the output"
        ),
        "config": REPLAY_CONFIG,
        "seed": {"analogue_gain": 6.0, "exposure_time": 20.0},
        "frames": [f for edge in sweep(98, 114) for f in under_cycle(edge)],
    }

    # --- clipped-pixel thresholds ----------------------------------------
    # overexposed_percent has its own thresholds, but in every sequence above
    # it is a function of mean brightness, so it never crosses 3 or 5 except
    # where the brightness thresholds already decided the outcome. A dark frame
    # with blown highlights in it -- a streetlamp, which this camera sees every
    # winter night -- separates the two.
    def clip_frame(mean, pct):
        return frame(1, mean, p95=255.0, over_pct=pct, exposure_us=20_000_000, gain=6.0)

    clip_sweep = [round(p / 2, 1) for p in range(0, 17)]
    sequences["synthetic_clipped_pixels"] = {
        "description": (
            "a dark scene with a bright light source in it, sweeping the "
            "clipped-pixel percentage across its own thresholds independently "
            "of mean brightness"
        ),
        "config": REPLAY_CONFIG,
        "seed": {"analogue_gain": 6.0, "exposure_time": 1.0},
        "frames": [
            f
            for pct in clip_sweep + clip_sweep[::-1]
            for f in (clip_frame(145, pct), clip_frame(145, pct), clip_frame(100, pct))
        ],
    }

    # --- clipped-pixel thresholds ----------------------------------------
    # The metering acts on the clipped fraction as well as the mean, at 5% and
    # 3%. Reaching those needs a frame whose highlights are gone while its mean
    # is unremarkable -- a night scene with a streetlamp in it, which this
    # camera sees every winter.
    #
    # The closed-loop simulation derives the clipped fraction from the spread
    # of the frame, so sweeping the spread at fixed light is what walks it
    # through the thresholds. Holding the spread fixed, as the earlier attempt
    # did, left the fraction pinned near zero and the release threshold could
    # be changed with nothing noticing.
    def spread_frame(spread, mean=100.0):
        return {
            "raw_lux": 5.0,
            "sun_elevation": None,
            "brightness": {
                "mean_brightness": float(mean),
                "median_brightness": float(mean),
                "std_brightness": float(spread),
                "percentile_5": 20.0,
                "percentile_25": 60.0,
                "percentile_75": 140.0,
                "percentile_95": 200.0,
                "underexposed_percent": 0.0,
                "overexposed_percent": 0.0,
            },
            # 100 brightness out of a 20-second exposure at gain 1: a scene
            # near the dark end of the ladder. That is deliberate. The
            # clipped-pixel flags do one thing -- pick the rate -- and the rate
            # is position-scaled, so anywhere but the dark end the ordinary
            # rate already exceeds the recovery rate and the flags cannot
            # change the output. A brighter version of this sequence reached
            # 3.5% clipping and still caught nothing.
            "capture_metadata": {
                "ExposureTime": 20_000_000,
                "AnalogueGain": 1.0,
                "ColourGains": DAY_GAINS,
                "Lux": 5.0,
            },
            "test_metadata": {
                "ExposureTime": 200000,
                "AnalogueGain": 1,
                "ColourGains": DAY_GAINS,
                "Lux": 5.0,
            },
        }

    # The light also has to keep moving. Held steady, the loop converges, the
    # measurement sits on the target, and the rate limit has no gap left to
    # close -- so which rate it picked stops mattering and the thresholds go
    # unobserved again. Alternating the scene between two levels keeps it
    # chasing, which is the state in which a rate is a rate.
    spreads = list(range(40, 125, 2))
    sequences["synthetic_clipping_sweep"] = {
        "description": (
            "a dark scene with a bright light source in it, alternating "
            "between two levels while its highlights spread wider and wider -- "
            "walking the clipped-pixel fraction through the 3% and 5% "
            "thresholds at a point on the ladder, and in a state of the loop, "
            "where the resulting rate change is visible. The clipped fraction "
            "the controller sees is computed by the harness from the spread of "
            "each frame, not read from overexposed_percent here -- which is "
            "why that field is zero throughout and the spread is what sweeps"
        ),
        "config": REPLAY_CONFIG,
        "seed": {"exposure_time": 20.0, "analogue_gain": 1.0, "brightness": 100.0},
        "frames": [
            spread_frame(spread, mean)
            for spread in spreads + spreads[::-1]
            for mean in (100.0, 55.0)
        ],
    }

    # --- hybrid brightness overrides -------------------------------------
    # Day-level lux with a dark frame forces TRANSITION, and night-level lux
    # with a bright frame does the same from the other side.
    sequences["synthetic_hybrid_override"] = {
        "description": (
            "lux and measured brightness disagreeing in both directions, "
            "which is what the hybrid override in determine_mode exists for"
        ),
        "config": REPLAY_CONFIG,
        "frames": (
            [frame(200, b) for b in sweep(120, 60)]
            + [frame(200, b) for b in sweep(60, 120)]
            + [frame(1, b, exposure_us=20_000_000, gain=6.0) for b in sweep(120, 200)]
            + [frame(1, b, exposure_us=20_000_000, gain=6.0) for b in sweep(200, 120)]
        ),
    }

    return sequences


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, sequence in build().items():
        frames = sequence.pop("frames")
        path = OUT_DIR / f"{name}.json"
        dump_frames(path, {"name": name, "source": "synthetic", **sequence}, frames)
        print(f"  {name}: {len(frames)} frames -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
