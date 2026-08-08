# Setting Up a New Camera Pi

From a blank SD card to a camera that captures every 30 seconds and publishes
a daily 4K video — the exact path, validated on sigerfjordcam-2 (Pi 4, Camera
Module 3, Raspberry Pi OS Lite trixie). [docs/INSTALL.md](docs/INSTALL.md)
explains what each step is for and what to do when one fails; this is the
walk-through.

## What you end up with

- A frame every 30 s in `/var/www/html/images/YYYY/MM/DD/`, exposure handled
  automatically from full sun to 20-second night exposures — **nothing to
  enable**, the adaptive ladder is the capture path.
- Yesterday's 4K video, keogram and slitscan in `/var/www/html/videos/YYYY/MM/`
  every morning at 05:00 (05:00→05:00, so a night lands in one file).
- `/var/www/html/status.jpg` always pointing at the newest frame.
- One service and three timers, installed by one script; images pruned after
  7 days (the videos are what preserves them), journal capped at 200 MB.

## 1. OS

Flash Raspberry Pi OS **Lite** with Raspberry Pi Imager and preseed hostname,
your user, ssh and wifi in the imager's settings — the Pi never needs a
monitor. Bookworm and Trixie both work.

First login:

```bash
sudo apt update && sudo apt full-upgrade -y && sudo reboot
```

Reboot *before* installing anything, so a kernel or firmware bump never lands
mid-deployment.

There is no camera step in raspi-config any more — Bookworm and later
autodetect CSI cameras (the "Legacy camera" toggle that remains is the wrong
stack). Just verify the hardware:

```bash
rpicam-still -o /tmp/test.jpg    # trixie ships rpicam-*; the libcamera-* names are gone
```

## 2. Packages

```bash
sudo apt install -y python3-picamera2 python3-yaml ffmpeg
pip3 install --break-system-packages 'astral>=3.2'   # sun elevation; apt's astral is too old
```

**No virtualenv.** The systemd units run `/usr/bin/python3`, picamera2 is
apt-only, and a venv's own numpy shadows the one picamera2 was compiled
against. `requirements.txt` is for CI, never for a Pi.

Optional, each buys one feature: `python3-requests` + `python3-requests-toolbelt`
(video upload; the second streams it instead of holding ~300 MB in memory),
`python3-matplotlib` (graph scripts — also lets the full test suite run
rather than skip those modules).

If the overlay should show Norwegian dates, generate the locale once:

```bash
sudo sed -i 's/^# *nb_NO.UTF-8 UTF-8/nb_NO.UTF-8 UTF-8/' /etc/locale.gen && sudo locale-gen
```

(or set `overlay.datetime.localized: false` and skip this.)

## 3. Web directories

Only if serving over HTTP, and only if they do not already exist:

```bash
sudo apt install -y apache2        # or nginx; anything that serves files
sudo mkdir -p /var/www/html/images /var/www/html/videos
sudo chown -R $USER:www-data /var/www/html/images /var/www/html/videos
sudo chmod -R 775 /var/www/html/images /var/www/html/videos
```

## 4. Clone and configure

```bash
cd ~
git clone https://github.com/ekstremedia/raspilapse.git
cd raspilapse
cp config/config.example.yml config/config.yml
nano config/config.yml
```

Per-camera values:

| Key | This camera's value | Notes |
|---|---|---|
| `location.latitude` / `longitude` / `timezone` | `68.66` / `15.39` / `Europe/Oslo` | recorded with each frame; does not affect exposure |
| `output.directory` | `/var/www/html/images` | must exist and be writable |
| `output.project_name` | `sigerfjordcam2` | leads every filename — no spaces |
| `output.symlink_latest` | `enabled: true`, `path: /var/www/html/status.jpg` | the live "current view" |
| `camera.resolution` | `3840x2160` | the daily video inherits this — this line *is* the 4K |
| `adaptive_timelapse.interval` | `30` | seconds between frames |
| `video.directory` | `/var/www/html/videos` | |
| `video.organize_by_date` | `true` | code default is false → flat pile of videos |
| `overlay.enabled` + `overlay.camera_name` | `true`, `"Sigerfjord #02"` | top bar: name + localized date/time |

Renames to know if you are pattern-matching from an old camera's config:
`reference_lux` and `direct_brightness_control` are gone (the knob is
`adaptive_timelapse.brightness_target.base`), the overlay name key is
`overlay.camera_name` (not `overlay.content.camera_name`), and exposure has
no modes to configure — the ladder ramps shutter to 20 s then gain, by
itself, out of the box.

Never change `output.filename_pattern`'s trailing timestamp: the video
renderer re-parses it out of every filename.

## 5. Check, then one test frame

```bash
./scripts/install.sh --check     # exits 0 when ready
python3 -m raspilapse.cli.capture --test
ls -R /var/www/html/images/$(date +%Y/%m/%d)/
```

Expect `<project>_<YYYY_MM_DD_HH_MM_SS>.jpg` plus a `_metadata.json` sidecar,
with the overlay bar across the top. The very first frames settle in from a
cold start — a blown or dark opener is normal for a frame or two.

If capture fails with `EPERM` on `/dev/media*` *from your shell* while
`rpicam-still` worked earlier, something is confining that shell (it is
inherited, so sudo does not help). The camera is fine — verify through
systemd instead. This has burned an hour more than once.

## 6. Install the services

```bash
./scripts/install.sh
sudo systemctl start raspilapse
systemctl list-timers 'raspilapse-*'
```

One installer, four components: the capture service, the 05:00 daily video,
the 02:00 cleanup, the upload retry. `--with-watchdog` and `--with-netwatch`
exist for a camera that has *proved* flaky — both run as root and can reboot
the Pi, so add them from evidence, not up front.

## 7. Same-day video test

No need to wait for 05:00 — after a few minutes of capture:

```bash
python3 -m raspilapse.cli.daily --date "$(date +%F)" --no-upload
ffprobe -v error -select_streams v:0 -show_entries stream=width,height \
    -of default=nw=1 /var/www/html/videos/$(date +%Y/%m)/*.mp4
```

Expect `width=3840 height=2160` and a keogram and slitscan beside the mp4 —
plus, if you set up a webserver in step 3, a 200 for all three URLs.
Tomorrow's 05:00 run covers the same window and simply overwrites this
partial render.

## 8. Disk budget

At 4K/30 s: **6–8 GB of images per day**, pruned after 7 days by the cleanup
timer → a ~45–55 GB rolling plateau. Change the window without editing
anything tracked by git:

```bash
sudo systemctl edit raspilapse-cleanup.service
# [Service]
# Environment=RASPILAPSE_KEEP_DAYS=14
# Environment=RASPILAPSE_IMAGE_DIR=/var/www/html/images   # match output.directory
```

Videos are ~0.5 GB/day and kept forever by default — on a 128 GB card that
is roughly two to three months of headroom after the image plateau, so
decide early: set `video.retention_days` (say 30), or archive them off-Pi.
Database rows are ~200 MB/year; `database.retention_days` if you care.

## If you run a sparse interval instead

Not this camera, but if you set `interval` to minutes rather than seconds:
the video duration is frames ÷ `video.fps` (48 frames/day at 25 fps is a
2-second clip — lower `video.fps` to taste), set `video.deflicker: false`
(its 10-frame window would smear hours of sky), and expect keograms only as
wide as the frame count.

## Traps

1. `config/config.yml` is gitignored — it will not survive the SD card.
   Keep a copy off-Pi; it is the only non-reproducible file.
2. picamera2 from apt, astral from pip — never the other way around.
3. `video.organize_by_date` defaults to false. Set it.
4. A `video_upload:` block without `enabled:` counts as enabled — set
   `enabled: false` explicitly while staging credentials.
5. The first frames after an empty database settle in; judge exposure from
   frame five, not frame one.
6. `EPERM` on `/dev/media*` in a restricted shell is not a broken camera
   (see step 5).
7. The month's last daily video files under the *next* month's folder — the
   subfolder comes from the window's 05:00 end.
8. `logging.level: INFO` while setting up, `WARNING` once it runs unattended.

## Sibling repos

A full-featured camera in this fleet can also run `python-reverb` (live
remote control), `pi-overlay-data` (ships/tide/aurora feeds for the overlay)
and `dashboard-raspilapse` (local web UI). None are needed for capture or
daily videos; see section 10 of
[UpgradeOldRaspilapse.md](UpgradeOldRaspilapse.md) for how they connect.
