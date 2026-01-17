# Next Session Context - Day Mode Brightness Fix

## Quick Summary
> "We fixed day mode brightness oscillation on 2026-01-17. Config was too loose, ML was trained on bad data."

## What Was Fixed (2026-01-17)

### Problem
Day mode brightness oscillated wildly (77-163) instead of staying in target range (105-135).
- Only 21% of day captures were in the "good" range
- ML was learning from its own bad predictions (self-reinforcing)

### Solution Applied

#### 1. Tightened Config Parameters
```yaml
# config/config.yml changes:
brightness_tolerance: 25          # Was 60
brightness_feedback_strength: 0.15  # Was 0.05
exposure_transition_speed: 0.15   # Was 0.08
fast_rampdown_speed: 0.35         # Was 0.20
fast_rampup_speed: 0.40           # Was 0.20
```

#### 2. Reset ML State
```bash
rm ml_state/ml_state_v2.json
sudo systemctl restart raspilapse
```
ML retrained from 2,311 good samples (brightness 105-135 only).

## Verification Commands

```bash
# Check brightness stability
python scripts/db_stats.py 1h

# Check ML state
cat ml_state/ml_state_v2.json | python3 -m json.tool | head -30

# Watch for feedback corrections
journalctl -u raspilapse -f | grep -i "feedback\|correction\|brightness"
```

## Current State

| Item | Value |
|------|-------|
| Branch | `mlv2` |
| Config brightness range | 105-135 (target 120) |
| Brightness tolerance | 25 (triggers at 95-145) |
| ML buckets | 18 |
| ML good frames | 2,311 |
| Last ML retrain | 2026-01-17T15:49:11 |

## Key Files Modified

- `config/config.yml` - 5 parameter changes
- `ml_state/ml_state_v2.json` - Reset and retrained

## If Problem Recurs

See `docs/CLAUDE.md` section "Troubleshooting: Day Mode Brightness Oscillation" for full procedure:
1. Check data quality with SQL query
2. Tighten config if needed
3. Reset ML state if <50% good data
4. Restart service and monitor

## Key Insight

The ML system already filters for good brightness when training. The problem was:
- Too many bad captures in history
- Wide tolerance meant feedback never triggered
- ML state file persisted bad learned patterns

Deleting ML state file forces retrain from only good samples in database.
Database itself is never deleted - all historical data preserved.
