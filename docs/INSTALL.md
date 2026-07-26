# Installation

From a fresh Raspberry Pi OS image to a camera capturing every 30 seconds.

If you just want the commands, they are in the [README](../README.md). This page
explains what each step is for and what to do when one fails.

## Before you start

- Raspberry Pi with a CSI camera port (Zero 2 W and up; a Pi 4 handles 4K)
- Camera Module V2, V3 or HQ
- Raspberry Pi OS Bookworm recommended; Bullseye works and ships Python 3.9
- Storage: roughly 6 GB per day at 4K on a 30 second interval, before cleanup

Connect the camera with the Pi powered off: lift the clip on the CSI port, seat
the ribbon with the contacts facing the HDMI connector, press the clip down.
Enable it in `sudo raspi-config` → Interface Options → Camera, and reboot.

Confirm the hardware works before touching this project at all:

```bash
rpicam-still -o /tmp/test.jpg
```

A file appearing means the camera and driver are fine. If not, the problem is
below Raspilapse — reseat the cable and re-check raspi-config.

## 1. System packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-picamera2 python3-yaml python3-pil python3-numpy \
                    python3-requests python3-requests-toolbelt python3-matplotlib ffmpeg
```

| Package | Needed for |
|---------|-----------|
| `python3-picamera2` | the camera itself — **apt only**, pip builds fail |
| `python3-yaml` | configuration |
| `python3-pil` | the overlay |
| `python3-numpy` | brightness analysis in the capture loop |
| `python3-requests`, `python3-requests-toolbelt` | uploading the daily video |
| `python3-matplotlib` | the graph scripts |
| `ffmpeg` | video assembly |

`requests-toolbelt` streams the upload rather than holding a ~300 MB video in
memory. Without it uploads still work, more expensively, and you get one warning
saying so.

## 2. astral

Sun elevation drives mode selection at high latitudes. The packaged version is
1.6, whose API this code does not use:

```bash
pip3 install --break-system-packages 'astral>=3.2'
```

`--break-system-packages` is Bookworm's way of allowing pip alongside apt. It is
safe here: astral has no apt package at a usable version, so nothing is being
overwritten.

> **On virtualenvs:** the systemd units run `/usr/bin/python3` directly, and
> picamera2 cannot be pip-installed, so a plain venv will not work. If you want
> one, create it with `--system-site-packages`.

## 3. Clone and configure

```bash
git clone https://github.com/ekstremedia/raspilapse.git
cd raspilapse
cp config/config.example.yml config/config.yml
nano config/config.yml
```

`config/config.yml` is gitignored — it holds your API keys. The example file is
the documented schema; every setting is explained inline.

At minimum, set:

```yaml
location:
  latitude: 68.7          # yours, not this
  longitude: 15.4
  timezone: "Europe/Oslo"

output:
  directory: "/var/www/html/images"   # or anywhere writable
  project_name: "my_camera"           # appears in every filename
```

Location matters more than it looks: sun elevation decides when day begins and
ends, and a wrong location gives wrong boundaries. It is what makes polar summer
and winter work.

If you are writing under a webserver root, create the directory first:

```bash
sudo mkdir -p /var/www/html/images
sudo chown -R $USER:www-data /var/www/html/images
sudo chmod -R 775 /var/www/html/images
```

## 4. Check before installing

```bash
./scripts/install.sh --check
```

This verifies every dependency against `/usr/bin/python3` specifically — the
interpreter the services use, which is not necessarily the one on your `PATH` —
parses your config, and warns about settings that will cause trouble later. It
installs nothing.

Fix anything it reports before continuing.

## 5. One test frame

```bash
python3 src/auto_timelapse.py --test
```

Captures a single frame through the full adaptive path and exits. Check the
image lands in your `output.directory` and looks reasonable — right exposure,
overlay where you expect it.

## 6. Install the services

```bash
./scripts/install.sh
sudo systemctl start raspilapse
```

The installer renders `systemd/*.in` with your username, group, project path and
interpreter, installs them, enables the timers, and caps the systemd journal at
200 MB. It prints the resulting schedule when it finishes.

You get four units:

| Unit | Does | When |
|------|------|------|
| `raspilapse.service` | continuous capture | always |
| `raspilapse-daily-video.timer` | yesterday's video, keogram, slitscan | 05:00 |
| `raspilapse-cleanup.timer` | expired images and database rows | 02:00 |
| `raspilapse-upload-retry.timer` | retry failed uploads | every 30 min |

Subsets and extras:

```bash
./scripts/install.sh --only capture,cleanup
./scripts/install.sh --with-watchdog     # restarts on stalled capture; runs as root
./scripts/install.sh --dry-run           # print the units, install nothing
./scripts/install.sh --uninstall
```

The watchdog is opt-in because it runs as root and can reboot the machine. It
exists for the case `Restart=always` cannot see: the process alive but the
camera no longer producing frames.

## 7. Confirm

```bash
python3 src/status.py
systemctl list-timers 'raspilapse-*'
tail -f logs/auto_timelapse.log
ls -lt /var/www/html/images/$(date +%Y/%m/%d)/ | head
```

A new file should appear every `adaptive_timelapse.interval` seconds.

Application logs are in `logs/<script>.log`. Under systemd they deliberately do
not also go to the journal — storing every line twice is how a journal reaches
several gigabytes — so `journalctl -u raspilapse` shows systemd and libcamera
output only.

## Optional

**Serving images over the web.** If `output.directory` is under a webserver
root, `output.symlink_latest` keeps a `status.jpg` pointing at the newest frame.

**Uploading daily videos.** Fill in `video_upload.url` and `api_key`. Until you
do, the retry service notices and exits cleanly rather than queueing forever.

**Weather overlay.** `weather.endpoint` expects JSON with a `modules` array; see
[WEATHER.md](WEATHER.md).

**Ships, tide and aurora overlays.** These read JSON files produced by a separate
service. Leave them disabled unless you have it.

## Upgrading

```bash
cd raspilapse
git pull
./scripts/install.sh --check     # catches new dependencies
./scripts/install.sh             # re-render the units if they changed
sudo systemctl restart raspilapse
```

Database migrations run automatically on start. `config/config.yml` is never
touched by a pull; check `config/config.example.yml` for new settings.

## When it does not work

[TROUBLESHOOTING.md](TROUBLESHOOTING.md).
