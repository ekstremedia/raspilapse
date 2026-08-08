# Setting Up a New Camera Pi

Notes for deploying a new camera, written while setting one up alongside the working
Spjutvika Pi. Reference config on the working Pi: `/home/pi/raspilapse/config/config.yml`.

## What actually makes up a camera

`raspilapse` is not the whole stack. A fully-featured camera is up to four separate repos:

| Repo | systemd unit | Required? | What it does |
|---|---|---|---|
| `raspilapse` | `raspilapse.service` + 3 timers | **yes** | Captures stills, builds + uploads daily video |
| `python-reverb` | `reverb-client.service` | if you want live remote control | Inbound WebSocket channel — on-demand current image, health pings, vitals |
| `pi-overlay-data` | `pi-overlay-data.service` | only for ships/tide/aurora overlay | Writes local JSON the overlay reads |
| `dashboard-raspilapse` | `raspilapse-dashboard.service` | only for the local web UI | Flask/gunicorn on `127.0.0.1:5000` |

### Is python-reverb part of raspilapse?

**No, not at the code level.** Zero cross-references — raspilapse has no websocket client, no
listener, no inbound control path at all. It's strictly outbound: POST video to the server,
GET weather. Neither repo imports or execs the other.

**But they're coupled at runtime**, via the shared image directory and a shared identity:

| | raspilapse | python-reverb |
|---|---|---|
| image dir | *writes* `/var/www/html/images/YYYY/MM/DD` | *reads* `IMAGE_BASE_PATH` (same path) |
| camera id | `video_upload.camera_id` | `DEVICE_ID` — must match |
| server | `video_upload.url` | `API_BASE_URL` / `REVERB_HOST` |
| token | `video_upload.api_key` | `API_TOKEN` — same value on the current Pi |

python-reverb subscribes to channel `device.{DEVICE_ID}` and handles three events
(`device_listener.py:125-127`):

- `health.ping` → POST `/api/device/pong`
- `vitals.request` → load/mem/temp/uptime/disk → POST `/api/device/vitals`
- `capture.request` → runs `scripts/capture.sh` → POST `/api/device/capture/complete`

`scripts/capture.sh` does **not** trigger the camera. It globs the newest JPG out of
raspilapse's output dir for today and curls it to `/api/camera/current-image`. So if
raspilapse isn't running (or it's past midnight with no captures yet), capture requests
fail with "No images found".

---

## Part 1 — raspilapse

### 1. OS + camera

```bash
sudo raspi-config          # Interface Options → Camera → Enable → reboot
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-picamera2 python3-yaml python3-pil python3-numpy
sudo apt install -y ffmpeg python3-matplotlib python3-openpyxl
pip3 install astral requests
rpicam-still -o test.jpg   # verify hardware before going further
```

**picamera2 must come from apt, never pip** — a pip install shadows the apt one and breaks.

### 2. Clone and configure

```bash
cd /home/pi
git clone https://github.com/ekstremedia/raspilapse.git
cd raspilapse
cp config/config.example.yml config/config.yml
```

Per-camera values to change in `config/config.yml`:

| Key | Notes |
|---|---|
| `location.latitude` / `longitude` / `timezone` | drives polar day/night detection |
| `output.project_name` | goes into the image filenames |
| `output.directory` | `/var/www/html/images` — keep it, python-reverb expects it |
| `overlay.content.camera_name` | display name burnt into the frame |
| `video_upload.enabled` | example ships `false` → set `true` |
| `video_upload.url` | `https://nesthus.no/api/piVideo/new-store` |
| `video_upload.api_key` | **new key from the server** |
| `video_upload.camera_id` | **unique** — do not reuse `spjutvika_01` |
| `weather.enabled` + `weather.endpoint` | its own Netatmo station UUID, or leave disabled |

### 3. Gaps in config.example.yml — add these by hand

The example is behind the live config. Two keys matter:

```yaml
adaptive_timelapse:
  direct_brightness_control: true
  brightness_damping: 0.5
```

Direct Brightness Control replaced the ML exposure path in Jan 2026 and is the recommended
system, but it never made it into the example. Copy verbatim from
`config/config.yml:71-72` on the working Pi. Without it you silently fall back to the
deprecated ML path.

Also: the example's overlay data paths point at `/www/pi-overlay-data/data/...`; the working
Pi uses `/home/pi/pi-overlay-data/data/...`. Fix these or disable the sections:

```yaml
barentswatch.ships_file: /home/pi/pi-overlay-data/data/ships_current.json
tide.tide_file:          /home/pi/pi-overlay-data/data/tide.json
aurora.aurora_file:      /home/pi/pi-overlay-data/data/aurora.json
```

### 4. Test, then install services

```bash
python3 src/auto_timelapse.py --test    # single capture, then exits
./scripts/install.sh                    # main capture service (enable only, no start)
sudo systemctl start raspilapse
```

`install.sh` installs **only** `raspilapse.service`. The three timers are separate — and one
has no installer at all:

```bash
./scripts/install_daily_video.sh        # daily video timer, 05:00
./scripts/install_cleanup.sh            # image cleanup timer, 02:00 (keeps 7 days)

# upload-retry has NO install script — do it by hand, easy to forget:
sudo cp systemd/raspilapse-upload-retry.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now raspilapse-upload-retry.timer
```

Verify:

```bash
systemctl status raspilapse.service
systemctl list-timers | grep raspilapse   # expect 3 timers
journalctl -u raspilapse.service -f
```

Expect a few overexposed frames on the very first run — the startup-flash fix seeds exposure
from the last DB capture, and a brand-new Pi has an empty database.

---

## Part 2 — python-reverb

Only needed if this camera should serve live current-image requests from the server.
There is **no install script** here; it's manual.

```bash
sudo apt install -y python3 python3-venv python3-pip git
cd /home/pi
git clone https://github.com/ekstremedia/python-reverb.git
cd python-reverb
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install . aiohttp        # aiohttp is not a core dep, must be named explicitly
cp .env.example .env
chmod 600 .env
```

Fill in `.env`:

```bash
DEVICE_ID=<new_camera_id>          # MUST match video_upload.camera_id in raspilapse
REVERB_APP_KEY=<from Laravel .env> # server-wide, same as existing Pi
REVERB_APP_SECRET=<from Laravel .env>
REVERB_HOST=nesthus.no
REVERB_PORT=443
REVERB_SCHEME=wss
API_BASE_URL=https://nesthus.no
API_TOKEN=base64:<token>           # same token as raspilapse video_upload.api_key
CAPTURE_SCRIPT=/home/pi/python-reverb/scripts/capture.sh
IMAGE_BASE_PATH=/var/www/html/images
REVERB_LOG_LEVEL=INFO              # existing Pi is on DEBUG; use INFO here
```

Test in the foreground first — you want to see the channel subscription land:

```bash
source venv/bin/activate
python device_listener.py
# expect: "listening on channel device.<new_camera_id>"
```

Then install the service (the repo ships the unit file, unlike raspilapse):

```bash
sudo cp reverb-client.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now reverb-client
sudo journalctl -u reverb-client -f
```

`./restart.sh` is a convenience wrapper for restart + status.

### Server side

The new `DEVICE_ID` has to exist on nesthus.no before any of this responds — the device
record, its API token, and something publishing to `device.{DEVICE_ID}`.

---

## Things to watch

- **Token is duplicated** across `raspilapse/config/config.yml` and `python-reverb/.env`.
  Rotating it means editing both files and restarting both services.
- **The Reverb channel is public**, not private — `device.{DEVICE_ID}`, no auth. Anyone able
  to publish to that channel on the server can trigger captures and vitals.
  `python-reverb/docs/RASPBERRY_PI_SETUP.md:315-329` suggests moving to
  `private-device.{ID}`; worth doing while adding a second camera.
- **`config.yml` is gitignored** in raspilapse — it won't come down with a `git pull`, and it
  won't be backed up by git either.
- **`docs/SETUP_COMPLETE.md` is stale** — it lists video at 00:04 and cleanup at 01:00; the
  shipped timers are 05:00 and 02:00. It also links three files that don't exist.
- **Rollout to an existing Pi** is just
  `cd /home/pi/raspilapse && git pull && sudo systemctl restart raspilapse`. See `UPGRADE.md`.

## End-to-end verification

```bash
# stills landing
ls -t /var/www/html/images/$(date +%Y/%m/%d)/ | head

# video build + upload path
python3 src/daily_timelapse.py --dry-run                 # show what it would do
python3 src/daily_timelapse.py --date $(date -d yesterday +%Y-%m-%d)
python3 src/retry_uploads.py --status                    # queue empty = upload succeeded

# remote control path
sudo journalctl -u reverb-client -f    # then trigger a capture.request from the server
```
