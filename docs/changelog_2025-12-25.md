# Changelog 2025-12-25

## Summary
Attempted to fix two issues:
1. "Light flash" at dawn - 20s exposure staying on too long causing overexposure
2. Daytime images slightly too dark

## Changes Made

### 1. Fast Ramp-Down for Overexposure Detection
**Files:** `src/auto_timelapse.py`, `config/config.yml`

Added automatic fast exposure ramp-down when overexposure is detected:
- New state variable `_overexposure_detected`
- New method `_check_overexposure()` that triggers when:
  - Mean brightness > 180, OR
  - Overexposed pixels > 10%
- Clears when brightness < 150 AND overexposed < 5%
- When triggered, exposure interpolation speed increases from 0.10 to 0.30 (configurable)
- Added `fast_rampdown_speed` config option (default 0.30)

### 2. Made reference_lux Configurable
**Files:** `src/auto_timelapse.py`, `config/config.yml`

Previously hardcoded, now configurable per-camera in config.yml:
```yaml
adaptive_timelapse:
  reference_lux: 3.6  # Higher = brighter, Lower = darker
```

**Tuning history:**
- 2.5: too dark
- 3.5: "better but could be a little brighter"
- 4.5: way too bright
- 4.0: still way too bright
- 3.6: current setting (OK on this camera)

### 3. Lux Calculation (Reverted)
**File:** `src/auto_timelapse.py`

Initially changed to use metadata lux from test shot, but reverted back to calculated lux:
- Calculated lux is more reliable and consistent across cameras
- Camera's metadata lux is a black box that may vary between models
- The calculated lux is still passed to the overlay for display (see #4)

### 4. Pass Calculated Lux to Overlay
**Files:** `src/auto_timelapse.py`, `src/capture_image.py`

- Added `extra_metadata` parameter to `capture()` method
- `capture_frame()` now passes calculated lux to overlay
- Overlay displays the accurate lux instead of camera's unreliable estimate during manual exposure

### 5. Bug Fix: UnboundLocalError
**File:** `src/auto_timelapse.py`

Fixed initialization order bug where `_fast_rampdown_speed` was set before `transition_config` was loaded. Moved the assignment after `transition_config` is defined.

## Issues Encountered

### Erratic Behavior 13:00-14:00
Repeatedly changing `reference_lux` and restarting the service caused:
- Exposure jumping erratically (1s-4s instead of smooth)
- Severe overexposure (60-80% clipped pixels)
- Brightness spikes to 250+

**Root cause:** Each restart resets interpolation state and brightness feedback correction factor, causing sudden jumps instead of gradual transitions.

**Lesson learned:** Don't tune exposure parameters during daylight with frequent restarts. The system is designed for gradual changes.

### Other Cameras Too Bright
Other cameras at different IPs showed different brightness despite same code:
- Different scenes (facing different directions)
- Different sensor sensitivity
- Solution: `reference_lux` is now configurable per-camera

## Files Modified
- `src/auto_timelapse.py` - Main changes
- `src/capture_image.py` - Added extra_metadata parameter
- `config/config.yml` - Added reference_lux and fast_rampdown_speed options

## Testing Notes
- `py_compile` only checks syntax, not runtime errors
- Should run `pytest tests/test_auto_timelapse.py` after changes
- The UnboundLocalError was not caught because we only ran syntax check

## Later Revision (same day)

### Reverted Metadata Lux Change
After reviewing the change to use metadata lux instead of calculated lux, decided to revert:

**Reason:** The original request was only to show accurate lux in the overlay, not to change how exposure decisions are made.

**What we kept:**
- Calculated lux for exposure logic (original behavior, worked well this morning)
- Passing calculated lux to overlay for display (new, fixes "400 lux at night" display issue)

**What we reverted:**
- Using metadata lux for exposure decisions (too risky, camera's estimate is a black box)

**Final state:**
- `calculate_lux()` computes lux from image brightness (reliable)
- This value is used for mode detection and exposure calculations
- Same value is passed to overlay for accurate display

## Next Steps
- Monitor tomorrow's dawn transition to see if fast ramp-down helps
- Tune `reference_lux` on other cameras individually
- Consider if brightness feedback needs adjustment
