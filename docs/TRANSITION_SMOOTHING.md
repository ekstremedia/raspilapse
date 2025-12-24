# Day/Night Transition Smoothing

This document describes the transition smoothing system implemented to eliminate flickering during day/night transitions in timelapse captures.

## Problem Statement

The original system had several issues during dawn/dusk transitions:

1. **Blue/color flickering**: Sudden white balance changes when switching between manual WB (night) and auto WB (day)
2. **Brightness flashes**: Camera auto-exposure causing sudden brightness jumps
3. **Rapid mode flipping**: Lux measurements near thresholds causing rapid switching between modes
4. **AWB-induced color shifts**: Enabling AWB caused dramatic color changes (56% red increase, 36% blue decrease)

### Evidence from Metadata Analysis

```
Night mode:  Lux: 0.09  | WB:[1.83, 2.02] | CT:4070K  (manual)
Transition:  Lux: 2.03  | WB:[1.83, 2.02] | CT:4070K  (manual)
Day mode:    Lux: 50.28 | WB:[2.86, 1.48] | CT:7849K  (AWB enabled - SUDDEN FLIP)
```

The white balance flip from `[1.83, 2.02]` to `[2.86, 1.48]` was the primary cause of visible flickering.

---

## Solution Architecture

### Core Principles

1. **Never use AWB during transitions** - Always use manual color gains
2. **Smooth interpolation** - Gradually transition all settings between frames
3. **Hysteresis** - Require multiple consecutive frames before mode changes
4. **Learn daylight WB** - Capture camera's AWB values in bright conditions as reference

### State Variables

```python
self._smoothed_lux: float        # EMA of lux readings
self._last_mode: str             # Previous mode for hysteresis
self._mode_hold_count: int       # Counter for hysteresis
self._day_wb_reference: tuple    # AWB gains from bright daylight [red, blue]
self._last_colour_gains: tuple   # Previous frame's WB for interpolation
```

### Processing Pipeline

```
Raw Lux → EMA Smoothing → Mode Determination → Hysteresis → Camera Settings
                                                    ↓
                                            WB Interpolation
```

---

## Configuration

All settings are in `config/config.yml` under `adaptive_timelapse`:

```yaml
# Light thresholds determine mode switching
light_thresholds:
  night: 5     # Below = night mode (was 10, lowered for earlier transition)
  day: 80      # Above = day mode (was 100)

day_mode:
  exposure_time: 0.01          # 10ms for bright conditions
  analogue_gain: 1.0           # Minimum gain
  fixed_colour_gains: [2.5, 1.6]  # Fixed WB gains (optional, overrides AWB learning)

transition_mode:
  smooth_transition: true

  # === SMOOTH TRANSITION SETTINGS ===

  # Lux smoothing factor (EMA alpha)
  lux_smoothing_factor: 0.3

  # Hysteresis: frames required before mode change
  hysteresis_frames: 3

  # Transition speeds (0.0-1.0, lower = smoother)
  wb_transition_speed: 0.15
  gain_transition_speed: 0.10    # Slowed from 0.15
  exposure_transition_speed: 0.10  # Slowed from 0.15

  # Use smooth WB in day mode
  smooth_wb_in_day_mode: true

  # === BRIGHTNESS FEEDBACK SETTINGS ===

  # Enable brightness feedback for butter-smooth transitions
  brightness_feedback_enabled: true

  # Target brightness (0-255)
  target_brightness: 120

  # Tolerance before correction kicks in
  brightness_tolerance: 40

  # How fast correction adjusts (0.0-1.0, lower = smoother)
  brightness_feedback_strength: 0.2

test_shot:
  enabled: true
  exposure_time: 0.1
  analogue_gain: 1.0
  frequency: 1  # Take test shot every N frames (1 = every frame)
```

**Exposure Formula:** `target_exposure = (20 * 2.5) / lux × correction_factor`
- Base formula: 50 / lux
- Correction factor adjusts based on actual vs target brightness
- At lux 100: ~500ms (adjusted by correction)
- At lux 600: ~83ms (adjusted by correction)

See [TRANSITION_TUNING_LOG.md](TRANSITION_TUNING_LOG.md) for tuning history and adjustments.

---

## Algorithm Details

### 1. Lux Smoothing (EMA)

Exponential Moving Average prevents sudden lux spikes from triggering mode changes.

```python
def _smooth_lux(self, raw_lux: float) -> float:
    alpha = 0.3  # configurable
    self._smoothed_lux = alpha * raw_lux + (1 - alpha) * self._smoothed_lux
    return self._smoothed_lux
```

**Example with alpha=0.3:**
```
Frame 1: raw=10,  smoothed=10.0
Frame 2: raw=50,  smoothed=22.0  (not 50!)
Frame 3: raw=55,  smoothed=31.9
Frame 4: raw=52,  smoothed=37.9
Frame 5: raw=100, smoothed=56.5  (spike dampened)
```

### 2. Hysteresis

Mode only changes after N consecutive frames request the same new mode.

```python
def _apply_hysteresis(self, new_mode: str) -> str:
    if new_mode != self._last_mode:
        self._mode_hold_count += 1
        if self._mode_hold_count >= self._hysteresis_frames:
            self._last_mode = new_mode  # Accept change
            self._mode_hold_count = 0
        else:
            return self._last_mode  # Hold previous mode
    else:
        self._mode_hold_count = 0
    return new_mode
```

**Example with hysteresis_frames=3:**
```
Frame 1: lux=95  → mode=transition (held at night, count=1)
Frame 2: lux=102 → mode=day        (held at night, count=2)
Frame 3: lux=98  → mode=transition (reset, different mode requested)
Frame 4: lux=105 → mode=day        (held, count=1)
Frame 5: lux=110 → mode=day        (held, count=2)
Frame 6: lux=108 → mode=day        (ACCEPTED, count=3)
```

### 3. White Balance Interpolation

Gradually moves WB gains toward target instead of instant switching.

```python
def _interpolate_colour_gains(self, target_gains: tuple) -> tuple:
    speed = 0.15  # configurable
    new_red = last[0] + speed * (target[0] - last[0])
    new_blue = last[1] + speed * (target[1] - last[1])
    return (new_red, new_blue)
```

**Example transitioning from night [1.83, 2.02] to day [2.50, 1.60]:**
```
Frame 0: [1.83, 2.02]  (night)
Frame 1: [1.93, 1.96]  (+0.10 red, -0.06 blue)
Frame 2: [2.02, 1.90]
Frame 3: [2.09, 1.86]
...
Frame 15: [2.45, 1.63] (approaching day)
Frame 20: [2.49, 1.60] (converged)
```

### 4. Day WB Reference Learning

Captures camera's AWB values during bright daylight (>200 lux) to use as target.

```python
def _update_day_wb_reference(self, metadata: Dict):
    colour_gains = metadata.get("ColourGains")
    lux = metadata.get("Lux", 0)

    if colour_gains and lux > 200:
        if 1.0 < colour_gains[0] < 4.0 and 1.0 < colour_gains[1] < 4.0:
            self._day_wb_reference = tuple(colour_gains)
```

Default day reference if none learned: `[2.5, 1.6]`

### 5. Lores Stream Brightness Measurement

The brightness feedback system uses a low-resolution camera stream to measure image brightness without disk I/O or overlay contamination.

**How it works:**
1. Camera captures both main (4K) and lores (320×240) streams simultaneously
2. Brightness is computed from lores buffer directly in memory
3. Avoids reading the saved JPEG file (saves ~50ms disk I/O)
4. Measures RAW camera output before any overlay is applied

```python
def _compute_brightness_from_lores(self, request) -> Dict:
    lores_array = request.make_array("lores")  # Get 320x240 RGB buffer

    # Convert to grayscale: Y = 0.299*R + 0.587*G + 0.114*B
    gray = 0.299 * lores[:,:,0] + 0.587 * lores[:,:,1] + 0.114 * lores[:,:,2]

    return {
        "mean_brightness": np.mean(gray),
        "median_brightness": np.median(gray),
        "std_brightness": np.std(gray),
        # ... percentiles, under/overexposed %
    }
```

**Benefits:**
- **Fast**: No disk I/O, direct memory access
- **Accurate**: Measures raw camera output, not overlaid image
- **Consistent**: Same metrics as disk-based analysis
- **Low overhead**: 320×240 = 76,800 pixels vs 8MP+ main image

### 6. Brightness Feedback (Butter-Smooth Transitions)

Real-time brightness correction that analyzes each captured image and gradually adjusts exposure to maintain consistent brightness. This eliminates light flashes during transitions.

**How it works:**
1. After each capture, analyze actual mean brightness (0-255)
2. Compare to target brightness (default: 120)
3. If outside tolerance (±40), calculate correction factor
4. Apply correction factor to next frame's target exposure
5. Correction changes VERY gradually (0.2 per frame) for smoothness

```python
def _apply_brightness_feedback(self, actual_brightness: float) -> float:
    error = actual_brightness - target_brightness  # 120

    # Within tolerance? Slowly decay correction back to 1.0
    if abs(error) <= tolerance:  # 40
        # Decay towards 1.0 at 0.05 per frame
        return correction_factor

    # Outside tolerance? Adjust correction gradually
    error_percent = error / target_brightness
    adjustment = error_percent * feedback_strength  # 0.2
    correction_factor *= (1.0 - adjustment)

    return correction_factor  # Range: 0.25 to 4.0
```

**Example: Morning transition with images getting too bright**
```
Frame 1: brightness=160, target=120, error=+40 → correction=0.93 (reduce exposure)
Frame 2: brightness=155, target=120, error=+35 → correction=0.88
Frame 3: brightness=145, target=120, error=+25 → correction=0.84
...
Frame 10: brightness=125, target=120, error=+5 → within tolerance, decay
Frame 15: brightness=122, target=120 → stable, correction≈1.0
```

**Why this eliminates light flashes:**
- Changes are spread across many frames (feedback_strength=0.2)
- Goes through TWO layers of smoothing:
  1. Correction factor changes gradually (0.2 per frame max)
  2. Target exposure goes through interpolation (0.1 per frame)
- Net effect: actual exposure changes at ~0.02 per frame = butter smooth

---

## Mode-Specific Behavior

### Night Mode
- Manual exposure: 20 seconds
- Manual gain: 6.0
- Manual WB: Smoothly interpolated toward night gains `[1.83, 2.02]`
- AWB: Disabled

### Transition Mode
- Interpolated exposure: 20s → 50ms based on position
- Interpolated gain: 2.5 → 1.0 based on position
- Manual WB: Interpolated between night and day reference
- AWB: Disabled (CRITICAL - prevents flickering)

### Day Mode
- Auto exposure enabled
- Manual WB: Smoothly interpolated toward day reference
- AWB: Disabled (when `smooth_wb_in_day_mode: true`)
- Day WB reference updated from camera metadata

---

## Tuning Guide

### If transitions are too slow:
```yaml
lux_smoothing_factor: 0.5      # Faster lux response (was 0.3)
hysteresis_frames: 2           # Faster mode switching (was 3)
wb_transition_speed: 0.25      # Faster WB changes (was 0.15)
```

### If still seeing some flickering:
```yaml
lux_smoothing_factor: 0.2      # More smoothing (was 0.3)
hysteresis_frames: 5           # More stability (was 3)
wb_transition_speed: 0.10      # Slower WB changes (was 0.15)
```

### If colors are wrong in day mode:
```yaml
smooth_wb_in_day_mode: false   # Use camera AWB in full daylight
```

---

## Files Modified

| File | Changes |
|------|---------|
| `src/auto_timelapse.py` | Added smoothing methods, state tracking, WB interpolation |
| `config/config.yml` | Added transition smoothing configuration options |

---

## Testing

### Verify smooth transitions:
```bash
# Watch logs for WB interpolation
tail -f logs/auto_timelapse.log | grep -E "(WB|Transition|Lux)"
```

### Check metadata for gradual WB changes:
```bash
# Extract WB gains from recent captures
for f in /var/www/html/images/2025/12/23/*_metadata.json; do
  python3 -c "import json; d=json.load(open('$f')); print(f\"{d.get('ColourGains', 'N/A')}\")"
done
```

---

## Diagnostic Metadata

Each captured frame includes comprehensive diagnostic information to help analyze and tune transition behavior.

### Diagnostic Fields

The `diagnostics` section in metadata JSON files contains:

```json
{
  "diagnostics": {
    "mode": "transition",
    "smoothed_lux": 45.5,
    "raw_lux": 48.2,
    "transition_position": 0.39,
    "target_exposure_s": 0.439,
    "target_exposure_ms": 439.56,
    "target_gain": 2.15,
    "interpolated_exposure_s": 0.35,
    "interpolated_exposure_ms": 350.0,
    "interpolated_gain": 2.0,
    "hysteresis_hold_count": 0,
    "hysteresis_last_mode": "transition",
    "brightness": {
      "mean_brightness": 127.5,
      "median_brightness": 125.0,
      "std_brightness": 45.2,
      "percentile_5": 35.0,
      "percentile_25": 95.0,
      "percentile_75": 160.0,
      "percentile_95": 210.0,
      "underexposed_percent": 1.2,
      "overexposed_percent": 0.5
    }
  }
}
```

### Field Descriptions

| Field | Description |
|-------|-------------|
| `mode` | Current light mode: `day`, `night`, or `transition` |
| `raw_lux` | Lux from test shot before EMA smoothing |
| `smoothed_lux` | Lux after exponential moving average |
| `transition_position` | Position in transition (0.0=night end, 1.0=day end), null if not transition |
| `target_exposure_ms` | Calculated exposure before interpolation smoothing |
| `interpolated_exposure_ms` | Actual exposure sent to camera (after smooth interpolation) |
| `target_gain` | Calculated ISO/gain before interpolation |
| `interpolated_gain` | Actual gain sent to camera |
| `hysteresis_hold_count` | Frames waiting to confirm mode change (0 = stable) |
| `hysteresis_last_mode` | Mode being held during hysteresis |

### Brightness Analysis

The `brightness` sub-section analyzes the actual captured image:

| Field | Description | Good Range |
|-------|-------------|------------|
| `mean_brightness` | Average pixel brightness (0-255) | 80-180 |
| `median_brightness` | Median pixel brightness | 80-180 |
| `std_brightness` | Brightness variation (contrast indicator) | 30-70 |
| `percentile_5` | Shadow level (darkest 5% of pixels) | >10 |
| `percentile_95` | Highlight level (brightest 5% of pixels) | <245 |
| `underexposed_percent` | % of pixels below 10 (clipped blacks) | <5% |
| `overexposed_percent` | % of pixels above 245 (clipped highlights) | <5% |

### Using Diagnostics for Debugging

**Check if exposure is tracking lux correctly:**
```bash
python3 -c "
import json, glob
for f in sorted(glob.glob('/var/www/html/images/2025/12/23/*_metadata.json'))[-10:]:
    with open(f) as fp:
        d = json.load(fp)
        diag = d.get('diagnostics', {})
        print(f\"{f.split('/')[-1][25:33]}: lux={diag.get('smoothed_lux', 'N/A'):7.2f}, target={diag.get('target_exposure_ms', 'N/A'):7.1f}ms, actual={diag.get('interpolated_exposure_ms', 'N/A'):7.1f}ms\")
"
```

**Check image brightness vs exposure:**
```bash
python3 -c "
import json, glob
for f in sorted(glob.glob('/var/www/html/images/2025/12/23/*_metadata.json'))[-10:]:
    with open(f) as fp:
        d = json.load(fp)
        diag = d.get('diagnostics', {})
        bright = diag.get('brightness', {})
        print(f\"{diag.get('mode', 'N/A'):10}: mean={bright.get('mean_brightness', 'N/A'):6.1f}, under={bright.get('underexposed_percent', 'N/A'):5.2f}%, over={bright.get('overexposed_percent', 'N/A'):5.2f}%\")
"
```

**Identify exposure calculation issues:**
- If `target_exposure_ms` differs greatly from `interpolated_exposure_ms`, the smoothing is still catching up
- If `underexposed_percent` > 10%, increase exposure or gain
- If `overexposed_percent` > 5%, decrease exposure
- If `hysteresis_hold_count` is frequently > 0, lux is fluctuating near thresholds

---

## Future Improvements

- [x] Diagnostic metadata for debugging transitions (implemented 2025-12-23)
- [x] Image brightness analysis in metadata (implemented 2025-12-23)
- [x] Brightness feedback system for butter-smooth transitions (implemented 2025-12-24)
- [x] Lores stream for fast brightness measurement (implemented 2025-12-24)
- [x] Fixed day WB gains config option (implemented 2025-12-24)
- [x] Test shot frequency control (implemented 2025-12-24)
- [ ] Adaptive WB transition speed based on lux rate of change
- [ ] Per-channel WB smoothing (red and blue could have different speeds)
- [ ] Sunset/sunrise detection for optimized transition timing
- [ ] Store day WB reference persistently between restarts
- [ ] Machine learning to predict optimal WB for given lux level

---

## Changelog

### 2025-12-24 - Performance & Configuration Improvements
- Added lores stream for fast in-memory brightness measurement
  - Avoids disk I/O overhead (no need to read saved JPEG)
  - Avoids overlay contamination (measures raw camera data)
  - Uses low-resolution (320×240) stream for minimal overhead
- Added `fixed_colour_gains` option in day_mode for consistent WB across sessions
- Added test shot `frequency` control to reduce overhead in stable lighting
- Removed unused config keys (`analogue_gain_min`, `analogue_gain_max`, `min_exposure_time`)
- Fixed overlay quality to use config value instead of hardcoded 95

### 2025-12-23 - Diagnostic Metadata & Exposure Fix
- Added diagnostic metadata to every captured frame
- Added image brightness analysis (mean, median, percentiles, under/overexposed %)
- Added exposure/gain target vs interpolated values in metadata
- Added transition position tracking
- Fixed exposure calculation to use continuous lux-based formula
- Formula: `exposure = 20 / lux` (inverse relationship, clamped to 10ms-20s)

### 2025-12-23 - Initial Implementation
- Added lux EMA smoothing
- Added mode hysteresis
- Added WB interpolation
- Added day WB reference learning
- Configuration options in config.yml
