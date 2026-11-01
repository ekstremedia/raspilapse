# Capture scheduling: design plan

Status: **plan, not implemented**. This document is the agreed design for
letting raspilapse capture only inside configured time windows. It exists so
the work can be picked up later without re-deriving the reasoning, and so it
can be shared with the person who asked for it.

## Background

A user in Australia is deploying raspilapse at an off-grid grassland site to
study how the shade of fixed solar panels affects habitat, flora and fauna.
The unit runs from a small solar panel and battery, behind a power HAT that
shuts the Pi down on low battery and powers it back on once charged. An LTE
link will be used to retrieve the timelapses.

His ask: stop captures between fixed times to save power overnight. He tried
a cron job that stops and restarts the service, and correctly identified the
weakness: if the Pi is off (low battery) when the cron job is due, the
transition is missed and the camera stays in the wrong state until the next
day.

He would prefer to keep the Pi running overnight rather than rely on the HAT's
scheduled shutdown, because he is worried about the unit not waking.

## Why the obvious approach is wrong

Any design that acts on a *transition* (a job at 22:00 to stop, a job at
06:00 to start) is edge-triggered. If the machine is off, asleep, or mid-boot
at the moment of the edge, the edge is lost and nothing corrects it. Cron,
systemd `OnCalendar` timers that start and stop the service, and the HAT's
own schedule all share this flaw.

The fix is to make the schedule **level-triggered**: the daemon never acts on
a transition. At every slot it asks "does this moment fall inside a capture
window?" and the answer is a pure function of the current time and the
config. There is no stored state and nothing to miss. A power cut at 21:59, a
reboot at 03:00, or the clock stepping at boot (no RTC) all end the same way:
the loop wakes, evaluates, and does the right thing.

This also answers his real wish. Once the schedule is stateless, it becomes
**safe to combine with the HAT's hard shutdown**. If the HAT cuts power at
night and restores it at dawn, raspilapse starts, finds itself inside a
window, and carries on. There is no task on either side to miss.

## Fit with the current code

The loop in `raspilapse/daemon.py` is already the right shape:

- Every path through an iteration ends in `_sleep_until_next_slot`, which
  sleeps in one-second slices and checks `self.running`, so a long pause can
  reuse it and still react to SIGTERM.
- Frames are placed on a grid of interval multiples since the epoch
  (`_next_slot`), so a window that opens mid-interval can simply re-anchor on
  the grid.
- The camera is opened and closed once per frame, so a paused daemon holds no
  camera and draws nothing beyond an idle Python process.
- `_init_location` already builds an astral `LocationInfo` from the
  `location:` block and `_get_sun_elevation` is computed per frame. Sun-relative
  window times are therefore nearly free.

The change to the loop itself is small: a `schedule.current_window(now)`
lookup at the top of each iteration, and when nothing matches, a sleep until
`schedule.next_open(now)` instead of until the next slot.

## Proposed configuration

```yaml
adaptive_timelapse:
  interval: 30
  schedule:
    # Capture only inside these windows. Absent or empty means always,
    # so existing installs do not change behaviour.
    # Times are interpreted in location.timezone.
    windows:
      - from: sunrise-30m
        to: sunset+30m
      - from: "22:00"        # optional sparse night coverage
        to: "04:00"          # wraps midnight
        interval: 600        # overrides adaptive_timelapse.interval
```

### Semantics

| Rule | Decision |
|---|---|
| No `schedule` block, or empty `windows` | Capture always. Backwards compatible. |
| Clock times | `"HH:MM"` or `"HH:MM:SS"`, in `location.timezone`, falling back to system local time if no location is configured. |
| Sun expressions | `sunrise`, `sunset`, `dawn`, `dusk`, `noon`, each with an optional `+`/`-` offset like `30m`, `1h`, `1h30m`. Require `location`. |
| Midnight wrap | A window whose `to` is earlier than `from` spans midnight. Evaluated against today's and yesterday's window so 01:00 matches a `22:00` to `04:00` window. |
| Overlapping windows | First match in list order wins. Simple to reason about, easy to document. |
| Per-window `interval` | Optional. Falls back to `adaptive_timelapse.interval`. Lets one config express "every 30 s by day, every 10 min by night". |
| Outside all windows | Paused. No camera, no reference shot, no capture. |
| No sunrise today (polar night) | A sunrise-to-sunset window is empty for that day. |
| No sunset today (midnight sun) | A sunrise-to-sunset window covers the whole day. |
| Window shorter than its interval | Capture at least one frame when the window opens. |

Polar cases are not academic: the project's reference camera is at 68.7°N.
Astral raises on days without a sunrise or sunset, so the parser must catch
that and apply the rule above rather than let it reach the loop.

### Validation

Reject at startup, with a clear message, rather than discovering at 03:00:

- Unparseable time or sun expression.
- Sun expression without a `location` block, or without astral installed.
- `interval` on a window that is not a positive number.
- Non-list `windows`.

## Things a pause will break

Each of these turns a nightly pause into a fault unless handled. They are the
reason this is more than a loop change.

### 1. The watchdog will fight it

`scripts/check_service.sh` restarts the service after
`STALL_THRESHOLD_SECONDS=600` without a new image, and reboots the Pi after
`MAX_RESTART_ATTEMPTS=2` (rate-limited to one reboot per six hours). A nightly
pause would produce restart, restart, reboot, every night.

Proposed fix: the daemon writes a small JSON state file (for example
`/var/lib/raspilapse/capture_state.json` or next to the `latest` symlink) on
every state change:

```json
{"state": "paused", "since": 1762000000, "until": 1762030920,
 "window": "sunrise-30m", "written": 1762000000}
```

The watchdog reads it and treats `paused` with `now < until` as healthy. A
heartbeat timestamp the daemon touches every slot, paused or not, would be an
even cleaner proof of life and would also catch a loop that is alive but
wedged while active. Both can coexist: heartbeat proves the process, image
age proves the camera, and the image-age check is skipped while paused.

### 2. Stale exposure state on resume

Within one process the lux smoother and ladder position in
`ExposureController` survive the pause. A dawn resume would otherwise start
from dusk settings and produce the brightness flash the startup seeding code
(`_seed_from_last_capture`) already guards against across restarts.

Fix: when resuming after more than 20 intervals (reuse the exact rule from
the seeding code), reset to a cold start. Short pauses, such as a ten-minute
gap, keep their state.

### 3. Status and logs

`raspilapse-status` should show "Paused until 05:42 (sunrise-30m)" rather than
looking dead. The pause should be logged once when entered, with the resume
time, and once when left. Not every second.

### 4. The other timers keep the modem busy

On an LTE link these matter more for battery than the capture loop does:

| Timer | Cadence | Night cost |
|---|---|---|
| `raspilapse-upload-retry` | every 30 min | Wakes the modem even with nothing to send |
| `raspilapse-netwatch` | its own timer | Probes the network, may restart NetworkManager |
| `raspilapse-watchdog` | every 5 min | Cheap, but see item 1 |
| `raspilapse-daily-video` | 05:00 | CPU-heavy ffmpeg run |

A follow-up worth offering: let the retry and netwatch scripts read the same
state file and go quiet while paused. Out of scope for the first PR, but the
state file should be designed with it in mind.

### 5. The daily video timer and hard shutdown

`raspilapse-daily-video.timer` has `Persistent=false`. If the HAT has the Pi
off at 05:00, the video for that day never renders. If a user goes the
hard-shutdown route, that needs to be `Persistent=true`, or the render time
needs to move inside the capture window. Document this next to the schedule
option.

### 6. Reference shot

`_wants_reference_shot` must not fire while paused. Trivial, but it opens its
own camera and would otherwise defeat the point.

## Alternatives considered

| Approach | Pros | Cons | Verdict |
|---|---|---|---|
| Cron `systemctl stop`/`start` | No code change | Edge-triggered. Missed transitions on power loss. Watchdog restarts the stopped service. | No |
| systemd timers that stop/start the service | Native, `Persistent=true` catches missed edges after boot | Still two edges to get right. `Persistent` only helps for the edge that falls during downtime, not for clock steps. Watchdog conflict remains. Sun-relative times impossible. | No |
| HAT scheduled shutdown only | Biggest power saving: the whole Pi is off | User does not trust the wake. No frames at all overnight. Video timer misses. Nothing in raspilapse knows it is "night". | Not alone, but safe to add on top of the in-daemon schedule |
| In-daemon level-triggered schedule (this plan) | Stateless, survives any outage, sun-relative, per-window intervals, status-aware | Code change touching loop, watchdog, status. Pi still idles. | **Yes** |

## Power: be honest about what this saves

Pausing captures removes the camera draw and the CPU spikes from capture,
exposure fusion and overlay rendering. It does not remove the Pi's idle draw.

| Component | Rough idle draw |
|---|---|
| Pi 4 idle | 2 to 3 W |
| Pi Zero 2 W idle | about 0.6 W |
| LTE modem idle | 1 to 3 W |
| Camera module, closed | negligible |

The daemon schedule is the safe, robust layer. The real savings come from the
HAT's scheduled shutdown and from powering the modem only in an upload
window. The stateless schedule is what makes both of those safe to use, which
is the point worth making in the reply.

## Further ideas

- **Schedule dry-run.** A subcommand (`raspilapse-schedule` or a flag on
  `raspilapse-status`) that prints the next seven days of resolved windows in
  local time, with per-window intervals. Deploying to a site you cannot
  drive to, this is worth more than any single feature. Also the natural place
  to test sun expressions against a latitude.
- **Remote check-ins while paused.** `raspilapse-snapshot` opens its own
  camera and the paused daemon holds none, so a frame can be grabbed over LTE
  at any hour. Worth stating in the docs.
- **Sun elevation as a window boundary.** `elevation>-6` style expressions
  would generalise `dawn`/`dusk`. The elevation is already computed per frame.
  Probably a later addition; the named events cover most cases.
- **Upload window.** A separate `upload.schedule` so uploads batch into a
  daytime slot when the battery is being charged and the modem can be powered
  up once. Pairs naturally with item 4 above.
- **Weekday or date ranges.** Possible (`days: [mon, tue]`,
  `from_date`/`to_date`) but not asked for. Leave out of the first version;
  the window list is easy to extend.
- **Overlay.** No change needed. The overlay already records sun elevation
  and timestamp per frame.

## Implementation checklist

One PR, in this order:

1. `raspilapse/schedule.py`: window parsing, sun expressions, matching,
   `current_window(now)` and `next_open(now)`. Pure functions, no I/O.
2. Tests: clock windows, midnight wrap, sun expressions at a mid-latitude,
   polar night and midnight sun at 68.7°N, overlapping windows, per-window
   interval, empty and absent config, invalid input.
3. Loop change in `daemon.py`: evaluate at the top of each iteration, pause
   through the existing sleep helper, re-anchor the grid on resume, skip the
   reference shot while paused, reset exposure state after a long pause.
4. State file written on pause and resume.
5. `scripts/check_service.sh`: read the state file, skip the image-age check
   while paused.
6. `raspilapse-status`: show paused state and resume time.
7. Dry-run subcommand.
8. Docs: `docs/CONFIG-REFERENCE.yml` entry, README section, note on
   `Persistent=` for the video timer under hard shutdown, power expectations.
9. `CHANGELOG.md`.

## Open questions

- Where should the state file live? `/var/lib/raspilapse` matches the watchdog
  state but is root-owned by `StateDirectory=`; the daemon runs as the login
  user. The image directory or the `latest` symlink's directory may be the
  pragmatic choice.
- Should `schedule` live under `adaptive_timelapse` (it governs the capture
  loop) or at top level (other services may read it later)? Leaning
  `adaptive_timelapse` now, with a single lookup helper so moving it is cheap.
- Should the first frame after a window opens be taken immediately or on the
  next grid slot? Leaning next grid slot for consistency, except when the
  window is shorter than the interval.

## Points for the reply to the request

- Commercial use is fine: the licence is MIT, attribution is all it asks.
- His diagnosis of the cron approach is right, and the fix is a schedule the
  daemon re-evaluates every slot rather than one it acts on at a transition.
- Sun-relative windows will suit a shade study better than clock times, given
  how much Australian day length moves across the year.
- A per-window interval lets him keep sparse night frames for fauna if he
  wants them.
- Once this exists, combining it with the HAT's scheduled shutdown becomes
  safe, and that is where the large power saving is. The daemon pause alone
  saves the camera and CPU spikes, not the Pi's idle draw or the modem.
