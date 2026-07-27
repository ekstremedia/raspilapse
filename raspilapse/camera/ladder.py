"""The exposure ladder: one continuous scale from bright to dark.

A camera has exactly two ways to gather more light, and they are not
interchangeable. Opening the shutter for longer costs nothing but time. Raising
the gain costs noise. So the order is forced: run the shutter to its ceiling
first, and only then start trading gain.

That single rule is the whole of what used to be three modes. `_settings_day`
pinned gain at 1.0; `_settings_transition` did the same until the shutter
reached 80% of its ceiling; `_settings_night` opened the shutter fully and
raised gain. They were three regions of this one curve, with different log
messages, selected by comparing an uncalibrated lux figure against absolute
thresholds -- `night: 3`, `day: 80` -- that had to be retuned for every camera
and every site, and were patched at high latitude by a sun-elevation override.

None of that survives here. The ladder is defined by the camera's own limits,
so it means the same thing at 68°N in January as it does on the equator.

Everything in this module is pure: no state, no logging, no config lookups.
"""

import math
from typing import Tuple

# Fractions of the configured maximum shutter, not absolute times, so these
# mean the same thing whatever a given camera's ceiling is.
#
# Both are taken from what the old mode boundaries did in practice rather than
# chosen: 0.8 is the knee at which _settings_transition began trading gain, and
# 0.01 is where 95% of frames the old code labelled "day" actually sat (0.16 s
# against a 20 s ceiling).
NIGHT_KNEE = 0.8
DAY_KNEE = 0.01

# The shortest exposure worth asking for. Below this the sensor cannot comply
# and the request is silently clamped anyway.
MIN_SHUTTER_S = 0.0001


class LightMode:
    """Names for regions of the ladder.

    A label, and only a label. It is written into the frame's metadata, the
    database column and the overlay, and it is read by the graph scripts. No
    exposure decision consults it -- that is the point of the ladder.
    """

    NIGHT = "night"
    TRANSITION = "transition"
    DAY = "day"


def allocate(
    required: float,
    max_shutter: float,
    max_gain: float,
    min_shutter: float = MIN_SHUTTER_S,
) -> Tuple[float, float]:
    """Split a required exposure into shutter time and analogue gain.

    `required` is the product the feedback loop asked for -- seconds times
    gain. Shutter takes as much of it as it can hold, and gain covers whatever
    is left over.

    Args:
        required: Wanted exposure product, in second-gain units
        max_shutter: Longest exposure the camera is allowed to take
        max_gain: Highest analogue gain the camera is allowed to use
        min_shutter: Shortest exposure worth requesting

    Returns:
        (shutter seconds, analogue gain). Both are within their limits, and
        their product is `required` except where that is impossible -- past
        either end of the ladder the camera simply cannot go further.
    """
    shutter = min(required, max_shutter)
    shutter = max(shutter, min_shutter)

    gain = required / shutter if shutter > 0 else 1.0
    gain = max(1.0, min(max_gain, gain))

    return shutter, gain


def position(
    required: float,
    max_shutter: float,
    max_gain: float,
    min_shutter: float = MIN_SHUTTER_S,
) -> float:
    """Where on the ladder a required exposure sits, from 0 (bright) to 1 (dark).

    Logarithmic, because light is: the step from 1/1000 s to 1/500 s is the
    same amount of light as the step from 10 s to 20 s, and a linear position
    would spend almost its whole range on the last few minutes of dusk.

    This is what white balance now interpolates against. It used to interpolate
    against a lux figure's position between two configured thresholds, which
    made the colour of a twilight frame depend on numbers tuned for one site.
    """
    darkest = max_shutter * max_gain
    brightest = min_shutter

    if darkest <= brightest:
        return 0.0

    span = math.log10(darkest) - math.log10(brightest)
    into = math.log10(max(brightest, min(darkest, required))) - math.log10(brightest)
    return max(0.0, min(1.0, into / span))


def label(shutter: float, gain: float, max_shutter: float) -> str:
    """Name the region of the ladder a setting sits in.

    Derived from the settings themselves, so it cannot disagree with what the
    camera is doing. The old mode could: 368k frames were labelled "day", and
    among them were frames at a 20-second exposure and gain 5.5, because the
    polar-day override forced the label while the camera was wide open.
    """
    # The gain clause never decides on its own for settings that came out of
    # allocate(): gain rises only once the shutter is at its ceiling, which
    # already satisfies the knee. It is here because this is a public function
    # over arbitrary settings, and a caller passing high gain with a short
    # shutter means night whatever the shutter says.
    if shutter >= max_shutter * NIGHT_KNEE or gain > 1.0:
        return LightMode.NIGHT
    if shutter <= max_shutter * DAY_KNEE:
        return LightMode.DAY
    return LightMode.TRANSITION
