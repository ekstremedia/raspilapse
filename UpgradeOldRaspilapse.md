# Upgrading an old Pi to raspilapse 1.5.0

Runbook for taking a camera running a pre-1.5.0 checkout (the `src/*.py` layout) up to
1.5.0. Written while doing it on `spjutvikacam` on 2026-08-07, which went from `2b6e0af`
to `62c6422` — 64 commits, 212 files.

**This is not a routine `git pull`.** The pull deletes every file the installed systemd
units point at. Between the pull and re-running the installer, the camera cannot start.
Plan on the service being down for the length of the whole procedure, not a restart.

Companion doc: `newcam.md` covers building a camera from scratch.

---

## What 1.5.0 breaks

| Change | Consequence |
|---|---|
| `src/*.py` → `raspilapse/` package | Every `ExecStart=... /src/auto_timelapse.py` is now a dead path. **Not mentioned in the changelog** — treat it as the main breaking change. |
| Five installers → one `scripts/install.sh` | `install_cleanup.sh`, `install_daily_video.sh`, `uninstall*.sh`, `test.sh` are gone. Units are now rendered from `systemd/*.in`. |
| Config defaults moved into `raspilapse/config.py` | The example config went 681 → 69 lines. Your old config still *loads*, but a lot of it is now inert. |
| ML exposure system deleted | `adaptive_timelapse.ml_exposure.*` and `direct_brightness_control` do nothing. |
| Day/transition/night modes → one exposure ladder | `light_thresholds`, `civil_twilight_threshold`, `hysteresis_frames` and most of `transition_mode` do nothing. `mode` survives as a label only. |
| DB schema v3 → v6 | Auto-migrates on first open. |
| New `video.retention_days` | Opt-in, but the shipped example says `7`, which will delete your whole video archive bar the last week. |

**Unknown config keys are silently ignored** — no error, no warning. That is why an
un-migrated config appears to work while half of it quietly does nothing.

---

## 0. Pre-flight

```bash
cd /home/pi/raspilapse
git status                  # working tree must be clean; stash or commit anything local
git log --oneline -1        # note this commit, it is your rollback point
df -h /                     # need room for a DB copy and a day of video
systemctl list-timers 'raspilapse-*'
```

Check whether anything else on the box reaches into raspilapse. On a full camera that
means `python-reverb`, `pi-overlay-data` and `dashboard-raspilapse`:

```bash
grep -rn --include='*.py' --include='*.sh' -E "raspilapse/(src|data|config)|from src|import src|auto_timelapse|daily_timelapse|make_timelapse|retry_uploads" \
  /home/pi/python-reverb /home/pi/pi-overlay-data /home/pi/dashboard-raspilapse 2>/dev/null | grep -v /venv/
```

Grep for **both** `src/` paths *and* `from src` / `import src`. On spjutvikacam the second
form was the one that broke the dashboard, and a path-only grep missed it entirely.

`python-reverb` is safe — it only reads the image directory and never imports raspilapse.

---

## 1. Stop and back up

```bash
sudo systemctl stop raspilapse-daily-video.service   # if a nightly encode is mid-flight
sudo systemctl stop raspilapse.service

cd /home/pi/raspilapse
cp data/timelapse.db data/timelapse.db.pre-1.5.0.bak
cp config/config.yml config/config.yml.pre-1.5.0.bak
mkdir -p ~/pre-1.5.0-units
sudo cp /etc/systemd/system/raspilapse*.{service,timer} ~/pre-1.5.0-units/
sudo chown "$USER:$USER" ~/pre-1.5.0-units/*
```

If you want pre-upgrade journal history, export it **now** — the installer caps the journal
at 200 MB and restarts journald, which vacuums whatever is over. Keep the window short;
with `logging.console: true` the journal is mostly duplicated capture logs and a 14-day
export can run for many minutes:

```bash
journalctl -u 'raspilapse*' --since "-3 days" --no-pager > ~/pre-1.5.0-journal.txt
```

If you killed a nightly encode, delete the partial `.mp4` — it is not resumable.

---

## 2. Dependencies

1.5.0 needs one thing most old installs lack:

```bash
sudo apt install -y python3-requests-toolbelt
```

Without it the daily upload still works, but it buffers the whole ~300 MB video in RAM
instead of streaming it.

`astral` must be **≥ 3.2** and must come from pip — apt ships 1.6, whose API predates
`LocationInfo`:

```bash
python3 -c "import astral; print(astral.__version__)"   # if < 3.2 or absent:
pip3 install --user 'astral>=3.2'
```

**Never** `pip3 install -r requirements.txt` on a Pi. It will try to move numpy and Pillow
underneath `python3-picamera2`, which breaks it with a binary-incompatibility error. The
file says so itself; it exists for CI and non-Pi checkouts.

---

## 3. Pull

```bash
git pull
```

`config/config.yml` is gitignored, so it survives untouched.

Clean up what git leaves behind — the tracked files go, but untracked bytecode and old ML
state do not:

```bash
rm -rf src/ ml_state/          # src/ will contain only stale __pycache__/*.pyc
ls                             # expect: raspilapse/ config/ scripts/ systemd/ tests/ docs/ ...
python3 -c "import raspilapse; print(raspilapse.__version__)"   # 1.5.0
```

---

## 4. Migrate the config

The old config loads without complaint, so nothing forces this — do it anyway, or you will
spend an evening tuning a key nothing reads.

### Delete (all inert in 1.5.0)

```
location.civil_twilight_threshold
adaptive_timelapse.reference_lux
adaptive_timelapse.direct_brightness_control
adaptive_timelapse.ml_exposure.*
adaptive_timelapse.light_thresholds.*
adaptive_timelapse.night_mode.awb_enable
adaptive_timelapse.day_mode.awb_enable
adaptive_timelapse.day_mode.exposure_time
adaptive_timelapse.day_mode.analogue_gain
adaptive_timelapse.test_shot.frequency
adaptive_timelapse.transition_mode.smooth_transition
adaptive_timelapse.transition_mode.sequential_ramping
adaptive_timelapse.transition_mode.hysteresis_frames
adaptive_timelapse.transition_mode.gain_transition_speed
adaptive_timelapse.transition_mode.smooth_wb_in_day_mode
adaptive_timelapse.transition_mode.smooth_exposure_in_day_mode
adaptive_timelapse.transition_mode.brightness_tolerance
adaptive_timelapse.transition_mode.brightness_feedback_strength
adaptive_timelapse.transition_mode.target_brightness
overlay.content.line_3_left
overlay.layout.section_spacing
timelapse:          # whole section
graphs:             # whole section
```

Two of these matter more than the rest:

- **`reference_lux` was the overall-brightness knob and is now dead.** Its replacement is
  `adaptive_timelapse.brightness_target.base` (default 120). If images shift after the
  upgrade, this is why.
- **`civil_twilight_threshold` is dead.** The polar-day override is gone; exposure works
  from measured brightness only, so it behaves the same at 68°N as anywhere. The `mode`
  column becomes an honest label instead of a forced one.

`line_3_left` deserves a note: top-bar mode has exactly four slots
(`line_1_left/right`, `line_2_left/right`). Ships were never rendered from a template
anyway — they draw as floating boxes below the bar from `barentswatch.enabled`, and tide
and aurora draw their own sections. Deleting `line_3_left` costs nothing visually.

### Change

| Key | To | Why |
|---|---|---|
| `logging.console` | `auto` | `true` writes every line to `logs/` **and** the journal. This is how journals reach 4 GB. `auto` keeps console output when you run by hand and drops it under systemd. |
| `video.codec.preset` / `crf` / `threads` | **delete the overrides** | 1.5.0 defaults are `veryfast` / `20` / `3`. Measured against the old `fast`/`25`/`2`: roughly twice as fast *and* higher SSIM at 4K. A pinned old value silently defeats the speedup. |

⚠ **Budget for the file-size jump before you delete the crf override.** Measured on
spjutvikacam's own frames, same day, same 2877 images:

| | old `fast` / crf 25 / 2 threads | new `veryfast` / crf 20 / 3 threads |
|---|---|---|
| encode rate | 0.9 fps | **1.6 fps** (1.8× faster) |
| bitrate | 22 Mbit/s | **57 Mbit/s** |
| daily video | 316 MB | **821 MB** (2.6×) |

Upstream measured 25 → 44 Mbit/s and "~500 MB/day"; a busy coastal scene with moving water
and cloud compresses far worse than that. The pictures are visibly better, but it is 2.6×
the nightly upload, so check the receiving server's storage and bandwidth before committing.
`crf: 22` is a sensible middle ground if 821 MB/day is too much — set it deliberately rather
than leaving the stale old value in place.

### Add (worth setting explicitly)

```yaml
adaptive_timelapse:
  brightness_target:
    base: 120            # replaces reference_lux
    overcast_boost: 15   # on by default; raises the target on flat scenes
  highlight_protection:
    enabled: true        # code default is false, despite what the reference shows
    apply_in_night: false
  transition_mode:
    critical_rampdown_speed: 0.70   # dawn-flare recovery
    critical_rampup_speed: 0.70     # dusk recovery
weather:
  max_backoff_seconds: 900
```

### Verify the migration mechanically

Do not eyeball it. Diff what the loader actually produces, old file vs new:

```bash
python3 - <<'PY'
from raspilapse.config import merge_defaults
import yaml
old = merge_defaults(yaml.safe_load(open('config/config.yml.pre-1.5.0.bak')))
new = merge_defaults(yaml.safe_load(open('config/config.yml')))
def flat(d, p=''):
    out = {}
    for k, v in d.items():
        kp = f"{p}.{k}" if p else k
        out.update(flat(v, kp)) if isinstance(v, dict) else out.__setitem__(kp, v)
    return out
fo, fn = flat(old), flat(new)
for k in sorted(set(fo) - set(fn)): print('-', k, '=', fo[k])
for k in sorted(set(fn) - set(fo)): print('+', k, '=', fn[k])
for k in sorted(set(fo) & set(fn)):
    if fo[k] != fn[k] and 'api_key' not in k: print('~', k, ':', fo[k], '->', fn[k])
print('api_key preserved:', old['video_upload']['api_key'] == new['video_upload']['api_key'])
PY
```

Every line should be one you intended. `config.yml` holds an API key — `chmod 600` it.

---

## 5. Database

Migration v3 → v6 is automatic on first open: adds a retry-queue index, drops three unused
capture indexes, adds 8 system-metric columns, and switches the file to WAL. Do it
deliberately so you can watch it:

```bash
python3 -m raspilapse.cli.db --stats
```

On a 594k-row / 268 MB database this took **16 seconds** — almost all of it dropping
indexes. All rows are preserved; nothing is rewritten.

The file will not shrink afterwards (freed index pages become freelist). `--vacuum`
reclaims it, needs free space equal to the DB size, and takes minutes. It is deliberately
never on the timer.

**Do not copy `database.retention_days: 180` out of the reference file** without checking.
Absent means keep everything. If your history is older than the window you set, the next
02:00 cleanup deletes the excess permanently. Check first:

```bash
python3 -m raspilapse.cli.db --prune --dry-run
```

---

## 6. Verify before installing anything

```bash
./scripts/install.sh --check     # dependency + config inventory, installs nothing
./scripts/install.sh --dry-run   # prints the rendered units
python3 -m pytest tests/ -q      # ~1130 tests, about 2.5 min on a Pi 4
```

⚠ **`--check` exits 1 even when it says "Ready to install."** Its EXIT trap ends on
`[ -n "$STAGING" ]`, which is false in check mode and flips the reported status. Read the
output, not `$?`. (`--check` also only *warns* about missing `picamera2`, `astral`,
`requests`, `requests_toolbelt` and `matplotlib` — it does not fail on them.)

Then take one frame:

```bash
python3 -m raspilapse.cli.capture --test
```

⚠ If this fails with `Camera(s) not found` / `Operation not permitted` on `/dev/media*`,
check whether your **shell** can open device nodes before blaming the camera:

```bash
sudo python3 -c "import os; os.close(os.open('/dev/media1', os.O_RDWR)); print('OK')"
```

`EPERM` there even as root means something is confining your session (a sandboxed shell, a
seccomp filter — it is inherited and cannot be dropped, so `sudo` does not help). The
camera is fine; verify through systemd instead, which spawns from PID 1 with no inherited
filter. This cost an hour on spjutvikacam and nearly triggered an unnecessary reboot.

---

## 7. Install the units

Run as the service user. **The installer refuses to run as root** — it calls `sudo` itself
where needed.

```bash
./scripts/install.sh
sudo systemctl start raspilapse
```

Do **not** use `--only`. The new templates render to the same filenames as the old units,
which is precisely what overwrites the stale `src/`-based ones. Anything you exclude is
left behind, still enabled, still pointing at a deleted file, and fails at its next timer.

What you get: `raspilapse.service` plus cleanup (02:00), daily-video (05:00) and
upload-retry (every 30 min) timers, all now invoking `python3 -m raspilapse.cli.*`.

Two side effects worth knowing:

- **The journald cap is installed unconditionally**, not tied to a component:
  `SystemMaxUse=200M`, `MaxRetentionSec=1month`, followed by a journald restart. On
  spjutvikacam this took the journal from 3.9 GB to 232 MB immediately.
- **The new timers drop `Requires=`.** The old ones pulled their service in the moment the
  *timer* started, so cleanup, daily-video and upload-retry all ran on every boot as well
  as on schedule. They now fire on schedule only. Intended, but it is a visible change.

---

## 8. Video retention

New in 1.5.0 and the only step that can destroy data. `video.retention_days` defaults to
**0 = disabled** in code, even though the reference file and changelog both say 7.

A video is protected only while its `upload_queue` row is in a state *other* than
`success`. So on a camera whose uploads all succeed, **nothing in the video directory is
protected**. Always dry-run:

```bash
python3 -m raspilapse.cli.prune_videos --retention-days 7 --dry-run
```

It lists every file and the total. On spjutvikacam, 7 days meant **306 files / 29.3 GB**
out of a 109-day archive. Decide with that number in front of you, not from the example
file. Matches `*.mp4`, `keogram_*.jpg` and `slitscan_*.jpg` by mtime, and removes emptied
`YYYY/MM` directories afterwards.

Source frames are separate and unchanged: `scripts/cleanup_old_images.sh` still hardcodes
`KEEP_DAYS=7` and `IMAGE_DIR=/var/www/html/images`. Editing the script is the only way to
change either.

---

## 9. Watchdogs — check the network stack first

```bash
./scripts/install.sh --with-watchdog     # capture stall watchdog
./scripts/install.sh --with-netwatch     # network recovery — SEE BELOW
```

**Install `--with-watchdog` only after you have confirmed frames are landing.** It reboots
after roughly 15 minutes of "service active, no new frame" (two restarts, then a reboot,
with a 6-hour floor). If a config mistake has the camera writing somewhere unexpected, that
is a reboot loop rather than a diagnosis.

**`--with-netwatch` requires NetworkManager.** Check before you install it:

```bash
systemctl is-active NetworkManager
ip -4 route show default          # which interface actually carries traffic?
```

`scripts/check_network.sh` is entirely `nmcli`-driven. On a dhcpcd + wpa_supplicant Pi —
which is what Bullseye images generally are, spjutvikacam included — every recovery step is
a no-op, the "is our SSID on the air" gate fails *open* (a failed `nmcli` scan is read as
"visible"), and escalation step 4 runs `systemctl restart NetworkManager`, which would
*start* NM on a box that deliberately is not using it. The result is no recovery plus a
reboot every 6 hours during an outage. Skip it, or migrate the Pi to NetworkManager first.
It is also pointless on a Pi wired over ethernet: `network_ok()` returns success as soon as
the default route is on anything other than `wlan0`.

Both watchdogs share one reboot floor (`/var/lib/raspilapse/last_reboot`, 6 hours) so they
cannot ping-pong.

If the Pi has a blind `0 4 */2 * * sudo reboot` cron, decide what it is still for. With
both watchdogs installed it is redundant. With only the capture watchdog it remains the
only thing that recovers a wedged network or kernel.

---

## 10. Sibling repos

Anything that shells out to or imports raspilapse needs repointing. Old → new:

| Old | New |
|---|---|
| `src/auto_timelapse.py` | `python3 -m raspilapse.cli.capture` |
| `src/daily_timelapse.py` | `python3 -m raspilapse.cli.daily` |
| `src/retry_uploads.py` | `python3 -m raspilapse.cli.retry_uploads` |
| `src/make_timelapse.py` | `python3 -m raspilapse.cli.timelapse` |
| `src/status.py` | `python3 -m raspilapse.cli.status` |
| `src/capture_image.py` | `python3 -m raspilapse.cli.snapshot` |
| `src/apply_overlay.py` | `python3 -m raspilapse.cli.apply_overlay` |
| `src/database.py --stats/--prune/--vacuum` | `python3 -m raspilapse.cli.db ...` |
| `src/create_keogram.py` | `python3 -m raspilapse.video.keogram` (no CLI wrapper) |
| `from src.upload_service import UploadService` | `from raspilapse.storage.upload import UploadService` |
| `src/analyze_timelapse.py`, `src/ml_exposure*.py`, `src/bootstrap_ml*.py` | deleted, no replacement |

All flags survived: `--test`, `--dry-run`, `--date`, `--status`, `-c/--config`. No
`pip install` is needed — the units run `python3 -m` with `WorkingDirectory` set, which
puts the package on `sys.path`. Do **not** build a venv; a venv's own numpy shadows the one
apt's `picamera2` was built against.

On spjutvikacam, `dashboard-raspilapse` needed three fixes:

- `app/routes/uploads.py` — `from src.upload_service` → `from raspilapse.storage.upload`.
  **This one crashes the whole dashboard at boot**, not just one page. Constructor and all
  methods are unchanged, so it is a one-line import.
- `app/services/job_service.py` — the `make_timelapse.py` path plus the `pgrep`/`pkill`
  patterns that look for it.
- `app/templates/timelapse.html` — the displayed example command.

`charts_service.py` needed nothing: v6 only *adds* columns, and it connects read/write as
`pi`, so WAL is fine. Restart the dashboard and load every page — an import error only
shows up on boot.

Its `config_schema.py` still describes the pre-1.5.0 config and `config_editor.py` writes
`config.yml` from it, so **saving from the dashboard's config editor can strip the new
keys**. Leave that editor alone until its schema is updated.

---

## 11. Verify

```bash
systemctl status raspilapse
systemctl list-timers 'raspilapse-*'
ls -t /var/www/html/images/$(date +%Y/%m/%d)/ | head
tail -f logs/auto_timelapse.log
tail logs/exposure.log                      # new in 1.5.0
python3 -m raspilapse.cli.status
python3 -m raspilapse.cli.retry_uploads --status
```

In the log you should see the restart-seeding fix, which is what stops the first frame
after every restart losing its exposure decision:

```
[Startup] Seeded from last capture: exposure=0.0004s, gain=1.12, WB=[2.50, 1.60], mode=day, brightness=121.2
```

Then look at an actual frame. Three changes are expected and are not faults:

1. **The overlay bar is lighter.** A compounding-alpha bug was fixed, so
   `background.color: [0, 0, 30, 70]` used to render at about 124 and now renders at 70.
   Multiply the alpha by ~1.8 to restore the old look.
2. **`{lux}` changes scale entirely** — it is now measured from the delivered frame rather
   than a fixed metering shot that saturated in daylight. Expect ~20 000 in full daylight
   where the old scale read single digits. Lux graphs get a hard discontinuity at the
   upgrade point.
3. **`mode` labels change.** Without the polar-day override, frames shot at a 20-second
   exposure are no longer labelled `day`. Historical day/night ratios are not comparable
   across the upgrade.

Finally, rebuild yesterday's video to exercise the whole daily path end to end:

```bash
sudo systemctl start raspilapse-daily-video.service   # defaults to yesterday
journalctl -u raspilapse-daily-video -f
```

Note `raspilapse.cli.daily` now takes an exclusive `flock` on `data/daily.lock`; a manual
run during the 05:00 job prints "already in progress" and exits 0.

### Re-uploading a day that was already uploaded

If you re-encode a day whose video the 05:00 job already sent, the upload is **skipped** —
`daily.py` returns early when `get_upload_by_date()` finds a `success` row:

```
Upload already completed for 2026-08-06, skipping
```

`retry_uploads --force` will not help: it only processes *pending* rows. Neither does
`--only-upload`, which hits the same gate. You have to clear the row first:

```bash
# save the row so this is reversible
python3 -c "
import sqlite3, json
c = sqlite3.connect('data/timelapse.db'); c.row_factory = sqlite3.Row
print(json.dumps(dict(c.execute(\"select * from upload_queue where video_date='YYYY-MM-DD'\").fetchone()), indent=2))
" > ~/upload_row.json

python3 -c "
import sqlite3
c = sqlite3.connect('data/timelapse.db')
c.execute(\"update upload_queue set status='pending', completed_at=NULL, retry_count=0, next_retry_at=NULL where video_date='YYYY-MM-DD'\")
c.commit()"

python3 -m raspilapse.cli.daily --only-upload --date YYYY-MM-DD
```

On nesthus.no this **replaced** the existing record rather than duplicating it — the
`server_response` came back with the same `video.id` (3009) and an updated `filesize`. Read
that id out of `upload_queue.server_response` before and after to confirm the same is true
of your server; the endpoint is called `new-store`, so do not assume it. 825 MB took 2m46s
at ~5 MB/s.

Side effect worth knowing: while the row is `pending`, that video is *protected* from
`prune_videos` (retention only deletes files whose queue row is `success`).

---

## 12. Rollback

```bash
sudo systemctl stop raspilapse
cd /home/pi/raspilapse
git checkout <pre-upgrade commit>
cp config/config.yml.pre-1.5.0.bak config/config.yml
cp data/timelapse.db.pre-1.5.0.bak data/timelapse.db
sudo cp ~/pre-1.5.0-units/* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl start raspilapse
```

The v6 columns are additive so old code tolerates the migrated DB, but WAL persists —
restore the backup if you want the file exactly as it was. Deleted videos are not
recoverable, which is the whole reason for the dry-run in step 8.

---

## Summary of the traps

1. `install.sh --check` exits 1 on success. Read the output.
2. A restricted shell gets `EPERM` on `/dev/media*`; that is not a broken camera. Test
   through systemd.
3. Grep sibling repos for `from src` as well as `src/` paths.
4. `reference_lux` is dead — `brightness_target.base` replaced it.
5. `video.retention_days` and `database.retention_days` default to 0 in code but to 7 and
   180 in the reference file. Dry-run both.
6. Delete your `video.codec` preset/crf/threads overrides or you keep the slow encoder.
7. `logging.console: true` is what makes the journal enormous. Set it to `auto`.
8. `--with-netwatch` needs NetworkManager. Check `systemctl is-active NetworkManager`.
9. Never `--only` on an upgrade; it strands the old units.
10. Never pip-install requirements.txt on a Pi.
