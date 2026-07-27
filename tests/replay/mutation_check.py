"""Check that the golden replay tests can actually fail.

A golden-master test that passes no matter what the code does is worse than no
test: it reports safety it does not provide. This breaks one exposure constant
at a time and asserts the golden tests notice.

    python3 tests/replay/mutation_check.py            # all mutations
    python3 tests/replay/mutation_check.py --list     # just show them

Every mutation here was at some point NOT caught. Each one that now is caught
is caught because a fixture was added or fixed to reach it -- the sequences in
sequences/ are the residue of this script, not the other way round. If you add
a constant to the controller, add a mutation for it, watch it survive, then add
the input that kills it.

The source file is restored in a finally, but it is edited in place while the
script runs: do not interrupt it with uncommitted changes to the controller.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Located at import time so the mutations keep working across the package move.
CANDIDATE_TARGETS = [
    REPO / "raspilapse" / "camera" / "exposure.py",
    REPO / "raspilapse" / "camera" / "exposure" / "__init__.py",
    REPO / "src" / "exposure.py",
]

# (description, needle, replacement). The needle must appear at least once.
MUTATIONS = [
    (
        "night exposure floor",
        "exposure_floor = night_max * 0.6",
        "exposure_floor = night_max * 0.61",
    ),
    (
        "transition shutter knee",
        "if target_exposure >= night_max * 0.8:",
        "if target_exposure >= night_max * 0.81:",
    ),
    ("feedback ratio upper clamp", "min(4.0, ratio))", "min(3.99, ratio))"),
    ("feedback ratio lower clamp", "ratio = max(0.25,", "ratio = max(0.26,"),
    ("overexposure warning", "brightness_warning = 150", "brightness_warning = 149"),
    ("overexposure critical", "brightness_critical = 170", "brightness_critical = 169"),
    ("overexposure release", "brightness_safe = 130", "brightness_safe = 131"),
    ("clipped-pixel warning", "overexposed_warning = 5", "overexposed_warning = 6"),
    ("clipped-pixel release", "overexposed_safe = 3", "overexposed_safe = 4"),
    ("underexposure warning", "brightness_warning = 90", "brightness_warning = 91"),
    ("underexposure critical", "brightness_critical = 70", "brightness_critical = 71"),
    ("underexposure release", "brightness_safe = 105", "brightness_safe = 106"),
    (
        "day gain floor",
        "target_gain = self._interpolate_gain(1.0)",
        "target_gain = self._interpolate_gain(1.01)",
    ),
    (
        "night gain floor (reduction path)",
        "target_gain = max(2.0, current_gain * brightness_ratio**0.5)",
        "target_gain = max(2.1, current_gain * brightness_ratio**0.5)",
    ),
    (
        "night gain floor (default path)",
        "target_gain = max(2.0, min(night_gain, target_gain))",
        "target_gain = max(2.1, min(night_gain, target_gain))",
    ),
    (
        "night brightness-feedback gate",
        "self._last_brightness is not None and self._last_brightness > 140:",
        "self._last_brightness is not None and self._last_brightness > 141:",
    ),
    ("entering-night gain speed", "base_gain_speed = 0.04", "base_gain_speed = 0.045"),
    ("entering-night exposure speed", "base_exposure_speed = 0.03", "base_exposure_speed = 0.035"),
    (
        "entering-night proximity throttle",
        "throttle = max(0.3, 1.0 - (proximity - 0.8) * 2)",
        "throttle = max(0.35, 1.0 - (proximity - 0.8) * 2)",
    ),
    (
        "highlight protection slew",
        "self._p95_scale += self._p95_slew * (raw - self._p95_scale)",
        "self._p95_scale += self._p95_slew * (raw - self._p95_scale) * 0.99",
    ),
    (
        "hysteresis frame count",
        "if self._mode_hold_count >= self._hysteresis_frames:",
        "if self._mode_hold_count > self._hysteresis_frames:",
    ),
    (
        "lux EMA weighting",
        "self._smoothed_lux = alpha * raw_lux + (1 - alpha) * self._smoothed_lux",
        "self._smoothed_lux = (1 - alpha) * raw_lux + alpha * self._smoothed_lux",
    ),
    ("overcast target boost", "self._overcast_boost", "0 * self._overcast_boost"),
    (
        "white-balance cross-fade direction",
        "red = night_gains[0] + position * (day_gains[0] - night_gains[0])",
        "red = night_gains[0] + (1-position) * (day_gains[0] - night_gains[0])",
    ),
    (
        "exposure log-space interpolation",
        "log_new = log_last + speed * (log_target - log_last)",
        "log_new = log_last + speed * 0.99 * (log_target - log_last)",
    ),
    (
        "hybrid override, bright side",
        "brightness > BrightnessZones.WARNING_HIGH",
        "brightness > BrightnessZones.WARNING_HIGH * 1.02",
    ),
    (
        "hybrid override, dark side",
        "brightness < BrightnessZones.WARNING_LOW",
        "brightness < BrightnessZones.WARNING_LOW * 0.98",
    ),
    ("night threshold comparison", "if lux < night_threshold:", "if lux <= night_threshold:"),
    ("day threshold comparison", "elif lux > day_threshold:", "elif lux >= day_threshold:"),
    (
        "exposure hard ceiling",
        "new_exposure = max(0.0001, min(20.0, new_exposure))",
        "new_exposure = max(0.0001, min(19.9, new_exposure))",
    ),
    ("gain interpolation ceiling", "max(1.0, min(16.0,", "max(1.0, min(15.9,"),
    ("gain interpolation floor", "max(1.0, min(16.0,", "max(1.001, min(16.0,"),
]


def find_target() -> Path:
    for path in CANDIDATE_TARGETS:
        if path.exists():
            return path
    raise SystemExit(f"could not find the exposure module; looked in {CANDIDATE_TARGETS}")


def golden_tests_fail() -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_replay_golden.py", "-q", "-x", "--no-header"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return result.returncode != 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list mutations and exit")
    args = parser.parse_args()

    if args.list:
        for description, needle, _ in MUTATIONS:
            print(f"  {description:38} {needle}")
        return 0

    target = find_target()
    original = target.read_text()

    with tempfile.TemporaryDirectory() as tmp:
        backup = Path(tmp) / target.name
        shutil.copy2(target, backup)

        survivors = []
        try:
            for description, needle, replacement in MUTATIONS:
                if needle not in original:
                    survivors.append((description, "needle not found -- mutation is stale"))
                    print(f"  STALE    {description}")
                    continue

                target.write_text(original.replace(needle, replacement, 1))
                if golden_tests_fail():
                    print(f"  caught   {description}")
                else:
                    survivors.append((description, "golden tests still passed"))
                    print(f"  SURVIVED {description}")
        finally:
            shutil.copy2(backup, target)

    print()
    if survivors:
        print(f"{len(survivors)} of {len(MUTATIONS)} mutations were not caught:")
        for description, why in survivors:
            print(f"  - {description}: {why}")
        print("\nAdd a sequence that reaches the branch, or explain why it is unreachable.")
        return 1

    print(f"All {len(MUTATIONS)} mutations caught. The golden tests can fail.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
