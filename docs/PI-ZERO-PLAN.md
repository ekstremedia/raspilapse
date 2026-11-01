# Raspilapse on a Pi Zero 2 W with solar and LTE: plan

Status: **plan, not implemented**. Companion to
[SCHEDULING-PLAN.md](SCHEDULING-PLAN.md), which covers pausing capture by
time of day. This document covers everything else needed to run raspilapse on
a Pi Zero 2 W at an off-grid site: the hardware budget, the code that blocks
it today, the choices, and the order to do things in.

Based on a static reading of main at f639bee. Nothing has been run on a Zero
yet, and the first phase below is to change that before writing much code.

## Direction

| Decision | Choice | Why |
|---|---|---|
| Capture resolution | 1080p, Camera Module 3, ideally its 2x2 binned 2304x1296 sensor mode | The Zero's 512 MB and single-core-class CPU budget. Binning also raises sensitivity for night frames. |
| Where the video is made | On the Pi, with the hardware encoder `h264_v4l2m2m`, then uploaded | Frames-to-server does not fit an LTE plan, see below. |
| Upload target | The existing nesthus endpoint `POST /api/piVideo/new-store` | Already accepts video plus optional keogram and slitscan. No server change needed. |
| Connectivity | LTE modem, powered only for uploads if the hardware allows | The modem, not the Pi, is the largest consumer. |

Monthly traffic estimates for a 30 s interval, which settle the "server makes
the timelapse" question:

| Approach | Per month |
|---|---|
| 4K frames to server | about 200 GB |
| 1080p frames to server | 30 to 40 GB |
| 1080p H.264 daily video | about 5 GB |

## Power and solar

Estimates, not measurements. Phase 0 replaces them with numbers.

| Item | Draw |
|---|---|
| Zero 2 W plus camera, capturing | 1 to 1.5 W |
| LTE Cat-1 or Cat-4 modem, connected and idle | 0.5 to 1 W |
| LTE modem, transmitting | 2 to 3 W |
| Daily budget | about 36 Wh |

For a site in southern Norway (Vestfold was used for the estimate) in
December, that budget needs roughly a 100 W panel at 60 to 70 degrees tilt and
a 12 V LiFePO4 battery of 20 Ah or more. LiFePO4 needs a low-temperature
charge cutoff. Sortland has polar night, so solar alone cannot carry a unit
there through winter.

The schedule plan's level-triggered windows are what make a low-battery
shutdown HAT safe to use. Read that document for the reasoning.

## What blocks this in raspilapse today

Ordered by how hard they block a first Zero running on mains.

### 1. The daily job cannot reach the hardware encoder

`raspilapse/video/timelapse.py` refuses a hardware codec unless an output
resolution of at most 1920x1080 is set, and the only thing that sets one is
the CLI flag `--hd`. `raspilapse/video/daily.py` never passes it. So
`video.codec.name: h264_v4l2m2m` turns the 05:00 job into a nightly failure,
and there is no config key for output resolution.

Options:

| Option | Pros | Cons |
|---|---|---|
| New key `video.resolution: {width, height}` | General. The daily job reads it, the CLI keeps `--hd` as a shortcut. | Needs a DEFAULTS entry, reference docs, and tests under the config test rules below. |
| Boolean `video.hd: true` | Smallest change | Encodes one fixed size into config. Awkward if a 720p variant is ever wanted. |
| Auto-scale when the codec is hardware | No new key | Silent behaviour. The existing error message was written precisely to avoid silent outcomes. |

Recommendation: `video.resolution`, optional, absent means source resolution.
When the camera already captures at 1080p the scale filter is a no-op and
costs nothing.

### 2. Keogram and slitscan cannot be turned off from config

`daily.py` hardcodes `--slitscan`, and `--no-keogram` is CLI-only. Both decode
every JPEG of the day at full size, 2880 decodes at a 30 s interval, without
PIL's `draft()` reduction. On a Zero that is a long, hot job at 05:00.

Recommendation: `video.keogram.enabled` and `video.slitscan.enabled`
(defaults true, preserving current behaviour), and use `draft()` in the
keogram reader since a one-pixel strip does not need a full-size decode.

### 3. No sensor mode is selected

`raspilapse/camera/capture.py` calls `create_still_configuration` with
`main` at `camera.resolution` and no `sensor=` argument, so libcamera picks
the mode. For a 1920x1080 main stream it may pick the full 4608x2592 readout
and scale, which costs ISP time and loses the binning sensitivity gain.

Recommendation: optional `camera.sensor_mode: {width, height}` passed through
as `sensor={"output_size": ...}`. Absent means today's behaviour. Document
the Module 3 modes next to it.

### 4. Per-frame CPU work that a Zero will feel

None of these block a first run. All are worth measuring in phase 0 before
touching, since the per-frame budget at a 30 s interval is generous even on a
Zero. Listed so they are not forgotten:

- Camera opened and closed every frame with an unconditional two-second
  stabilise sleep. The open-per-frame design is deliberate (settings only
  apply reliably at start, see the comment in `daemon.py`), so the lever is
  the sleep, not the design.
- Each frame decoded more than once at full size: the save, the overlay's
  open, draw and re-encode, and `_analyze_image_brightness` with a median and
  four percentiles over the full frame. The brightness pass could run on a
  downscaled copy or the lores stream.
- The overlay reloads two TrueType fonts per frame and forks `vcgencmd` per
  frame. Both are cheap to cache.
- OpenCV can stay uninstalled. It is imported lazily and `dynamic_range`
  falls back to off without it, which is the right setting on a Zero anyway.

### 5. Upload robustness over LTE

`raspilapse/storage/upload.py` does a single multipart POST with a read
timeout of 3600 s and no resume. Consequences on a flaky LTE link:

- A drop at 95 percent resends the whole file.
- A stalled socket can hold the modem awake for an hour before failing.
- Without `requests_toolbelt` the whole video is buffered in RAM, which on
  512 MB is a crash, not a warning.

Recommendation for the first version: make `requests_toolbelt` a hard
requirement on install, drop the read timeout to a few minutes, and keep the
existing retry queue. Chunked or resumable upload needs a server change and is
a later phase.

### 6. Watchdog, netwatch and cleanup assume a mains Wi-Fi camera

- `scripts/check_service.sh` restarts after 10 minutes without a frame and
  reboots after two restarts. Long night intervals or a schedule pause trip
  it. The schedule plan's state file is the fix; the threshold should also
  derive from the configured interval.
- `scripts/check_network.sh` is Wi-Fi only: `wlan0`, `nmcli`, `rfkill`. On an
  LTE modem (`wwan0`, `usb0`, `ppp0`) it does nothing useful and may do harm.
  Either make the interface and recovery action configurable or document that
  netwatch is not installed on LTE units. ModemManager has its own reset path.
- `scripts/cleanup_old_images.sh` deletes JPEGs older than a fixed number of
  days regardless of whether the day's video uploaded. Videos that never
  uploaded are kept forever with no cap. On a small SD card with an unreliable
  link, retention should be upload-aware: keep frames until the video is
  confirmed uploaded, cap pending videos by count or size.

### 7. systemd units sized for a Pi 4

- `raspilapse-daily-video.service.in` sets `MemoryHigh=1800M` and
  `MemoryMax=2400M`. On 512 MB those are inert. Size them to the machine, or
  compute them in `install.sh`.
- `raspilapse-daily-video.timer` is `Persistent=false`. If a HAT has the Pi
  off at 05:00 there is no video that day. On battery units this should be
  true, or the render should move inside the capture window.

### 8. Unverified on this hardware

To be checked in phase 0, each could change the plan:

- `h264_v4l2m2m` from the Pi OS ffmpeg build on a Zero 2 W at 1080p, and its
  speed.
- CMA contention when the 05:00 encode runs while capture continues. The
  encoder and the camera share the CMA pool, and 512 MB leaves little slack.
- Time taken by the software deflicker filter and JPEG decode at 1080p for a
  day of frames. If it is hours, the deflicker needs a config switch too.

## Optional: a live image for remote check-ins

Nesthus already has `POST /api/camera/current-image` (same bearer-key family,
10 MB cap). Raspilapse has no current-image upload. A small post of the
latest frame once every N minutes, inside the modem window, gives a remote
"is it alive and pointed right" check without pulling a whole video. Worth
adding, but after the blockers.

## Relationship to the scheduling plan

The two plans touch the same files in places: the watchdog state file, the
retry and netwatch timers going quiet, and `Persistent=`. They should be
sequenced, not merged.

Recommended order: do items 1, 2, 3 and 7 from this plan first. They are
small, independent, and are what gets a Zero running on mains to measure.
Then the schedule plan as one PR. Then items 5 and 6 here, which benefit from
the state file the schedule plan introduces.

## Phases

### Phase 0: measure on mains

No code. A Zero 2 W with Camera Module 3 on a USB power meter for a few days
with the current code at 1080p and `libx264`. Record: watts idle, watts during
capture, time per frame, time for the 05:00 job, RAM peak during the encode.
Try `h264_v4l2m2m` by hand with `--hw --hd`. Try the binned sensor mode by
hand. This decides how much of item 4 is real.

### Phase 1: minimum code for a Zero on mains

Items 1, 2, 3 and 7. One PR. After this a Zero runs the daily job with the
hardware encoder from config alone.

### Phase 2: schedule plan

As written in SCHEDULING-PLAN.md.

### Phase 3: battery and modem

Items 5 and 6, plus the optional current-image post. Modem power control is
hardware specific and lives outside raspilapse, but the upload window and the
quiet timers are what make it possible.

### Phase 4: field trial

Mains-free, on the bench first with the real panel and battery, then at the
site. Log battery voltage into the database if the HAT exposes it. The
overlay and the solar pattern graphs already have a place for it.

## Config test rules to respect

`tests/test_config_example.py` enforces:

- Every hard-indexed key (`config["a"]["b"]`) must exist in `DEFAULTS` in
  `raspilapse/config.py`. Keys read with `.get()` are exempt.
- Every `DEFAULTS` leaf must be documented in `docs/CONFIG-REFERENCE.yml`.
- Every documented key's leaf name must appear as a quoted string somewhere
  in `raspilapse/` or `scripts/`.
- `config/config.example.yml` must stay under 120 lines.

Tests to extend: `tests/test_make_timelapse.py` (the hardware-codec refusal
cases) and `tests/test_daily_timelapse.py` (daily command construction, which
mocks `subprocess.run`). CI runs ruff, black and pytest on Python 3.9 to 3.12,
plus an import check without picamera2 or cv2. `make test` runs it locally.

## Open questions

- Is a Zero 2 W the right board, or is a Pi 4 with the schedule plan's hard
  shutdown a better trade? The Zero wins on idle draw by a wide margin. The
  Pi 4 wins on headroom for the 05:00 job. Phase 0 answers this.
- Which modem and how is its power switched? A USB modem on a switched USB
  port, or a HAT with its own power control, decides how much of phase 3 is
  possible.
- Does the site have any winter sun at all? If not, the question is battery
  size for the dark months, not panel size.
