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

The source files are restored in a finally, but they are edited in place while
the script runs: do not interrupt it with uncommitted changes to them.

Two constants are deliberately absent, having been shown unreachable rather
than merely unreached:

    ladder.label's `gain > 1.0` clause never decides on its own, because
    allocate() raises gain only once the shutter is at its ceiling, which
    already satisfies the knee. It is covered directly in test_ladder.py.

    the brightness floor in _required_exposure -- any value below
    target/MAX_CORRECTION, 30 at the default target, gives the same clamped
    ratio. It keeps the division defined and is not a tuning knob.

One more is absent for a different reason. metering's clipped-pixel release
threshold needs four conditions at once to show up in a rendered frame: a mean
between the underexposure release and the overexposure release, a clipped
fraction between 3% and 4%, a ladder position past 0.87 so that the recovery
rate exceeds the ordinary one, and a loop that has not yet converged. That is a
real corner rather than dead code, and it is pinned directly in
test_metering.py instead.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

LADDER = "raspilapse/camera/ladder.py"
EXPOSURE = "raspilapse/camera/exposure.py"
METERING = "raspilapse/camera/metering.py"

# (file, description, needle, replacement). The needle must appear at least once.
MUTATIONS = [
    # --- the ladder itself ------------------------------------------------
    (LADDER, "night knee", "NIGHT_KNEE = 0.8", "NIGHT_KNEE = 0.81"),
    (LADDER, "day knee", "DAY_KNEE = 0.01", "DAY_KNEE = 0.011"),
    (LADDER, "shortest usable shutter", "MIN_SHUTTER_S = 0.0001", "MIN_SHUTTER_S = 0.00011"),
    (
        LADDER,
        "shutter fills before gain",
        "shutter = min(required, max_shutter)",
        "shutter = min(required * 0.99, max_shutter)",
    ),
    (
        LADDER,
        "gain covers the remainder",
        "gain = required / shutter if shutter > 0 else 1.0",
        "gain = required / shutter * 0.99 if shutter > 0 else 1.0",
    ),
    (
        LADDER,
        "gain floor",
        "gain = max(1.0, min(max_gain, gain))",
        "gain = max(1.001, min(max_gain, gain))",
    ),
    (
        LADDER,
        "gain ceiling",
        "gain = max(1.0, min(max_gain, gain))",
        "gain = max(1.0, min(max_gain * 0.99, gain))",
    ),
    (
        LADDER,
        "ladder position scale",
        "return max(0.0, min(1.0, into / span))",
        "return max(0.0, min(1.0, into / span * 0.99))",
    ),
    # --- the feedback loop -------------------------------------------------
    (
        EXPOSURE,
        "cold start exposure",
        "COLD_START_EXPOSURE_S = 0.02",
        "COLD_START_EXPOSURE_S = 0.021",
    ),
    (EXPOSURE, "correction ceiling", "MAX_CORRECTION = 4.0", "MAX_CORRECTION = 3.99"),
    (EXPOSURE, "correction floor", "MIN_CORRECTION = 0.25", "MIN_CORRECTION = 0.26"),
    (
        EXPOSURE,
        "damping default",
        'self._damping = adaptive.get("brightness_damping", 0.5)',
        'self._damping = adaptive.get("brightness_damping", 0.5) * 0.99',
    ),
    (
        EXPOSURE,
        "feedback applies the ratio",
        "required = self._required * (ratio**self._damping)",
        "required = self._required * (ratio**self._damping) * 0.99",
    ),
    (
        EXPOSURE,
        "rate limit interpolation",
        "moved = 10 ** (log_last + speed * (log_target - log_last))",
        "moved = 10 ** (log_last + speed * 0.99 * (log_target - log_last))",
    ),
    # --- white balance -----------------------------------------------------
    (
        EXPOSURE,
        "white-balance cross-fade direction",
        "day[0] + into * (night[0] - day[0]),",
        "day[0] + (1 - into) * (night[0] - day[0]),",
    ),
    (
        EXPOSURE,
        "white-balance slew",
        "+ self._wb_speed * (target[0] - self._last_colour_gains[0]),",
        "+ self._wb_speed * 0.99 * (target[0] - self._last_colour_gains[0]),",
    ),
    (
        EXPOSURE,
        "cross-fade spans the transition band",
        "return max(0.0, min(1.0, (position - day_edge) / (night_edge - day_edge)))",
        "return max(0.0, min(1.0, (position - day_edge) / (night_edge - day_edge) * 0.99))",
    ),
    # --- metering ----------------------------------------------------------
    (
        METERING,
        "overexposure warning",
        "warning, critical, safe = 150, 170, 130",
        "warning, critical, safe = 149, 170, 130",
    ),
    (
        METERING,
        "overexposure critical",
        "warning, critical, safe = 150, 170, 130",
        "warning, critical, safe = 150, 169, 130",
    ),
    (
        METERING,
        "overexposure release",
        "warning, critical, safe = 150, 170, 130",
        "warning, critical, safe = 150, 170, 131",
    ),
    (
        METERING,
        "clipped-pixel warning",
        "clipped_warning, clipped_safe = 5, 3",
        "clipped_warning, clipped_safe = 6, 3",
    ),
    (
        METERING,
        "underexposure warning",
        "warning, critical, safe = 90, 70, 105",
        "warning, critical, safe = 91, 70, 105",
    ),
    (
        METERING,
        "underexposure critical",
        "warning, critical, safe = 90, 70, 105",
        "warning, critical, safe = 90, 71, 105",
    ),
    (
        METERING,
        "underexposure release",
        "warning, critical, safe = 90, 70, 105",
        "warning, critical, safe = 90, 70, 106",
    ),
    (
        METERING,
        "rate scales along the ladder",
        "smooth = position * self._normal_speed + (1.0 - position) * 1.0",
        "smooth = position * self._normal_speed + (1.0 - position) * 0.99",
    ),
    (
        METERING,
        "recovery only hurries",
        "return max(smooth, recovery)",
        "return min(smooth, recovery)",
    ),
    (
        METERING,
        "overcast boost",
        "self._base_target + self._overcast_boost * (1.0 - into)",
        "self._base_target + self._overcast_boost * (1.0 - into) * 0.99",
    ),
    (
        METERING,
        "overcast full-boost branch",
        "return min(self._base_target + self._overcast_boost, self._max_target)",
        "return min(self._base_target + self._overcast_boost - 1, self._max_target)",
    ),
    (
        METERING,
        "highlight protection slew",
        "self._p95_scale += self._p95_slew * (raw - self._p95_scale)",
        "self._p95_scale += self._p95_slew * (raw - self._p95_scale) * 0.99",
    ),
    (
        METERING,
        "highlight curve, gentle segment",
        "return 1.0 - ((p95 - safe) / (warning - safe)) * 0.05",
        "return 1.0 - ((p95 - safe) / (warning - safe)) * 0.051",
    ),
    (
        METERING,
        "highlight curve, moderate segment",
        "return 0.95 - ((p95 - warning) / (critical - warning)) * 0.10",
        "return 0.95 - ((p95 - warning) / (critical - warning)) * 0.101",
    ),
    (
        METERING,
        "highlight curve, steep segment",
        "return max(floor, 0.85 - ((p95 - critical) / 15) * 0.15)",
        "return max(floor, 0.85 - ((p95 - critical) / 15) * 0.151)",
    ),
    (
        METERING,
        "highlight protection off at the dark end",
        "if dark and not self._p95_apply_in_dark:",
        "if False and not self._p95_apply_in_dark:",
    ),
]


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
        for relative, description, needle, _ in MUTATIONS:
            print(f"  {Path(relative).name:14} {description:38} {needle}")
        return 0

    targets = {relative: REPO / relative for relative, _, _, _ in MUTATIONS}
    for relative, path in targets.items():
        if not path.exists():
            raise SystemExit(f"missing {relative}")

    originals = {relative: path.read_text() for relative, path in targets.items()}

    with tempfile.TemporaryDirectory() as tmp:
        for relative, path in targets.items():
            shutil.copy2(path, Path(tmp) / Path(relative).name)

        survivors = []
        try:
            for relative, description, needle, replacement in MUTATIONS:
                source = originals[relative]
                if needle not in source:
                    survivors.append((description, "needle not found -- mutation is stale"))
                    print(f"  STALE    {description}")
                    continue

                targets[relative].write_text(source.replace(needle, replacement, 1))
                if golden_tests_fail():
                    print(f"  caught   {description}")
                else:
                    survivors.append((description, "golden tests still passed"))
                    print(f"  SURVIVED {description}")
                targets[relative].write_text(source)
        finally:
            for relative, path in targets.items():
                path.write_text(originals[relative])

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
