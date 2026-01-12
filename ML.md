# ML Exposure System Documentation

This document describes the Machine Learning-based adaptive exposure system for Raspilapse timelapse photography.

## Overview

The system uses multiple approaches to maintain consistent image brightness during day/night transitions:

1. **Formula-based exposure** - Mathematical relationship between lux and exposure time
2. **Brightness feedback** - Gradual correction based on actual image brightness
3. **Emergency corrections** - Immediate correction when severely off-target
4. **ML predictions** - Learned exposure patterns from historical data

## System Architecture

```text
                    ┌─────────────────────┐
                    │   Test Shot (100ms) │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  Calculate Lux from │
                    │  Brightness + Metadata
                    └──────────┬──────────┘
                               │
                               ▼
              ┌────────────────┴────────────────┐
              │         Determine Mode          │
              │  (night/transition/day)         │
              │  + Hybrid Brightness Override   │
              └────────────────┬────────────────┘
                               │
                               ▼
              ┌────────────────────────────────┐
              │      Calculate Target Exposure │
              │  1. Lux-based formula          │
              │  2. × Brightness correction    │
              │  3. × ML prediction blend      │
              │  4. × Emergency factor         │
              └────────────────┬───────────────┘
                               │
                               ▼
              ┌────────────────────────────────┐
              │       Capture Main Frame       │
              └────────────────┬───────────────┘
                               │
                               ▼
              ┌────────────────────────────────┐
              │    Analyze Brightness          │
              │    Update Feedback             │
              │    Store in Database           │
              │    Learn (ML v1)               │
              └────────────────────────────────┘
```

## Components

### 1. Emergency Brightness Zones (`BrightnessZones` class)

Immediate exposure correction when brightness is severely off-target:

| Zone | Brightness | Action |
|------|------------|--------|
| Emergency High | > 180 | 30% exposure reduction |
| Warning High | > 160 | 15% exposure reduction |
| Target | ~120 | No change |
| Warning Low | < 80 | 20% exposure increase |
| Emergency Low | < 60 | 40% exposure increase |

**Location**: `src/auto_timelapse.py` - `BrightnessZones` class and `_get_emergency_brightness_factor()` method

### 2. Hybrid Mode Detection

Mode is determined by both lux AND brightness:

- **Standard lux thresholds**: night < 3 lux, day > 80 lux
- **Brightness override**: If brightness contradicts lux-based mode, force transition mode

Example: At dawn, lux may still be 1.0 (night) but brightness is already 180 (overexposed). The hybrid system forces transition mode to start reducing exposure.

**Location**: `src/auto_timelapse.py` - `determine_mode()` method

### 3. Urgency-Scaled Feedback

Brightness correction speed scales with error magnitude:

| Error | Urgency | Speed Multiplier |
|-------|---------|------------------|
| > 60 | URGENT | 3x |
| > 40 | Elevated | 2x |
| > 25 | Mild | 1.5x |
| ≤ 25 | Normal | 1x |

**Location**: `src/auto_timelapse.py` - `_apply_brightness_feedback()` method

### 4. ML v1 (Frame-by-Frame Learning) - DEPRECATED

> **⚠️ DEPRECATED**: ML v1 has been replaced by ML v2 in `auto_timelapse.py`.
> v1 is kept for reference but is no longer used.

Original ML system that learned incrementally from each captured frame.

**Problem**: Learned from ALL frames, including bad ones. If transitions were problematic, it would learn and perpetuate those problematic patterns.

**Files** (deprecated):
- `src/ml_exposure.py` - Original ML class (not used)
- `src/bootstrap_ml.py` - Original bootstrap script (not used)
- `ml_state/ml_state.json` - Old state file (can be deleted)

### 5. ML v2 (Database-Driven Learning) - ACTIVE

**✅ INTEGRATED**: ML v2 is now the active ML system used by `auto_timelapse.py`.

Enhanced ML system that trains ONLY on good frames from the database.

**Key Improvements over v1**:
- Trains only on frames with brightness 100-140 (proven good)
- **Never learns from bad frames** - avoids perpetuating transition problems
- **Arctic-aware**: Uses sun elevation for time periods (not clock hours)
- **Aurora support**: Includes high-contrast night frames in training
- Retrains automatically when state is stale (>24h)
- Higher initial trust (0.5 vs 0.0)
- **Requires database** - won't enable if database is disabled

**Files**:
- `src/ml_exposure_v2.py` - ML v2 class (used by auto_timelapse.py)
- `src/bootstrap_ml_v2.py` - Bootstrap from database
- `ml_state/ml_state_v2.json` - Persisted state

**Usage**:
```bash
# Bootstrap ML v2 from database
python src/bootstrap_ml_v2.py

# Analyze database without writing state
python src/bootstrap_ml_v2.py --analyze

# Custom brightness range
python src/bootstrap_ml_v2.py --brightness-min 95 --brightness-max 145
```

#### Arctic-Aware Time Periods

Instead of using clock hours (which fail at high latitudes), ML v2 uses **sun elevation** to determine time periods:

| Period | Sun Elevation | Description |
|--------|---------------|-------------|
| Night | < -12° | Astronomical night (deep darkness) |
| Twilight | -12° to 0° | Civil + nautical twilight |
| Day | > 0° | Sun above horizon |

This works correctly year-round at any latitude, including:
- **Polar night** (68°N in December): Sun stays below -12° = always "night"
- **Midnight sun** (68°N in June): Sun stays above 0° = always "day"
- **Normal days**: Proper transitions based on actual sun position

The system falls back to clock-based periods if `sun_elevation` is not available in the database.

#### Aurora Frame Support

Training data includes two types of "good" frames:

1. **Standard frames**: brightness 100-140 (target exposure)
2. **Aurora/night frames**: Low mean brightness (30-90) BUT high highlights (p95 > 150) at low lux (< 5)

This prevents rejecting valid aurora/star photography where the sky is dark but contains bright highlights.

## Database Schema

The SQLite database stores comprehensive capture data:

```sql
CREATE TABLE captures (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    unix_timestamp REAL,
    camera_id TEXT,
    image_path TEXT,

    -- Camera metadata
    exposure_time_us INTEGER,
    analogue_gain REAL,
    colour_gains_r REAL,
    colour_gains_b REAL,
    colour_temperature INTEGER,

    -- Light analysis
    lux REAL,
    mode TEXT,  -- night/transition/day
    sun_elevation REAL,

    -- Brightness metrics
    brightness_mean REAL,
    brightness_median REAL,
    brightness_std REAL,
    brightness_p5 REAL,
    brightness_p25 REAL,
    brightness_p75 REAL,
    brightness_p95 REAL,
    underexposed_pct REAL,
    overexposed_pct REAL,

    -- Weather data
    weather_temperature REAL,
    weather_humidity INTEGER,
    ...
);
```

## Database Migrations

The database schema auto-migrates when the timelapse starts. No manual steps required.

**Current Schema Version**: 2

| Version | Changes |
|---------|---------|
| 1 | Initial schema |
| 2 | Added `sun_elevation` column for Arctic-aware ML |

**How it works**:
1. On startup, `CaptureDatabase` checks the current schema version
2. If migrations are pending, they run automatically
3. Existing data is preserved
4. Schema version is updated

**When pulling new code to other cameras**:
```
[DB] Applying migration v2: Add sun_elevation column for Arctic-aware ML
[DB] Migration v2 complete
[DB] Initialized: data/timelapse.db (schema v2)
```

**Adding future migrations** (in `src/database.py`):
```python
MIGRATIONS = {
    2: ("Add sun_elevation column", ["ALTER TABLE captures ADD COLUMN sun_elevation REAL"]),
    3: ("Future migration", ["ALTER TABLE captures ADD COLUMN new_field TEXT"]),
}
```

## Configuration

In `config/config.yml`:

```yaml
adaptive_timelapse:
  transition_mode:
    target_brightness: 120
    brightness_tolerance: 40
    brightness_feedback_strength: 0.3
    fast_rampdown_speed: 0.30
    critical_rampdown_speed: 0.70
    fast_rampup_speed: 0.50
    critical_rampup_speed: 0.70

ml:
  enabled: true
  state_file: "ml_state.json"
  state_file_v2: "ml_state_v2.json"
  solar_learning_rate: 0.1
  exposure_learning_rate: 0.05
  initial_trust: 0.0
  initial_trust_v2: 0.5
  trust_increment: 0.001
  max_trust: 0.8
  good_brightness_min: 100
  good_brightness_max: 140
  min_samples: 10
```

## Troubleshooting

### Problem: Overexposure during morning transition

**Symptoms**: Brightness climbs to 180-230 before exposure starts reducing

**Solutions**:
1. Emergency zones will now apply immediate 15-30% reduction
2. Hybrid mode detection will force transition mode earlier
3. Urgency scaling will speed up feedback when error > 40

### Problem: Underexposure during day

**Symptoms**: Brightness stays at 70-90 during bright daylight

**Solutions**:
1. Hybrid mode detection forces transition if brightness < 80 in day mode
2. Emergency zones apply 20-40% exposure increase
3. Urgency scaling speeds up feedback

### Problem: Wild oscillations during transitions

**Symptoms**: Brightness swings between 170 and 230 repeatedly

**Root cause**: Feedback too slow, overcorrection when catching up

**Solutions**:
1. Emergency factor limits overcorrection
2. ML v2 provides stable baseline from proven good exposures
3. Urgency scaling provides proportional response

## Development Notes

### Running Tests

```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_ml_exposure_v2.py -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html
```

### Adding New Features

1. Add emergency zone thresholds in `BrightnessZones` class
2. Modify `_get_emergency_brightness_factor()` for new correction logic
3. Update `determine_mode()` for new hybrid conditions
4. Add tests in `tests/` directory

### Monitoring

View real-time logs:
```bash
tail -f logs/timelapse.log | grep -E "\[Emergency\]|\[Hybrid\]|\[Feedback\]|\[ML"
```

Generate graphs from database:
```bash
python src/generate_database_graph.py --period 1d
```

## Version History

- **v1.0**: Original formula-based exposure with slow feedback
- **v1.1**: Added ML v1 frame-by-frame learning
- **v2.0**: Added emergency zones, hybrid mode detection, urgency scaling, ML v2
- **v2.1**: Arctic-aware ML v2 with solar elevation-based time periods, aurora frame support, database migrations
