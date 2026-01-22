# Upgrade Guide

Instructions for updating Raspilapse on other Raspberry Pis after pulling new code.

---

## 2026-01-18: Direct Brightness Control

This update replaces the ML-based exposure system with a simpler, faster physics-based approach.

### What Changed

| Aspect | Before (ML) | After (Direct) |
|--------|-------------|----------------|
| Convergence speed | 10+ frames (5+ min) | 3-5 frames (90 sec) |
| Complexity | 5 interacting systems | 1 simple ratio |
| Brightness stability | Often stuck at 85-95 | Converges to 115-120 |

### Upgrade Steps

#### 1. Pull the Latest Code

```bash
cd ~/raspilapse
git pull origin mlv2
```

#### 2. Add Config Parameters

The new config parameters are NOT automatically added (config.yml is in .gitignore).

**Option A: Manual edit**
```bash
nano config/config.yml
```

Add these lines under `adaptive_timelapse:` (after `reference_lux:`):

```yaml
  # Direct brightness feedback control (replaces ML)
  direct_brightness_control: true
  brightness_damping: 0.5
```

**Option B: One-liner**
```bash
sed -i '/reference_lux:/a\
\
  # Direct brightness feedback control\
  direct_brightness_control: true\
  brightness_damping: 0.5' config/config.yml
```

#### 3. Restart the Service

```bash
sudo systemctl restart raspilapse
```

#### 4. Verify

```bash
# Check direct control is active
journalctl -u raspilapse --since "1 min ago" | grep -E "DirectFB|Skipped"
```

You should see:
```
[ML v2] Skipped - using direct brightness control instead
[DirectFB] brightness=XX, target=120, ratio=X.XX, change=X.XXx, exp: X.XXs → X.XXs
```

#### 5. Monitor Convergence

```bash
# Wait a few minutes, then check
python scripts/db_stats.py 5m
```

Brightness should converge toward 115-120 within 5-6 frames.

---

## Configuration Reference

### New Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `direct_brightness_control` | `false` | Enable direct feedback (bypass ML) |
| `brightness_damping` | `0.5` | Correction strength (0.5=conservative, 0.8=aggressive) |

### Damping Values

| Value | Behavior | Use Case |
|-------|----------|----------|
| 0.5 | Conservative, 50% correction/frame | Stable, slower convergence |
| 0.7 | Balanced, 70% correction/frame | Good balance of speed/stability |
| 0.8 | Aggressive, 80% correction/frame | Fast convergence, may oscillate |

### Example Config Block

```yaml
adaptive_timelapse:
  enabled: true
  interval: 30
  reference_lux: 3.8

  # Direct brightness feedback (new)
  direct_brightness_control: true
  brightness_damping: 0.5

  # ... rest of config
```

---

## Rollback

If direct control causes issues, disable it:

```yaml
# In config/config.yml
direct_brightness_control: false
```

Then restart:
```bash
sudo systemctl restart raspilapse
```

The legacy ML system will be used instead.

---

## Troubleshooting

### Direct control not active

Check the log for:
```bash
journalctl -u raspilapse | grep "direct_brightness_control"
```

If you see ML v2 initializing instead of "Skipped", the config parameter wasn't added correctly.

### Brightness oscillating

Try reducing damping:
```yaml
brightness_damping: 0.4  # More conservative
```

### Brightness converging too slowly

Try increasing damping:
```yaml
brightness_damping: 0.7  # More aggressive
```

---

## Files Changed in This Update

| File | Change |
|------|--------|
| `src/auto_timelapse.py` | Added `_calculate_exposure_from_brightness()` method |
| `docs/CLAUDE.md` | Added "Direct Brightness Control" documentation |
| `ML.md` | Added deprecation notice |
| `docs/NEXT_SESSION_CONTEXT.md` | Updated with new system info |
| `UPGRADE.md` | This file |
