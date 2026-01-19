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
new_exposure = current_exposure × ratio^damping
```

With damping=0.5 (conservative):
- 50% of the correction applied each frame
- Converges in 5-6 frames instead of 10+
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
| Branch | `mlv2` |
| Exposure control | Direct brightness feedback |
| ML system | Disabled (still available for rollback) |
| Target brightness | 120 |
| Damping | 0.5 (conservative) |
| Convergence | 5-6 frames |

## Key Files Modified

- `config/config.yml` - Added `direct_brightness_control`, `brightness_damping`
- `src/auto_timelapse.py` - Added `_calculate_exposure_from_brightness()` method
- `docs/CLAUDE.md` - Added "Direct Brightness Control" section
- `ML.md` - Added deprecation notice
- `UPGRADE.md` - Instructions for updating other Pis

## For Other Pis

See `UPGRADE.md` for instructions on updating other Raspberry Pis to use direct brightness control.

## Rollback

If direct control causes issues:
```yaml
# In config/config.yml:
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
