# Transition Tuning Log

This document tracks adjustments made to day/night transition parameters and their effects.

---

## 2025-12-24 - Adjustment #2: Brightness Feedback System

### Goal
Achieve butter-smooth transitions with NO light flashes at all.

### Problem
Even with improved thresholds and interpolation, there were still visible brightness jumps:
- Morning: Images getting too bright before exposure ramped down
- Evening: Exposure ramps up faster than light decreases

### Solution: Brightness Feedback Loop

Added a real-time brightness correction system that:
1. Analyzes actual image brightness after each capture
2. Compares to target brightness (120)
3. Gradually adjusts a correction factor
4. Applies correction to next frame's target exposure

**Key design for smoothness:**
- TWO layers of smoothing:
  1. Correction factor changes at 0.2 per frame max
  2. Target exposure interpolates at 0.1 per frame
- Net effect: ~0.02 actual change per frame = imperceptible

### New Configuration Options

```yaml
transition_mode:
  # Enable brightness feedback
  brightness_feedback_enabled: true

  # Target brightness (0-255)
  target_brightness: 120

  # Tolerance before correction kicks in
  brightness_tolerance: 40

  # Correction speed (lower = smoother)
  brightness_feedback_strength: 0.2
```

### Files Modified

- `src/auto_timelapse.py`:
  - Added `_apply_brightness_feedback()` method
  - Added `_brightness_correction_factor` state variable
  - Modified `_calculate_target_exposure_from_lux()` to apply correction
  - Added brightness feedback call after each capture
  - Added correction factor to diagnostic metadata
- `config/config.yml`: Added brightness feedback settings
- `config/config.example.yml`: Added brightness feedback settings
- `docs/TRANSITION_SMOOTHING.md`: Documented brightness feedback algorithm

### Expected Results

- Morning: If brightness climbs, correction factor decreases, reducing exposure gradually
- Evening: If brightness drops, correction factor increases, boosting exposure gradually
- All changes happen smoothly over multiple frames
- No visible light flashes

---

## 2025-12-24 - Analysis Session #1

### Observed Issues

**Morning Transition (night → day):**
| Time | Lux | Mode | Exposure | Brightness | Issue |
|------|-----|------|----------|------------|-------|
| 08:30 | 2-5 | night | 20000ms | 162→220 | OVEREXPOSED - brightness climbing while still in night mode |
| 09:20 | 10 | transition | 13976ms | 235 | Finally starts transitioning, but already very bright |
| 09:30 | 15-30 | transition | 4000→1200ms | 170→100 | Good ramp down |
| 10:00 | 217 | day | 100ms | 46 | Jumped to day - suddenly TOO DARK |
| 10:30 | 550 | day | 35ms | 39 | Day mode way too dark (target: ~120) |

**Evening Transition (day → night):**
| Time | Lux | Mode | Exposure | Brightness | Issue |
|------|-----|------|----------|------------|-------|
| 13:30 | 542 | day | 35ms | 32 | Day images very dark |
| 14:00 | 130 | day | 137ms | 38 | Still too dark |
| 14:30 | 88→60 | transition | 179→273ms | 37→42 | Reasonable transition |
| 14:42 | 10 | transition→night | 1607→2583ms | 63→80 | Night mode kicks in |
| 14:50 | 7-8 | night | 5691→9243ms | 126→167 | LIGHT FLASH - brightness spikes |
| 14:55 | 6-7 | night | 14000→18000ms | 200→220 | Still too bright, slowly normalizing |

### Root Cause Analysis

1. **Night threshold too high (10 lux)**
   - At lux 5-9, still in night mode with 20s exposure
   - Image already overexposed before transition begins
   - Should transition earlier when ambient light increases

2. **Day mode exposure formula too low**
   - Formula: `exposure = 20 / lux`
   - At lux 600: exposure = 33ms → brightness = 39
   - Target brightness should be ~120 (mid-tone)
   - Need ~3x more exposure in day mode

3. **Transition interpolation too slow for fast light changes**
   - Morning: light increases faster than exposure decreases
   - Evening: exposure increases faster than light decreases
   - Causes temporary over/under exposure

4. **Night mode ramp-up too aggressive**
   - When entering night mode, exposure jumps from transition values
   - Logarithmic interpolation helps, but speed still too fast

### Configuration Before Changes

```yaml
light_thresholds:
  night: 10    # Too high - images overexposed at lux 5-9
  day: 100

transition_mode:
  exposure_transition_speed: 0.15  # May be too fast
  gain_transition_speed: 0.15
  lux_smoothing_factor: 0.3
```

Exposure formula: `target_exposure = 20 / lux`
- At lux 100: 200ms
- At lux 600: 33ms → brightness ~39 (too dark)

---

## 2025-12-24 - Adjustment #1

### Changes Made

1. **Lower night threshold: 10 → 5 lux**
   - Start transitioning earlier before overexposure
   - Night mode only for truly dark conditions (lux < 5)

2. **Increase day exposure multiplier: 20 → 50**
   - New formula: `target_exposure = 50 / lux`
   - At lux 100: 500ms (was 200ms)
   - At lux 600: 83ms (was 33ms) → should give brightness ~100

3. **Slower transition speeds: 0.15 → 0.10**
   - Smoother exposure/gain changes
   - Less aggressive response to lux changes

4. **Lower day threshold: 100 → 80 lux**
   - Enter day mode slightly earlier
   - Prevents staying in transition too long

### Expected Results

- Morning: Start ramping down exposure at lux 5 instead of 10
- Day: Images ~3x brighter (brightness ~100 instead of ~40)
- Evening: Slower ramp-up should prevent light flash
- Overall: Smoother transitions with less dramatic jumps

### Files Modified

- `config/config.yml` - Threshold and speed changes
- `src/auto_timelapse.py` - Exposure formula multiplier

---

## Tuning Reference

### Target Brightness Values

| Condition | Target Brightness | Acceptable Range |
|-----------|------------------|------------------|
| Night (stars visible) | 80-120 | 60-150 |
| Transition | 100-140 | 80-160 |
| Day (overcast) | 100-130 | 80-150 |
| Day (bright) | 110-140 | 90-160 |

### Exposure Formula Reference

Current formula: `target_exposure = MULTIPLIER / lux`

| Multiplier | Lux 100 | Lux 300 | Lux 600 | Notes |
|------------|---------|---------|---------|-------|
| 20 | 200ms | 67ms | 33ms | Original - too dark |
| 50 | 500ms | 167ms | 83ms | Adjustment #1 |
| 80 | 800ms | 267ms | 133ms | If still too dark |

### Transition Speed Reference

| Speed | Description | Use Case |
|-------|-------------|----------|
| 0.05 | Very slow | Extremely smooth, may lag behind |
| 0.10 | Slow | Good for gradual transitions |
| 0.15 | Medium | Default, balance of smooth/responsive |
| 0.20 | Fast | Quick response, may show jumps |
| 0.30 | Very fast | Near-instant, visible jumps |

---

## Quick Commands

**Analyze recent transitions:**
```bash
python3 -c "
import json, glob
for f in sorted(glob.glob('/var/www/html/images/2025/12/24/*_metadata.json'))[-50:]:
    d = json.load(open(f))
    diag = d.get('diagnostics', {})
    bright = diag.get('brightness', {})
    exp = d.get('ExposureTime', 0) / 1000
    print(f\"{f.split('/')[-1][20:28]}: lux={diag.get('smoothed_lux', 0):7.2f}, mode={diag.get('mode', '?'):10}, exp={exp:8.1f}ms, bright={bright.get('mean_brightness', 0):6.1f}\")
"
```

**Generate fresh graphs:**
```bash
python3 src/analyze_timelapse.py --hours 12
```

**Create transition timelapse:**
```bash
# Morning transition
ls /var/www/html/images/2025/12/25/kringelen_nord_2025_12_25_0[6-9]*.jpg 2>/dev/null | sort > /tmp/morning.txt
# Evening transition
ls /var/www/html/images/2025/12/25/kringelen_nord_2025_12_25_1[3-6]*.jpg 2>/dev/null | sort > /tmp/evening.txt
```
