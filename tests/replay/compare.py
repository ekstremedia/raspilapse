"""Run both controllers against the same light and see which exposes it better.

The golden tests say whether behaviour changed. They cannot say whether the
change is an improvement, because they replay recorded brightness: the
measurement a frame produced under the *old* controller's exposure. Feed that
to a controller that would have chosen differently and the loop is open --
lower the exposure and the replayed brightness does not fall in response, so
the controller lowers it again, and again, into a runaway that could never
happen on a camera.

So this closes the loop. Each recorded frame gives up the one thing that is a
property of the scene rather than of the camera:

    luminance = measured brightness / (shutter x gain)

Play that sequence at both controllers, let each pick its own exposure, and
show each the brightness its own choice would actually have produced. Same
light, same weather, same six months -- two cameras.

    python3 tests/replay/compare.py
    python3 tests/replay/compare.py dusk_transition --verbose

What the numbers mean:

    brightness error   how far from the target it sits. Lower is better.
    flicker            mean frame-to-frame movement in stops. Lower is
                       smoother; this is what a viewer actually sees.
    recovery           frames to get back within a third of a stop after the
                       light steps. Lower is more responsive.

The old controller is read out of git so it cannot rot into agreement with the
new one.
"""

import argparse
import importlib.util
import math
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.replay.harness import SEQUENCE_DIR, load_sequence  # noqa: E402

# The commit before the ladder landed. Its exposure.py is the reference.
LEGACY_REF = "2080066"

# A sensor cannot report more than this, so a frame at the ceiling tells us
# only that the scene was at least this bright.
SATURATED = 254.0


def load_legacy_controller(ref: str):
    """Import the pre-ladder ExposureController out of git history."""
    repo = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["git", "show", f"{ref}:raspilapse/camera/exposure.py"],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    if result.returncode != 0:
        raise SystemExit(f"could not read exposure.py at {ref}: {result.stderr.strip()}")

    path = Path(tempfile.mkdtemp()) / "legacy_exposure.py"
    path.write_text(result.stdout)

    spec = importlib.util.spec_from_file_location("legacy_exposure", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["legacy_exposure"] = module
    spec.loader.exec_module(module)
    return module


def scene_luminance(sequence):
    """Recover what the scene was doing, independent of how it was photographed.

    Returns one value per frame: the brightness the sensor would report at an
    exposure product of 1.0. None where the recorded frame was saturated, since
    a clipped measurement puts only a lower bound on the light.
    """
    out = []
    for frame in sequence["frames"]:
        metadata = frame.get("capture_metadata") or {}
        brightness = (frame.get("brightness") or {}).get("mean_brightness")
        exposure_us = metadata.get("ExposureTime")
        gain = metadata.get("AnalogueGain") or 1.0

        # `is None`, not falsy. A frame measuring 0.0 is a measurement, and
        # dropping it would exclude the darkest frames of deep_dark and
        # crashing_light from exactly the comparison used to judge the ladder.
        if brightness is None or not exposure_us:
            out.append(None)
            continue

        product = (exposure_us / 1e6) * gain
        if product <= 0:
            out.append(None)
            continue

        out.append(brightness / product)
    return out


def observe(luminance, product, reference_frame):
    """What the sensor would report for this scene at this exposure.

    First order: brightness is proportional to light times exposure, clipped at
    the top of the range. p95 and the contrast measure are carried across from
    the recorded frame, scaled by the same factor -- crude, but they only feed
    highlight protection and the overcast boost, not the exposure itself.
    """
    brightness = max(0.0, min(255.0, luminance * product))

    recorded = reference_frame.get("brightness") or {}
    recorded_mean = recorded.get("mean_brightness") or 1.0
    scale = brightness / recorded_mean if recorded_mean else 1.0

    return {
        "mean_brightness": brightness,
        "percentile_95": max(0.0, min(255.0, (recorded.get("percentile_95") or 0) * scale)),
        "std_brightness": recorded.get("std_brightness"),
        "overexposed_percent": 100.0 if brightness >= SATURATED else 0.0,
        "underexposed_percent": 100.0 if brightness <= 1.0 else 0.0,
    }


def simulate_legacy(sequence, luminance, module):
    """Closed-loop run of the pre-ladder controller."""
    config = sequence["config"]
    controller = module.ExposureController(config)
    modes = module.LightMode

    seed = sequence.get("seed")
    if seed:
        controller.seed_from_capture(**seed)

    civil_twilight = config.get("location", {}).get("civil_twilight_threshold", -6)

    out = []
    previous_mode = None

    for frame, light in zip(sequence["frames"], luminance):
        lux = controller.smooth_lux(frame["raw_lux"])
        sun = frame.get("sun_elevation")
        polar = sun is not None and sun > civil_twilight

        mode = controller.apply_hysteresis(controller.determine_mode(lux, sun, polar))
        if mode == modes.DAY and previous_mode != modes.DAY:
            controller.reset_seed_state()
        previous_mode = mode

        settings = controller.get_camera_settings(mode, lux)
        product = (settings.get("ExposureTime", 0) / 1e6) * settings.get("AnalogueGain", 1.0)
        out.append({"mode": mode, "product": product, "brightness": None})

        if light is not None:
            metrics = observe(light, product, frame)
            out[-1]["brightness"] = metrics["mean_brightness"]
            controller.observe_frame(metrics)

    return out


def simulate_new(sequence, luminance):
    """Closed-loop run of the ladder controller."""
    from raspilapse.camera.exposure import ExposureController

    controller = ExposureController(sequence["config"])
    seed = sequence.get("seed")
    if seed:
        controller.seed_from_capture(**seed)

    out = []
    for frame, light in zip(sequence["frames"], luminance):
        controller.smooth_lux(frame["raw_lux"])
        settings = controller.decide()
        product = (settings.get("ExposureTime", 0) / 1e6) * settings.get("AnalogueGain", 1.0)
        out.append({"mode": controller.last_mode, "product": product, "brightness": None})

        if light is not None:
            metrics = observe(light, product, frame)
            out[-1]["brightness"] = metrics["mean_brightness"]
            controller.observe_frame(metrics)

    return out


# Both controllers start cold, and they start from different places: the old
# one opened at its night ceiling, which is accidentally near-correct for a
# dark scene, while the ladder starts at 20 ms. Scoring the convergence would
# be scoring their initial conditions, so it is excluded.
WARMUP_FRAMES = 20


def brightness_error(run, target, warmup=WARMUP_FRAMES):
    seen = [r["brightness"] for r in run[warmup:] if r["brightness"] is not None]
    if not seen:
        return float("nan")
    return math.sqrt(statistics.fmean((b - target) ** 2 for b in seen))


def flicker(run):
    """Mean frame-to-frame movement in stops -- what a viewer sees."""
    steps = []
    for a, b in zip(run[WARMUP_FRAMES:], run[WARMUP_FRAMES + 1 :]):
        if a["product"] > 0 and b["product"] > 0:
            steps.append(abs(math.log2(b["product"] / a["product"])))
    return statistics.fmean(steps) if steps else 0.0


def settled_fraction(run, target, tolerance=0.15, warmup=WARMUP_FRAMES):
    """Share of frames within `tolerance` of the target, as a fraction."""
    seen = [r["brightness"] for r in run[warmup:] if r["brightness"] is not None]
    if not seen:
        return float("nan")
    return sum(1 for b in seen if abs(b - target) <= target * tolerance) / len(seen)


def compare(name, legacy_module, verbose=False):
    sequence = load_sequence(name)
    luminance = scene_luminance(sequence)
    usable = sum(1 for value in luminance if value is not None)
    if usable < 10:
        print(f"\n{name}: only {usable} frames carry usable metadata, skipping")
        return None

    target = (
        sequence["config"]["adaptive_timelapse"]
        .get("transition_mode", {})
        .get("target_brightness", 120)
    )

    old = simulate_legacy(sequence, luminance, legacy_module)
    new = simulate_new(sequence, luminance)

    old_error, new_error = brightness_error(old, target), brightness_error(new, target)
    old_flicker, new_flicker = flicker(old), flicker(new)
    old_settled, new_settled = settled_fraction(old, target), settled_fraction(new, target)

    def arrow(old_value, new_value, lower_is_better=True):
        if math.isnan(old_value) or math.isnan(new_value):
            return ""
        better = new_value < old_value if lower_is_better else new_value > old_value
        if abs(new_value - old_value) < old_value * 0.02:
            return "  ="
        return "  better" if better else "  WORSE"

    print(f"\n{name}  ({len(old)} frames, {usable} with metadata, target {target})")
    print(
        f"  brightness error   old {old_error:7.2f}   new {new_error:7.2f}"
        f"{arrow(old_error, new_error)}"
    )
    print(
        f"  flicker, stops     old {old_flicker:7.4f}   new {new_flicker:7.4f}"
        f"{arrow(old_flicker, new_flicker)}"
    )
    print(
        f"  settled            old {old_settled:6.1%}    new {new_settled:6.1%}"
        f"{arrow(old_settled, new_settled, lower_is_better=False)}"
    )

    if verbose:
        print("    frame   luminance      old exp   old bri      new exp   new bri")
        for i in range(0, len(old), max(1, len(old) // 20)):
            light = luminance[i]
            if light is None:
                continue
            print(
                f"    {i:5}   {light:9.1f}   {old[i]['product']:10.5f} "
                f"{old[i]['brightness'] or 0:7.1f}   {new[i]['product']:10.5f} "
                f"{new[i]['brightness'] or 0:7.1f}"
            )

    return {
        "old_error": old_error,
        "new_error": new_error,
        "old_flicker": old_flicker,
        "new_flicker": new_flicker,
        "old_settled": old_settled,
        "new_settled": new_settled,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sequences", nargs="*", help="sequence names; default all")
    parser.add_argument("--ref", default=LEGACY_REF, help="git ref holding the old controller")
    parser.add_argument("--verbose", action="store_true", help="print a sample of frames")
    args = parser.parse_args()

    legacy = load_legacy_controller(args.ref)
    names = args.sequences or sorted(p.stem for p in SEQUENCE_DIR.glob("*.json"))

    print(f"Exposure ladder vs {args.ref}, closed loop over recorded scene luminance")

    results = [r for r in (compare(n, legacy, args.verbose) for n in names) if r]
    if not results:
        return 1

    print(f"\n=== across {len(results)} sequences ===")
    for label, old_key, new_key, lower in (
        ("brightness error", "old_error", "new_error", True),
        ("flicker", "old_flicker", "new_flicker", True),
        ("settled", "old_settled", "new_settled", False),
    ):
        old_values = [r[old_key] for r in results if not math.isnan(r[old_key])]
        new_values = [r[new_key] for r in results if not math.isnan(r[new_key])]
        # A sequence no longer than WARMUP_FRAMES scores NaN on everything, and
        # fmean raises on an empty list -- so a single short sequence would end
        # in a StatisticsError instead of a summary.
        if not old_values or not new_values:
            print(f"  {label:18} no frames past warmup to compare")
            continue
        wins = sum(
            1
            for r in results
            if not math.isnan(r[old_key])
            and (r[new_key] < r[old_key] if lower else r[new_key] > r[old_key])
        )
        print(
            f"  {label:18} old {statistics.fmean(old_values):8.4f}   "
            f"new {statistics.fmean(new_values):8.4f}   "
            f"new wins {wins}/{len(old_values)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
