# Next Session Context - Direct Brightness Control

## Quick Summary
> "On 2026-01-18 we replaced the ML exposure system with Direct Brightness Control.
> Simple physics-based feedback that converges in 3-5 frames instead of 10+."

## What Changed (2026-01-18)

### Problem with ML System
The ML-based exposure system had multiple smoothing layers that fought each other:
- Formula + ML blend + drift correction + interpolation (each at 15% speed)
- Even with "64% emergency increase", actual exposure only changed ~10% per frame
- Took 10+ frames (5+ minutes) to recover from brightness errors
- Day mode brightness was stuck at 85-95 instead of target 120

### Solution: Direct Brightness Control
Replaced complex ML with simple physics-based feedback:

```python
ratio = target_brightness / actual_brightness
new_exposure = current_exposure * ratio ** damping
```

With damping=0.5 (conservative):
- 50% of the correction applied each frame
- Converges in 3-5 frames instead of 10+
- No oscillation, stable convergence

### Results

| Metric | Before (ML) | After (Direct) |
|--------|-------------|----------------|
| Convergence speed | 10+ frames | 3-5 frames |
| Brightness stuck at | 85-95 | Converges to 115-120 |
| Complexity | 5 interacting systems | 1 simple ratio |

## Configuration

```yaml
# config/config.yml
adaptive_timelapse:
  direct_brightness_control: true   # Enable direct control
  brightness_damping: 0.5           # Conservative (0.5-0.8)

  transition_mode:
    target_brightness: 120          # Target mean brightness
```

## Verification Commands

```bash
# Check direct control is active
journalctl -u raspilapse | grep "DirectFB\|Skipped"
# Should see: "[ML v2] Skipped - using direct brightness control instead"
# And: "[DirectFB] brightness=X, target=120, ratio=Y..."

# Monitor brightness convergence
python scripts/db_stats.py 5m
# Should see brightness converging to 105-135 range

# Check service status
sudo systemctl status raspilapse
```

## Current State

| Item | Value |
|------|-------|
| Branch | `brightness_control` |
| Exposure control | Direct brightness feedback |
| ML system | Disabled (still available for rollback) |
| Target brightness | 120 |
| Damping | 0.5 (conservative) |
| Convergence | 3-5 frames |

## Key Files Modified

- `config/config.yml` - Added `direct_brightness_control`, `brightness_damping`
- `src/auto_timelapse.py` - Added `_calculate_exposure_from_brightness()` method
- `docs/CLAUDE.md` - Added "Direct Brightness Control" section
- `ML.md` - Added deprecation notice
- `UPGRADE.md` - Instructions for updating other Pis

## For Other Pis

See `UPGRADE.md` for instructions on updating other Raspberry Pis to use direct brightness control.

## Rollback

If direct control causes issues, edit `config/config.yml`:
```yaml
adaptive_timelapse:
  direct_brightness_control: false  # Or remove the line entirely
```
Then restart: `sudo systemctl restart raspilapse`

## Key Insight

The ML system was overengineered. The fundamental physics is simple:
- `exposure × scene_brightness = image_brightness`
- Therefore: `new_exposure = old_exposure × (target / actual)`

Adding damping (exponent < 1.0) prevents oscillation while still converging quickly.

---

## Overlay Improvements (2026-01-19)

### Fixed Widget Positioning
- **Aurora widget**: Now uses fixed-width templates to prevent shifting when arrow characters change (↑↓→)
- **Tide widget**: Expanded to show cm values: `H 13:18 (227cm) | L 07:10 (76cm)`
- **Ship boxes**: Consistent spacing with `box_margin` for both vertical and horizontal gaps

### Better Error Handling
- `apply_overlay()` now returns `None` on failure instead of original path
- **Main try/catch** wraps entire overlay drawing (lines 1391-1842 in overlay.py)
- Separate try/catch for image save operation
- Proper error logging with stack traces
- **Per-widget try/catch blocks** (added 2026-01-19): Each widget (aurora, tide, ships) now has its own try/catch so one failing widget doesn't break the entire overlay

### Overlay Debugging Commands

```bash
# Check overlay errors (widget failures logged individually)
grep -E "(ERROR|WARNING)" /home/pi/raspilapse/logs/overlay.log | tail -20

# Check if overlay is being applied
tail -50 /home/pi/raspilapse/logs/capture_image.log | grep -i overlay

# Check tide data freshness (is next_high in the past?)
cat /home/pi/pi-overlay-data/data/tides_current.json | python3 -m json.tool

# Errors to look for:
# "Failed to draw aurora widget: ..." - Aurora data issue
# "Failed to draw tide widget: ..."   - Tide data issue
# "Failed to draw ship boxes: ..."    - Ships data issue
# "Failed to apply overlay: ..."      - General overlay failure
# "Failed to save overlay image: ..." - Disk/permission issue
```

### Tide Data Freshness
- Reduced pi-overlay-data tide cache from 24h to 1h
- API endpoint updated to refresh hourly (was every 6h)

### Fixed: Tide Now Shows Future Events Only (2026-01-19)
**Problem**: After a high/low tide passed, overlay showed past events until backend refreshed.
- Example: At 14:55, overlay showed "H 13:18" (past high) instead of next high

**Solution**: Raspilapse now **always calculates** next high/low from the `points` array:
- New method `TideData._find_extremes_from_points()` analyzes the points array
- Finds local maxima (highs) and minima (lows) by detecting direction changes
- `get_next_high()` and `get_next_low()` filter to only return **future** events
- **No fallback** to backend's pre-calculated `next_high`/`next_low` (those fields are now ignored)
- Cache increased from 60s to 600s (10 min) since points cover 24 hours

**Backend simplification** (optional):
- Backend can remove `next_high`/`next_low` fields from tide data
- Only the `points` array is needed now
- Reduces backend complexity and eliminates stale data issues

**Files changed**:
- `src/overlay.py`: Added `_find_extremes_from_points()`, simplified `get_next_high()`, `get_next_low()`
- `tests/test_overlay.py`: Added `TestTideDataCalculation` class with 5 tests

**Verification**:
```bash
# Test the new calculation
python3 -c "
import yaml
from src.overlay import TideData
from datetime import datetime

with open('config/config.yml') as f:
    config = yaml.safe_load(f)

tide = TideData(config)
print(f'Now: {datetime.now()}')
print(f'Next high: {tide.get_next_high()}')
print(f'Next low: {tide.get_next_low()}')
"
```

---

## Mode Transition Brightness Fixes (Iteration 2: 2026-01-20)

### Problem: Artifacts Still Visible After Iteration 1 (2026-01-19)

Data from 2026-01-20 showed the previous fixes helped but artifacts remain:

1. **Morning dip (~08:08-08:11)**: Night mode reduces exposure but NOT gain, so brightness climbs until transition slashes both
2. **Evening flash (~16:33-16:37)**: Coordinated ramps at 8%/5% still cause brightness overshoot from combined exposure+gain increase

### Iteration 2 Root Cause Analysis

**Morning Dip**:
```
08:05:46  night  exp=12.16s  gain=6.00  bright=145  ← exposure hit floor (12s)
08:07:46  night  exp=12.03s  gain=6.00  bright=153  ← brightness still climbing!
08:08:16  trans  exp=10.38s  gain=5.50  bright=170  ← transition slashes both → DIP
```
- Night mode reduces EXPOSURE (20s→12s) but gain stays fixed at 6.0
- When exposure hits floor (60% of max), brightness can still climb

**Evening Flash**:
```
16:31:35  trans  exp=16.0s   gain=1.21  bright=62   ← low brightness
16:36:54  night  exp=18.39s  gain=3.64  bright=120  ← OVERSHOOT after 5 min
```
- Even at 8% gain ramps, combined exposure+gain increase causes overshoot

### Solution: Four Fixes in `auto_timelapse.py`

**Fix 1a: Brightness Feedback in Night Mode** (lines 2032-2042):
- When brightness > 140, night mode reduces exposure via physics feedback
- Minimum 60% max exposure (12s) to prevent over-reduction

**Fix 1b: Night Mode Gain Reduction** (lines 2044-2057, NEW):
- When exposure near floor (≤13.2s) AND brightness > 150
- Reduce gain proportionally: `gain = gain * (120/brightness)^0.5`
- Minimum gain 2.0 prevents complete darkness

**Fix 2a: Slower Coordinated Ramps** (lines 2074-2075):
- Base speeds reduced: gain 0.04 (was 0.08), exposure 0.03 (was 0.05)
- Spreads transition over ~20-30 minutes

**Fix 2b: Brightness Throttling** (lines 2077-2090, NEW):
- When brightness > 64 (80% of target 80), throttle ramp speed
- Throttle from 100% at brightness 64 to 30% at brightness 80+
- Prevents overshoot by slowing down as brightness approaches target

### Verification

```bash
# Monitor transitions in real-time
journalctl -u raspilapse -f | grep -E "(gain reduction|throttle|Entering night)"

# After next dawn/dusk:
python scripts/db_stats.py 30m

# Expected:
# - Morning: brightness stays 110-150, no drop below 100
# - Evening: brightness stays 60-90, no spike above 110
```

### For Other Cameras

```bash
cd /home/pi/raspilapse
git pull
sudo systemctl restart raspilapse
```
