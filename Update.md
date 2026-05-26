# Update log

Dated entries describing changes that landed in this repo. After `git pull` on another Pi, run the post-pull steps for each entry that is newer than your last sync. Newest first.

---

## 2026-05-26 — Logging cleanup

### TL;DR

```bash
cd ~/raspilapse
git pull
# Merge the new rotation cap into your local config.yml (gitignored):
#   under logging:
#     max_size_mb: 5
#     backup_count: 2
sudo systemctl restart raspilapse.service

# One-off cleanup of the bloated logs/ directory:
rm logs/script_name.log logs/test_script.log logs/daily_timelapse_cron.log 2>/dev/null
rm logs/*.log.[1-9] 2>/dev/null
: > logs/auto_timelapse.log
: > logs/capture_image.log
: > logs/weather.log
: > logs/overlay.log
```

### What changed

The `logs/` directory had grown to ~230 MB across 34 files, almost all of it repetitive INFO chatter from the capture loop (camera init/close, "Overlay initialized", "Weather data fetcher initialized" — fired twice per ~30 s cycle). Real signal was buried.

| File | Change |
|------|--------|
| `src/overlay.py:801` | `Overlay initialized` → DEBUG |
| `src/weather.py:42` | `Weather data fetcher initialized` → DEBUG |
| `src/capture_image.py` | Camera lifecycle (init/start/resolution/started/init complete/closing) and per-frame overlay application → DEBUG. Only `Image captured successfully: <path>` stays at INFO. |
| `src/auto_timelapse.py` | `Initializing camera for timelapse...` → DEBUG. `Taking test shot to measure light levels...` → DEBUG. `[Proactive] Very bright test shot ...` (fired every daylight cycle) → DEBUG. `[Overcast] Dynamic target: X→Y` now only INFO when `|delta| ≥ 5`, else DEBUG. |
| `config/config.yml` and `config/config.example.yml` | `max_size_mb: 10 → 5`, `backup_count: 5 → 2`. Per-logger ceiling drops from 60 MB to 15 MB. |

### Before / after

| Metric | Before | After |
|--------|--------|-------|
| `logs/` total size | **230 MB** | **1.5 MB** (after one-off cleanup; ~25 MB steady state) |
| Files in `logs/` | 34 | 11 |
| `auto_timelapse.log` per-cycle INFO lines | 8–9 | 1 (`Frame captured: …`) + mode/exposure summary |
| `capture_image.log` per-cycle INFO lines | 14–16 (test + actual) | 1 (`Image captured successfully: …`) |
| `weather.log` per-cycle INFO lines | 4 | 0 (only errors/warnings) |
| `overlay.log` per-cycle INFO lines | 2 | 0 (only locale warning at boot) |
| Per-day log lines (capture path) | ~50,000 | ~6,000 |
| Rotation cap per logger | 60 MB (5 × 10 MB) | 15 MB (2 × 5 MB) |
| Orphan log files | 3 (1.2 MB + 2×0 B) | 0 |

To get the verbose chatter back temporarily for debugging, flip `logging.level` to `"DEBUG"` in `config/config.yml` and restart — the lines are still there, just suppressed at INFO.

### Verify

```bash
# After restart, wait ~5 min, then:
for f in auto_timelapse capture_image weather overlay; do
  echo "$f.log: $(grep -c INFO logs/$f.log) INFO lines"
done

# Expect: auto_timelapse ~10/5min, capture_image ~10/5min, weather=0, overlay=0
ls -lh logs/
```

---

## 2026-05-26 — Streaming uploads, reboot-safety, DB index

### TL;DR

```bash
cd ~/raspilapse
git pull
/usr/bin/python3 -m pip install --user requests-toolbelt
sudo systemctl restart raspilapse-upload-retry.service raspilapse.service
```

The SQLite migration to schema v4 (new composite index on `upload_queue`) runs automatically on the next service start. No manual SQL required.

### What changed

Daily upload to `ekstremedia.no/api/piVideo/new-store` had been failing since 2026-05-23 with `('Connection aborted.', timeout('The write operation timed out'))`. `requests.post(files=…)` was buffering the entire ~300 MB multipart body before sending; the SSL socket stalled and tripped a write timeout long before the body actually moved. Direct `curl` POSTs to the same endpoint worked fine (~5 MB/s, 65 s for a 311 MB video), confirming the server was healthy.

| File | Change |
|------|--------|
| `src/upload_service.py` | POST body now streamed via `requests_toolbelt.MultipartEncoder`. Timeout bumped to `(30, 3600)`. New `_reset_stale_uploading_rows()` called at the top of `process_retry_queue()` to recover rows stranded in `status='uploading'` by the 13:00 reboot. `last_attempt_at` stamped when marking a row uploading. Silent `except: pass` on the mark-uploading path now logs a warning. |
| `src/auto_timelapse.py` | `brightness_metrics = None` initialized before the `if brightness_feedback_enabled:` branch so the later `store_capture(brightness_metrics=…)` never NameErrors when the feature is disabled. Per-frame metadata JSON now read once per capture and reused (was opened+parsed twice). |
| `src/database.py` | `SCHEMA_VERSION = 4`. Migration v4 adds `idx_upload_queue_status_retry (status, next_retry_at)` for the retry-queue scan. |

### Post-pull steps

#### 1. Pull

```bash
cd ~/raspilapse
git pull
```

#### 2. Install the new Python dep against the *system* Python

The systemd units invoke `/usr/bin/python3` (3.9). The default `pip3` on this Pi targets a different Python — installing with it will silently miss the runtime.

```bash
/usr/bin/python3 -m pip install --user requests-toolbelt
/usr/bin/python3 -c "import requests_toolbelt; print('ok', requests_toolbelt.__version__)"
```

#### 3. Restart the affected services

```bash
sudo systemctl restart raspilapse-upload-retry.service raspilapse.service
```

The schema-v4 migration runs the first time either service starts:

```
[DB] Applying migration v4: Add composite index for retry-queue scans
[DB] Migration v4 complete
```

#### 4. Verify

```bash
# Confirm schema is at v4 and the helper exists
/usr/bin/python3 -c "
import sys, sqlite3
sys.path.insert(0,'/home/pi/raspilapse')
from src.upload_service import UploadService
import yaml
cfg = yaml.safe_load(open('/home/pi/raspilapse/config/config.yml'))
us = UploadService(cfg)
print('reset stale rows:', us._reset_stale_uploading_rows(30))
print('schema:', sqlite3.connect('/home/pi/raspilapse/data/timelapse.db').execute('SELECT MAX(version) FROM schema_version').fetchone()[0])
"
# Expect: "reset stale rows: 0" and "schema: 4" on a healthy install.
```

If the upload queue still has `'failed'` or `'uploading'` rows left over from the regression window, requeue them:

```bash
/usr/bin/python3 -c "
import sqlite3
c = sqlite3.connect('/home/pi/raspilapse/data/timelapse.db')
c.execute(\"UPDATE upload_queue SET status='pending', retry_count=0, next_retry_at=datetime('now'), last_error=NULL WHERE status IN ('failed','uploading')\")
c.commit()
print('re-queued', c.total_changes)
"
sudo systemctl start raspilapse-upload-retry.service
journalctl -u raspilapse-upload-retry.service -n 30 --no-pager
```

---

## 2026-01-18 — Direct Brightness Control

Replaces the ML-based exposure system with a direct physics-based feedback loop. Faster convergence (~90 s vs 5 min), simpler code, more stable brightness (~115–120 vs stuck at 85–95).

### Post-pull steps

#### 1. Pull

```bash
cd ~/raspilapse
git pull
```

#### 2. Add config parameters (config.yml is gitignored, so this is manual)

Under `adaptive_timelapse:` (after `reference_lux:`), add:

```yaml
  direct_brightness_control: true
  brightness_damping: 0.5    # 0.5 conservative · 0.7 balanced · 0.8 aggressive
```

Or as a one-shot edit:

```bash
sed -i '/reference_lux:/a\
\
  direct_brightness_control: true\
  brightness_damping: 0.5' config/config.yml
```

#### 3. Restart and verify

```bash
sudo systemctl restart raspilapse
journalctl -u raspilapse --since "1 min ago" | grep -E "DirectFB|Skipped"
```

Expect:

```
[ML v2] Skipped - using direct brightness control instead
[DirectFB] brightness=XX, target=120, ratio=X.XX, change=X.XXx, exp: X.XXs → X.XXs
```

Brightness should converge to 115–120 within 5–6 frames.

### Rollback

Set `direct_brightness_control: false` in `config.yml` and restart `raspilapse` — the legacy ML path is preserved.
