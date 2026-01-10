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
