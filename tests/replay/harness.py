"""Drive ExposureController through the capture loop's exact call order.

`replay()` is a transcription of the per-frame sequence in the capture loop --
smooth, decide, seed, observe. Keep it in step with that loop: if the loop's
ordering changes, this must change with it, or the golden files stop meaning
anything.

The loop it drives is closed. The sequences store the brightness each frame
actually measured, but that measurement is a property of the exposure the
camera happened to be using at the time, so feeding it back to a controller
that would have chosen differently is not a replay of anything -- the
controller acts, the measurement does not respond, and it acts again. What the
sequences really carry is one number per frame that belongs to the scene rather
than the camera:

    luminance = measured brightness / (shutter x gain)

Play that at the controller, and show it the brightness its own choice would
have produced. See scene_luminance() and observe().

"""

import json
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# A sensor cannot report more than this.
SATURATED = 255.0

SEQUENCE_DIR = Path(__file__).parent / "sequences"
GOLDEN_DIR = Path(__file__).parent / "golden"


def load_controller() -> Callable[..., Any]:
    """Return the ExposureController class.

    Indirect so that record_golden and compare share one import site. It used
    to fall back to `src.exposure` and a bare `exposure` for the duration of
    the package move; those paths no longer exist, and keeping them would only
    turn a real ImportError from a broken transitive import into a confusing
    one from a module nobody ships.
    """
    from raspilapse.camera.exposure import ExposureController

    return ExposureController


def load_modes() -> Any:
    """Return the LightMode constants."""
    from raspilapse.camera.exposure import LightMode

    return LightMode


def load_sequence(name: str) -> Dict:
    with open(SEQUENCE_DIR / f"{name}.json") as f:
        return json.load(f)


def load_golden(name: str) -> Dict:
    with open(GOLDEN_DIR / f"{name}.json") as f:
        return json.load(f)


def dump_frames(path: Path, header: Dict, frames: List[Dict]) -> None:
    """Write a fixture as one frame per line.

    Fully indented these files run to 5 MB and one of them trips the repo's
    large-file hook; fully compact they are a single line, and an intended
    change to a golden file becomes an unreviewable one-line diff. A line per
    frame gives both: small enough to commit, and a diff that points at the
    frame where the behaviour changed.
    """
    with open(path, "w") as f:
        f.write("{\n")
        for key, value in header.items():
            f.write(f"  {json.dumps(key)}: {json.dumps(value, sort_keys=True)},\n")
        f.write('  "frames": [\n')
        last = len(frames) - 1
        for i, frame in enumerate(frames):
            line = json.dumps(frame, sort_keys=True, separators=(",", ":"))
            f.write(f"    {line}{',' if i < last else ''}\n")
        f.write("  ]\n}\n")


def _round_floats(value: Any, places: int = 6) -> Any:
    """Round recursively.

    The controller's arithmetic is deterministic, but it is float arithmetic,
    and the refactor reorders some of it (a sum of the same terms in a
    different association order can differ in the last bit). Six places is far
    below anything the camera can act on -- ExposureTime is an integer of
    microseconds -- while still catching any change that matters.
    """
    if isinstance(value, dict):
        return {k: _round_floats(v, places) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_floats(v, places) for v in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, places)
    return value


def scene_luminance(frame: Dict) -> Optional[float]:
    """What the scene was doing, independent of how it was photographed.

    The brightness the sensor would report at an exposure product of 1.0.
    None where the frame carries no usable metadata.
    """
    metadata = frame.get("capture_metadata") or {}
    brightness = (frame.get("brightness") or {}).get("mean_brightness")
    exposure_us = metadata.get("ExposureTime")
    gain = metadata.get("AnalogueGain") or 1.0

    # `is None`, not falsy: a frame that genuinely measured 0.0 is a
    # measurement, and the darkest frames are exactly the ones the closed loop
    # most needs to see. This is the same distinction _required_exposure makes.
    #
    # A saturated frame is the opposite case and is rejected: at the sensor
    # ceiling the reading bounds the light from below rather than measuring it,
    # so dividing would understate the scene. Same rule as compare.py, so the
    # goldens and the comparison share one scene model.
    if brightness is None or brightness >= SATURATED or not exposure_us:
        return None

    product = (exposure_us / 1e6) * gain
    return brightness / product if product > 0 else None


def _tail_fraction(mean: float, std: float, threshold: float) -> float:
    """Share of a normal distribution beyond `threshold`, as a percentage.

    Used to estimate how much of a frame clips. It was a step function to begin
    with -- 0% or 100% -- which meant the simulated frames never produced a
    clipped fraction anywhere near the 3% and 5% thresholds the metering acts
    on, and two of those constants could be changed without any test noticing.
    """
    if std is None or std <= 0:
        return 100.0 if mean >= threshold else 0.0
    return 100.0 * 0.5 * math.erfc((threshold - mean) / (std * math.sqrt(2.0)))


def observe(luminance: float, product: float, frame: Dict) -> Dict:
    """What the sensor would report for this scene at this exposure.

    First order: brightness is proportional to light times exposure, clipped at
    the top of the range. The spread is carried across from the recorded frame
    and scaled by the same factor, and the clipped and crushed fractions come
    from the tails of a normal of that mean and spread -- crude, but continuous,
    which the step function it replaced was not.
    """
    brightness = max(0.0, min(SATURATED, luminance * product))

    recorded = frame.get("brightness") or {}
    recorded_mean = recorded.get("mean_brightness") or 1.0
    scale = (luminance * product) / recorded_mean if recorded_mean else 1.0

    recorded_std = recorded.get("std_brightness")
    std = recorded_std * scale if recorded_std else None
    unclipped_mean = luminance * product

    return {
        "mean_brightness": brightness,
        "percentile_95": max(0.0, min(SATURATED, (recorded.get("percentile_95") or 0) * scale)),
        "std_brightness": min(std, 128.0) if std is not None else None,
        "overexposed_percent": round(_tail_fraction(unclipped_mean, std, 245.0), 4),
        "underexposed_percent": round(_tail_fraction(-unclipped_mean, std, -10.0), 4),
    }


def exposure_product(settings: Dict) -> float:
    return (settings.get("ExposureTime", 0) / 1e6) * settings.get("AnalogueGain", 1.0)


def replay(sequence: Dict, controller_cls: Optional[Any] = None) -> List[Dict]:
    """Run one recorded sequence through the controller, closed-loop.

    Returns one record per frame: the settings dict handed to the camera, the
    mode chosen, the brightness those settings would have produced, and the
    diagnostics block written into the frame's metadata.
    """
    if controller_cls is None:
        controller_cls = load_controller()
    modes = load_modes()

    controller = controller_cls(sequence["config"])

    seed = sequence.get("seed")
    if seed:
        controller.seed_from_capture(**seed)

    results: List[Dict] = []
    previous_mode: Optional[str] = None
    last_day_capture_metadata: Optional[Dict] = None

    for frame in sequence["frames"]:
        # Lux is still measured and recorded, but nothing decides from it.
        lux = controller.smooth_lux(frame["raw_lux"])

        settings = controller.decide()
        mode = controller.last_mode

        # Read now, not after the seeding below. seed_from_metadata overwrites
        # the shutter, gain and ladder position these describe, so a handover
        # frame would otherwise record the seed rather than the exposure the
        # frame was taken with -- and every golden would bake that in.
        diagnostics = _round_floats(controller.diagnostics())

        entering_manual = previous_mode == modes.DAY and mode in (
            modes.TRANSITION,
            modes.NIGHT,
        )
        if entering_manual and not controller.transition_seeded:
            controller.seed_from_metadata(frame.get("test_metadata", {}), last_day_capture_metadata)

        if mode == modes.DAY and previous_mode != modes.DAY:
            controller.reset_seed_state()

        previous_mode = mode

        # --- the frame is taken here ---

        luminance = scene_luminance(frame)
        measured = None
        if luminance is not None:
            metrics = observe(luminance, exposure_product(settings), frame)
            measured = metrics["mean_brightness"]
            controller.observe_frame(metrics)

        results.append(
            {
                "mode": mode,
                "ladder_position": _round_floats(controller.ladder_position),
                "smoothed_lux": _round_floats(lux),
                "measured_brightness": _round_floats(measured, 3),
                "settings": _round_floats(settings),
                "diagnostics": diagnostics,
            }
        )

        capture_metadata = frame.get("capture_metadata")
        if capture_metadata and mode == modes.DAY:
            controller.update_day_wb_reference(capture_metadata)
            last_day_capture_metadata = capture_metadata

    return results
