# Exposure control

How Raspilapse decides what the camera should do, from noon to midnight and
back, without visible steps between frames.

All of it lives in `raspilapse/camera/exposure.py`. `ExposureController` owns every piece of
per-frame state; `auto_timelapse.py` feeds it measurements and asks it for
settings.

## The cycle

Once per interval (default 30 s):

1. **Test shot** — a frame at fixed settings (0.1 s, gain 1.0), purely to
   measure light. Overwritten each time in `metadata/`; not part of the
   timelapse.
2. **Lux** — estimated from that frame's mean brightness together with its
   exposure and gain, then smoothed with an exponential moving average so a
   passing cloud doesn't shift the mode.
3. **Mode** — `night`, `transition` or `day`, from smoothed lux, sun elevation
   and the brightness of the previous frame.
4. **Settings** — exposure, gain and colour gains for this mode.
5. **Capture** — the real frame, with overlay, metadata and a database row.
6. **Observe** — the frame's own brightness feeds the next cycle.

Step 6 is the whole control loop. `observe_frame()` is the only thing that
writes the controller's inputs.

## Mode selection

```
lux < night_threshold                    -> night
lux > day_threshold                      -> day
between                                  -> transition
```

Two overrides sit on top:

**Civil twilight.** Above 68°N the sun can stay below the horizon for weeks, or
never set at all. If sun elevation is above `civil_twilight_threshold` (-6° by
default) the mode is forced to day regardless of lux — otherwise a dim polar
noon reads as night and gets a 20-second exposure.

**Brightness disagreement.** If the lux reading says night but the last frame
came back brighter than 160, or says day but the frame was darker than 80,
brightness wins. Lux is inferred; brightness is measured.

**Hysteresis.** A mode change must persist for `hysteresis_frames` cycles
before it takes effect, so a lux value hovering on a threshold cannot flip the
camera back and forth.

## The exposure loop

```
ratio        = target_brightness / measured_brightness
new_exposure = current_exposure * ratio ** brightness_damping
```

That is the entire controller. No lookup table, no per-camera calibration
beyond `reference_lux`, no model to train.

With `brightness_damping: 0.5` and a frame at 75 against a target of 120:

| Frame | Measured | Ratio | Correction | Result |
|-------|----------|-------|------------|--------|
| 1 | 75 | 1.60 | 1.26× | ~95 |
| 2 | 95 | 1.26 | 1.12× | ~107 |
| 3 | 107 | 1.12 | 1.06× | ~113 |
| 4 | 113 | 1.06 | 1.03× | ~117 |

Converged in four frames. The ratio is clamped to 0.25–4.0 so no single frame
can make a wild correction from a bad measurement.

Higher damping converges faster and overshoots more:

| `brightness_damping` | Character |
|---|---|
| 0.5 | conservative — recommended |
| 0.7 | balanced |
| 0.8 | aggressive |

### Shutter first, then gain

Gain adds noise, so the shutter does the work. Gain only rises once the
exposure is within 20% of `night_mode.max_exposure_time`, and then only as far
as needed:

```
if target_exposure >= night_max * 0.8:
    gain = min(night_gain, target_exposure / (night_max * 0.8))
    exposure = night_max * 0.8
else:
    gain = 1.0
```

### Startup seeding

On a cold start the ISP has no history, and the first test shot frequently
comes back saturated — which produces a wrong lux, which produces a blown first
frame. So on startup the controller is seeded from the last good capture in the
database: exposure, gain, colour gains, brightness, lux and mode.

"Good" excludes rows with brightness above 180 or more than 10% clipped pixels,
so a bad frame cannot poison the next start. If the first test shot after that
still comes back above 250, the seeded lux is used instead of the calculated
one.

```
[Startup] Seeded from last capture: exposure=0.0022s, gain=1.12, WB=[2.50, 1.60], mode=day, brightness=118.6
```

## Highlight protection

Mean brightness says nothing about whether the sky is blown out. A frame can
average a perfect 120 while the top 5% of pixels are pure white.

When enabled, the 95th percentile brightness pulls the *target* down:

```
effective_target = target_brightness * highlight_scale
```

| p95 | Scale |
|-----|-------|
| ≤ 200 | 1.00 — exactly, so there is a real deadband |
| 200-220 | 1.00 → 0.95 |
| 220-240 | 0.95 → 0.85 |
| > 240 | down to `min_scale` (0.70) |

It scales the target, not the controller's output. Both settle, but scaling the
target leaves the loop's own fixed point intact, so how much protection you get
depends only on the `highlight_protection` settings. Scaling the output instead
would tie it to `brightness_damping`, an unrelated knob.

Three guards keep it calm: an exponential slew on the scale (0.25 per frame, so
one noisy sample cannot step the target), the exact 1.0 below `safe_p95`, and
`min_scale` as a hard floor.

**Night is exempt by default.** Streetlamps and the moon push p95 high while the
frame as a whole is already darker than target; protecting those makes aurora
frames worse. Set `apply_in_night: true` if your scene calls for it.

Set `enabled: false` to turn the whole thing off.

## White balance

Manual in every mode. AWB drifting between frames is the single largest source
of colour flicker in a timelapse, and the flip when crossing a mode boundary is
dramatic:

```
Night       lux 0.09   WB [1.83, 2.02]  4070 K   manual
Transition  lux 2.03   WB [1.83, 2.02]  4070 K   manual
Day         lux 50.28  WB [2.86, 1.48]  7849 K   AWB -- visible jump
```

Instead, every delivered frame is taken with `AwbEnable: 0`, and the gains
cross-fade along the ladder at `wb_transition_speed` — a fraction of the gap
per frame, so there is no step to see.

The daylight end of that cross-fade comes from one of two places, in this
order:

1. `day_mode.fixed_colour_gains`, if set. Fixed means fixed: nothing the camera
   observes overrides it.
2. Otherwise a reference the camera learns, from the periodic test shot — the
   one frame taken with AWB on, and so the only reading of what the scene's
   white actually is. Readings taken away from the bright end are discarded,
   because AWB has nothing to go on there.

The reference is never learned from an ordinary frame. Those are taken with AWB
off, so their `ColourGains` are the ones the controller just chose; learning
from them makes the reference its own input, and it drifts instead of
correcting.

AWB is also expensive at night: leaving it on during a 20-second exposure costs
roughly a 5× slowdown in libcamera.

If you have `fixed_colour_gains` set, the test shot is doing nothing for you and
`test_shot.enabled: false` skips it — worth having, since it costs a camera
teardown and restart each time it fires.

## Recovery

Two symmetric mechanisms handle a scene changing faster than the normal ramp:

- **Overexposure** — brightness above 180 or more than 10% clipped pixels
  switches to `fast_rampdown_speed`; above 200, `critical_rampdown_speed`.
- **Underexposure** — the mirror image, using the `rampup` speeds.

These matter most at dawn, when the sky can brighten faster than a conservative
ramp will follow.

## Tuning

Symptoms and the setting to reach for:

| Symptom | Try |
|---------|-----|
| Images too dark overall | raise `reference_lux` or `target_brightness` |
| Images too bright | lower them |
| Brightness oscillating frame to frame | lower `brightness_damping` |
| Slow to recover after a light change | raise `brightness_damping`, or the ramp speeds |
| Blown-out skies | enable `highlight_protection` |
| Mode flipping at dusk | raise `hysteresis_frames` |
| Colour shifting between frames | lower `wb_transition_speed` |
| Daylight colour drifting over days | set `day_mode.fixed_colour_gains` |

Watch what it is actually doing:

```bash
python3 scripts/db_stats.py 30m     # brightness should sit near target
python3 scripts/db_graphs.py 24h    # brightness.png, exposure_gain.png, white_balance.png
tail -f logs/auto_timelapse.log
```

Set `adaptive_timelapse.diagnostics.enabled: true` and every frame's metadata
JSON gains a `diagnostics` block with the mode, lux, target, what the controller
decided and what was applied. It ships disabled: the brightness analysis it does
costs 100-300 ms per capture.
