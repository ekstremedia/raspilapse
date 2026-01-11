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
  exposure_time: 0.02          # 20ms for bright conditions (raised from 10ms in Adj #4)
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

  # === EV SAFETY CLAMP ===

  # Holy Grail technique - prevents brightness jumps at day/night transition
  # DISABLE for scenes with bright point light sources (street lamps)
  ev_safety_clamp_enabled: true  # Default: true

  # === SEQUENTIAL RAMPING ===

  # Prioritize shutter over gain for lower noise
  sequential_ramping: true  # Default: true
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

### 7. EV Safety Clamp (Holy Grail Technique)

The EV Safety Clamp prevents brightness jumps when transitioning from day mode (auto-exposure) to night/transition mode (manual exposure). It implements the "Holy Grail" timelapse technique.

**How it works:**
1. When entering transition mode from day mode, capture the last auto-exposure values (exposure time, gain, WB)
2. These become the "seed" values for the manual mode
3. **On the first manual frame only**, compare proposed manual EV to the seeded auto EV
4. If they differ by >5%, clamp the manual values to match the seed EV
5. Subsequent frames use normal interpolation (clamp not applied again)

```python
def _apply_ev_safety_clamp(self, target_exposure, target_gain):
    # Skip if clamp was already applied (only apply on first frame)
    if self._ev_clamp_applied:
        return target_exposure, target_gain

    seed_ev = self._seed_exposure * self._seed_gain
    proposed_ev = target_exposure * target_gain

    ev_diff_percent = abs(proposed_ev / seed_ev - 1.0) * 100

    if ev_diff_percent > 5.0:
        # Clamp exposure to match seed EV
        clamped_exposure = seed_ev / target_gain
        self._ev_clamp_applied = True  # Mark as applied
        return clamped_exposure, target_gain

    return target_exposure, target_gain
```

**Important**: The `_ev_clamp_applied` flag ensures the clamp only runs once per transition cycle. When the camera returns to day mode, this flag is reset along with `_transition_seeded`.

**IMPORTANT: Street Lamp / Bright Point Light Issue**

The EV safety clamp can cause severely underexposed images when a bright point light source (street lamp, security light, etc.) is in the frame:

1. During day mode, the camera's auto-exposure sees the bright lamp
2. Auto-exposure uses short exposure (e.g., 300µs) due to the bright spot
3. When transitioning to night mode, this incorrect short exposure is seeded
4. The EV clamp forces all night exposures to match this short seed
5. Result: 330ms exposures instead of 20s → almost black images

**Configuration:**
```yaml
adaptive_timelapse:
  transition_mode:
    # Disable for scenes with bright point light sources
    ev_safety_clamp_enabled: false  # Default: true
```

**When to disable:**
- Street lamps in frame
- Security lights
- Any bright, fixed point light sources
- Cameras pointing at lit buildings/parking lots

**When to keep enabled:**
- Natural landscapes without artificial lights
- Scenes with uniform lighting distribution
- When you see brightness jumps at day/night transitions

### 8. Two-Tier Overexposure Detection

Detects overexposure at two severity levels for appropriate response speed.

```python
def _check_overexposure(self, brightness_metrics: Dict) -> bool:
    mean_brightness = brightness_metrics.get("mean_brightness", 0)
    overexposed_pct = brightness_metrics.get("overexposed_percent", 0)

    if mean_brightness > 170 or overexposed_pct > 10:
        self._overexposure_severity = "critical"  # Use aggressive correction
    elif mean_brightness > 150 or overexposed_pct > 5:
        self._overexposure_severity = "warning"   # Use fast correction
    elif mean_brightness < 130 and overexposed_pct < 3:
        self._overexposure_severity = None        # Clear, use normal speed

    return self._overexposure_detected
```

**Threshold Summary:**

| Level | Brightness | Clipped | Ramp Speed |
|-------|------------|---------|------------|
| Critical | > 170 | > 10% | 0.70 |
| Warning | > 150 | > 5% | 0.50 |
| Normal | < 130 | < 3% | 0.10 |

### 9. Proactive Exposure Correction

Analyzes test shot brightness BEFORE actual capture to prevent overexposure proactively.

**Note:** Thresholds were softened in Adjustment #4 (2026-01-09) to prevent dark dips.

```python
def _apply_proactive_exposure_correction(self, test_image_path, raw_lux):
    brightness_metrics = self._analyze_image_brightness(test_image_path)
    test_brightness = brightness_metrics.get("mean_brightness", 128)

    if test_brightness > 200:  # Was 180
        # Very bright test shot - 20% reduction (was 30%)
        self._brightness_correction_factor *= 0.8
    elif test_brightness > 160:  # Was 140
        # Bright test shot - 10% reduction (was 15%)
        self._brightness_correction_factor *= 0.9

    # Floor at 0.5 to prevent over-darkening (was 0.25)
    self._brightness_correction_factor = max(0.5, self._brightness_correction_factor)

    # Also check for rapid brightening
    if raw_lux / self._previous_raw_lux > 2.0:
        # Lux more than doubled - proportional reduction
        self._brightness_correction_factor *= 0.85  # Was 0.8
```

**Why this helps:**
- Acts BEFORE the capture, not after
- Uses the short-exposure test shot as a preview
- If test shot is bright, the long-exposure actual capture will be very bright
- Proactive reduction prevents overexposure from happening
- Softened thresholds (160/200 vs 140/180) prevent false positives
- Higher floor (0.5 vs 0.25) prevents over-darkening

### 10. Rapid Lux Change Detection

Detects when ambient light is changing quickly (dawn/dusk).

```python
def _detect_rapid_lux_change(self, raw_lux: float) -> bool:
    ratio = max(raw_lux / self._previous_raw_lux,
                self._previous_raw_lux / raw_lux)

    if ratio > 3.0:  # Configurable threshold
        logger.info(f"[RapidLux] Rapid change: {ratio:.1f}x")
        return True
    return False
```

This enables logging and potentially faster response during transition periods.

### 11. Sequential Ramping (Noise Reduction)

Sequential ramping prioritizes shutter speed over ISO gain to minimize noise during transitions.

**Phase 1 (Shutter Priority):** As light decreases, increase shutter speed first while keeping gain low
**Phase 2 (Gain Priority):** Once shutter hits maximum (20s), increase gain

This produces cleaner images because longer exposures at low ISO have less noise than shorter exposures at high ISO.

```yaml
adaptive_timelapse:
  transition_mode:
    sequential_ramping: true  # Default: true
```

### 12. Underexposure Detection and Fast Ramp-Up

Added in Adjustment #4 (2026-01-09), significantly improved in Adjustment #5 (2026-01-10).

**Problem:** During day-to-night transitions, exposure needs to ramp from ~20ms to ~20s. With slow interpolation (0.10 per frame), the exposure lags behind the rapidly dropping light, causing dark frames - visible as a dark band in slitscans.

**Solution:** Symmetric to overexposure detection - detect underexposure and use fast ramp-UP speed.

```python
def _check_underexposure(self, brightness_metrics: Dict) -> bool:
    mean_brightness = brightness_metrics.get("mean_brightness", 128)

    # Thresholds (lowered for faster response)
    brightness_warning = 90   # Early warning (target is 120)
    brightness_critical = 70  # Critical underexposure
    brightness_safe = 105     # Clear above this

    if mean_brightness < brightness_critical:
        self._underexposure_severity = "critical"
    elif mean_brightness < brightness_warning:
        self._underexposure_severity = "warning"
    elif mean_brightness > brightness_safe:
        self._underexposure_severity = None

    return self._underexposure_detected
```

**Key Change (2026-01-10):** Now works in ANY mode, not just at minimum exposure. This is critical for smooth day-to-night transitions where exposure is ramping UP but lagging behind the light drop.

**Fast Ramp-Up Speed (New!):**

When underexposure is detected, exposure interpolation speed increases from 0.10 to 0.50-0.70:

```python
def _get_rampup_speed(self) -> float:
    """Get appropriate ramp-up speed based on underexposure severity."""
    if not self._underexposure_detected:
        return None
    if self._underexposure_severity == "critical":
        return self._critical_rampup_speed  # 0.70
    return self._fast_rampup_speed  # 0.50
```

**Threshold Summary:**

| Level | Brightness | Ramp Speed | Action |
|-------|------------|------------|--------|
| Critical | < 70 | 0.70 (7x faster) | Aggressive ramp-up |
| Warning | < 90 | 0.50 (5x faster) | Fast ramp-up |
| Normal | > 105 | 0.10 | Normal interpolation |

**Configuration:**
```yaml
transition_mode:
  # Fast ramp-up speeds for underexposure correction
  fast_rampup_speed: 0.50      # Warning level
  critical_rampup_speed: 0.70  # Critical level
```

**State Variables:**
```python
self._underexposure_detected: bool = False
self._underexposure_severity: str = None  # "warning" or "critical"
self._fast_rampup_speed: float = 0.50
self._critical_rampup_speed: float = 0.70
```

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
- [x] Fast overexposure ramp-down (implemented 2025-12-25)
- [x] Configurable reference_lux per camera (implemented 2025-12-25)
- [x] FFMPEG deflicker filter in video output (implemented 2025-12-25)
- [x] EV Safety Clamp config option for bright point light sources (implemented 2026-01-03)
- [x] Sequential ramping documentation (implemented 2026-01-03)
- [x] Two-tier overexposure detection with severity levels (implemented 2026-01-08)
- [x] Proactive exposure correction based on test shot brightness (implemented 2026-01-08)
- [x] Rapid lux change detection (implemented 2026-01-08)
- [x] Fast ramp-up for underexposure (symmetric to ramp-down) (implemented 2026-01-10)
- [ ] Adaptive WB transition speed based on lux rate of change
- [ ] Per-channel WB smoothing (red and blue could have different speeds)
- [ ] Sunset/sunrise detection for optimized transition timing
- [ ] Store day WB reference persistently between restarts
- [ ] Machine learning to predict optimal WB for given lux level

---

## Changelog

### 2026-01-10 - EV Safety Clamp Single-Apply Fix

**Problem Identified:**
- Camera 1 stuck at ~376ms exposure while camera 2 properly used 10.8s for same lux (~7)
- Logs showed: `[Safety] EV clamp applied: proposed EV differs by 5251.9%. Adjusted exposure 20.0000s → 0.3737s`
- Root cause: EV safety clamp was applying on EVERY frame, not just the first frame after seeding

**The Bug:**
- The clamp was supposed to only apply on the "first manual frame" to prevent brightness flash
- But the code only checked if we HAD seed values, not if we'd ALREADY applied the clamp
- Result: clamp kept forcing exposure down to match stale seed EV indefinitely

**The Fix:**
- Added `_ev_clamp_applied` flag to track if clamp has been applied this transition cycle
- Clamp only applies when: seeded AND NOT already applied
- After applying, sets `_ev_clamp_applied = True`
- Flag resets to False when returning to day mode

**Code Changes:**
```python
# New flag in __init__
self._ev_clamp_applied: bool = False

# In _apply_ev_safety_clamp()
if self._ev_clamp_applied:
    return target_exposure, target_gain  # Skip if already applied

# After applying clamp
self._ev_clamp_applied = True

# In day mode reset
self._ev_clamp_applied = False
```

**Test Coverage:**
- `test_ev_clamp_applies_only_once`: Verifies clamp applies first time, skips second time
- `test_ev_clamp_flag_resets_on_day_mode`: Verifies flag resets properly

### 2026-01-10 - Fast Ramp-Up for Underexposure (Dark Band Fix)

**Problem Identified:**
- Slitscan showed prominent dark band during day-to-night transition
- Exposure graphs showed exposure lagging behind rapidly dropping light
- Root cause: exposure ramp-up too slow (0.10 per frame) to track fast light changes

**Fast Ramp-Up for Underexposure (NEW!):**
- Added `_get_rampup_speed()` method - symmetric to existing `_get_rampdown_speed()`
- Warning level (brightness < 90): uses `fast_rampup_speed` (0.50 = 5x faster)
- Critical level (brightness < 70): uses `critical_rampup_speed` (0.70 = 7x faster)
- New config options: `fast_rampup_speed`, `critical_rampup_speed`

**Fixed Underexposure Detection:**
- Now triggers in ANY mode, not just at minimum exposure
- Previous version only detected underexposure when `at_min_exposure` was true
- This missed the critical case: day-to-night transition where exposure is ramping UP
- Lowered thresholds: warning=90 (was 100), critical=70 (was 80), safe=105 (was 110)

**Updated Camera Settings Logic:**
- All 3 modes (night, day, transition) now check both underexposure AND overexposure
- Underexposure takes priority (ramp-up used if both flags somehow set)
- Applies fast ramp-up speed to exposure interpolation

**Lowered Brightness Correction Floor:**
- Changed from 0.5 to 0.3 in all 4 locations
- Allows faster recovery during bright→dark transitions

**Test Coverage:**
- Added 13 new tests for underexposure detection and ramp-up speed
- TestUnderexposureDetection: 6 tests
- TestRampUpSpeed: 4 tests
- TestExposureSpeedSelection: 3 tests

### 2026-01-08 - Multi-Layer Overexposure Prevention

**Two-Tier Overexposure Detection:**
- Changed from single threshold to warning + critical levels
- Warning: brightness > 150 or > 5% clipped (uses `fast_rampdown_speed`)
- Critical: brightness > 170 or > 10% clipped (uses `critical_rampdown_speed`)
- Clear: brightness < 130 and < 3% clipped (lowered from 150/5%)
- New `_overexposure_severity` state tracking

**Proactive Exposure Correction:**
- New `_apply_proactive_exposure_correction()` analyzes test shot BEFORE actual capture
- If test shot brightness > 180: applies 30% exposure reduction
- If test shot brightness > 140: applies 15% exposure reduction
- If lux doubled since last frame: proportional reduction
- Prevents overexposure before it happens, rather than reacting after

**Rapid Lux Change Detection:**
- New `_detect_rapid_lux_change()` method
- Detects when lux changes by more than 3x between frames
- Configurable via `lux_change_threshold` (default: 3.0)
- Logs `[RapidLux]` warning when detected

**Severity-Aware Ramp-Down:**
- New `_get_rampdown_speed()` returns speed based on severity
- Warning: uses `fast_rampdown_speed` (0.50)
- Critical: uses `critical_rampdown_speed` (0.70)
- New config option `critical_rampdown_speed` (default: 0.70)

**Config Fix:**
- Re-enabled EV Safety Clamp on Kringelen camera
- Was accidentally set to `false`, causing severe bright bands

### 2026-01-03 - EV Safety Clamp Config & Street Lamp Fix

**EV Safety Clamp Configuration:**
- Added `ev_safety_clamp_enabled` config option (default: true)
- Allows disabling the Holy Grail EV clamp for scenes with bright point light sources
- **Critical bug fix:** Street lamps and security lights in frame were causing severely underexposed night images (330ms instead of 20s)
- When disabled, exposure is calculated purely from lux values without seed constraints

**Root Cause Analysis:**
- The EV Safety Clamp seeds from the last auto-exposure values when transitioning to manual mode
- Bright point light sources (street lamps) fool auto-exposure into using short exposures
- The clamp then forces all subsequent night exposures to match this incorrect seed
- Result: 6000%+ EV difference between proposed (20s) and clamped (330ms) exposure

**Sequential Ramping Documentation:**
- Added documentation for the sequential ramping feature
- Phase 1: Shutter priority (increase exposure while keeping gain low)
- Phase 2: Gain priority (increase ISO after shutter maxes out)
- Reduces noise by preferring longer exposures over higher gain

**Test Coverage:**
- Added comprehensive tests for `_apply_ev_safety_clamp()` function
- Added tests for `_calculate_sequential_ramping()` function
- Added edge case tests for bright point light sources
- Added street lamp scenario test demonstrating the bug and fix

### 2025-12-25 - Overexposure Detection & Brightness Tuning

**Fast Overexposure Ramp-Down:**
- Added automatic detection of overexposed frames (brightness > 180 or >10% clipped pixels)
- When detected, exposure interpolation speed increases from 0.10 to 0.30 (3x faster)
- Prevents the "light flash" problem at dawn when 20s exposure stays on too long
- Configurable via `fast_rampdown_speed` in config.yml
- Logs `[FastRamp] OVEREXPOSURE DETECTED` when triggered

**Configurable Reference Lux:**
- New config option `adaptive_timelapse.reference_lux` (default: 3.8)
- Controls overall image brightness: higher = brighter images
- Allows per-camera tuning based on scene and sensor sensitivity
- Formula: `exposure = (20 * reference_lux) / lux`

**Calculated Lux to Overlay:**
- Overlay now shows calculated lux instead of camera's unreliable metadata estimate
- Fixed "lux: 400 at night" display issue on some cameras

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
