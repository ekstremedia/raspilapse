# Claude Code Todo: "Holy Grail" Timelapse Transition logic

## Status: ✅ IMPLEMENTED (2025-12-24)

## Objective
Upgrade `src/auto_timelapse.py` from a binary "Day/Night" switch to a smooth **Linear Ramping System**. This will eliminate the "flash" (exposure pop) and "color snap" (AWB shift) during sunset and sunrise.

---

## 1. Seamless Handover (The Flash Killer) ✅
The "flash" occurs when the script switches from `AeEnable: True` to `Manual` with different settings.
* **Task:** The moment `current_lux` drops below the `day` threshold for the first time, the script must query the **camera metadata** of the *very last* frame.
* **Metadata to Capture:** `ExposureTime` and `AnalogueGain`.
* **The Seed:** Use these real-world values as the starting point for all interpolation math. This ensures Frame 1 of the transition is identical in brightness to the last frame of Auto mode.

**Implementation:**
- Added `_seed_from_metadata()` method to capture real camera settings
- Added `_last_day_capture_metadata` to store last day mode capture's metadata
- Seeds exposure, gain, and WB gains when entering transition mode
- Logs: `[Holy Grail] Seeded exposure from last capture: 0.0234s`

## 2. Twilight Zone Interpolation (The Lerp) ✅ (Already Existed)
Create a sliding scale between the two thresholds in `config.yml`.
* **Range:** Between `light_thresholds.day` (e.g., 100 lux) and `light_thresholds.night` (e.g., 10 lux).
* **Factor (t):** Calculate a progress value `t` where `0.0` is Day and `1.0` is Night.
* **Ramping:** Linearly interpolate the `ExposureTime` and `AnalogueGain` between the "Seed" values (from Task 1) and the `night_mode` targets defined in the config.
* **Manual Mode:** `AeEnable` must be `False` for the entire duration of the transition.

**Implementation (pre-existing):**
- `_interpolate_exposure()` - logarithmic interpolation (better than linear for perceived brightness)
- `_interpolate_gain()` - linear interpolation with clamping
- `_interpolate_colour_gains()` - linear WB interpolation
- All interpolations use configurable speeds (0.1-0.2 recommended)

## 3. AWB Locking (The Color Stabilizer) ✅
As light fades, Auto White Balance becomes unreliable and "flickers."
* **Task:** Once the transition begins (Lux < Day threshold), disable `AwbEnable`.
* **Fix Gains:** Capture the `AwbGains` (Red and Blue) from the last "Auto" frame metadata and apply them manually for every shot until the system returns to "Day" mode.

**Implementation:**
- AWB gains captured from test shot (which has AWB enabled) at transition entry
- Stored in `_seed_wb_gains` and used as `_day_wb_reference`
- AWB is disabled during entire transition (`AwbEnable: 0`)
- WB gains are manually interpolated between day and night values

## 4. Implementation Details ✅
* **Smoothness:** Ensure the `ema_lux` (Moving Average) is used for these calculations to prevent temporary shadows (like a bird or cloud) from triggering a transition.
* **Wait Time:** Ensure the loop accounts for longer shutter speeds (e.g., if shutter is 20s, the interval timer shouldn't start until the capture is finished).
* **Logging:** Update logs to show transition progress:
  `[Transition] Progress: 45% | Shutter: 1.5s | Gain: 2.4 | AWB: Locked`

**Implementation:**
- EMA lux smoothing: `_smooth_lux()` with configurable factor (default 0.3)
- Mode hysteresis: `_apply_hysteresis()` requires N consecutive frames before mode change
- New logging: `_log_transition_progress()` outputs Holy Grail format
- Loop timing: Already waits for capture to complete before calculating next interval

---

## New State Variables Added
```python
# Holy Grail transition state
self._transition_seeded: bool = False
self._seed_exposure: float = None
self._seed_gain: float = None
self._seed_wb_gains: tuple = None
self._previous_mode: str = None
self._last_day_capture_metadata: Dict = None
```

## New Methods Added
```python
def _seed_from_metadata(self, metadata: Dict, capture_metadata: Dict = None)
def _log_transition_progress(self, lux: float, position: float)
```

## Example Log Output
```
[Holy Grail] Seeded WB from AWB: [2.54, 1.62]
[Holy Grail] Seeded exposure from last capture: 0.0234s
[Holy Grail] Seeded gain from last capture: 1.45
[Holy Grail] Transition seeded - AWB locked, smooth interpolation will prevent flash
[Transition] Progress: 45% | Lux: 42.5 | Shutter: 234ms | Gain: 1.85 | AWB: Locked
```
