"""Build replay fixtures from a live capture database.

A development tool, not part of the test run. The fixtures it writes are
committed, so CI never needs the database (which is gitignored and 200 GB-ish
over its lifetime).

    python3 tests/replay/extract_sequences.py --db data/timelapse.db

On the recorded lux: the database stores the *smoothed* lux, because that is
what the loop passes to store_capture(). The harness feeds it back in as the
raw input and smooths it again, so these sequences are not a bit-exact replay
of that night. They do not need to be. What a golden master needs is an input
stream that is realistic in shape and identical on every run, and this is both.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.replay.harness import dump_frames  # noqa: E402

OUT_DIR = Path(__file__).parent / "sequences"

# Tuning from the camera these sequences were recorded on. Embedded rather than
# read from config/config.yml so the fixtures stay reproducible when that file
# is edited -- and because config.yml is gitignored and holds API keys.
#
# hdr.enabled is false here on purpose: _apply_hdr's output depends on whether
# libcamera exposes HdrModeEnum, which differs between a Pi 4 and a Pi 5. A
# golden file that changes with the host it runs on is worthless. HDR is
# covered by a unit test instead.
REPLAY_CONFIG = {
    "location": {
        "latitude": 68.7,
        "longitude": 15.4,
        "timezone": "Europe/Oslo",
        "civil_twilight_threshold": -6,
    },
    "adaptive_timelapse": {
        "enabled": True,
        "reference_lux": 3.8,
        "brightness_damping": 0.5,
        "highlight_protection": {
            "enabled": True,
            "safe_p95": 200,
            "warning_p95": 220,
            "critical_p95": 240,
            "min_scale": 0.7,
            "slew": 0.25,
            "apply_in_night": False,
        },
        "interval": 30,
        "num_frames": 0,
        "light_thresholds": {"night": 3, "day": 80},
        "night_mode": {
            "max_exposure_time": 20,
            "analogue_gain": 6,
            "awb_enable": False,
            "colour_gains": [1.83, 2.02],
        },
        "day_mode": {
            "exposure_time": 0.01,
            "analogue_gain": 1,
            "fixed_colour_gains": [2.5, 1.6],
            "awb_enable": True,
        },
        "transition_mode": {
            "smooth_transition": True,
            "sequential_ramping": True,
            "lux_smoothing_factor": 0.3,
            "hysteresis_frames": 3,
            "wb_transition_speed": 0.15,
            "gain_transition_speed": 0.1,
            "exposure_transition_speed": 0.08,
            "smooth_wb_in_day_mode": True,
            "smooth_exposure_in_day_mode": True,
            "brightness_feedback_enabled": True,
            "target_brightness": 120,
            "fast_rampdown_speed": 0.2,
            "fast_rampup_speed": 0.2,
            "critical_rampdown_speed": 0.7,
            "lux_change_threshold": 3,
            "ev_safety_clamp_enabled": True,
        },
        "hdr": {"enabled": False, "day_mode": "SingleExposure", "night_mode": "Off"},
        "brightness_target": {
            "base": 120,
            "overcast_boost": 15,
            "max_target": 140,
            "contrast_threshold_low": 25,
            "contrast_threshold_high": 40,
        },
        "test_shot": {"enabled": True, "exposure_time": 0.2, "analogue_gain": 1, "frequency": 1},
        "diagnostics": {"enabled": True},
    },
}

# The first three windows cross real mode boundaries on a day the camera saw
# all three modes. The last three exist because perturbing a constant in the
# night floor, the feedback clamp and the highlight-protection floor did not
# make the golden tests fail -- the mode-boundary windows never reach those
# branches. Each was picked by querying for frames that do. If you add a branch
# with its own constants, add a window that reaches it, and prove it by
# breaking the constant on purpose.
WINDOWS = [
    (
        "dawn_transition",
        "2026-04-26T00:00:00",
        "2026-04-26T03:30:00",
        "night and transition flapping, then the climb into day",
    ),
    (
        "dusk_transition",
        "2026-04-26T22:30:00",
        "2026-04-27T02:00:00",
        "day falling through transition into night",
    ),
    (
        "stable_day",
        "2026-04-26T11:00:00",
        "2026-04-26T12:00:00",
        "an ordinary hour of afternoon daylight -- converged, but real: cloud takes the mean from 121 to 174 and back, which is what a baseline for a camera that lives outdoors should look like",
    ),
    (
        "bright_night",
        "2026-02-05T00:30:00",
        "2026-02-05T02:30:00",
        "night mode on a bright scene, which is what pulls exposure back off "
        "the ceiling and reaches the night exposure floor",
    ),
    (
        "deep_dark",
        "2026-03-16T00:30:00",
        "2026-03-16T02:00:00",
        "the darkest frames on record, where the feedback ratio saturates "
        "against its upper clamp",
    ),
    (
        "blown_highlights",
        "2026-07-08T13:00:00",
        "2026-07-08T14:00:00",
        "midsummer noon with the histogram against the top stop: highlight "
        "protection and overexposure detection both active",
    ),
    (
        "crashing_light",
        "2026-04-09T23:30:00",
        "2026-04-10T01:00:00",
        "light collapsing faster than the loop can follow, driving the "
        "brightness feedback ratio into its upper clamp while still in "
        "transition mode",
    ),
    (
        "very_bright_night",
        "2026-01-17T20:00:00",
        "2026-01-17T22:30:00",
        "polar-night frames bright enough to push night mode down to its "
        "exposure floor and start trading gain away",
    ),
    (
        "night_underexposure_edge",
        "2026-01-30T00:00:00",
        "2026-01-30T02:00:00",
        "night frames sitting on the underexposure warning boundary, where "
        "the ramp-up speed changes",
    ),
]

COLUMNS = """
    timestamp, exposure_time_us, analogue_gain, colour_gains_r, colour_gains_b,
    lux, mode, sun_elevation, brightness_mean, brightness_median, brightness_std,
    brightness_p5, brightness_p25, brightness_p75, brightness_p95,
    underexposed_pct, overexposed_pct
"""


def build_frames(rows, test_exposure_s: float, test_gain: float):
    frames = []
    for r in rows:
        brightness = None
        if r["brightness_mean"] is not None:
            brightness = {
                "mean_brightness": r["brightness_mean"],
                "median_brightness": r["brightness_median"],
                "std_brightness": r["brightness_std"],
                "percentile_5": r["brightness_p5"],
                "percentile_25": r["brightness_p25"],
                "percentile_75": r["brightness_p75"],
                "percentile_95": r["brightness_p95"],
                "underexposed_percent": r["underexposed_pct"],
                "overexposed_percent": r["overexposed_pct"],
            }

        capture_metadata = {}
        if r["exposure_time_us"] is not None:
            capture_metadata["ExposureTime"] = r["exposure_time_us"]
        if r["analogue_gain"] is not None:
            capture_metadata["AnalogueGain"] = r["analogue_gain"]
        if r["colour_gains_r"] is not None and r["colour_gains_b"] is not None:
            capture_metadata["ColourGains"] = [r["colour_gains_r"], r["colour_gains_b"]]
        if r["lux"] is not None:
            capture_metadata["Lux"] = r["lux"]

        # The test shot runs at fixed settings with AWB on, so its metadata is
        # reconstructible: the settings are known, and its ColourGains are the
        # only AWB reading in the system -- approximated here by the day
        # reference the capture itself recorded.
        test_metadata = {
            "ExposureTime": int(test_exposure_s * 1_000_000),
            "AnalogueGain": test_gain,
        }
        if r["colour_gains_r"] is not None and r["colour_gains_b"] is not None:
            test_metadata["ColourGains"] = [r["colour_gains_r"], r["colour_gains_b"]]
        if r["lux"] is not None:
            test_metadata["Lux"] = r["lux"]

        frames.append(
            {
                "timestamp": r["timestamp"],
                "raw_lux": r["lux"],
                "sun_elevation": r["sun_elevation"],
                "recorded_mode": r["mode"],
                "brightness": brightness,
                "capture_metadata": capture_metadata,
                "test_metadata": test_metadata,
            }
        )
    return frames


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/timelapse.db")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    test_shot = REPLAY_CONFIG["adaptive_timelapse"]["test_shot"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for name, start, end, description in WINDOWS:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM captures "
            "WHERE timestamp >= ? AND timestamp < ? AND lux IS NOT NULL "
            "ORDER BY timestamp",
            (start, end),
        ).fetchall()

        if not rows:
            print(f"  {name}: no rows in {start}..{end}, skipped")
            continue

        frames = build_frames(rows, test_shot["exposure_time"], test_shot["analogue_gain"])
        path = OUT_DIR / f"{name}.json"
        dump_frames(
            path,
            {
                "name": name,
                "description": description,
                "source": f"captures {start} .. {end}",
                "config": REPLAY_CONFIG,
            },
            frames,
        )
        print(f"  {name}: {len(rows)} frames -> {path}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
