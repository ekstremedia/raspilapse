# Weather Data Integration

Raspilapse supports displaying real-time weather data from Netatmo weather stations in the image overlay.

## Features

- Fetch weather data from Netatmo API endpoint
- Display temperature, humidity, wind, rain, and pressure
- Smart caching (5-minute default, configurable)
- Automatic fallback to "-" for stale/unavailable data
- Seamless integration with existing overlay system
- Wind speed conversion (km/h to m/s)
- Compass wind direction (N, NE, E, SE, S, SW, W, NW)

## Configuration

### 1. Enable Weather Data Fetching

Edit `config/config.yml`:

```yaml
weather:
  # Enable/disable weather data fetching
  enabled: true

  # Netatmo API endpoint URL
  endpoint: "http://your-server.local/api/netatmo/stations/your-station-id"

  # Cache duration in seconds (how long to keep weather data before refreshing)
  cache_duration: 300  # 5 minutes

  # Request timeout in seconds
  timeout: 5
```

### 2. Enable Weather Overlay

In the same config file, enable the weather section in the overlay:

```yaml
overlay:
  enabled: true

  content:
    # ... existing sections ...

    # Weather info (requires weather.enabled = true)
    weather:
      enabled: true
      lines:
        - "Temp: {temp} | Humidity: {humidity} | Wind: {wind} | Rain: {rain_24h}"
```

## Available Weather Variables

You can use these placeholders in your overlay templates:

| Variable | Description | Example Output |
|----------|-------------|----------------|
| `{temp}` or `{temperature_outdoor}` | Outdoor temperature | `-0.2°C` |
| `{humidity}` | Outdoor humidity | `82%` |
| `{wind}` | Wind speed with gust | `5.0 m/s (gust 7.2)` |
| `{wind_speed}` | Wind speed only | `5.0 m/s` |
| `{wind_gust}` | Wind gust speed | `7.2 m/s` |
| `{wind_dir}` | Wind direction | `S` (South) |
| `{rain}` | Current rain | `0.0 mm` |
| `{rain_1h}` | Rain in last hour | `0.5 mm` |
| `{rain_24h}` | Rain in last 24 hours | `2.3 mm` |
| `{pressure}` | Atmospheric pressure | `1012 hPa` |

## How It Works

### Data Flow

1. **Fetch**: On each image capture, Raspilapse checks if cached weather data is still valid (<5 minutes old)
2. **Cache**: If valid, use cached data. Otherwise, fetch fresh data from the API
3. **Parse**: Extract outdoor temperature, wind, rain, and pressure data from Netatmo modules
4. **Display**: Format values and insert into overlay template
5. **Stale Data**: If cache expires and API fetch fails, show "-" for all values

### Caching Behavior

- **Fresh data** (< 5 minutes old): Display cached values
- **Stale data** (> 5 minutes old):
  - Try to fetch fresh data
  - If fetch succeeds: Update cache and display new values
  - If fetch fails: Show "-" for all weather values (prevents showing outdated data)

### Netatmo Module Detection

The weather module automatically detects and extracts data from these Netatmo module types:

- **Outdoor Module**: Temperature, Humidity
- **Wind Gauge**: Wind speed, gust, direction
- **Rain Gauge**: Current rain, 1h rain, 24h rain
- **Indoor Module**: Atmospheric pressure (fallback)

## Example Configurations

### Minimal Weather Display

```yaml
overlay:
  content:
    weather:
      enabled: true
      lines:
        - "{temp} | {wind} | {rain_24h}"
```

Output: `-0.2°C | 5.0 m/s (gust 7.2) | 2.3 mm`

### Detailed Weather Display

```yaml
overlay:
  content:
    weather:
      enabled: true
      lines:
        - "Temperature: {temp} | Humidity: {humidity}"
        - "Wind: {wind_speed} from {wind_dir} (gust: {wind_gust})"
        - "Rain: {rain_1h} (1h) | {rain_24h} (24h) | Pressure: {pressure}"
```

Output:
```
Temperature: -0.2°C | Humidity: 82%
Wind: 5.0 m/s from S (gust: 7.2 m/s)
Rain: 0.5 mm (1h) | 2.3 mm (24h) | Pressure: 1012 hPa
```

### Combined Camera + Weather

```yaml
overlay:
  content:
    main:
      - "{camera_name}"
      - "{date} {time}"

    camera_settings:
      enabled: true
      lines:
        - "Mode: {mode} | Exposure: {exposure} | ISO: {iso}"

    weather:
      enabled: true
      lines:
        - "Weather: {temp} | {humidity} | Wind: {wind_dir} {wind_speed}"
```

## API Endpoint Format

Your Netatmo API endpoint should return JSON in this format:

```json
{
  "data": {
    "modules": [
      {
        "type": "Outdoor Module",
        "measurements": {
          "Temperature": -0.2,
          "Humidity": 82
        }
      },
      {
        "type": "Wind Gauge",
        "measurements": {
          "WindStrength": 18,
          "WindAngle": 185,
          "GustStrength": 26
        }
      },
      {
        "type": "Rain Gauge",
        "measurements": {
          "Rain": 0,
          "sum_rain_1": 0.5,
          "sum_rain_24": 2.3
        }
      }
    ]
  }
}
```

## Troubleshooting

### Weather Shows "-" Values

**Possible causes:**
1. Weather is disabled (`weather.enabled: false`)
2. API endpoint is unreachable
3. Cached data is stale (>5 minutes) and refresh failed
4. Invalid JSON response from API

**Check logs:**
```bash
tail -f logs/auto_timelapse.log
```

Look for messages like:
- `Weather data fetcher initialized` (success)
- `Network error fetching weather data` (connection issue)
- `Cache expired (age: 320s, limit: 300s)` (stale data)
- `Weather data is stale and refresh failed, showing '-' values` (API down)

### Slow Image Captures

If captures are slow, check the `weather.timeout` setting:

```yaml
weather:
  timeout: 5  # Reduce to 2-3 seconds if network is fast
```

### API Rate Limiting

To avoid overwhelming your API:

```yaml
weather:
  cache_duration: 600  # 10 minutes (slower updates, fewer requests)
```

## Advanced Usage

### Custom Weather Line Format

You can create custom weather display formats:

```yaml
overlay:
  content:
    weather:
      enabled: true
      lines:
        - "Temp: {temp} | Humidity: {humidity} | Wind: {wind}"
```

### Conditional Weather Display

The weather section only appears if `weather.enabled: true` in the content:

```yaml
overlay:
  content:
    weather:
      enabled: false  # Hide weather, even if data is available
```

## Performance

- **Network overhead**: ~50-200ms per API call (only when cache expires)
- **Memory**: <1KB for cached weather data
- **CPU**: Negligible (JSON parsing + string formatting)

## Security

- Weather data is fetched over HTTP (consider using HTTPS for production)
- No authentication credentials are stored in config
- Endpoint URL should be a trusted local server
- API timeout prevents indefinite hanging (default: 5 seconds)

## Contributing

To add support for additional weather stations:

1. Create a new parser method in `src/weather.py`
2. Add format methods for new data types
3. Update config documentation
4. Add unit tests in `tests/test_weather.py`

Example:
```python
def _parse_acurite_data(self, data: Dict) -> Dict:
    """Parse AccuRite weather station data."""
    # Your parsing logic here
    pass
```
