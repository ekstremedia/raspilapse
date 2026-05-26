# Update log

Dated entries describing changes that landed in this repo. After `git pull` on another Pi, run the post-pull steps for each entry that is newer than your last sync. Newest first.

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
