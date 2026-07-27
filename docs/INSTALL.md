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
sudo apt install -y python3-picamera2 python3-yaml ffmpeg
```

| Package | Needed for |
|---------|-----------|
| `python3-picamera2` | the camera itself — **apt only**, pip builds fail |
| `python3-yaml` | configuration |
| `ffmpeg` | video assembly |

That is everything required. `python3-picamera2` depends on `python3-numpy` and
`python3-pil`, so the brightness metering and the overlay are already covered —
this command used to name them separately, which made the install look bigger
than it is.

## 2. Optional extras

Each buys exactly one feature. Skip any of them and that feature reports itself
as unavailable; nothing else changes.

| Install | Buys |
|---------|------|
| `pip3 install --break-system-packages 'astral>=3.2'` | sun elevation recorded with each frame, and the polar-day override |
| `sudo apt install -y python3-requests python3-requests-toolbelt` | uploading the daily video |
| `sudo apt install -y python3-matplotlib` | `scripts/db_graphs.py` and `scripts/graph_solar_patterns.py` |

`./scripts/install.sh --check` lists which of these you have and what each
missing one would give you.

astral is the only one that needs pip: apt ships 1.6, whose API predates the
`LocationInfo` this code uses. `--break-system-packages` is Bookworm's way of
allowing pip alongside apt, and is safe here because nothing is being
overwritten.

`requests-toolbelt` streams the upload rather than holding a ~300 MB video in
memory. Without it uploads still work, more expensively, and you get one warning
saying so.

> **On virtualenvs:** don't, unless you have a reason. The systemd units run
> `/usr/bin/python3` directly, picamera2 cannot be pip-installed, and a venv
> with its own numpy will shadow the one picamera2 was compiled against — which
> fails at import with `numpy.dtype size changed`. If you want one anyway,
> create it with `--system-site-packages` and do not install numpy into it.

## 3. Clone and configure

```bash
git clone https://github.com/ekstremedia/raspilapse.git
cd raspilapse
cp config/config.example.yml config/config.yml
nano config/config.yml
```

`config/config.yml` is gitignored — it holds your API keys. The example is
short on purpose: anything it does not mention has a default, so your config
only needs the settings you actually want to change.
[CONFIG-REFERENCE.yml](CONFIG-REFERENCE.yml) is the full annotated schema.

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
python3 -m raspilapse.cli.capture --test
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
python3 -m raspilapse.cli.status
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
touched by a pull; check `docs/CONFIG-REFERENCE.yml` for new settings.

## When it does not work

[TROUBLESHOOTING.md](TROUBLESHOOTING.md).
