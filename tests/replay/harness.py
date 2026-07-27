"""Drive ExposureController through the capture loop's exact call order.

`replay()` is a transcription of the per-frame sequence in the capture loop --
smooth, decide, hysteresis, seed, settings, observe. Keep it in step with that
loop: if the loop's ordering changes, this must change with it, or the golden
files stop meaning anything.

The controller is imported through `load_controller()` rather than a plain
import so this module works either side of the package move, and the golden
files recorded before it stay directly comparable after.
"""

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SEQUENCE_DIR = Path(__file__).parent / "sequences"
GOLDEN_DIR = Path(__file__).parent / "golden"


def load_controller() -> Callable[..., Any]:
    """Return the ExposureController class from wherever it currently lives."""
    try:
        from raspilapse.camera.exposure import ExposureController  # noqa: F401

        return ExposureController
    except ImportError:
        pass
    try:
        from src.exposure import ExposureController

        return ExposureController
    except ImportError:
        from exposure import ExposureController

        return ExposureController


def load_modes() -> Any:
    """Return the LightMode constants from wherever they currently live."""
    try:
        from raspilapse.camera.exposure import LightMode  # noqa: F401

        return LightMode
    except ImportError:
        pass
    try:
        from src.exposure import LightMode

        return LightMode
    except ImportError:
        from exposure import LightMode

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


def replay(sequence: Dict, controller_cls: Optional[Any] = None) -> List[Dict]:
    """Run one recorded sequence through the controller.

    Returns one record per frame: the settings dict handed to the camera, the
    mode chosen, and the diagnostics block written into the frame's metadata.
    """
    if controller_cls is None:
        controller_cls = load_controller()
    modes = load_modes()

    config = sequence["config"]
    controller = controller_cls(config)

    seed = sequence.get("seed")
    if seed:
        controller.seed_from_capture(**seed)

    civil_twilight = config.get("location", {}).get("civil_twilight_threshold", -6)

    results: List[Dict] = []
    previous_mode: Optional[str] = None
    last_day_capture_metadata: Optional[Dict] = None

    for frame in sequence["frames"]:
        raw_lux = frame["raw_lux"]
        sun_elevation = frame.get("sun_elevation")

        lux = controller.smooth_lux(raw_lux)

        # The loop asks its own location for polar day, then passes the answer
        # in. Mirrored here rather than imported so the harness does not depend
        # on astral being installed.
        is_polar_day = sun_elevation is not None and sun_elevation > civil_twilight

        raw_mode = controller.determine_mode(lux, sun_elevation, is_polar_day)
        mode = controller.apply_hysteresis(raw_mode)

        entering_manual = previous_mode == modes.DAY and mode in (
            modes.TRANSITION,
            modes.NIGHT,
        )
        if entering_manual and not controller.transition_seeded:
            controller.seed_from_metadata(frame.get("test_metadata", {}), last_day_capture_metadata)

        if mode == modes.DAY and previous_mode != modes.DAY:
            controller.reset_seed_state()

        previous_mode = mode

        settings = controller.get_camera_settings(mode, lux)

        results.append(
            {
                "mode": mode,
                "raw_mode": raw_mode,
                "smoothed_lux": _round_floats(lux),
                "settings": _round_floats(settings),
                "diagnostics": _round_floats(controller.diagnostics()),
            }
        )

        # --- everything below happens after the frame is taken ---

        brightness = frame.get("brightness")
        if brightness:
            controller.observe_frame(brightness)

        capture_metadata = frame.get("capture_metadata")
        if capture_metadata and mode == modes.DAY:
            controller.update_day_wb_reference(capture_metadata)
            last_day_capture_metadata = capture_metadata

    return results
