# TODO.md — Adaptive day/night transition improvements (Picamera2)

Owner: Claude Code  
Scope: Implement the agreed improvements to reduce flicker/flashing across night↔day transitions, reduce overhead, and make exposure control more stable and deterministic.

Primary files touched:
- `src/auto_timelapse.py`
- `src/capture_image.py`
- `src/overlay.py`
- `config/config.example.yml`
- `docs/TRANSITION_SMOOTHING.md` (and any other docs that describe the tuning knobs)
- tests: `tests/test_auto_timelapse.py`, `tests/test_capture_image.py`, `tests/test_overlay*.py` (as needed)

---

## Goals (what “done” means)

1. **Single camera instance** per timelapse run (no “open camera for test shot” + “open camera for real capture” every frame).
2. **Brightness measurement uses in-memory data from the capture request**, not the overlay-modified JPEG on disk.
3. **Use a low-res `lores` stream for measurement** (fast, low I/O) while saving the full-res image from `main`.
4. **Reduce measurement/test-shot frequency** (do not measure every single frame unless in transition window / first frame / explicitly configured).
5. **White balance is fixed**: one set of colour gains for day, one for night, and smooth interpolation during transition.
6. **Exposure control is EV-based** (single target exposure value), rate-limited per frame, and then split into shutter + gain with priorities:
   - Prefer longer shutter up to max, then increase gain (darker scenes)
   - Reverse on the way back: reduce gain before reducing shutter (brighter scenes)
7. **Test-shot (if still used) must disable AE** (`ae_enable: false`) so “fixed measurement settings” are truly fixed.
8. **Overlay JPEG save uses configured quality** (`output.quality`), not a hardcoded value.
9. Remove the unused config keys the user called out:
   - `adaptive_timelapse.transition_mode.analogue_gain_min`
   - `adaptive_timelapse.transition_mode.analogue_gain_max`
   - `adaptive_timelapse.night_mode.min_exposure_time`

---

## Phase 1 — Config schema changes

### 1.1 Remove unused keys
- Update `config/config.example.yml`:
  - Remove:
    - `adaptive_timelapse.transition_mode.analogue_gain_min`
    - `adaptive_timelapse.transition_mode.analogue_gain_max`
    - `adaptive_timelapse.night_mode.min_exposure_time`
- Update `docs/TRANSITION_SMOOTHING.md` and any docs that mention these keys.

### 1.2 Add fixed day WB gains (Option A)
- Add to `config/config.example.yml`:
  - `adaptive_timelapse.day_mode.colour_gains: [R, B]` (example values are fine; user will tune)
- Ensure `day_mode.awb_enable` is not required for day operation when `colour_gains` is present (AWB should be OFF in fixed-WB mode).

### 1.3 Add measurement frequency controls (minimal + practical)
Add a small config block (either new or extend existing `test_shot` section). Preferred: extend existing `adaptive_timelapse.test_shot` to avoid adding a new top-level concept.

Proposed keys (defaults chosen to preserve behavior but allow reduction):
- `adaptive_timelapse.test_shot.enabled` (keep)
- `adaptive_timelapse.test_shot.every_n_frames: 5`
- `adaptive_timelapse.test_shot.force_every_frame_in_transition: true`
- `adaptive_timelapse.test_shot.save_debug_image: false` (default off; when true, save `test_shot.jpg` for inspection)

Note: even if “test_shot” stops saving images, keep the config name to reduce migration friction.

---

## Phase 2 — Camera pipeline changes (add lores stream, single instance)

### 2.1 Add a `lores` stream to camera configuration
Update `src/capture_image.py` in `ImageCapture.initialize_camera()`:

- When building `camera_config = self.picam2.create_still_configuration(...)`:
  - Add a `lores` stream, e.g.:
    - `lores={"size": (320, 240), "format": "RGB888"}` (pick a format that’s easy to analyze without stride/plane issues)
  - Keep saving full-resolution still from `main`.

Acceptance:
- Existing capture still works: `request.save("main", ...)` produces the same image output.
- `request.make_array("lores")` works and returns a numpy array of expected shape.

### 2.2 Expose brightness metrics from the capture request (no disk reads)
Add a helper in `src/capture_image.py` (or in `auto_timelapse.py` if you prefer):

- `ImageCapture._compute_brightness_metrics_from_lores(request) -> dict`
  - Use `request.make_array("lores")`
  - Convert to grayscale in-memory (simple mean across channels is OK for now)
  - Compute:
    - `mean_brightness` (0–255)
    - `median_brightness`
    - optionally `p10`, `p90` for robustness (nice-to-have)

Store results somewhere accessible without changing public return values:
- Option A (preferred): store on the instance:
  - `self.last_brightness_metrics = {...}`
  - `self.last_request_metadata = metadata_dict` (optional)
- Option B: inject into metadata dict under a namespace key, e.g.:
  - `metadata_dict["raspilapse_brightness"] = {...}` (and persist in JSON metadata when enabled)

### 2.3 Ensure brightness feedback uses the in-memory metrics
Update `src/auto_timelapse.py`:
- Stop calling `_analyze_image_brightness(image_path)` for feedback.
- Instead use the metrics computed from `lores`:
  - e.g. `capture.last_brightness_metrics["mean_brightness"]`
- Keep `_analyze_image_brightness()` as a fallback/debug tool, but it should not be the default feedback path.

Acceptance:
- No overlay region affects brightness feedback.
- No disk I/O is required for brightness feedback.

---

## Phase 3 — Replace “test shot opens another camera” with in-stream measurement

### 3.1 Refactor test shot to reuse the main `ImageCapture` instance
In `src/auto_timelapse.py`:

Current behavior:
- `take_test_shot()` creates a new `ImageCapture(...)` context manager every loop.

New behavior:
- Initialize `capture = ImageCapture(self.camera_config)` once at the start of `run()`.
- `take_test_shot()` should accept `capture` as a parameter and reuse it:
  - `take_test_shot(self, capture: ImageCapture) -> (lux, metadata_like, brightness_metrics_like)`
  - It should:
    1. Apply measurement controls via `capture.update_controls()`
    2. Capture a request (no need to save `test_shot.jpg` unless `save_debug_image: true`)
    3. Read metadata via `request.get_metadata()`
    4. Compute brightness from `lores` via `request.make_array("lores")`
    5. Release request

### 3.2 **Critical**: disable AE for measurement
In test/measurement controls, explicitly set:
- `ae_enable: false`
- use a fixed `exposure_time` and `analogue_gain`
- `awb_enable: true` is allowed ONLY if you want to use it for calibration; otherwise keep it false.

Acceptance:
- Measurement is deterministic (not silently overridden by auto exposure).

### 3.3 Reduce measurement frequency
In the main loop:
- Only run measurement when needed:
  - Always on first frame (initialize lux/EV state)
  - Every `every_n_frames`
  - If `force_every_frame_in_transition: true`, measure every frame when `mode == TRANSITION`
- Otherwise reuse last smoothed lux / last computed lux.

---

## Phase 4 — Fixed day/night WB gains + smooth interpolation

### 4.1 Make day WB gains come from config (no continuous learning)
Update `src/auto_timelapse.py`:
- `_get_target_colour_gains()`:
  - Day gains should come from:
    1. `adaptive_timelapse.day_mode.colour_gains` if present
    2. else fallback to the current `_day_wb_reference` behavior (optional compatibility)
- Ensure `get_camera_settings()` sets:
  - `AwbEnable = 0`
  - `ColourGains = (...)` always (day/night/transition) in fixed-WB mode

### 4.2 Transition interpolation
Keep the existing transition position logic:
- position = 0.0 at night threshold
- position = 1.0 at day threshold
Compute:
- `ColourGains = night_gains + position * (day_gains - night_gains)`
Then apply your smoothing/interpolation (`_interpolate_colour_gains`) to avoid step changes.

Acceptance:
- AWB is not running during capture.
- WB does not flicker frame-to-frame.
- WB transitions smoothly at dawn/dusk.

---

## Phase 5 — EV-based exposure controller (single state, rate-limited)

### 5.1 Implement EV state + rate limiting
Create a new internal representation:
- `E = exposure_seconds * analogue_gain`
- `EV = log2(E)` (or use `log2(exposure_us * gain)`; just be consistent)

Add new state variables in `AdaptiveTimelapse.__init__()`:
- `self._last_ev = None`

Implement:
- `target_E` derived from lux in the same “inverse lux” spirit:
  - simplest: `target_E = k / lux`
  - incorporate existing brightness correction factor: `target_E *= self._brightness_correction_factor`
  - keep clamping so it doesn’t explode at extreme lux

Then:
- `target_EV = log2(target_E)`
- Rate limit per frame:
  - `EV_new = clamp(target_EV, EV_old - max_step_down, EV_old + max_step_up)`
  - Use existing config speeds as defaults if you prefer:
    - e.g. map `exposure_transition_speed` to max_step in EV units

### 5.2 Convert EV back into shutter + gain with priorities
Given:
- `min_shutter = adaptive_timelapse.day_mode.exposure_time`
- `max_shutter = adaptive_timelapse.night_mode.max_exposure_time`
- `min_gain = adaptive_timelapse.day_mode.analogue_gain`
- `max_gain = adaptive_timelapse.night_mode.analogue_gain`

Allocation rule (priority “shutter first, then gain”):
1. Compute `E = 2 ** EV_new`
2. Choose shutter:
   - `shutter = clamp(E / min_gain, min_shutter, max_shutter)`
3. Choose gain:
   - `gain = clamp(E / shutter, min_gain, max_gain)`

This naturally:
- increases shutter first up to max, then gain
- and reverses on the way back (gain drops before shutter once you hit min_gain)

### 5.3 Replace the dual independent smoothing
Update `get_camera_settings()` so that:
- It no longer separately interpolates gain + exposure from separate target functions
- It uses the EV controller to output:
  - `ExposureTime`
  - `AnalogueGain`
- Keep WB interpolation separate.

### 5.4 Keep brightness feedback, but apply it to EV/E (not “exposure only”)
Currently `_apply_brightness_feedback()` adjusts `self._brightness_correction_factor` which multiplies exposure.

Update so the correction factor multiplies `target_E` (not just shutter), which makes it consistent with EV/gain/shutter splits.

Acceptance:
- No more “gain changes fast while shutter changes slow” coupling artifacts.
- Exposure changes are smooth and bounded (no “flashes”).

---

## Phase 6 — Overlay quality respects config

### 6.1 Implement configured quality in `src/overlay.py`
Replace hardcoded `quality=95` with:
```python
quality = self.config.get("output", {}).get("quality", 95)
img.save(output_path, quality=quality)