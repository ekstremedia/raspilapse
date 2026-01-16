# ML-Based Adaptive Exposure System

## Overview

The ML exposure system is a lightweight machine learning component that continuously learns and improves timelapse exposure settings. It runs automatically as part of the `auto_timelapse.py` script - no manual intervention required after initial setup.

## How It Works

### Automatic Operation

When enabled, the system:

1. **Learns from every frame** - After each capture, the system analyzes the metadata and learns what worked
2. **Predicts optimal exposure** - Uses learned patterns to suggest exposure settings
3. **Blends with existing formula** - Predictions are blended with the proven formula based on a trust level
4. **Adapts to your location** - Learns the specific light patterns at your camera location (68.7°N)

### The Learning Components

```
┌─────────────────────────────────────────────────────────────┐
│                    ML Exposure Predictor                     │
├─────────────────────────────────────────────────────────────┤
│  1. Solar Pattern Memory    - Learns expected lux by time   │
│  2. Lux-Exposure Mapper     - Learns optimal exposure/lux   │
│  3. Trend Predictor         - Anticipates light changes     │
│  4. Correction Memory       - Remembers what worked         │
└─────────────────────────────────────────────────────────────┘
```

#### 1. Solar Pattern Memory
Learns the expected light level (lux) for each time of day, indexed by:
- Day of year (handles seasonal changes - polar winter to midnight sun)
- Hour and 15-minute bucket

This lets the system know "at 10:15 AM on January 10th, we typically see ~50 lux".

#### 2. Lux-to-Exposure Mapper
Learns which exposure settings produce good brightness (110-130 range) for each lux level:
- Only learns from "good" frames (brightness in target range)
- Bucketized by lux level for efficiency
- More samples = higher confidence

#### 3. Trend Predictor
Uses recent lux history to predict where light is heading:
- Detects rapidly changing light (dawn/dusk transitions)
- Proactively adjusts exposure BEFORE under/overexposure occurs
- Helps eliminate "lag" during transitions

#### 4. Correction Memory
Remembers what brightness corrections worked:
- "At lux ~50 with brightness ~80, increasing exposure by 15% worked"
- Helps recover from exposure errors faster

## Trust System

The ML system uses a **trust-based blending** approach:

```
final_exposure = (trust × ML_prediction) + ((1-trust) × formula_exposure)
```

- **Initial trust: 0%** - Starts with 100% formula, 0% ML
- **Trust increment: 0.1% per good prediction** - Builds trust slowly
- **Maximum trust: 80%** - Never goes 100% ML, always keeps formula as backup

This means:
- The system is safe to enable immediately
- Bad ML predictions are overridden by the formula
- Trust builds naturally as the system proves itself

## Current Status

After bootstrapping from 7 days of historical data:

| Metric | Value |
|--------|-------|
| Frames processed | 20,940 |
| Solar pattern days | 8 |
| Current trust level | 0% (building) |

The system learned solar patterns but needs new frames with brightness data to learn lux-exposure mappings.

## Configuration

Located in `config/config.yml` under `adaptive_timelapse.ml_exposure`:

```yaml
ml_exposure:
  enabled: true              # ML active and learning
  shadow_mode: false         # Actually use predictions (not just log)

  # Learning rates (higher = faster but less stable)
  solar_learning_rate: 0.1
  exposure_learning_rate: 0.05
  correction_learning_rate: 0.1

  # Trust settings
  initial_trust: 0.0         # Start with formula only
  trust_increment: 0.001     # +0.1% per good frame
  max_trust: 0.8             # Cap at 80% ML influence
```

## Files

| File | Purpose |
|------|---------|
| `src/ml_exposure.py` | Main ML predictor class |
| `src/bootstrap_ml.py` | Bootstrap from historical data |
| `ml_state/ml_state.json` | Persisted learned state |

## Commands

### View Learned State
```bash
python src/bootstrap_ml.py --show-table
```

### Re-bootstrap from Historical Data
```bash
python src/bootstrap_ml.py --days 7
```

### Check ML Statistics
The system logs ML activity. Look for `[ML]` prefixed messages:
```bash
journalctl -u auto-timelapse -f | grep ML
```

## Polar Location Adaptation

At 68.7°N latitude, the system handles:

| Season | Light Conditions | ML Adaptation |
|--------|-----------------|---------------|
| January | Polar twilight, very short "days" | Learns low-lux patterns |
| March | Days lengthening ~7 min/day | Updates patterns daily |
| May-July | 24-hour sun (midnight sun) | Learns continuous daylight |
| September | Days shortening rapidly | Readapts to transitions |

The solar pattern memory is indexed by day-of-year, so it naturally adapts as seasons change. Each day updates its own patterns while retaining historical learning.

## Troubleshooting

### ML not affecting exposure?
1. Check trust level - starts at 0%, builds slowly
2. Verify `shadow_mode: false` in config
3. Check logs for `[ML]` messages

### Want to reset ML learning?
```bash
rm ml_state/ml_state.json
# Then re-bootstrap if desired:
python src/bootstrap_ml.py --days 7
```

### Want to disable ML temporarily?
```yaml
ml_exposure:
  enabled: false  # Disables ML, uses formula only
```

Or use shadow mode to log predictions without using them:
```yaml
ml_exposure:
  enabled: true
  shadow_mode: true  # Log but don't apply
```

## How Trust Builds

The system builds trust by tracking "good" predictions:

1. Frame captured with brightness 100-140 (near target 120)
2. Confidence counter increments
3. Trust = min(max_trust, initial_trust + confidence × trust_increment)

At 0.1% per good frame:
- After 100 good frames: 10% trust
- After 500 good frames: 50% trust
- After 800 good frames: 80% trust (maximum)

With ~2880 frames per day, trust can reach maximum within a day or two of good captures.

## ML v2: ML-First with Smart Safety (v1.3.0+)

### Philosophy Change

ML v2 takes a fundamentally different approach: **trust ML predictions for smooth transitions**, with graduated safety mechanisms that only intervene when necessary.

The goal is:
- Smooth predictable exposure curves from ML > reactive corrections
- Small brightness variations (70-170) are acceptable if the curve is smooth
- No vertical banding in slitscan images

### New Features

#### 1. Bucket Interpolation

ML v2 now fills data gaps by interpolating between adjacent buckets:

```text
┌─────────────────────────────────────────────────────────────┐
│  Problem: Data gaps in certain lux ranges                    │
│  - 0.0-0.5 lux: Deep night (rare training data)             │
│  - 5-20 lux: Transition zone (fast-changing, hard to train) │
│                                                              │
│  Solution: Logarithmic interpolation between known buckets   │
│  - Uses both lower and upper adjacent buckets if available   │
│  - Reduced confidence (70%) for interpolated predictions     │
│  - Falls back to nearest bucket with 50% confidence         │
└─────────────────────────────────────────────────────────────┘
```

#### 2. Sustained Drift Correction

Instead of reactive per-frame brightness feedback (which caused oscillation), ML v2 uses sustained drift detection:

```python
class SustainedDriftCorrector:
    """Only correct after 3+ consecutive frames of consistent error."""

    # Triggers when:
    # - 3+ frames all too dark (error < -20)
    # - OR 3+ frames all too bright (error > +20)

    # Correction:
    # - Max 30% adjustment per update
    # - Capped at 0.5x to 2.0x total
    # - Gradually decays back to 1.0 when pattern breaks
```

#### 3. Graduated Trust Reduction

ML trust is dynamically reduced when brightness deviates from target:

| Brightness | Trust Multiplier |
|------------|------------------|
| < 50       | 0.0 (force formula) |
| 50-70      | 0% → 100% ramp |
| 70-170     | 100% (full trust) |
| 170-200    | 100% → 0% ramp |
| > 200      | 0.0 (force formula) |

#### 4. Rapid Light Change Detection

During sunrise/sunset when lux changes rapidly:

```text
┌─────────────────────────────────────────────────────────────┐
│  Lux change rate (log-space per minute):                     │
│                                                              │
│  < 0.3 log-lux/min → Full ML trust                          │
│  0.3 - 1.0 log-lux/min → Reduced trust (up to 50%)          │
│  > 1.0 log-lux/min → Minimum trust (formula leads)          │
│                                                              │
│  This helps formula adapt faster during Arctic transitions   │
└─────────────────────────────────────────────────────────────┘
```

#### 5. Simplified Safety Clamps

Removed intermediate zones (WARNING_HIGH, WARNING_LOW, etc.). Now only extreme cases trigger hard corrections:

| Brightness | Action |
|------------|--------|
| > 220      | Force 30% exposure reduction |
| < 35       | Force 80% exposure increase |

### Configuration (ML v2)

```yaml
ml_exposure:
  enabled: true
  initial_trust_v2: 0.70      # Higher baseline trust
  max_trust: 0.90             # Allow higher trust when confident
  good_brightness_min: 105    # Tighter training range
  good_brightness_max: 135    # Tighter training range

transition_mode:
  brightness_feedback_strength: 0.05  # Very gentle
  brightness_tolerance: 60            # Wider tolerance
  exposure_transition_speed: 0.08     # Slower for smoothness
  fast_rampdown_speed: 0.20           # Much gentler
  fast_rampup_speed: 0.20             # Much gentler
```

### Expected Outcomes

1. **Smooth transitions**: ML predicts based on learned daily patterns
2. **No oscillation**: Sustained drift correction prevents frame-to-frame fighting
3. **Graceful degradation**: Trust reduces when brightness deviates or light changes rapidly
4. **Safety rails only for severe cases**: Not constant intervention
5. **Small variations acceptable**: 70-170 range is fine if curve is smooth

#### 6. Proactive P95 Highlight Protection

Implements proactive highlight protection based on the Raspberry Pi Camera Algorithm Guide's histogram constraint concept: "top 2% of pixels must be at or below a threshold."

Instead of waiting for pixels to clip (>245) and then correcting, the system monitors p95 (95th percentile brightness) and reduces exposure BEFORE highlights blow out.

```text
┌─────────────────────────────────────────────────────────────┐
│  P95 Highlight Protection Thresholds                        │
│                                                              │
│  p95 < 200     → No adjustment (highlights have headroom)   │
│  p95 200-220   → Gentle reduction (0.95-1.0x exposure)      │
│  p95 220-240   → Moderate reduction (0.85-0.95x exposure)   │
│  p95 > 240     → Aggressive reduction (0.70-0.85x exposure) │
│                                                              │
│  Philosophy: Prevent clipping BEFORE it happens             │
└─────────────────────────────────────────────────────────────┘
```

This is especially useful for:
- Sunrise when sky can blow out before overall brightness rises
- Aurora/stars with bright peaks against dark sky
- High-contrast scenes with bright reflections

### Verification

1. Run timelapse for full day/night cycle
2. Generate graphs: `python scripts/db_graphs.py 1d`
3. Check brightness graph - curve should be smooth without oscillation
4. Check that brightness stays mostly in 70-170 range
5. Generate slitscan - should show no vertical banding
6. Monitor logs for sustained drift corrections (should be rare)
