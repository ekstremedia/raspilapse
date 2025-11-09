#!/usr/bin/env python3
"""Quick test script to verify weather data fetching."""

import sys
from pathlib import Path
import yaml

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from weather import WeatherData

# Load config
with open("config/config.yml", "r") as f:
    config = yaml.safe_load(f)

print("=" * 60)
print("WEATHER DATA FETCH TEST")
print("=" * 60)

# Initialize weather fetcher
weather = WeatherData(config)

print(f"\nWeather enabled: {weather.enabled}")
print(f"Endpoint: {config.get('weather', {}).get('endpoint')}")
print(f"Cache duration: {weather.cache_duration}")

# Try to fetch weather data
print("\nFetching weather data...")
data = weather.get_weather_data()

if data:
    print("\n✅ SUCCESS! Weather data fetched:")
    print(f"  Temperature: {data.get('temperature')}°C")
    print(f"  Humidity: {data.get('humidity')}%")
    print(f"  Wind Speed: {data.get('wind_speed')} km/h")
    print(f"  Wind Gust: {data.get('wind_gust')} km/h")
    print(f"  Wind Direction: {data.get('wind_angle')}°")
    print(f"  Rain: {data.get('rain')} mm")
    print(f"  Rain 1h: {data.get('rain_1h')} mm")
    print(f"  Rain 24h: {data.get('rain_24h')} mm")
    print(f"  Pressure: {data.get('pressure')} hPa")

    print("\nFormatted for overlay:")
    print(f"  Temp: {weather._format_temperature(data.get('temperature'))}")
    print(f"  Humidity: {weather._format_humidity(data.get('humidity'))}")
    print(f"  Wind: {weather._format_wind(data.get('wind_speed'), data.get('wind_gust'))}")
    print(f"  Rain 24h: {weather._format_rain(data.get('rain_24h'))}")
else:
    print("\n❌ FAILED: Could not fetch weather data")
    print("Check:")
    print("  1. Is the endpoint URL correct?")
    print("  2. Is the server reachable?")
    print("  3. Check logs/overlay.log for errors")

print("\n" + "=" * 60)
