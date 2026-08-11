"""Render the overlay with every input pinned, and hash the pixels.

A development tool, not part of the test run -- see the note on environments
below for why it is not a pytest test.

The overlay's product is an image. The 120 tests in tests/test_overlay*.py check
formatters, data preparation and error handling; none of them looks at a pixel.
So a change can move a section eight pixels left, or drop the tide wave
entirely, with the whole suite green. This renders the overlay and compares the
bytes:

    python3 tests/overlay_render_check.py > /tmp/before.json
    ...make your change...
    python3 tests/overlay_render_check.py > /tmp/after.json
    diff /tmp/before.json /tmp/after.json

Identical output means you did not change the picture. That is the check that
made it safe to take apply_overlay from 469 lines to 65.

Everything the render touches from outside is frozen -- the clock, the weather,
the tide, the aurora, the system metrics -- because otherwise two runs a minute
apart differ for reasons that have nothing to do with the code. The input image
is generated rather than read from disk, so there is no binary fixture to commit
and no dependence on which frames the camera happened to take today.

**Why this is not a pytest test with a committed hash.** Text lands on different
pixels under a different Pillow, a different FreeType or a different build of
DejaVu Sans. A committed hash would fail on a machine where nothing is wrong,
which trains people to re-record it, which is how a golden stops meaning
anything. Compared against itself on one machine it is exact; compared across
machines it is noise. The behaviour that *is* environment-independent -- section
ordering, widths, failure isolation -- is asserted in tests/test_overlay.py
instead.

The cases are chosen so that no two exercise the same path: the first three are
the top bar in each mode, the fourth flips the tide into its chronological
branch, and the last two are the corner-box layout, which shares nothing with
the top bar but the save. Dropping the corner-box cases was enough to hide a
real regression during the refactor -- the unit tests caught that one instead,
which is the argument for keeping both.
"""

import hashlib
import json
import logging
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from raspilapse.overlay.render import ImageOverlay  # noqa: E402

FROZEN = datetime(2026, 7, 27, 22, 30, 32)

AURORA = {"kp_str": "2.3", "bz_str": "0.9", "bz_arrow": "↑", "storm": "G0", "speed_str": "556"}

TIDE = {
    "level_str": "142cm",
    "arrow": "→",
    "target_level_str": "38cm",
    "high_time": datetime(2026, 7, 27, 23, 55),
    "low_time": datetime(2026, 7, 28, 6, 10),
    "high_time_str": "23:55",
    "high_level_str": "168cm",
    "low_time_str": "06:10",
    "low_level_str": "38cm",
    "level": 1.42,
    "high_level": 1.68,
    "low_level": 0.38,
    "next_event_type": "high",
    "trend": "rising",
}

# Same station, low water first. The high/low line is written in chronological
# order, so this is the only way to reach that branch -- with TIDE alone, an
# always-high-first mutant renders identically.
TIDE_LOW_FIRST = dict(
    TIDE,
    low_time=datetime(2026, 7, 27, 23, 10),
    high_time=datetime(2026, 7, 28, 5, 40),
    next_event_type="low",
)

WEATHER = {"temperature": 12.4, "wind_speed": 3.1}
FIELDS = {"temperature": "12.4", "wind_speed": "3.1"}
METRICS = {"cpu_temp": 48.2, "cpu_usage": 12.0, "memory_usage": 31.5, "disk_usage": 44.0}

METADATA = {
    "ExposureTime": 2493978,
    "AnalogueGain": 1.12,
    "ColourGains": [1.83, 2.02],
    "Lux": 3.8,
    "SensorTemperature": 41.0,
    "resolution": [4056, 3040],
}

CONFIG = {
    "overlay": {
        "enabled": True,
        "position": "top-bar",
        "font": {"family": "DejaVuSans.ttf", "size_ratio": 0.018, "color": [255, 255, 255, 255]},
        "background": {"enabled": True, "color": [0, 0, 0, 180], "padding": 0.3},
        "datetime": {"localized": False, "date_format": "%Y-%m-%d", "time_format": "%H:%M"},
        "content": {
            "line_1_left": "{datetime_localized}",
            "line_2_left": "{exposure} | {iso} | {mode}",
            "line_1_right": "{temperature}C {wind_speed}m/s",
            "line_2_right": "Lux {lux}",
        },
        "tide": {"enabled": True},
        "aurora": {"enabled": True},
        "ships": {"enabled": False},
        "weather": {"enabled": True},
    },
    "output": {"quality": 95},
}

# (name, tide reading, position preset)
CASES = [
    ("day", TIDE, "top-bar"),
    ("transition", TIDE, "top-bar"),
    ("night", TIDE, "top-bar"),
    ("night_low_first", TIDE_LOW_FIRST, "top-bar"),
    ("day_corner", TIDE, "bottom-left"),
    ("day_corner_tr", TIDE, "top-right"),
]


def gradient(width: int = 1600, height: int = 900) -> Image.Image:
    """A deterministic stand-in for a photograph.

    Colour varies along both axes so that a section drawn at the wrong offset
    lands on different pixels rather than on more of the same flat grey.
    """
    img = Image.new("RGB", (width, height))
    px = img.load()
    for y in range(height):
        for x in range(width):
            v = (x * 255 // width + y * 128 // height) % 256
            px[x, y] = (v, (v * 3) % 256, 255 - v)
    return img


def render_all(workdir: Path) -> dict:
    """Render every case and return {case name: pixel hash}."""
    overlay = ImageOverlay(CONFIG)
    overlay.tide.enabled = True
    overlay.aurora.enabled = True
    overlay.ships.enabled = False

    base = gradient()
    hashes = {}

    for name, tide_data, position in CASES:
        overlay.overlay_config["position"] = position
        work = workdir / f"{name}.jpg"
        base.save(work, quality=95)

        with (
            patch("raspilapse.overlay.render.datetime") as dt,
            patch.object(overlay.aurora, "get_widget_data", return_value=AURORA),
            patch.object(overlay.tide, "get_widget_data", return_value=tide_data),
            patch.object(overlay.tide, "format_tide_compact", return_value="142cm"),
            patch.object(overlay.weather, "get_weather_data", return_value=WEATHER),
            patch.object(overlay.weather, "format_fields", return_value=FIELDS),
            patch.object(overlay.system_monitor, "get_all_metrics", return_value=METRICS),
        ):
            dt.now.return_value = FROZEN
            # "night_low_first" and "day_corner" carry their variant in the
            # name; the mode passed to the overlay is the leading word.
            result = overlay.apply_overlay(str(work), METADATA, name.split("_")[0])

        if result is None:
            hashes[name] = "RENDER FAILED"
            continue
        with Image.open(work) as im:
            hashes[name] = hashlib.sha256(im.tobytes()).hexdigest()[:16]

    return hashes


def main() -> int:
    """Render every case and print the hashes as JSON."""
    logging.disable(logging.CRITICAL)
    with tempfile.TemporaryDirectory() as tmp:
        hashes = render_all(Path(tmp))
    print(json.dumps(hashes, indent=1, sort_keys=True))
    return 1 if any(v == "RENDER FAILED" for v in hashes.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
