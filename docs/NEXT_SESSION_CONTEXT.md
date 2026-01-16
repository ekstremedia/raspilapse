# Next Session Context - ML v2 Review

## Quick Summary to Tell Claude
> "We implemented ML v2 with P95 highlight protection for the timelapse. Here's how last night went:"

## Data to Share

### 1. Generate Graphs
```bash
python scripts/db_graphs.py 24h
```
Share the brightness/exposure graphs from `graphs/` folder.

### 2. Check Logs
Look for these log prefixes:
```bash
journalctl -u auto-timelapse --since "yesterday" | grep -E "\[P95-Protect\]|\[Drift\]|\[ML-First\]|\[Safety\]"
```

### 3. Slitscan (if available)
Shows vertical banding = flickering/oscillation problem.

## Key Questions to Answer
- Did brightness stay mostly in 70-170 range?
- Were there any blown highlights (p95 hitting 255)?
- Did transitions (sunrise/sunset) look smooth or oscillate?
- Any unexpected behavior?

## Example Message
> "ML v2 ran overnight. Brightness graph shows [smooth/oscillating] transitions. P95 peaked at [X] during sunrise. Saw [N] drift corrections in logs. [Attach graphs]. What should we tweak?"

## Current State

| Item | Value |
|------|-------|
| Branch | `mlv2` |
| Config brightness range | 105-135 |
| Initial trust | 0.70 |
| Max trust | 0.90 |
| Key files | `src/auto_timelapse.py`, `src/ml_exposure_v2.py` |

## Key Features Implemented
1. **ML-first exposure** - Trust ML predictions, blend with formula
2. **Bucket interpolation** - Fill data gaps in lux ranges
3. **Sustained drift correction** - Only correct after 3+ frames of consistent error
4. **Graduated trust reduction** - Reduce trust when brightness deviates
5. **Rapid light change detection** - Reduce trust during sunrise/sunset
6. **P95 highlight protection** - Proactively reduce exposure before highlights clip

## P95 Thresholds
| P95 Value | Action |
|-----------|--------|
| < 200 | No adjustment |
| 200-220 | Gentle reduction (0.95-1.0x) |
| 220-240 | Moderate reduction (0.85-0.95x) |
| > 240 | Aggressive reduction (0.70-0.85x) |
