# Troubleshooting

Start here:

```bash
./scripts/install.sh --check      # dependencies and config, changes nothing
python3 -m raspilapse.cli.status             # service state, config summary, recent captures
tail -f logs/auto_timelapse.log   # what it is doing right now
systemctl status raspilapse
```

Application logs live in `logs/<script>.log`. Under systemd they do **not** also
go to the journal — that would store every line twice — so `journalctl -u
raspilapse` shows systemd and libcamera messages, not Raspilapse's own.

---

## Nothing is being captured

**Is the service running?**

```bash
systemctl status raspilapse
sudo systemctl restart raspilapse
```

**Is the camera visible to the system at all?**

```bash
rpicam-still -o /tmp/test.jpg
```

If that fails, the problem is below Raspilapse. Check the ribbon cable seating
at both ends, then `sudo raspi-config` → Interface Options → Camera.

**Permission denied on the camera**

```bash
sudo usermod -aG video $USER
```

Then log out and back in — group membership only applies to new sessions.

**`ModuleNotFoundError: No module named 'picamera2'`**

Install it with apt, never pip:

```bash
sudo apt install -y python3-picamera2
```

The systemd units run `/usr/bin/python3`, so that is the interpreter the
packages have to be visible to. A virtualenv will not work unless it is created
with `--system-site-packages`, because picamera2 cannot be pip-installed.

**Output directory not writable**

`./scripts/install.sh --check` prints where images are going. The capture user
needs write access:

```bash
sudo chown -R $USER:www-data /var/www/html/images
sudo chmod -R 775 /var/www/html/images
```

---

## Images look wrong

**Too dark or too bright overall.** Raise or lower
`adaptive_timelapse.brightness_target.base` (default 120). See
[EXPOSURE.md](EXPOSURE.md).

**Brightness oscillating between frames.** Lower `brightness_damping`. If you
have just enabled `highlight_protection`, set `enabled: false` to confirm
whether that is the cause — it reverts to the previous behaviour with no code
change.

**Blown-out skies.** Highlight protection is on by default; lower
`adaptive_timelapse.highlight_protection.min_scale` for more of it, or check
it was not disabled. Protection works by underexposing, which costs shadows —
if you find yourself wanting more of both ends, that is what
`dynamic_range.method: fusion` is for (the under-bracket protects the sky
without darkening the frame).

**Shadows too dark while the sky looks right.** One exposure cannot hold
both; enable `adaptive_timelapse.dynamic_range.method: fusion` and tune
`fusion.ev_spread` — it is a continuous dial, and it moves shadows far more
than any post-processing: 2.0 is conservative, 3.0 opens dark foliage
dramatically, and tenths in between are real steps. Add
`tone_map: {enabled: true}` for local contrast on top. Compare recipes on
your own scene with `raspilapse-drtest` (in steady light — sequential shots
in fast-changing weather are not comparable).

**Colour shifting frame to frame.** Every frame is taken with manual white
balance, so this is not AWB running — it is the cross-fade moving. Lower
`transition_mode.wb_transition_speed` to slow it down.

**Daylight colour drifting over days, or not matching
`day_mode.fixed_colour_gains`.** The configured gains take priority over the
learned reference, so a value set there is what you get. If the key is absent,
the camera learns its own white point from the test shot and it will follow the
season. Set `fixed_colour_gains` to pin it. The recorded gains per frame are in
the `colour_gains_r` / `colour_gains_b` database columns, and
`scripts/db_graphs.py` plots them as `white_balance.png` — a flat line there is
what fixed looks like.

**The first frame after a restart is blown out.** The controller seeds from the
last good database row on startup. If the database is empty or disabled, there
is nothing to seed from and the first frame or two will settle in. Check for:

```
[Startup] Seeded from last capture: exposure=...
```

Look at what actually happened:

```bash
python3 scripts/db_stats.py 1h
python3 scripts/db_graphs.py 24h    # then look at graphs/brightness.png
```

---

## The daily video did not appear

```bash
systemctl status raspilapse-daily-video
systemctl list-timers 'raspilapse-*'
python3 -m raspilapse.cli.daily --date 2026-07-25   # run it by hand
```

**"No images for &lt;date&gt;"** is not a failure — it exits 0. The camera was
off, or cleanup already removed that day's frames.

**Encoding runs out of memory.** 4K is demanding. In `config.yml`, lower
`video.codec.threads` to 1, or set `preset: ultrafast`.

**Uploads are queued and never sent.**

```bash
python3 -m raspilapse.cli.retry_uploads --status
```

Rows sitting at `pending` with `[FILE MISSING]` have outlived their source
video. Clear them:

```bash
python3 -m raspilapse.cli.retry_uploads --purge-missing
```

If `video_upload.url` or `api_key` is empty, the retry service says so and exits
0 — uploads simply are not configured.

---

## Disk filling up

```bash
df -h
du -sh /var/www/html/images logs data
python3 -m raspilapse.cli.db --stats
journalctl --disk-usage
```

Rough arithmetic at 4K and a 30-second interval: about 300 KB per frame, 2,880
frames a day, so **6-8 GB per day** before cleanup. With the default 7-day image
retention that settles at roughly 50 GB. Halving the interval or dropping
`output.quality` from 75 moves this proportionally.

Images are removed by `raspilapse-cleanup.timer` after `KEEP_DAYS` (7 by
default, in `scripts/cleanup_old_images.sh`). Database rows live longer —
`database.retention_days`, 180 in the shipped example — because they hold the
lux, brightness and weather history the graphs are drawn from. A row whose
`image_path` no longer exists is expected, not broken.

The **videos** made from those images have their own window,
`video.retention_days`. Until that key existed the source frames were bounded
and the thing they turn into was not, so the video directory grew forever — 3.2
GB from twelve days on this camera. At 4K/crf 20 a day is roughly 500 MB, so 7
days settles near 3.4 GB. A file is never deleted while the upload queue still
holds a row for it in any state other than `success`, including `failed`;
those are logged as retained rather than silently skipped. Check before
trusting it:

```bash
python3 -m raspilapse.cli.prune_videos --dry-run
```

Reclaiming space after a large prune needs an explicit vacuum, which is slow and
needs free disk equal to the database size:

```bash
python3 -m raspilapse.cli.db --prune
python3 -m raspilapse.cli.db --vacuum
```

The journal is capped at 200 MB by `systemd/journald-raspilapse.conf`, installed
alongside the units. If it is larger, the drop-in is missing:

```bash
sudo journalctl --vacuum-size=200M
```

---

## Serving the images over the web

If `output.directory` is under a webserver root, nginx can list the tree and
serve the latest frame directly.

```bash
sudo apt install nginx
```

`/etc/nginx/sites-available/timelapse`:

```nginx
server {
    listen 80;
    server_name _;
    root /var/www/html;

    location /images/ {
        autoindex on;
        autoindex_exact_size off;
        autoindex_localtime on;
    }

    location /status.jpg {
        alias /var/www/html/status.jpg;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/timelapse /etc/nginx/sites-enabled/
sudo systemctl restart nginx
```

Frames are then at `http://<pi>/images/YYYY/MM/DD/`, and
`http://<pi>/status.jpg` is whatever was captured most recently — that symlink
is maintained by `output.symlink_latest`.

## Is it capturing at the right rate?

```bash
# Frames in the last hour. At a 30s interval, expect ~120.
find /var/www/html/images -name '*.jpg' -mmin -60 | wc -l

python3 scripts/db_stats.py 1h          # the same question, from the database
```

A rate well below the expected number, with the service still `active`, is the
stall the watchdog exists for.

---

## Logs are enormous

Check `logging.level` in `config.yml`. Use `INFO` while setting up and `WARNING`
for 24/7 running.

Check `logging.console` is `auto`. Set to `true` under systemd it writes every
line twice, once to `logs/` and once to the journal.

Rotation is per logger: `logging.max_size_mb` for the live file plus
`logging.backup_count` rotations, so the defaults give 5 MB x 3 = 15 MB each.
Eleven loggers write today, so the worst case for `logs/` is around 165 MB —
though only auto_timelapse, capture_image, overlay and weather get anywhere
near their limit in practice.

---

## Captures stall without the service dying

The process stays alive while the camera stops producing frames — a wedged
libcamera pipeline, a full card, a USB reset. `Restart=always` cannot see that,
so there is an opt-in watchdog:

```bash
./scripts/install.sh --with-watchdog
```

It runs as root every 5 minutes, restarts the service if no frame has appeared
in 10 minutes, and reboots after two failed restarts. Its escalation counter
lives in `/var/lib/raspilapse/` so it survives a reboot.

---

## The camera looks frozen but is still capturing

Check this **before** assuming the Pi has hung. The symptoms of a dead network
and a dead Pi are identical from a desk: no new video uploaded, no live image on
the server, nothing responding. But capture writes to the local card and does
not care whether the network exists, so the camera can be perfectly healthy and
completely invisible at the same time.

Ask the card, not the dashboard:

```bash
ls -t /var/www/html/images/$(date +%Y/%m/%d)/*.jpg | head -3
sqlite3 data/timelapse.db \
  "SELECT datetime(unix_timestamp,'unixepoch','localtime') FROM captures
   ORDER BY unix_timestamp DESC LIMIT 3;"
```

Recent timestamps mean the camera never stopped and the problem is the network.

This is not hypothetical. On 6 August 2026 this camera's access point dropped at
23:31, both BSSIDs timed out within 23 seconds, and NetworkManager read the
timeout as a wrong password:

```text
NetworkManager: Activation: (wifi) disconnected during association, asking for new key
NetworkManager: state change: need-auth -> failed (reason 'no-secrets')
```

`failed (reason 'no-secrets')` sets an autoconnect **blocked reason** on the
profile rather than a retry counter, so `connection.autoconnect-retries=-1` does
not clear it and neither does anything else in `NetworkManager.conf`. Only an
agent supplying secrets, an explicit `nmcli con up` / `nmcli dev connect`, a
change to the profile's secrets, or restarting NetworkManager will. wlan0 sat
`inactive` for 8h35m until someone power-cycled the Pi — which "fixed" it and
made a network fault look like a hang. Capture ran the whole time: 2880 frames
that day, none missed.

To recover automatically instead:

```bash
./scripts/install.sh --with-netwatch
```

Runs as root every 2 minutes. Two checks of grace so an access point reboot
rides out untouched, then `nmcli dev connect`, the priority profile explicitly,
a radio cycle, a NetworkManager restart, and only then a reboot. The reboot
needs 30 minutes of continuous failure, your SSID seen on the air at least once
during it, and 6 hours since the last watchdog reboot — so an access point that
is genuinely switched off never triggers one, however long it stays off.

Watch it decide without letting it act:

```bash
sudo env RASPILAPSE_NETWORK_STATE=/tmp/netstate bash scripts/check_network.sh --dry-run
```

---

## Tests fail after pulling

```bash
pip3 install -r requirements-dev.txt
make all
```

If `black --check` fails on code you did not touch, your black is a different
version from the pin in `requirements-dev.txt`. Black's stable style changes
between yearly releases. `black --version` will show it.

---

## Reference material

Camera hardware and Picamera2 belong to Raspberry Pi, not to this project:

- [Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)
- [Picamera2 on GitHub](https://github.com/raspberrypi/picamera2)
- [Camera documentation](https://www.raspberrypi.com/documentation/accessories/camera.html)
