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

All settings are in `config/config.yml` under `adaptive_timelapse.transition_mode`:

```yaml
transition_mode:
  smooth_transition: true

  # Analogue gain range
  analogue_gain_min: 1.0
  analogue_gain_max: 2.5

  # === SMOOTH TRANSITION SETTINGS ===

  # Lux smoothing factor (EMA alpha)
  # Formula: smoothed = alpha * raw + (1 - alpha) * previous
  # Lower = smoother but slower response
  # Range: 0.1 - 0.5, Default: 0.3
  lux_smoothing_factor: 0.3

  # Hysteresis: frames required before mode change
  # Higher = more stable but slower transitions
  # Range: 2 - 5, Default: 3
  hysteresis_frames: 3

  # White balance transition speed
  # How fast WB gains change per frame (0.0 - 1.0)
  # Lower = smoother color transitions
  # Range: 0.1 - 0.3, Default: 0.15
  wb_transition_speed: 0.15

  # Use smooth WB in day mode
  # true = always use interpolated manual WB
  # false = use camera AWB in full day mode (legacy)
  smooth_wb_in_day_mode: true
```

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

## Future Improvements

- [ ] Adaptive WB transition speed based on lux rate of change
- [ ] Per-channel WB smoothing (red and blue could have different speeds)
- [ ] Sunset/sunrise detection for optimized transition timing
- [ ] Store day WB reference persistently between restarts
- [ ] Histogram-based brightness normalization during transitions
- [ ] Machine learning to predict optimal WB for given lux level

---

## Changelog

### 2025-12-23 - Initial Implementation
- Added lux EMA smoothing
- Added mode hysteresis
- Added WB interpolation
- Added day WB reference learning
- Configuration options in config.yml
