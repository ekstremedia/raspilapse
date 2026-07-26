# Raspilapse — Howto / project guide

A step-by-step tour of how this project actually runs on the Pi, from a single captured frame all the way to a daily video on the website.

## 1. What this project does

Raspilapse runs a Raspberry Pi camera continuously, takes a still photo every ~10–30 s, stamps each photo with a metadata overlay (timestamp, weather, exposure), then once a day at 05:00 stitches the last 24 hours of photos into a single MP4 timelapse + keogram + slitscan and uploads them to `https://ekstremedia.no/api/piVideo/new-store` for display on the website.

## 2. Runtime layout

- **Hardware:** Raspberry Pi 4, picamera2, 3.5 GB RAM, Wi-Fi only (eth0 is down).
- **Python:** system `/usr/bin/python3` (3.9.2). All systemd units invoke this interpreter directly — *not* the `pip3` in `~/.local/bin`, which targets a different Python.
- **Disk layout:**
  - Code: `/home/pi/raspilapse/`
  - Captured frames + per-frame metadata JSON: `/var/www/html/images/YYYY/MM/DD/`
  - Daily videos + keograms + slitscans: `/var/www/html/videos/YYYY/MM/`
  - SQLite DB: `/home/pi/raspilapse/data/timelapse.db`
  - Logs: `/home/pi/raspilapse/logs/`
  - Config: `/home/pi/raspilapse/config/config.yml`

## 3. The four moving parts (systemd)

| Unit | When | What it does |
|------|------|--------------|
| `raspilapse.service` | always on (`Restart=always`) | `src/auto_timelapse.py` — continuous capture loop |
| `raspilapse-daily-video.timer` → service | daily 05:00 (±5 min) | `src/daily_timelapse.py` — build + upload yesterday's video |
| `raspilapse-cleanup.timer` → service | daily 02:00 | `scripts/cleanup_old_images.sh` — delete frames >7 days old |
| `raspilapse-upload-retry.timer` → service | every 30 min, starting 5 min after boot | `src/retry_uploads.py` — drain the upload queue |

Plus one crontab line: `0 13 */2 * * sudo reboot` — the Pi reboots every other day at 13:00.

## 4. Step-by-step flow

### 4a. Continuous capture (`raspilapse.service`)

`src/auto_timelapse.py` runs an infinite loop. Each iteration:

1. **Decide mode and exposure.** `src/auto_timelapse.py` picks day/transition/night from smoothed lux and sun elevation, then sets exposure by direct brightness feedback: `new = current * (target / measured) ** damping`.
2. **Shoot.** `src/capture_image.py` opens picamera2, captures the frame, and saves it to `/var/www/html/images/YYYY/MM/DD/kringelen_YYYY-MM-DD_HH-MM-SS.jpg`. A sibling `_metadata.json` is written with exposure, AWB gains, lux, sun elevation, weather snapshot.
3. **Stamp the overlay.** `src/overlay.py` + `src/apply_overlay.py` draw the timestamp / weather / camera badge directly onto the JPEG.
4. **Record in DB.** `src/database.py` inserts a row into the `captures` table (timestamp, mode, lux, brightness, weather, system metrics).
5. **Sleep** until the next capture interval. Repeat.

The lores stream is sampled for fast brightness feedback (no disk re-read) — `capture.last_brightness_metrics` is what the loop reads when `adaptive_timelapse.transition_mode.brightness_feedback_enabled` is `true` (the default).

### 4b. Daily video (05:00)

`raspilapse-daily-video.timer` fires `src/daily_timelapse.py`, which:

1. Computes `target_date = yesterday`.
2. Shells out to `src/make_timelapse.py` with `--start 05:00 --end 05:00 --slitscan`. That script:
   - Finds all JPEGs between yesterday 05:00 and today 05:00 in `/var/www/html/images`.
   - Hands the file list to ffmpeg → MP4 at `/var/www/html/videos/YYYY/MM/kringelen_<from>_to_<to>.mp4` (typically 250–325 MB).
   - Generates the slitscan as `slitscan_kringelen_<from>_to_<to>.jpg`.
3. Calls `src/create_keogram.py` (or it's invoked inside make_timelapse) to produce the keogram `keogram_kringelen_<from>_to_<to>.jpg`.
4. Hands the three file paths to `src/upload_service.py`:
   - Builds a streaming multipart body with `requests_toolbelt.MultipartEncoder` (so a ~300 MB upload doesn't buffer in RAM).
   - POSTs to `https://ekstremedia.no/api/piVideo/new-store` with `Authorization: Bearer <api_key>` from `config/config.yml`.
   - Inserts a row into `upload_queue` (DB) with `status='success'` on HTTP 200, or `status='pending'` with an exponential-backoff `next_retry_at` on failure.

### 4c. Upload retry (every 30 min)

`raspilapse-upload-retry.timer` fires `src/retry_uploads.py`, which calls `UploadService.process_retry_queue()`:

1. Resets any `status='uploading'` rows older than 30 min back to `'pending'` (defends against the Pi rebooting mid-upload at 13:00).
2. Selects all `'pending'` / `'failed'` rows whose `next_retry_at` is due.
3. For each: marks `'uploading'`, attempts the same streaming POST, then marks `'success'` or schedules the next retry with doubled backoff (up to `max_retries=5`).

### 4d. Cleanup (02:00)

`scripts/cleanup_old_images.sh` deletes JPEGs and `_metadata.json` files under `/var/www/html/images/` with mtime older than 7 days, then prunes empty directories. The daily video is already built from those frames by then, and uploads have had ~21 hours to drain.

## 5. Where to look when something breaks

| Symptom | First place to look |
|---------|---------------------|
| No new photos appearing | `journalctl -u raspilapse.service -n 200` and `logs/auto_timelapse.log` |
| Daily video didn't generate | `journalctl -u raspilapse-daily-video.service -n 200` and `logs/daily_timelapse.log` |
| Upload failed / not on website | `logs/upload_service.log`; query `upload_queue` (see commands below) |
| Disk filling up | `df -h /var/www`; check `logs/` rotation and image cleanup logs |
| Camera looks wrong (exposure / colors) | `logs/capture_image.log` + the per-frame `_metadata.json` near the bad photo |

### Useful one-liners

```bash
# Status of all raspilapse units & timers
systemctl status raspilapse.service raspilapse-daily-video.service raspilapse-upload-retry.service
systemctl list-timers --all | grep raspilapse

# Inspect the upload queue
/usr/bin/python3 -c "
import sqlite3
c = sqlite3.connect('/home/pi/raspilapse/data/timelapse.db')
for r in c.execute('SELECT id, video_date, status, retry_count, substr(IFNULL(last_error,\"\"),1,60) FROM upload_queue ORDER BY id DESC LIMIT 10'):
    print(r)"

# Re-queue a failed upload for a specific date
/usr/bin/python3 -c "
import sqlite3
c = sqlite3.connect('/home/pi/raspilapse/data/timelapse.db')
c.execute(\"UPDATE upload_queue SET status='pending', retry_count=0, next_retry_at=datetime('now'), last_error=NULL WHERE video_date=?\", ('2026-05-25',))
c.commit()"

# Manually drive the retry queue once
/usr/bin/python3 /home/pi/raspilapse/src/retry_uploads.py

# Force the daily video to rebuild & re-upload yesterday
sudo systemctl start raspilapse-daily-video.service
journalctl -u raspilapse-daily-video.service -f
```

## 6. Config knobs that matter

`config/config.yml`:

- `video_upload.url` / `video_upload.api_key` — where uploads go and how they authenticate.
- `video_upload.enabled` — set `false` to suppress uploads entirely.
- `video.directory` / `output.image_dir` — where MP4s and JPEGs live.
- `adaptive_timelapse.transition_mode.brightness_feedback_enabled` — set `false` only if you know what you're doing (turns off the lores-stream brightness feedback).
- `output.project_name` — used as the file-name prefix (`kringelen_…`).

## 7. Known fragilities

- **Wi-Fi only.** `eth0` is down. If the AP misbehaves, captures still work but uploads queue up.
- **Reboot at 13:00 every other day.** Anything running at exactly that moment gets killed. The upload service now reconciles stale `'uploading'` rows on the next retry tick, so no manual cleanup is needed.
- **One config file across Pis.** When something is fixed in the repo, sync the other Pis via `git pull` and follow `Update.md` for any post-pull steps.
