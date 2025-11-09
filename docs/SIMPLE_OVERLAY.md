# Simplified Overlay Configuration

## Overview

The overlay system now uses a clean, simple 2-line structure with straightforward naming. No more nested sections or complex hierarchies - just 4 clear fields for your content.

## Configuration Structure

```yaml
overlay:
  content:
    # Line 1 - Left side
    line_1_left: "{camera_name}"

    # Line 1 - Right side
    line_1_right: "Exp: {exposure} {iso} Lux: {lux} | {temp} Fuktighet: {humidity} Vind:{wind}"

    # Line 2 - Left side
    line_2_left: "{date} {time}"

    # Line 2 - Right side
    line_2_right: "Gain: {gain} | Sensor: {temperature}°C | Color: {color_gains}"
```

## Layout

```
Line 1: [line_1_left]                    [line_1_right]
Line 2: [line_2_left]                    [line_2_right]
```

- **Line 1 Left**: Typically your camera name (bold font)
- **Line 1 Right**: Whatever info you want - camera settings, weather, etc.
- **Line 2 Left**: Typically date/time (automatically localized if configured)
- **Line 2 Right**: Any additional details you want to show

## Customization Examples

### Minimal (just camera name and time)
```yaml
content:
  line_1_left: "{camera_name}"
  line_1_right: ""
  line_2_left: "{date} {time}"
  line_2_right: ""
```

### Technical Focus
```yaml
content:
  line_1_left: "{camera_name}"
  line_1_right: "Mode: {mode} | Exp: {exposure} | ISO: {iso}"
  line_2_left: "{date} {time}"
  line_2_right: "Lux: {lux} | Gain: {gain} | Temp: {temperature}°C"
```

### Weather Focus
```yaml
content:
  line_1_left: "{camera_name}"
  line_1_right: "{temp} {humidity} Wind: {wind_speed} m/s"
  line_2_left: "{date} {time}"
  line_2_right: "Rain 24h: {rain_24h} | Pressure: {pressure}"
```

### Ultra Compact
```yaml
content:
  line_1_left: "{camera_name}"
  line_1_right: "{exposure} {iso}"
  line_2_left: "{time}"
  line_2_right: "{temp}"
```

## Available Variables

You can use any of these variables in any line:

**Camera Info:**
- `{camera_name}` - Your configured camera name
- `{mode}` - Light mode (Day/Night/Transition)
- `{exposure}` - Human-readable exposure (e.g., "2.0s", "1/500s")
- `{iso}` - ISO equivalent (e.g., "ISO 250")
- `{lux}` - Calculated light level
- `{gain}` - Raw analogue gain value
- `{temperature}` - Sensor temperature in °C
- `{color_gains}` - White balance gains

**Date/Time:**
- `{date}` - Current date
- `{time}` - Current time
- `{datetime}` - Full datetime
- `{datetime_localized}` - Localized format

**Weather (if enabled):**
- `{temp}` - Outdoor temperature
- `{humidity}` - Humidity percentage
- `{wind}` - Wind with gust
- `{wind_speed}` - Wind speed only
- `{wind_dir}` - Wind direction (N, NE, etc.)
- `{rain}` - Current rain
- `{rain_24h}` - 24-hour rain total
- `{pressure}` - Atmospheric pressure

## Benefits of Simplified Structure

1. **Clear and intuitive** - Just 4 fields, no confusion
2. **Flexible** - Put any variables in any position
3. **No nested complexity** - No more enabled/disabled subsections
4. **Easy to customize** - Change what you want, leave empty what you don't
5. **Consistent naming** - line_1_left, line_1_right, line_2_left, line_2_right

## Migration from Old Structure

The old structure had:
```yaml
content:
  main:
    - "{camera_name}"
    - "{date} {time}"
  camera_settings:
    enabled: true
    lines:
      - "..."
  weather:
    enabled: true
    lines:
      - "..."
  details:
    enabled: true
    lines:
      - "..."
```

The new structure is simply:
```yaml
content:
  line_1_left: "{camera_name}"
  line_1_right: "your content here"
  line_2_left: "{date} {time}"
  line_2_right: "your content here"
```

## Notes

- Leave a field empty (`""`) if you don't want content there
- Line 1 left uses bold font, others use regular font
- The date/time on line 2 left is automatically localized if you use `{date} {time}`
- All fields support all variables - mix and match as you like
- For corner positions (not top-bar), lines are stacked vertically