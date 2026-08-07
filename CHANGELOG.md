# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Cameras that "froze" every week or two were losing their network, not
  hanging.** Diagnosed on a Pi that had run 9.3 days: capture never missed a
  frame — exactly 2880 a day, one gap over 60 seconds in ten days, CPU
  temperature flat at 42-46°C and load flat at 0.34-0.38 throughout, no OOM
  anywhere. The daily ffmpeg job, the obvious suspect, succeeded every single
  day and finished two hours before the reboot.

  What actually happened: the access point dropped at 23:31, both BSSIDs timed
  out within 23 seconds, and NetworkManager read the timeout as a wrong
  password — `need-auth` → `no secrets: No agents were available` → `failed
  (reason 'no-secrets')`. That sets an autoconnect *blocked reason* on the
  profile rather than a retry counter, so `connection.autoconnect-retries=-1`,
  which was already set, could not clear it. wlan0 sat `inactive` for 8h35m
  until a human power-cycled the Pi. The only symptoms were the upload and the
  live image going quiet, which is indistinguishable from a hang unless you
  look at the card.

  `scripts/check_network.sh` now recovers it, opt-in via
  `./scripts/install.sh --with-netwatch`. Two checks of grace, then `nmcli dev
  connect` (the step that clears the block), the priority profile explicitly, a
  radio cycle, a NetworkManager restart, and only then a reboot — gated on 30
  minutes of continuous failure, the SSID having been seen on the air, and 6
  hours since the last watchdog reboot, so an access point that is switched off
  can never trigger one. `docs/TROUBLESHOOTING.md` gained "The camera looks
  frozen but is still capturing".

- **The capture watchdog was spamming the journal it would need later.**
  `find | sort -rn | head -1` left `sort` writing into a closed pipe every run;
  invisible interactively, but systemd sets `IgnoreSIGPIPE=yes`, so under the
  service it printed two error lines every five minutes into a journal already
  at its 200 MB cap. Diagnosing the outage above, `journalctl --list-boots`
  only reached back two days of a nine-day run. Now one `awk` pass.

- **`video.codec.name: h264_v4l2m2m` silently converted the 05:00 job into a
  nightly failure.** The Pi's V4L2 encoder stops at 1080p and nothing sets an
  output resolution by default, so it reached ffmpeg and died with "can't
  configure encoder". Refused up front with an actionable message instead.

### Added
- **Memory, disk, uptime, process RSS and network state in `captures`**
  (schema v6). `SystemMonitor` had collected memory and disk every 30 seconds
  since it was written; `store_capture` bound the CPU temperature and three
  load averages and dropped the rest. Ruling out a memory leak during the
  outage above meant arguing from temperature and load alone. `system_uptime_s`
  makes every reboot visible in the capture timeline itself, and
  `network_signal_dbm` is the one that would have predicted the drop rather
  than merely recording it. Migrating this camera's real 549,288-row database
  took 111 ms.

- **`video.retention_days`, defaulting to 7.** `cleanup_old_images.sh` has
  expired source frames for a long time; nothing ever expired the videos made
  from them, so a bounded input fed an unbounded output — 3.2 GB from twelve
  days here. Runs as a third step of the existing nightly cleanup timer. Never
  deletes a file the upload queue still holds in any state other than
  `success`, including `failed`, because that video exists nowhere else.

- **A shared reboot floor between the two watchdogs.** Both can reboot the
  machine and a Pi with wedged wifi looks stalled to the other one, so
  `/var/lib/raspilapse/last_reboot` now stops them cycling. Written with
  `sync`, because ext4's commit window will otherwise lose it across the very
  reboot it bounds.

### Changed
- **The daily video is both faster and closer to the source**: preset `fast` →
  `veryfast`, crf 25 → 20, threads 2 → 3, still native 4K. Measured on 60 real
  frames: 25.2 → 43.8 Mbit/s, 75s → 35s, SSIM 0.985634 → 0.990225. A preset
  does not set quality, crf does, and at 4K the encode is dominated by decode
  and filtering rather than bitrate — so the old settings were paying for a
  slower preset and spending the savings nowhere. crf 25 at 4K quantises away
  the fine texture 4K exists to carry. Peak memory *fell*, 1399 MB → 1021 MB.

- **The keogram and slitscan share one decode pass.** Both are a vertical strip
  from every frame; they were built by two separate full passes over the day's
  4K images, 314s + 271s. `create_time_slices` does one, 2.20x faster on 300
  real frames, with both outputs sha256-identical to what the split version
  produced. `create_keogram` and `create_slitscan` remain as wrappers.

- `CPUWeight=20` on the daily video unit, since `Nice` alone does not hold back
  a multi-threaded encode. `MemoryHigh`/`MemoryMax` are set too, but note that
  Raspberry Pi OS ships the memory cgroup controller disabled — they need
  `cgroup_enable=memory cgroup_memory=1` in `cmdline.txt` to do anything.

- A `flock` on `data/daily.lock` so a manual `raspilapse-daily` cannot run two
  ffmpegs alongside the timer's. Contention exits 0, not 1: a duplicate
  invocation is not a failure.

- **A one-frame colour step at every dusk, and daylight colour that drifted a
  little each day.** Both came from AWB readings reaching the manual gains by
  paths that stopped making sense when white balance became manual on every
  frame.

  The step: `_seed_across_mode_change` primed the controller from the AWB
  reference shot on the day-to-transition crossing, and `seed_from_metadata`
  assigned those gains straight to `_last_colour_gains`, bypassing the
  `wb_transition_speed` cross-fade. So AWB's twilight guess arrived whole, in
  one frame, and slid back over the following ten. Seven of them are visible in
  this camera's database between 26 July and 3 August, one per dusk, each about
  0.3 in R. The seed now carries exposure only; colour crosses the boundary the
  way it crosses everywhere else.

  The drift: `_target_colour_gains` read `day_mode.fixed_colour_gains` and then
  unconditionally overwrote it with `_day_wb_reference`, so a configured white
  point silently did nothing. Worse, that reference was learned in `_record`
  from the frame's own capture metadata -- a frame taken with AWB off, whose
  `ColourGains` are the ones the controller just chose. The reference was its
  own input: it held wherever it was pushed and adopted each dusk's step
  permanently. Daylight R went 2.500 -> 2.547 over nine days.

  Configured gains now win, the learned reference is the fallback for a camera
  that has not set one, and it is learned in `_take_reference_shot` from the
  one frame actually taken with AWB on.

  If you have been running the drifted values and want the rendered output to
  match, set `fixed_colour_gains` to the gains your recent frames carry rather
  than the config's original pair.

## [1.4.0] - 2026-07-26

A cleanup pass over the whole project. Behaviour is unchanged apart from
highlight protection, which is new and can be turned off in config.

### Removed
- **The ML exposure system.** It had not run since January: `_init_ml_predictor()`
  returned early whenever `direct_brightness_control` was true. With ML inert the
  "legacy" branches of `get_camera_settings` were unreachable too, and the formula
  functions behind them had one live caller left -- the metadata diagnostics, which
  re-ran the entire exposure calculation to fill in a JSON field.
  Gone: `ml_exposure.py`, `ml_exposure_v2.py`, `bootstrap_ml.py`,
  `bootstrap_ml_v2.py`, `ML.md`, `ml_state/` and four test modules.
- **`analyze_timelapse.py`** (1,967 lines). It read per-frame metadata JSON that
  cleanup deletes after 7 days, so it could never look back further than a week,
  while `scripts/db_graphs.py` reads months from the database. Its one
  non-overlapping chart, white balance, is now `create_white_balance_graph()`.
- **`manuals/*.pdf`** -- 45 MB of third-party PDFs nothing referenced.
- **A leaked Codecov token** from `docs/MAINTAINER.md`. Rotate it if you forked
  this repo before now; it remains in history.
- **`ml_state/ml_state.json`**, which shipped one camera's learned model to
  everyone who cloned.
- Five installer scripts, `test.sh`, `check_disk_space.sh`,
  `check_capture_rate.sh`, and twelve documentation files.

### Added
- **Highlight protection** (`adaptive_timelapse.highlight_protection`). Lowers the
  brightness target when the top of the histogram nears clipping, so bright skies
  keep detail. Off at night by default. `enabled: false` reverts.
  Not to be confused with the p95 protection listed under 1.3.0: that one scaled
  the *exposure*, lived in the now-deleted ML path, and never reached the camera.
  This one scales the *target*, which is what makes its equilibrium independent
  of `brightness_damping`.
- **`scripts/install.sh` as the single entry point**, with `--only`, `--check`,
  `--dry-run`, `--uninstall` and `--with-watchdog`. It renders `systemd/*.in`
  templates rather than copying units that hardcode `pi` and `/home/pi`.
- **`database.retention_days`** and `python3 src/database.py --prune|--vacuum|--stats`.
  Defaults to 0, keep everything; the example ships 180 days.
- **`src/exposure.py`** and **`src/config_utils.py`**.
- **`tests/test_config_example.py`**, which fails when the example config and the
  code that reads it drift apart in either direction.

### Fixed
- **The daily-video service could not run.** `upload_service.py` imported
  `requests_toolbelt` unguarded and `/usr/bin/python3` did not have it. Guarded,
  with a `requests.post` fallback.
- **An empty day failed the unit.** `make_timelapse.py` now exits 2 for "no images"
  and `daily_timelapse.py` maps that to success.
- **The upload retry queue retried the impossible.** 172 rows, all pending since
  January, all pointing at videos deleted long ago, and no installer had ever
  installed the timer that drains them. Rows whose source is gone are cancelled;
  `failed` is treated as terminal; `--purge-missing` clears a backlog.
- **The installed daily-video timer had drifted** from the repo: `Requires=`, two
  `OnCalendar=` lines and `Persistent=true`, all removed months ago and never
  redeployed, which is why it fired at boot and failed.
- **Every log line was stored twice**, once in `logs/` and once in the journal.
  `logging.console` becomes tri-state; `auto` skips the console handler under
  systemd. journald is capped at 200 MB.
- **Logging ignored `-c/--config`.** Seven modules call `get_logger()` at import
  time, before argparse runs; `configure_logging()` now reconfigures them.
- **Weather hammered its endpoint.** The 300 s cache was per-instance and the
  instance was rebuilt twice per capture cycle, so it never applied. There was no
  backoff either: one outage produced 72,536 identical error lines. Also fixed
  `data.get("data", {})` returning `None` on `"data": null`, which was 2,204 more.
- **`brightness_p25` and `brightness_p75` were NULL on every row** ever written --
  the producer emitted p10/p90.
- **The upload queue schema was defined twice** and had drifted, leaving the live
  database pinned at v3 with the v4 index missing.
- **18 tests had never run**, in four classes shadowed by a later class of the
  same name.
- **The overlay bar was darker than its config said.** Each gradient row was
  drawn as a two-pixel rectangle, so every row got painted twice and its alpha
  compounded: `background.color` alpha 70 rendered at roughly 124. The bar also
  ran one scanline past `bar_height`. Both fixed, which means **the bar now
  renders lighter than before at the same setting** -- if you want the old look,
  multiply your alpha by about 1.8. `config.example.yml` keeps 70, which for the
  first time is what it actually produces.
- **Tide points with an explicit `"level_cm": null`** reached the interpolation
  arithmetic as `None` and raised, taking the whole overlay down over one bad
  forecast entry. `.get("level_cm", 0)` only defaults a *missing* key.
- **A never-present ships/tide/aurora file was stat()ed on every render.** The
  retry interval was gated on there being a cached value, so it did nothing in
  the one case it existed for.

### Changed
- SQLite runs in WAL mode. Three unused indexes dropped (26 MB on a 515k-row
  database, three fewer B-tree writes per capture).
- `auto_timelapse.py` is under 1,000 lines, down from 3,230.
- ruff replaces flake8 and pylint, and the CI lint step can now fail -- it was
  `--exit-zero` *and* `continue-on-error`.
- black pinned to one version across `requirements-dev.txt`, `pyproject.toml` and
  `.pre-commit-config.yaml`. The mismatch was the cause of the recurring CI
  formatting failures.
- `pyproject.toml` version is now read from `src/__version__.py`, and its
  dependency list matches what the code imports.
- `graph_ml_patterns.py` -> `scripts/graph_solar_patterns.py`; it was never ML.
- The `timelapse:` and `graphs:` config blocks are gone -- no code read either,
  and `timelapse.interval: 3` sat next to `adaptive_timelapse.interval: 30`.

### Migrating

Existing installs:

```bash
git pull
./scripts/install.sh --check
./scripts/install.sh              # redeploys the corrected units
sudo systemctl restart raspilapse
```

Then, in `config/config.yml`:

- set `logging.console: auto`
- optionally add `adaptive_timelapse.highlight_protection` (see the example)
- optionally set `database.retention_days` -- absent means keep everything
- remove `adaptive_timelapse.ml_exposure` and `direct_brightness_control`,
  both now inert

If uploads were configured, clear any stale queue:

```bash
python3 src/retry_uploads.py --purge-missing
```

## [1.3.2] - 2026-01-23

### Fixed
- **Startup brightness flash after reboot/restart**: First frame(s) after reboot were severely overexposed (254 brightness, 97% clipped)
  - Root cause: On startup, `_last_exposure_time` was None, and the test shot was saturated due to camera ISP needing time to stabilize
  - Added `get_last_capture()` database method to retrieve the most recent valid capture (excludes overexposed frames)
  - Added `_seed_from_last_capture()` to initialize exposure settings from database on startup
  - Now seeds: `_last_exposure_time`, `_last_analogue_gain`, `_last_colour_gains`, `_last_brightness`, `_smoothed_lux`, `_last_mode`
  - Added safety check: if brightness is None but seeded exposure exists, use seeded exposure
  - Added saturated test shot detection on first frame - uses seeded lux if available

### Before/After
- **Before**: First frame brightness 254, 97.65% overexposed, took 7+ frames (3.5+ min) to recover
- **After**: First frame brightness 118.55, 0% overexposed, immediately on target

### Log Messages
- `[Startup] Seeded from last capture: exposure=X.XXXXs, gain=X.XX, WB=[X.XX, X.XX], mode=X, brightness=XXX.X`
- `[Startup] First test shot saturated (XXX.X/255) - using seeded lux=XXX.X instead of calculated=XXXX.X`
- `[DirectFB] No brightness data yet, using seeded exposure X.XXXXs`

## [1.3.1] - 2026-01-17

### Fixed
- **Day mode brightness oscillation**: Brightness was ranging 77-163 instead of target 105-135
  - Root cause: Config too loose + ML trained on bad data (only 21% of captures in good range)
  - Tightened `brightness_tolerance` from 60 to 25 (triggers feedback at 95-145)
  - Increased `brightness_feedback_strength` from 0.05 to 0.15 (3x faster corrections)
  - Increased `exposure_transition_speed` from 0.08 to 0.15 (~2x faster)
  - Increased `fast_rampup_speed` from 0.20 to 0.40 (faster underexposure recovery)
  - Increased `fast_rampdown_speed` from 0.20 to 0.35 (faster overexposure correction)

### Changed
- **ML state reset procedure documented**: When ML is trained on bad data, delete `ml_state/ml_state_v2.json` and restart
  - ML automatically retrains from only good samples (brightness 105-135) in database
  - Database is never deleted - all historical data preserved
  - Added troubleshooting section to `docs/ML_EXPOSURE_SYSTEM.md` and `docs/CLAUDE.md`

### Documentation
- Added "Day Mode Brightness Oscillation" troubleshooting to `docs/CLAUDE.md`
- Added "ML Trained on Bad Data" troubleshooting to `docs/ML_EXPOSURE_SYSTEM.md`
- Updated `docs/NEXT_SESSION_CONTEXT.md` with fix details

## [1.3.0] - 2026-01-16

### Changed

#### ML-First Exposure with Smart Safety
- **Philosophy change**: Trust ML predictions for smooth transitions, with graduated safety mechanisms
- **Higher ML trust**: Initial trust increased from 0.5 to 0.70, max trust from 0.8 to 0.90
- **Tighter training range**: Good brightness range narrowed from 100-140 to 105-135 for higher quality ML data

#### Bucket Interpolation for ML Data Gaps
- **New**: ML v2 now interpolates between adjacent buckets when exact match unavailable
- **Fills data gaps**: Addresses missing data in 0.0-0.5 lux (deep night) and 5-20 lux (transition zone)
- Uses logarithmic interpolation in both lux and exposure space
- Reduced confidence (70%) for interpolated predictions

#### Sustained Drift Correction (Replaces Per-Frame Feedback)
- **New**: `SustainedDriftCorrector` class only corrects after 3+ consecutive frames of consistent error
- Prevents frame-to-frame oscillation that caused brightness flickering
- Gradual decay back to neutral when error pattern breaks
- Max 30% correction per update, capped at 0.5x-2.0x range

#### Graduated Trust Reduction
- **New**: `get_brightness_adjusted_trust()` method reduces ML trust as brightness deviates from target
- Severe cases (brightness < 50 or > 200): Force formula (trust = 0)
- Warning zones: Graduated reduction (50-70 brightness ramps from 0% to 100% trust)

#### Rapid Light Change Detection
- **New**: `get_lux_stability_trust()` method reduces trust during sunrise/sunset transitions
- Detects rate of change in log-lux space
- Above 0.3 log-lux/minute: Up to 50% trust reduction
- Helps formula adapt faster during rapid Arctic light changes

#### Simplified Safety Clamps
- Removed intermediate emergency zones (WARNING_HIGH, WARNING_LOW, etc.)
- Now only applies hard corrections for extreme cases:
  - Brightness > 220: Force 30% exposure reduction
  - Brightness < 35: Force 80% exposure increase
- Philosophy: Small brightness variations (70-170) are acceptable if curve is smooth

#### Proactive P95 Highlight Protection
- **New**: `get_p95_highlight_factor()` method prevents highlight clipping BEFORE it happens
- Based on Raspberry Pi Camera Algorithm Guide's histogram constraint concept
- Monitors p95 (95th percentile brightness) and reduces exposure proactively:
  - p95 < 200: No adjustment (highlights have headroom)
  - p95 200-220: Gentle reduction (0.95-1.0x exposure)
  - p95 220-240: Moderate reduction (0.85-0.95x exposure)
  - p95 > 240: Aggressive reduction (0.70-0.85x exposure)
- Especially useful for sunrise sky blowout, Aurora bright peaks, and high-contrast scenes

### Config Changes (v1.3.0)
```yaml
ml_exposure:
  initial_trust_v2: 0.70    # Was 0.5
  max_trust: 0.90           # Was 0.8
  good_brightness_min: 105  # Was 100
  good_brightness_max: 135  # Was 140

transition_mode:
  brightness_feedback_strength: 0.05  # Was 0.2 - very gentle
  brightness_tolerance: 60            # Was 40 - wider tolerance
  exposure_transition_speed: 0.08     # Was 0.10 - slower for smoothness
  fast_rampdown_speed: 0.20           # Was 0.50 - much gentler
  fast_rampup_speed: 0.20             # Was 0.50 - much gentler
```

**Note**: These v1.3.0 values were further tuned in v1.3.1 - see above for current recommended values.

### Technical Details
- **Expected outcome**: Smooth transitions without oscillation
- **Brightness target**: 70-170 range acceptable if curve is smooth (no banding in slitscan)
- **Drift corrections should be rare**: Only for systematic ML prediction errors
- **ML data gaps filled**: Interpolation provides predictions even for untrained lux zones

## [1.2.2] - 2026-01-15

### Fixed
- **Critical: Severe underexposure during Arctic winter twilight**
  - Root cause: Exposure interpolation (15% per frame) too slow for rapid Arctic light changes, combined with emergency factor cap (1.5x) being far too low
  - Symptoms: Images going nearly black (brightness ~17 instead of target ~120) during afternoon/evening at high latitudes
  - The lux calculation was accurate (correctly detecting 1154 → 125 lux), but exposure couldn't catch up
  - At 30-second intervals, log-space interpolation takes 5+ minutes to reach target exposure
  - Emergency factor was capped at 1.5x when 4x+ correction was needed

### Changed
- **Increased emergency factor cap from 1.5x to 4.0x** - allows much faster recovery from severe underexposure
- **Increased EMERGENCY_LOW_FACTOR from 1.4x to 2.0x** - 100% exposure increase for brightness < 60
- **Added new CRITICAL_LOW brightness zone** - 300% exposure increase for brightness < 40 (Arctic twilight conditions)

### Technical Details
- Emergency factor asymmetric by design: aggressive on underexposure (up to 4x), conservative on overexposure (max 50% reduction)
- New zone thresholds:
  - EMERGENCY_LOW: brightness < 60 → 2.0x correction (was 1.4x)
  - CRITICAL_LOW: brightness < 40 → 4.0x correction (new)
- Factors still smoothed over multiple frames to prevent flickering
- Only affects severe underexposure scenarios - normal operation unchanged

### Why this only affected Arctic locations
- At 68°N in January, daylight lasts only 2-3 hours with rapid light changes
- Sun barely rises above horizon during polar twilight period
- Light drops much faster than at lower latitudes
- Standard exposure interpolation designed for temperate latitudes couldn't keep up

### Added
- New test `test_critical_low_factor` for CRITICAL_LOW zone
- Updated test assertions for new EMERGENCY_LOW_FACTOR value

## [1.2.1] - 2026-01-14

### Fixed
- **Critical: Brightness correction not applied in transition mode with sequential ramping**
  - Root cause: `_brightness_correction_factor` was only applied in `_calculate_target_exposure_from_lux()`, but sequential ramping bypassed this function entirely
  - Symptoms: Images stayed dark (brightness ~35 instead of target ~120) even with correction factor at maximum (4.0x)
  - The feedback system correctly detected underexposure but corrections were never applied
  - Fix: Apply both brightness correction factor AND emergency brightness factor to sequential ramping results
  - This affects cameras where auto-exposure seed values don't match the actual scene brightness (e.g., different sensor sensitivity)

- **Daytime flickering caused by emergency factor oscillation**
  - Root cause: Emergency brightness factor used hard thresholds (180) causing on/off toggling every frame
  - Symptoms: Exposure bouncing between ~14ms and ~16ms every frame, visible as flickering in slitscan
  - Pattern: brightness 187 → factor 0.7 → exposure drops → brightness 173 → factor 1.0 → exposure rises → repeat
  - Fix: Replaced hard threshold with smoothed emergency factor that gradually moves towards target
  - Applies corrections faster (2x speed) when brightness worsening, relaxes slower (0.5x speed) when improving
  - Prevents oscillation by not instantly reverting correction when brightness crosses threshold

### Technical Details
- Sequential ramping calculates exposure from seed values (captured during day mode auto-exposure)
- If the seed exposure produces dark images on a particular camera, the brightness feedback system detects this
- Previously, the correction factor was calculated but never applied to the transition mode exposure
- Now, correction is applied immediately after sequential ramping calculation, before EV safety clamp
- Emergency factor also applied for severe underexposure (brightness < 60)

### Why this only affected some cameras
- Different cameras have different sensor sensitivity
- The "other camera" happened to have seed values that produced correct brightness
- This camera's seeds produced dark images, requiring the correction that was being ignored

## [1.2.0] - 2026-01-12

### Changed
- **ML v2 Integration Complete**: `auto_timelapse.py` now uses ML v2 instead of v1
  - ML v2 is database-driven and only learns from good frames (brightness 100-140)
  - Passes `sun_elevation` to predictions for Arctic-aware time periods
  - Requires database to be enabled (fails gracefully if not)
  - Removes frame-by-frame learning (v1's `learn_from_frame()`) - prevents perpetuating bad exposures
  - Higher initial trust (0.5) since it's trained only on proven good data

### Deprecated
- **ML v1** (`src/ml_exposure.py`): No longer used by `auto_timelapse.py`
  - Kept for reference but not imported
  - Old state file `ml_state/ml_state.json` can be safely deleted

### Added
- New tests for ML v2 integration in `tests/test_auto_timelapse.py`
  - `test_ml_v2_disabled_by_default`
  - `test_ml_v2_requires_database`
  - `test_ml_v2_disabled_without_database`

### Documentation
- Updated `ML.md` to reflect v2 integration and v1 deprecation

## [1.1.0] - 2026-01-11

### Added
- **Arctic-Aware ML v2**: Solar elevation-based time periods instead of clock hours
  - Uses sun elevation to determine night/twilight/day periods
  - Works correctly year-round at any latitude (including polar night and midnight sun)
  - Falls back to clock-based periods if sun_elevation not available
  - New period definitions: night (< -12°), twilight (-12° to 0°), day (> 0°)

- **Aurora Frame Support in ML Training**: High-contrast night frames now included
  - Standard frames: brightness 100-140 (target exposure)
  - Aurora/night frames: brightness 30-90 with p95 > 150 at lux < 5
  - Prevents rejecting valid aurora/star photography

- **Database Migration System**: Auto-migrates schema on startup
  - Schema version tracking in `schema_version` table
  - Migration v2: Adds `sun_elevation` column
  - No manual steps required when pulling new code to other cameras
  - Gracefully handles "duplicate column" errors for fresh databases

### Changed
- Bumped database schema version from 1 to 2
- ML v2 now uses `sun_elevation` from database when available
- Updated `bootstrap_ml_v2.py` with Arctic-aware period detection

### Documentation
- Updated `ML.md` with Arctic-aware features, aurora support, and migration system
- Added version history to ML documentation

## [1.0.9] - 2026-01-11

### Added
- **Database Graph Generator** (`scripts/db_graphs.py`): Generate visually pleasing PNG graphs from capture database
  - 6 graph types: lux_levels, exposure_gain, brightness, weather, system, overview
  - Dark theme with vibrant colors for easy reading
  - Gaussian smoothing for smooth curves (window=15)
  - Temperature line with blue/red gradient based on value (blue ≤0°C, red >0°C)
  - Plain number formatting on axes (no scientific notation)
  - Hourly x-axis labels for easy time reading
  - Time range options: `24h` (default), `6h`, `7d`, `--all`
  - Custom output directory with `-o` flag
  - New tests: 25 tests in `tests/test_db_graphs.py`

## [1.0.8] - 2026-01-10

### Fixed
- **EV Safety Clamp applying every frame instead of once**: Critical bug causing severely underexposed images during transition mode
  - The clamp was designed to only apply on the first manual frame after seeding, but was applying on EVERY frame
  - This prevented exposure from ever ramping up properly (e.g., stuck at 376ms instead of 20s)
  - Added `_ev_clamp_applied` flag to ensure clamp only runs once per transition cycle
  - Flag resets when returning to day mode
  - New tests: `test_ev_clamp_applies_only_once`, `test_ev_clamp_flag_resets_on_day_mode`

### Added
- **ML-Based Adaptive Exposure System**: Lightweight machine learning that continuously learns and improves exposure settings
  - Solar Pattern Memory: Learns expected lux for each time of day, indexed by day-of-year/hour/minute
  - Lux-Exposure Mapper: Learns optimal exposure settings for each light level
  - Trend Predictor: Anticipates light changes using linear extrapolation
  - Correction Memory: Remembers which brightness corrections worked
  - Trust-based blending: Starts at 0% ML, gradually increases to 80% as predictions prove accurate
  - Shadow mode for testing: Log predictions without applying them
  - Aurora-safe learning: Accepts high-contrast night frames (low mean brightness + high highlights)
  - New files: `src/ml_exposure.py`, `src/bootstrap_ml.py`, `src/graph_ml_patterns.py`
  - New documentation: `docs/ML_EXPOSURE_SYSTEM.md`
  - 43 tests in `tests/test_ml_exposure.py`

- **Daily Solar Patterns Graph**: Visualization of light patterns from database
  - Shows lux curves by time of day for each recent day (last 14 days)
  - Displays daily midday light levels with trend line
  - Tracks polar winter recovery
  - Generated at `graphs/daily_solar_patterns.png` via `db_graphs.py`

- **Fast Underexposure Ramp-Up**: Symmetric to existing overexposure ramp-down
  - Triggers when brightness < 70 (warning) or < 50 (critical)
  - Uses faster interpolation to recover from dark frames
  - Configurable via `fast_rampup_speed` and `critical_rampup_speed`

- **SQLite Database Storage**: Historical capture data for analysis and graphs
  - Stores every capture with full metadata, brightness analysis, weather data, and system metrics
  - System metrics: CPU temperature and load averages (1min/5min/15min)
  - Single denormalized table (36 columns) for efficient querying
  - Query methods: by time range, by lux range, hourly averages
  - Never crashes timelapse - all DB operations gracefully handle errors
  - Configurable via `database.enabled` and `database.path` in config
  - New files: `src/database.py`, `tests/test_database.py` (35 tests)
  - Database stored at `data/timelapse.db`

- **Database Statistics Viewer**: CLI tool to view capture statistics
  - Shows summary, averages, and recent captures in table format
  - Time range options: `5m`, `1h`, `24h`, `7d`, `--all`
  - Limit option: `-n 10` for last N captures
  - Mode distribution breakdown
  - New files: `scripts/db_stats.py`, `tests/test_db_stats.py` (21 tests)

### Changed
- Bootstrapped ML system from 7 days of historical data (20,940 frames)
- ML system enabled and active in config (shadow_mode: false)

### Fixed
- **Weather overlay blinking**: Now returns stale cached data when fetch fails instead of returning None
  - Prevents weather text from disappearing/blinking during network issues
  - Logs warning with stale data age when using cached fallback

### Removed
- Temperature graph (`graphs/temperature.png`) - not useful for analysis

## [1.0.7] - 2026-01-09

### Fixed
- **Daily video timer running twice on reboot**: Removed `Requires=raspilapse-daily-video.service` from timer's `[Unit]` section
  - The `Requires=` directive was causing the service to start immediately when the timer started on boot
  - Timers automatically trigger their matching `.service` by name convention, so `Requires=` was unnecessary
  - Combined with `Persistent=false`, this ensures the daily video only runs once at 05:00

## [1.0.6] - 2026-01-08

### Added
- **Two-tier overexposure detection**: Early warning and critical levels for faster response
  - Warning level: brightness > 150 or > 5% clipped pixels
  - Critical level: brightness > 170 or > 10% clipped pixels
  - Configurable via `critical_rampdown_speed` (default: 0.70)

- **Proactive exposure correction**: Analyzes test shot before capture
  - If test shot is very bright (>180): 30% exposure reduction
  - If test shot is bright (>140): 15% exposure reduction
  - If lux doubled since last frame: proportional reduction
  - Prevents overexposure before it happens

- **Rapid lux change detection**: Detects when light changes quickly
  - New `_detect_rapid_lux_change()` method
  - Configurable threshold via `lux_change_threshold` (default: 3.0x)
  - Logs warning when rapid change detected

- **Severity-aware ramp-down**: Different speeds for warning vs critical
  - New `_get_rampdown_speed()` method
  - Warning: uses `fast_rampdown_speed` (0.50)
  - Critical: uses `critical_rampdown_speed` (0.70)

### Changed
- **Overexposure thresholds lowered** for earlier detection:
  - Trigger: 180 → 150 (warning), 170 (critical)
  - Clear: 150 → 130
  - Clipped pixels: 10% → 5% (warning), 10% (critical)
  - Clear clipped: 5% → 3%

### Fixed
- **Re-enabled EV Safety Clamp** on Kringelen camera
  - Was accidentally disabled (`ev_safety_clamp_enabled: false`)
  - Now properly enabled to prevent brightness jumps at auto→manual transition
  - This was the main cause of severe bright bands in transitions

### Documentation
- Added `workLogs/2026-01-08.md` with detailed session notes
- Updated `docs/TRANSITION_TUNING_LOG.md` with new adjustment details

## [1.0.5] - 2025-12-25

### Added
- **Fast overexposure ramp-down**: Automatic faster exposure reduction when overexposure detected
  - Triggers when brightness > 180 or > 10% clipped pixels
  - Uses 3x faster interpolation speed (0.30 vs 0.10) to quickly reduce exposure
  - Configurable via `fast_rampdown_speed` in config.yml
  - Prevents "light flash" at dawn when 20s exposure stays on too long

- **Configurable reference_lux**: Per-camera brightness tuning
  - New config option `adaptive_timelapse.reference_lux` (default: 3.8)
  - Higher values = brighter images, lower = darker
  - Allows tuning each camera independently based on scene/sensor

- **FFMPEG deflicker filter**: Smooths exposure transitions in rendered videos
  - Filter: `deflicker=mode=pm:size=10` (Predictive Mean, 10-frame window)
  - Configurable via `video.deflicker` and `video.deflicker_size`
  - Eliminates remaining brightness flicker in final timelapse

- **Enhanced make_timelapse.py parameters**:
  - `--start-date` and `--end-date` for specific date ranges
  - `--today` flag for same-day timelapses
  - Config-based default times (`default_start_time`, `default_end_time`)
  - Improved filename format with times to avoid overwrites:
    - Same day: `project_2025-12-25_0700-1500.mp4`
    - Multi-day: `project_2025-12-24_0500_to_2025-12-25_0500.mp4`

- **Calculated lux passed to overlay**: Overlay now shows accurate calculated lux
  - Previously showed camera's unreliable metadata estimate
  - Fixed "lux: 400 at night" issue on some cameras

### Changed
- Default timelapse time range now 05:00 to 05:00 (configurable in config.yml)
- reference_lux default changed from 2.5 to 3.8 for brighter daytime images

### Fixed
- Initialization order bug where `_fast_rampdown_speed` was set before config loaded
- **daily_timelapse.py file search**: Now searches recursively in date-organized subdirectories
  - Previously failed to find videos in `/var/www/html/videos/YYYY/MM/` structure
  - Both video and keogram file finding now use `**/` glob pattern
- **Keogram overlay crop**: Increased from 5% to 7% (108px → 151px at 4K)
  - 5% wasn't enough to fully remove 2-line overlay bar with padding
  - Updated defaults in `create_keogram.py` and `make_timelapse.py`

### Documentation
- Added `docs/changelog_2025-12-25.md` with detailed session notes
- Updated TIMELAPSE_VIDEO.md with new parameters and deflicker filter
- Updated TRANSITION_SMOOTHING.md with overexposure detection

## [1.0.4] - 2025-12-23

### Added
- **Diagnostic metadata**: Each captured frame now includes comprehensive diagnostic data
  - Mode information (day/night/transition)
  - Raw vs smoothed lux values
  - Target vs interpolated exposure/gain (shows smoothing in action)
  - Transition position (0-1) when in transition mode
  - Hysteresis state tracking
- **Image brightness analysis**: Automatic analysis of captured images
  - Mean/median brightness (0-255)
  - Brightness percentiles (5th, 25th, 75th, 95th)
  - Underexposed/overexposed pixel percentages
  - Helps diagnose exposure issues

### Fixed
- **Continuous exposure calculation**: Exposure now adjusts continuously with lux
  - Formula: `exposure = 20 / lux` (inverse relationship)
  - Previously used threshold-based fixed values causing sudden jumps
  - At lux 400: target is now ~50ms instead of fixed 10ms
  - Prevents dark images during cloudy winter days

### Documentation
- Updated `TRANSITION_SMOOTHING.md` with diagnostic metadata section
- Updated `ADAPTIVE_TIMELAPSE_FLOW.md` with diagnostic information
- Added debugging commands for exposure analysis

## [1.0.3] - 2025-12-23

### Added
- **Date-organized video output**: Videos now saved to `YYYY/MM/` subdirectories
  - New config option `video.organize_by_date: true`
  - New config option `video.date_format: "%Y/%m"`
  - Example: `/var/www/html/videos/2025/12/kringelen_nord_daily_2025-12-23.mp4`
- **Memory-optimized encoding**: Prevents OOM kills on 4K video encoding
  - New config option `video.codec.preset: "ultrafast"`
  - New config option `video.codec.threads: 2`
  - Safe for Raspberry Pi with 4GB RAM encoding 4K video

### Changed
- **Service timeout**: Changed from 10 minutes to `infinity`
  - Prevents ffmpeg being killed mid-encode
  - Videos now complete regardless of encoding time
- **Default video directory**: Now `/var/www/html/videos` (absolute path)
- **Default CRF**: Changed from 20 to 23 for smaller file sizes

### Fixed
- **Video corruption**: Fixed "moov atom not found" errors
  - Caused by systemd killing ffmpeg after 10-minute timeout
  - Now uses unlimited timeout for complete encoding
- **OOM kills during 4K encoding**: Fixed by using ultrafast preset and 2 threads
  - Previously ffmpeg used too much RAM and was killed by OOM killer

### Documentation
- Updated `TIMELAPSE_VIDEO.md` with new configuration options
- Updated `DAILY_VIDEO.md` with performance notes and troubleshooting
- Added OOM troubleshooting guide
- Added memory usage guidance for 4K encoding

## [1.0.0] - 2025-11-09

### 🎉 First Stable Release!

Raspilapse v1.0.0 is production-ready for year-long operation.

### Added
- **Systemd Cleanup Service**: Automatic old image deletion
  - `raspilapse-cleanup.timer` runs daily at 01:00 AM
  - Configurable retention period (default 7 days)
  - Prevents disk from filling up
  - Logs cleanup statistics
- **Daily Video Generation**: Automated timelapse compilation
  - `raspilapse-daily-video.timer` runs at 00:04 AM
  - Creates videos from previous day's images
  - H.264 encoding with configurable quality
- **Weather Data Integration**: Netatmo API support
  - Fetch outdoor temperature, humidity, wind, rain
  - Display in image overlays
  - Configurable endpoint and caching
- **Analysis Tools**: Beautiful graphs and Excel export
  - Dark-themed lux analysis graphs
  - Exposure, gain, temperature tracking
  - Excel export with statistics and hourly averages
- **Status Display**: Colored terminal status output
  - Service status monitoring
  - Configuration summary
  - Recent captures with timing
  - Average interval calculation
- **Comprehensive Documentation** (18 guides):
  - `SERVICES_OVERVIEW.md` - Complete systemd reference
  - `LONG_TERM_STABILITY.md` - Year-long operation guide
  - `MONITORING_SETUP.md` - Monitoring and alerting
  - `YEAR_LONG_CHECKLIST.md` - Monthly maintenance
  - `SETUP_COMPLETE.md` - Post-installation summary
- **Monitoring Scripts**: Automated health checks
  - Disk space monitoring
  - Service health verification
  - Capture rate tracking

### Changed
- **Project Structure Reorganization** 🏗️
  - All documentation moved to `docs/` folder
  - All scripts consolidated in `scripts/` folder
  - Clean root directory (only essential config files)
  - Professional, maintainable structure
- **Script Paths** (Breaking Change):
  - `install_service.sh` → `scripts/install.sh`
  - `uninstall_service.sh` → `scripts/uninstall.sh`
  - `test.sh` → `scripts/test.sh`
- **Version**: Updated from 0.9.0-beta to 1.0.0
- **README**: Updated with new paths and structure

### Fixed
- Metadata accumulation in `metadata/` folder
  - Test shots now use fixed filenames (overwritten)
  - No more thousands of test metadata files
- Documentation references throughout project
- Service file locations and references

### Removed
- Obsolete test scripts from root directory
- Duplicate service files
- Outdated documentation fragments

### Production Ready
- ✅ **221 passing tests** (1 skipped)
- ✅ **No memory leaks** - Stable at ~150MB RSS
- ✅ **Low CPU usage** - 4% average
- ✅ **Tested for days** of continuous operation
- ✅ **Auto-recovery** from failures
- ✅ **Disk space management** built-in
- ✅ **Year-long stability** verified

### Migration from 0.9.0-beta
1. Pull latest changes: `git pull origin main`
2. Update service: `sudo systemctl stop raspilapse && ./scripts/install.sh`
3. Documentation moved to `docs/` folder
4. No configuration changes required

See the v1.0.0 release notes on GitHub for complete release details.

---

## [0.9.0-beta] - 2025-11-05

### Added
- **Image Overlay System**: Professional text overlays with camera settings and metadata
  - Configurable positioning (corners, top-bar, custom)
  - Localized datetime formatting (Norwegian, English, etc.)
  - Gradient transparent backgrounds
  - Resolution-independent scaling
  - Color gains tuple display
  - Standalone `apply_overlay.py` script for batch processing
- **Web Integration**: Automatic symlink to latest captured image
  - Configurable symlink path for web servers
  - Perfect for live status pages
- **Enhanced Testing**: Comprehensive test suite with 64 unit tests
  - Overlay system tests
  - Adaptive timelapse tests
  - Symlink functionality tests
  - Full CI/CD integration
- **Project Metadata**:
  - MIT License file
  - Version tracking system
  - pyproject.toml for modern packaging
  - Enhanced README with badges

### Changed
- Overlay now reads configuration dynamically instead of hardcoding values
- Improved error handling for symlink creation
- Better logging for overlay operations

### Fixed
- Black code formatting across all files
- CI pipeline dependencies (added Pillow to requirements.txt)
- Import errors in test modules

## [0.8.0-beta] - 2025-11-04

### Added
- Image overlay prototype with basic text rendering
- Font loading with fallback options
- Background transparency support

## [0.7.0-beta] - 2025-11-03

### Added
- **Adaptive Timelapse**: Automatic exposure adjustment based on ambient light
  - Day mode: Optimized for bright sunlight
  - Night mode: Long exposures up to 20 seconds for stars/aurora
  - Transition mode: Smooth adjustment during dawn/dusk
  - Test shot system for light measurement
- Lux calculation from camera metadata
- Light mode detection with configurable thresholds

### Changed
- Filename patterns now support timestamp formatting
- Test shots stored in metadata/ folder (overwritten, not accumulated)
- Project structure reorganization

## [0.6.0-beta] - 2025-11-02

### Added
- **Long Exposure Optimization**: Dramatically improved capture speed
  - FrameDurationLimits configuration for proper frame period
  - Buffer management (buffer_count=3, queue=False)
  - Non-blocking metadata capture with capture_request()
  - Fast camera shutdown between configuration changes
  - AWB locking for night mode
- Comprehensive documentation in CLAUDE.md

### Changed
- Capture time for 20s exposures reduced from 99-124s to 18-20s (5-7x faster!)
- Camera now properly closes between test shots and timelapse captures

### Fixed
- Long exposure blocking delays
- Camera state conflicts with multiple instances
- Auto white balance slowdown during long exposures

## [0.5.0-beta] - 2025-11-01

### Added
- **Comprehensive Logging System**
  - Configurable log levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)
  - Automatic log rotation based on file size
  - Both console and file output
  - Script-specific log files
- Metadata capture with every image
- Detailed error tracking and debugging

### Changed
- Improved error messages throughout the codebase
- Better structured logging configuration

## [0.4.0-beta] - 2025-10-30

### Added
- Initial public beta release
- YAML-based configuration system
- Camera configuration module
- Image capture module
- Basic timelapse functionality
- Support for Camera V3 and V2
- Multiple resolution support
- Image transforms (flip/rotate)
- Camera controls (exposure, gain, white balance)
- Test suite with pytest
- CI/CD with GitHub Actions

### Documentation
- Installation guide (INSTALL.md)
- Usage guide (USAGE.md)
- Technical reference (CLAUDE.md)
- README with examples

---

## Version Numbering

This project follows [Semantic Versioning](https://semver.org/):
- **0.x.x-beta**: Beta releases (current)
- **1.0.0**: First stable release
- **MAJOR**: Incompatible API changes
- **MINOR**: New functionality (backwards compatible)
- **PATCH**: Bug fixes (backwards compatible)

---

**[Compare versions on GitHub](https://github.com/ekstremedia/raspilapse/releases)**
