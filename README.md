# Raspilapse

![Tests](https://github.com/ekstremedia/raspilapse/workflows/Tests/badge.svg)
[![codecov](https://codecov.io/gh/ekstremedia/raspilapse/branch/main/graph/badge.svg)](https://codecov.io/gh/ekstremedia/raspilapse)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Continuous timelapse capture for the Raspberry Pi camera. Runs 24/7, adapts
exposure from daylight through twilight to 20-second night exposures without
flicker, burns an information overlay into each frame, and assembles a video
once a day.

Built for a camera at 68°N, where "day" and "night" stop meaning what they
usually do, so the exposure logic works from measured brightness and sun
elevation rather than the clock.

## Requirements

- Raspberry Pi with a CSI camera port
- Camera Module V2, V3 or HQ
- Raspberry Pi OS Bookworm (Bullseye works; it ships Python 3.9)
- Roughly 6 GB of disk per day at 4K/30s, before cleanup

## Install

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-yaml python3-pil python3-numpy \
                    python3-requests python3-requests-toolbelt python3-matplotlib \
                    python3-pip ffmpeg

# astral 3.x is needed for sun elevation; the packaged version is too old
pip3 install --break-system-packages 'astral>=3.2'

git clone https://github.com/ekstremedia/raspilapse.git
cd raspilapse
cp config/config.example.yml config/config.yml
nano config/config.yml     # at minimum: location, output.project_name, output.directory
```

Check everything is in place before installing any services:

```bash
./scripts/install.sh --check
```

Take one frame to confirm the camera works:

```bash
python3 src/auto_timelapse.py --test
```

Then install the systemd units:

```bash
./scripts/install.sh
sudo systemctl start raspilapse
```

That gives you four units:

| Unit | What it does | When |
|------|--------------|------|
| `raspilapse.service` | Continuous capture | always |
| `raspilapse-daily-video.timer` | Yesterday's video, keogram, slitscan | 05:00 |
| `raspilapse-cleanup.timer` | Delete expired images and database rows | 02:00 |
| `raspilapse-upload-retry.timer` | Retry failed uploads | every 30 min |

`systemctl list-timers 'raspilapse-*'` is the authority on the schedule.

Other installer options:

```bash
./scripts/install.sh --only capture,cleanup   # a subset
./scripts/install.sh --with-watchdog          # restart on stalled capture (runs as root)
./scripts/install.sh --dry-run                # print the units, install nothing
./scripts/install.sh --uninstall
```

## Configuration

`config/config.example.yml` is the documented schema — every setting is
explained there, and a test fails if it drifts from what the code reads. Copy
it to `config/config.yml`, which is gitignored because it holds your API keys.

The settings most worth understanding:

| Setting | Why it matters |
|---------|----------------|
| `location.latitude` / `longitude` | Sun elevation drives mode selection. Wrong location means wrong day/night boundaries. |
| `output.directory` | Where frames land. Needs to exist and be writable. |
| `adaptive_timelapse.interval` | Seconds between frames. 30 is a good default. |
| `adaptive_timelapse.reference_lux` | Overall brightness. Raise for brighter images. |
| `adaptive_timelapse.transition_mode.target_brightness` | What the exposure loop aims for, 0-255. |
| `logging.level` | `INFO` while setting up, `WARNING` for 24/7. |

## How exposure works

Each cycle takes a fixed-settings test shot, measures its brightness, picks a
mode, and sets the camera:

```
mode      = f(smoothed lux, sun elevation)      night | transition | day
exposure  = current * (target / measured) ** damping
```

Direct proportional feedback rather than a lookup table, so it converges in
three to five frames and needs no per-camera calibration beyond
`reference_lux`. Shutter does the work first; gain only rises once the shutter
is within 20% of its ceiling.

| Mode | Exposure | Gain |
|------|----------|------|
| Day | brightness feedback | pinned at floor |
| Transition | brightness feedback | ramps once the shutter nears max |
| Night | up to 20 s, reduced if the scene is bright | up to configured max |

White balance is manual in every mode — AWB drifting between frames is the main
cause of colour flicker in a timelapse. Optional highlight protection lowers the
brightness target when the top of the histogram nears clipping, so bright skies
keep detail.

See [docs/EXPOSURE.md](docs/EXPOSURE.md) for the details.

## Usage

```bash
python3 src/auto_timelapse.py --test     # one frame, then exit
python3 src/status.py                    # service state, config summary, recent captures
python3 src/capture_image.py             # single capture, no adaptive logic

python3 src/make_timelapse.py            # video for the configured window
python3 src/make_timelapse.py --start 07:00 --end 15:00 --today
python3 src/daily_timelapse.py --date 2026-07-25   # video + keogram + upload

python3 scripts/db_stats.py 24h          # capture statistics
python3 scripts/db_graphs.py 7d          # graphs into graphs/
python3 src/database.py --stats          # database size and retention
python3 src/database.py --prune --dry-run
```

Logs are in `logs/<script>.log`. Under systemd the application log goes there
rather than to the journal, so lines are not stored twice; `journalctl -u
raspilapse` still shows systemd and libcamera output.

## Layout

```
src/            auto_timelapse.py   capture loop, scheduling, lifecycle
                exposure.py         all exposure decisions and their state
                capture_image.py    Picamera2 wrapper
                overlay.py          burned-in overlay
                database.py         SQLite storage + maintenance CLI
                make_timelapse.py   ffmpeg video assembly
                daily_timelapse.py  daily video + upload
scripts/        install.sh, cleanup, watchdog, graph and stats tools
systemd/        unit templates, rendered by install.sh
config/         config.example.yml is the schema
```

## Documentation

| Document | Contents |
|----------|----------|
| [docs/INSTALL.md](docs/INSTALL.md) | Full installation walkthrough |
| [docs/USAGE.md](docs/USAGE.md) | Day-to-day commands |
| [docs/SERVICE.md](docs/SERVICE.md) | systemd units and schedules |
| [docs/EXPOSURE.md](docs/EXPOSURE.md) | Exposure control and transitions |
| [docs/OVERLAY.md](docs/OVERLAY.md) | Overlay configuration |
| [docs/WEATHER.md](docs/WEATHER.md) | Weather data integration |
| [docs/TIMELAPSE_VIDEO.md](docs/TIMELAPSE_VIDEO.md) | Video, keogram, slitscan |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | When something is wrong |
| [CHANGELOG.md](CHANGELOG.md) | Version history |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup |

## Troubleshooting

```bash
./scripts/install.sh --check      # dependencies and config
python3 src/status.py             # is it capturing?
tail -f logs/auto_timelapse.log   # what is it doing?
rpicam-still -o /tmp/test.jpg     # is the camera alive at all?
```

Camera not detected: check the ribbon cable, then `sudo raspi-config`.
Permission denied on the camera: `sudo usermod -aG video $USER`, then log out
and back in. Import errors for picamera2: install it with apt, never pip.

More in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## License

MIT — see [LICENSE](LICENSE).

Copyright © 2024-2026 Terje Nesthus · [ekstremedia.no](https://ekstremedia.no)
