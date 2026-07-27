# Overlay

Raspilapse burns a two-line information bar into every frame — camera name,
timestamp, exposure settings, and optionally weather, tide, ships and aurora.

The overlay is applied during capture, so it is part of the JPEG rather than
something added afterwards. It therefore also appears in the daily video.

## The content model

Four slots, arranged as two lines of two:

```text
┌────────────────────────────────────────────────────────────────────┐
│ line_1_left                                          line_1_right  │
│ line_2_left                                          line_2_right  │
└────────────────────────────────────────────────────────────────────┘
```

That is the whole model. `overlay.content` accepts exactly these four keys and
nothing else:

```yaml
overlay:
  enabled: true
  content:
    line_1_left:  "{camera_name}"
    line_1_right: "{temp}, humidity: {humidity}, wind: {wind} {wind_dir}"
    line_2_left:  "{date} {time}"
    line_2_right: "Exposure: {exposure}, {iso}, lux: {lux} - CPU: {cpu_temp}"
```

Each value is a template. Anything in `{braces}` is substituted; everything
else is printed literally. An unknown placeholder leaves that slot showing its
raw template rather than aborting the overlay, so a typo costs you one line and
not the whole bar.

## Placeholders

### Camera and capture

| Placeholder | Example |
|---|---|
| `{camera_name}` | from `overlay.camera_name` |
| `{date}` `{time}` `{datetime}` | `2026-07-27`, `01:12`, both |
| `{datetime_localized}` | `mandag, 27 juli 2026 01:12` — see `overlay.datetime` |
| `{exposure}` `{exposure_ms}` `{exposure_us}` | `190.9ms`, `190.9`, `190900` |
| `{iso}` `{gain}` | `ISO 112`, `1.12` |
| `{lux}` | `435.8` |
| `{mode}` | `day`, `night`, `transition` |
| `{night}` | `Yes` / `No` |
| `{wb}` `{wb_gains}` `{color_gains}` | white balance as text and as gains |
| `{temperature}` | **sensor** temperature, not outdoor |
| `{resolution}` | `3840x2160` |
| `{af_mode}` `{lens_position}` `{focus_distance}` | autofocus state, where supported |

### System

`{cpu_temp}` `{cpu_temp_raw}` `{load}` `{load_1min}` `{load_5min}`
`{load_15min}` `{memory}` `{memory_percent}` `{memory_used}` `{memory_free}`
`{memory_total}` `{disk}` `{disk_free}` `{disk_used}` `{disk_total}`
`{disk_percent}` `{uptime}`

### Weather

Requires `weather.enabled`. See [WEATHER.md](WEATHER.md).

`{temp}` `{temperature_outdoor}` `{humidity}` `{wind}` `{wind_speed}`
`{wind_gust}` `{wind_dir}` `{rain}` `{rain_1h}` `{rain_24h}` `{pressure}`

> `{temp}` is the outdoor reading; `{temperature}` is the camera sensor. Two
> deliberately different names for two deliberately different things.

### Tide, ships and aurora

These read JSON files written by a separate service — see the `barentswatch`,
`tide` and `aurora` sections of the config. Leave them disabled unless you run
that service.

`{tide}` `{tide_level}` `{tide_trend}` `{tide_arrow}` `{tide_target}`
`{tide_high_time}` `{tide_high_level}` `{tide_low_time}` `{tide_low_level}`
`{ships}` `{ships_count}` `{ships_moving}`

Tide and aurora also draw directly into the right-hand end of the top bar — a
wave sparkline and a Kp/Bz readout — rather than through placeholders. Ships
render as labelled boxes below the bar.

## Appearance

```yaml
overlay:
  position: "top-bar"        # see below
  margin: 10                 # px from the edge

  font:
    family: "DejaVuSans-Bold.ttf"
    size_ratio: 0.020        # fraction of image height, so it scales
    color: [255, 255, 255, 255]

  background:
    enabled: true
    color: [0, 0, 30, 70]    # RGBA; the bar fades toward the image
    padding: 0.6             # multiple of the font size

  layout:
    line_spacing: 1.2
    bottom_padding_multiplier: 0.7

  datetime:
    localized: true          # weekday and month in your locale
    locale: "nb_NO.UTF-8"    # must be generated on the system
    show_seconds: false
    date_format: "%Y-%m-%d"  # used when localized is false
    time_format: "%H:%M"
```

`size_ratio` is a fraction of image height rather than a pixel size, so the bar
looks the same on 1080p and on 4K. At 0.020 that is about 43 px on 4K.

### Position

`top-bar` is what the example ships and what the four-slot model is designed
for: a full-width band across the top, fading into the image.

The other values place a text block in a corner instead — `top-left`,
`top-right`, `bottom-left`, `bottom-right`, `center`, and `custom` (which uses
`overlay.custom_position.x`/`.y` as percentages). In those modes the four slots
stack as separate lines rather than being laid out left and right.

### Localization

`localized: true` needs the locale generated on the system:

```bash
sudo dpkg-reconfigure locales     # tick nb_NO.UTF-8, or whichever you want
locale -a | grep nb_NO            # confirm
```

Without it the code falls back to English and logs a warning.

## Applying an overlay after the fact

`raspilapse/cli/apply_overlay.py` re-renders onto existing images using their sidecar
metadata JSON:

```bash
python3 -m raspilapse.cli.overlay /var/www/html/images/2026/07/27/*.jpg --output-dir /tmp/out
python3 -m raspilapse.cli.overlay frame.jpg --overwrite
```

Useful for trying a template change without waiting for the next capture.

## When the overlay looks wrong

**Nothing is drawn.** Check `overlay.enabled`, then check you are using the
four `line_N_side` keys — any other key under `content` is silently ignored.

**A slot shows `{something}` literally.** That placeholder does not exist.
Check it against the tables above; the log names it:

```bash
grep "Unknown variable" logs/overlay.log
```

**Weather fields show `-`.** Nothing has ever been fetched successfully. If
they show *stale* values instead, that is deliberate — see
[WEATHER.md](WEATHER.md).

**Dates are in English despite `localized: true`.** The locale is not generated
on the system; see above.

**Font not found.** The family name is resolved against the system font path.
`fc-list | grep DejaVu` shows what is available.

Overlay activity goes to `logs/overlay.log`, most of it at DEBUG — set
`logging.level: DEBUG` while experimenting.
