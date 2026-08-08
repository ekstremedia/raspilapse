# Weather

Raspilapse can pull readings from a Netatmo weather station and put them in the
overlay: temperature, humidity, wind, rain and pressure.

## Configuration

```yaml
weather:
  enabled: true
  endpoint: "https://example.com/api/netatmo/stations/<your-station-id>"

  # How long a successful reading is reused before fetching again.
  cache_duration: 300

  # After a failed fetch, wait before trying again, doubling on each
  # consecutive failure up to this cap.
  max_backoff_seconds: 900

  timeout: 5
```

Then reference the values from any overlay slot — see [OVERLAY.md](OVERLAY.md)
for the four-slot content model:

```yaml
overlay:
  content:
    line_1_right: "{temp}, humidity: {humidity}, wind: {wind} {wind_dir}, rain: {rain}"
```

## Placeholders

| Placeholder | Example | Notes |
|---|---|---|
| `{temp}` | ` 12.0°C` | fixed width, so the bar does not jitter |
| `{temperature_outdoor}` | ` 12.0°C` | alias of `{temp}` |
| `{humidity}` | ` 93%` | |
| `{wind}` | ` 0.6 m/s (gust  1.4)` | gust shown only when higher than the mean |
| `{wind_speed}` `{wind_gust}` | ` 0.6 m/s` | separately, if you prefer |
| `{wind_dir}` | `NE` | compass point derived from the angle |
| `{rain}` `{rain_1h}` `{rain_24h}` | ` 0.0 mm` | now, last hour, last 24 h |
| `{pressure}` | `1013 hPa` | |

> `{temp}` is the outdoor reading. `{temperature}` is the **camera sensor**
> temperature and comes from capture metadata, not from here.

Values are fixed-width on purpose: a varying width shifts everything to its
right from frame to frame, which is very visible in a timelapse.

Wind arrives from Netatmo in km/h and is converted to m/s.

## What the endpoint has to return

JSON with a `modules` array, either at the root or nested under `data`:

```json
{
  "modules": [
    {"type": "Outdoor Module", "measurements": {"Temperature": -0.2, "Humidity": 82}},
    {"type": "Wind Gauge",     "measurements": {"WindStrength": 12, "GustStrength": 18, "WindAngle": 225}},
    {"type": "Rain Gauge",     "measurements": {"Rain": 0, "sum_rain_1": 0.5, "sum_rain_24": 2.3}},
    {"type": "Indoor Module",  "measurements": {"Pressure": 1013}}
  ],
  "last_updated": "2026-07-27T01:12:00Z"
}
```

Recognised module types: **Outdoor Module** (temperature, humidity), **Wind
Gauge**, **Rain Gauge**, and **Indoor Module** (the only source of pressure).
A missing module renders its fields as `N/A` rather than dropping them, so the
bar keeps its width.

Raspilapse does not talk to Netatmo directly — it expects a URL you control
that returns this shape. Netatmo's own API needs OAuth, which is why the
indirection exists.

## What happens when a fetch fails

**The last good reading keeps being displayed.** This is deliberate: a value
that blinks to `-` and back every few minutes is far more distracting in a
timelapse than one that is a few minutes stale.

`-` appears only when nothing has *ever* been fetched successfully — a fresh
install, or a wrong endpoint.

Failures back off. The first waits `cache_duration` (or 30 s if that is
shorter), the next twice that, and
so on up to `max_backoff_seconds`. Without that, a DNS outage meant one request
and one error line per capture indefinitely; a single outage once produced
72,536 identical lines in one log file. The error is logged once and then
suppressed until the message changes or ten minutes pass, at which point you
get a summary with the suppressed count.

The cache is shared per endpoint across the whole process. It has to be: the
overlay — and with it the weather fetcher — is rebuilt twice per capture cycle,
so a per-instance cache started empty every time and `cache_duration` never
took effect at all.

## Checking it

```bash
tail logs/weather.log
```

Successful fetches log at DEBUG, so set `logging.level: DEBUG` to watch them.
Failures log at WARNING and are visible at the default level.

To test the endpoint independently:

```bash
curl -s "$(python3 -c "
import yaml; print(yaml.safe_load(open('config/config.yml'))['weather']['endpoint'])")" | head -40
```

## When it doesn't work

**All fields show `-`.** Nothing has ever been fetched. Check
`weather.enabled`, check the endpoint responds, and read `logs/weather.log`.

**Values are hours old.** Fetching is failing and stale data is being served,
by design. The log has the reason; note that repeats are suppressed, so look
for the first occurrence and the periodic summary rather than a recent line.

**Only some fields populate.** That module type is missing from the response,
or named differently. Compare the `curl` output against the module types above.

**Nothing appears in the image at all.** The placeholders are probably in a
config key the overlay does not read. Only `line_1_left`, `line_1_right`,
`line_2_left` and `line_2_right` are used — see [OVERLAY.md](OVERLAY.md).
