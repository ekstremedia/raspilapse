# Exposure control

How Raspilapse decides what the camera should do, from noon to midnight and
back, without visible steps between frames.

The moving parts: `raspilapse/camera/exposure.py` holds the feedback loop and
its state, `ladder.py` splits a wanted exposure into shutter and gain,
`metering.py` measures what the last frame looked like and sets the target to
aim at. The capture daemon (`raspilapse/daemon.py`) feeds measurements in and
asks for settings.

## The cycle

Once per interval (default 30 s):

1. **Decide** — from the last frame's measured brightness, compute the
   required exposure and split it into shutter, gain and colour gains.
2. **Capture** — open the camera with those settings and take the real frame,
   with overlay, metadata and a database row.
3. **Measure** — mean brightness, contrast and highlight percentiles, read
   from the camera's own low-res stream. No disk round-trip, and the overlay
   is not in the measurement.
4. **Observe** — that measurement is the whole input to the next cycle.

Step 4 is the control loop. `observe_frame()` is the only thing that writes
the controller's inputs. A white-balance reference shot fires occasionally on
cameras that need one (see White balance); it is not part of the cycle.

## The loop and the ladder

```
required = last * (target / measured) ** damping
shutter  = min(required, ceiling)          # the shutter fills first
gain     = required / shutter              # gain covers what is left
```

That is the entire controller. No lookup table, no per-camera calibration, no
model to train. A longer shutter costs nothing but time; gain costs noise, so
the order is forced: the shutter runs to its ceiling
(`night_mode.max_exposure_time`, default 20 s) before gain rises toward its
own limit (`night_mode.analogue_gain`, default 6).

With `brightness_damping: 0.5` and a frame at 75 against a target of 120:

| Frame | Measured | Ratio | Correction | Result |
|-------|----------|-------|------------|--------|
| 1 | 75 | 1.60 | 1.26× | ~95 |
| 2 | 95 | 1.26 | 1.12× | ~107 |
| 3 | 107 | 1.12 | 1.06× | ~113 |
| 4 | 113 | 1.06 | 1.03× | ~117 |

Converged in four frames. The ratio is clamped to 0.25–4.0 so no single frame
can make a wild correction from a bad measurement.

| `brightness_damping` | Character |
|---|---|
| 0.5 | conservative — recommended |
| 0.7 | balanced |
| 0.8 | aggressive |

**There are no modes.** `night`, `transition` and `day` survive only as
labels, derived from the settings themselves — shutter at 80% of its ceiling
or any gain above 1.0 is `night`, shutter at or below 1% of the ceiling is
`day` — and written into the metadata, the database and the overlay. No
exposure decision consults them. The three modes this replaced were selected
by comparing an uncalibrated lux figure against absolute thresholds that had
to be retuned per camera and per site, then overridden by sun elevation at
high latitude. The ladder is defined by the camera's own limits, so it means
the same thing at 68°N in January as on the equator.

## The target

The loop aims at `adaptive_timelapse.brightness_target.base` (default 120,
0–255), with two adjustments:

- **Overcast boost.** A flat, low-contrast sky reads as dark at a fixed
  target, so as contrast falls the target rises — by up to `overcast_boost`
  (15), capped at `max_target` (140). Sunny scenes keep the base target; so
  does the dark end of the ladder, where a raised target would wash out
  aurora and stars.
- **Highlight protection** pulls the target down when the bright end of the
  frame approaches clipping — next section.

## Highlight protection

Mean brightness says nothing about whether the sky is blown out. A frame can
average a perfect 120 while the top 5% of pixels are pure white.

The 95th percentile brightness pulls the *target* down:

```
effective_target = target * highlight_scale
```

| p95 | Scale |
|-----|-------|
| ≤ 200 | 1.00 — exactly, so there is a real deadband |
| 200-220 | 1.00 → 0.95 |
| 220-240 | 0.95 → 0.85 |
| > 240 | down to `min_scale` (0.70) |

It scales the target, not the controller's output. Both settle, but scaling
the target leaves the loop's own fixed point intact, so how much protection
you get depends only on the `highlight_protection` settings. Scaling the
output instead would tie it to `brightness_damping`, an unrelated knob.

Three guards keep it calm: an exponential slew on the scale (0.25 per frame,
so one noisy sample cannot step the target), the exact 1.0 below `safe_p95`,
and `min_scale` as a hard floor.

**On by default.** **Night is exempt by default**: streetlamps and the moon
push p95 high while the frame as a whole is already darker than target, and
protecting those makes aurora frames worse. Set `apply_in_night: true` if
your scene calls for it, and `enabled: false` to turn the whole thing off.

## Startup seeding

On a cold start the first frame begins from 0.02 s with no history, so the
controller seeds from the last good capture in the database — exposure,
colour gains and brightness. "Good" excludes rows brighter than 180 or more
than 10% clipped, so a bad frame cannot poison the next start.

Two things are deliberately *not* seeded:

- **A stale row.** Older than 20 intervals, the seed is skipped entirely — a
  20-second night row seeded into a bright morning costs ~24 pure-white
  frames before the ramp catches up, which is worse than the cold start it
  was meant to prevent.
- **Reported gain, below the ceiling.** The `analogue_gain` column records
  what the sensor delivered, and this sensor's floor is 1.1228 against a
  commanded 1.0. The value is trusted only where the ladder genuinely
  commands gain — a row at the shutter ceiling with gain above 1.2.

```
[Startup] Seeded from last capture: exposure=0.0022s, WB=[2.50, 1.60], mode=day, brightness=118.6
```

## White balance

Manual in every mode. AWB drifting between frames is the single largest
source of colour flicker in a timelapse, and the flip when crossing from
night into daylight is dramatic. Instead, every delivered frame is taken with
`AwbEnable: 0`, and the gains cross-fade along the ladder's position at
`wb_transition_speed` — a fraction of the gap per frame, so there is no step
to see. The position is logarithmic, because light is: the step from 1/1000 s
to 1/500 s is the same amount of light as the step from 10 s to 20 s.

The daylight end of that cross-fade comes from one of two places, in this
order:

1. `day_mode.fixed_colour_gains`, if set. Fixed means fixed: nothing the
   camera observes overrides it, and the reference shot below is skipped
   automatically — there is nothing for it to teach.
2. Otherwise a reference the camera learns from a periodic reference shot —
   the one frame taken with AWB on, and so the only reading of what the
   scene's white actually is. Readings taken away from the bright end are
   discarded, because AWB has nothing to go on there.

The reference is never learned from an ordinary frame. Those are taken with
AWB off, so their `ColourGains` are the ones the controller just chose;
learning from them makes the reference its own input, and it drifts instead
of correcting.

Either source is only the *anchor*. With `day_mode.wb_feedback` enabled, a
closed loop trims the daylight white point around it, steered by what the
frames actually render. Every day frame's lores stream is scanned for
near-neutral pixels — overcast cloud, grey water, the scene's own grey card —
and the trim steps toward whatever makes them render grey, clamped to
`max_trim` (default ±12%) around the anchor and moving well under 1% per
frame, far below the cross-fade's own smoothing.

The loop exists because a better fixed number cannot: libcamera infers a
colour temperature from the manual gains and swaps its colour matrix as that
estimate moves, so rendered colour responds super-linearly to a gain change,
and the right gains differ with the weather. Measured on the camera this was
built for (2026-08-09): the same fixed gains that suited clear sky rendered
overcast grey as khaki, and a 16% red-gain cut moved the rendered red by 27%.
The trim is day-only — at night there is nothing neutral to meter, and an
aurora must never be white-balanced away — and it survives restarts in
`data/wb_trim.json`.

## Recovery

Two symmetric mechanisms handle a scene changing faster than the normal ramp:

- **Overexposure** — brightness above 180 or more than 10% clipped pixels
  switches to `fast_rampdown_speed`; above 200, `critical_rampdown_speed`.
- **Underexposure** — the mirror image: below 90 the `rampup` speeds engage,
  below 70 the critical one, releasing once brightness is back above 105.

These matter most at dawn, when the sky can brighten faster than a
conservative ramp will follow.

## Tuning

Symptoms and the setting to reach for:

| Symptom | Try |
|---------|-----|
| Images too dark or too bright overall | raise or lower `brightness_target.base` (default 120) |
| Brightness oscillating frame to frame | lower `brightness_damping` |
| Slow to recover after a light change | raise `brightness_damping`, or the ramp speeds |
| Blown-out skies | lower `highlight_protection.min_scale` (or check it wasn't disabled) |
| Shadows crushed against a bright sky | `dynamic_range.method: fusion`, then tune `fusion.ev_spread` |
| Colour shifting between frames | lower `wb_transition_speed` |
| Daylight colour drifting over days | set `day_mode.fixed_colour_gains` |
| A cast over daylight frames (grey clouds render khaki or teal) | enable `day_mode.wb_feedback`; or re-tune `fixed_colour_gains` against a grey reference |

Highlight protection and crushed shadows are two faces of one limit: a single
exposure per frame. The `adaptive_timelapse.dynamic_range` block (see
CONFIG-REFERENCE.yml) is the way past it — exposure fusion captures the ends
of the range in their own brackets instead of trading one against the other.

Watch what it is actually doing:

```bash
python3 scripts/db_stats.py 30m     # brightness should sit near target
python3 scripts/db_graphs.py 24h    # brightness.png, exposure_gain.png, white_balance.png
tail -f logs/auto_timelapse.log
```

Set `adaptive_timelapse.diagnostics.enabled: true` and every frame's metadata
JSON gains a `diagnostics` block with the ladder position, lux, target, what
the controller decided and what was applied. It ships disabled: the
brightness analysis it does costs 100-300 ms per capture.
