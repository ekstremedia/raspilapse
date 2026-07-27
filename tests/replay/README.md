# Replay fixtures

**What this directory is:** recorded sunsets, and what the exposure code decided
about them.

3.8 MB of JSON, against 1.1 MB of Python in the whole project. That ratio
deserves an explanation, so here it is.

## The problem

The code in `raspilapse/camera/` decides your camera's shutter speed and gain,
frame by frame, from daylight through dusk to a twenty-second night exposure.
It is the part of this project most worth not breaking, and the hardest to test
— you cannot point a camera at a sunset every time you edit a line, and by the
time a mistake shows up in a rendered video it has already ruined a night.

So the sunsets are recorded instead.

## The two halves

**`sequences/` — the light that arrived.** One file per stretch of time, one
entry per frame. Each says what the scene looked like and what the camera was
doing at that moment:

```json
{"timestamp": "2026-04-26T23:20:06",
 "brightness": {"mean_brightness": 125.15, ...},
 "capture_metadata": {"ExposureTime": 2493978, "AnalogueGain": 1.12}}
```

That is a real frame from a real dusk: 23:20 on 26 April, a 2.5-second exposure,
the frame landing at brightness 125 out of 255.

**`golden/` — what the code decided about it.** Feed a sequence through the
exposure controller and write down every decision:

```json
{"mode": "transition",
 "settings": {"ExposureTime": 2682261, "AnalogueGain": 1.0},
 "diagnostics": {...}}
```

The test in `tests/test_replay_golden.py` is then simply: *same light in, same
decisions out*. If an edit changes what the camera would do, the test fails and
names the frame it first diverged on.

That is what made it safe to move every module in this project and rewrite the
exposure controller: not an opinion that behaviour was preserved, but a
frame-by-frame check that it was.

## Real and synthetic sequences

**Real** ones come out of `data/timelapse.db` via `extract_sequences.py` —
`dusk_transition` is a real dusk, `deep_dark` the darkest frames on record,
`very_bright_night` a bright polar night in January.

**Synthetic** ones are written by `synthetic_sequences.py`, because six months
of one camera does not produce every condition the code handles.
`synthetic_extreme_gain` is a camera configured with a gain ceiling this one
does not have; no recorded frame would ever exercise that path.

## Why so many, and how that was decided

A recorded-output test can look thorough while checking nothing. So
`mutation_check.py` breaks the exposure code on purpose — one constant at a
time, 33 of them — and asserts the golden tests notice. **Every fixture here
exists because some mutation survived until it was added.** The sequences are
the residue of that experiment, not a guess at what might be useful.

Measured coverage, by how many of the 33 mutations each sequence detects:

| Sequence | Catches | Notes |
|---|---|---|
| `dawn_transition` | 26 | night/transition flapping, then the climb into day |
| `dusk_transition` | 22 | day falling through transition into night |
| `synthetic_clipped_pixels` | 19 | dark scene, blown highlights |
| `night_underexposure_edge` | 18 | frames on the underexposure boundary |
| `very_bright_night` | 18 | polar night bright enough to trade gain away |
| `bright_night` | 17 | **only one that catches** overexposure critical |
| `crashing_light` | 16 | light collapsing faster than the loop follows |
| `deep_dark` | 16 | the darkest frames on record |
| `synthetic_starved_light` | 16 | one-step crash into the feedback ratio clamp |
| `synthetic_extreme_gain` | 14 | a gain ceiling above the interpolator's |
| `synthetic_low_night_gain` | 14 | a night gain below the 2.0 floor |
| `synthetic_night_brightness_sweep` | 14 | every over/under threshold, mid-ramp |
| `stable_day` | 13 | an ordinary afternoon, cloud and all |
| `synthetic_clamped_highlights` | 13 | **only one that catches** the correction floor |
| `synthetic_underexposure_release` | 12 | **only one that catches** underexposure release |
| `blown_highlights` | 11 | midsummer noon against the top stop |
| `synthetic_clipping_sweep` | 11 | **only one that catches** the clipped-pixel warning |

Four are the sole detector of something. The rest overlap — and that overlap is
deliberate rather than accidental waste, because the recorded sequences do a
second job (below).

Three sequences were deleted when the exposure ladder landed:
`synthetic_threshold_edge_day`, `synthetic_threshold_edge_night` and
`synthetic_hybrid_override`. They pinned `determine_mode`'s `<` against `<=`
and the hybrid brightness override, and the ladder has neither a threshold to
sit on the edge of nor an override to trigger. They went on catching mutations
by coincidence afterwards, which is not a reason to keep a fixture.

## The second job

`compare.py` runs the current controller and any older one side by side over the
same recorded light, with the loop closed, and reports which exposes it more
accurately and with less flicker. That is what showed the ladder was an
improvement rather than merely a change:

```text
brightness error   35.0 -> 25.6
flicker, stops      0.077 -> 0.031
settled              38% -> 61%
```

Six of these sequences would be enough for the mutation check alone. The rest
are kept because that argument needs a realistic spread of light — dusk, dawn,
deep dark, blown highlights, polar night — and a thin sample would make the next
exposure change much harder to justify.

## Working with them

```bash
python3 tests/replay/extract_sequences.py --db data/timelapse.db   # real, needs the database
python3 tests/replay/synthetic_sequences.py                        # synthetic
python3 tests/replay/record_golden.py                              # re-record decisions
python3 tests/replay/mutation_check.py                             # prove the tests can fail
python3 tests/replay/compare.py                                    # this controller vs an older one
```

**Do not run `record_golden.py` to make a failing test pass.** Re-recording turns
a regression into a new baseline, which is the one way this whole apparatus
fails silently. The bar for re-recording: the commit's entire purpose is the
behaviour change, the message says so, and `compare.py` says the new behaviour
is better. It has happened once, for the exposure ladder.

If you add a constant to the controller, add a mutation for it in
`mutation_check.py`, watch it survive, then add the input that kills it. That
order matters — it is what stops a fixture being added on a hunch.

The files are one JSON object per line on purpose: fully indented they run to
5 MB and trip the repository's large-file hook, fully compact they become an
unreviewable single-line diff. A line per frame gives a diff that points at the
frame where behaviour changed.
