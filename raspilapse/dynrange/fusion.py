"""Exposure fusion: bracket the exposure, merge the best of every bracket.

Mertens-Kautz-Van Reeth fusion weighs every pixel by how well-exposed it is
in each bracket and blends through a multi-scale pyramid. There is no
radiance map and no tone-mapping operator, so the result looks like one
well-graded photograph rather than "HDR" -- which is exactly the brief.

No image alignment (cv2.AlignMTB) on purpose: the camera is on a fixed
mount, so only scene motion moves between brackets -- clouds, water, birds
-- and Mertens degrades gracefully to soft ghosting there, invisible at
timelapse playback. Alignment would cost seconds per frame to solve a
problem this rig does not have.

The timelapse constraint shapes the maths: the bracket spread is a
continuous, monotonic function of the base exposure, so consecutive frames
can never jump between looks. As the ladder climbs toward night the spread
narrows to zero and the plan degenerates to today's single-shot path --
the day-to-night transition is smooth by construction, not by switchery.
"""

import math
from typing import Callable, List

from raspilapse.logging_setup import get_logger

logger = get_logger("dynrange")

# At and below this base exposure the scene is bright enough for the full
# configured spread. Between here and single_shot_above_s the spread fades
# on a log-exposure ramp.
FULL_SPREAD_BELOW_S = 0.05

# Estimated non-capture cost of a fused frame: pyramid fusion of three 4K
# frames plus the JPEG encode and disk write, measured conservatively on a
# Pi 4. Only the budget guard reads these.
_FUSE_COST_S = 3.0
_ENCODE_COST_S = 1.0

# Keep this much of the slot free for the close/reopen cycle, the observe
# step and the schedule's own slack.
_SLOT_RESERVE_S = 5.0


def spread_ev(base_s: float, ev_spread: float, single_shot_above_s: float) -> float:
    """The bracket spread (in EV) a base exposure of ``base_s`` gets.

    Full ``ev_spread`` at short exposures, zero at ``single_shot_above_s``
    and beyond, log-ramped between -- continuous and monotonic in the base
    exposure, so the look can never flicker between consecutive frames.
    """
    if ev_spread <= 0 or base_s >= single_shot_above_s:
        return 0.0
    if base_s <= FULL_SPREAD_BELOW_S:
        return float(ev_spread)
    fraction = math.log2(single_shot_above_s / base_s) / math.log2(
        single_shot_above_s / FULL_SPREAD_BELOW_S
    )
    return float(ev_spread) * min(max(fraction, 0.0), 1.0)


def estimated_cost_s(exposures: List[float], settle_frames: int) -> float:
    """Wall-clock estimate for capturing and fusing this bracket list.

    The base shot is one frame period; every further bracket pays
    ``settle_frames`` discarded periods for the controls to land, plus its
    own frame. A frame period is the exposure plus the 100 ms slack
    FrameDurationLimits adds.
    """
    if len(exposures) <= 1:
        return exposures[0] + 0.1 if exposures else 0.0
    cost = exposures[0] + 0.1
    for exposure in exposures[1:]:
        cost += (settle_frames + 1) * (exposure + 0.1)
    return cost + _FUSE_COST_S + _ENCODE_COST_S


def plan_brackets(
    base_s: float,
    brackets: int,
    ev_spread: float,
    single_shot_above_s: float,
    interval_s: float,
    settle_frames: int,
) -> List[float]:
    """Exposure list for one slot, base first. A single entry means no fusion.

    Three brackets are base/under/over; two are base/under -- when only one
    extra shot fits, highlight rescue beats shadow lift, because blown
    highlights are unrecoverable downstream while shadows merely stay dark.
    The budget guard drops the over bracket first (it is the longest), then
    the under, rather than letting a slot overrun and skip the next frame.
    """
    spread = spread_ev(base_s, ev_spread, single_shot_above_s)
    if spread <= 0:
        return [base_s]

    factor = 2.0**spread
    if brackets >= 3:
        plan = [base_s, base_s / factor, base_s * factor]
    else:
        plan = [base_s, base_s / factor]

    while len(plan) > 1 and estimated_cost_s(plan, settle_frames) > interval_s - _SLOT_RESERVE_S:
        dropped = plan.pop()
        logger.debug(
            f"Fusion budget: dropping the {dropped:.3f}s bracket "
            f"(interval {interval_s:.0f}s, settle ~{settle_frames} frames)"
        )
    return plan


def build_fuse_fn(quality: int) -> Callable[[List], bytes]:
    """A callable that fuses bracket arrays and returns encoded JPEG bytes.

    Returning bytes keeps the capture path free of both cv2 and Pillow: the
    caller writes bytes to disk and never learns what produced them. cv2 is
    imported here, on first use, for the usual reasons (import cost, CI).
    """

    def fuse(frames: List) -> bytes:
        import cv2

        merged = cv2.createMergeMertens().process(frames)
        # Mertens returns float32 in roughly 0..1 but overshoots slightly on
        # saturated pixels; clip before scaling or they wrap to black.
        eight_bit = (merged.clip(0.0, 1.0) * 255).astype("uint8")
        ok, encoded = cv2.imencode(".jpg", eight_bit, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
        if not ok:
            raise RuntimeError("cv2.imencode refused the fused frame")
        return encoded.tobytes()

    return fuse
