#!/usr/bin/env python3
"""
Verify weather data is included in overlay
"""
import yaml
from src.capture_image import ImageCapture
from src.overlay import ImageOverlay
from src.weather import WeatherData
import json

print("=" * 60)
print("WEATHER OVERLAY VERIFICATION")
print("=" * 60)

# Load config
with open("config/config.yml", "r") as f:
    config = yaml.safe_load(f)

# Initialize components
print("\n1. Initializing components...")
weather = WeatherData(config)
overlay = ImageOverlay(config)

# For ImageCapture, we need to use CameraConfig
from src.capture_image import CameraConfig
camera_config = CameraConfig("config/config.yml")
capture = ImageCapture(camera_config)

# Get weather data
print("\n2. Fetching weather data...")
weather_data = weather.get_weather_data()
if weather_data:
    print("   ✅ Weather data available:")
    print(f"      Temperature: {weather_data.get('temperature', 'N/A')}°C")
    print(f"      Humidity: {weather_data.get('humidity', 'N/A')}%")
    print(f"      Wind: {weather_data.get('wind_speed', 'N/A')} km/h")
    print(f"      Rain 24h: {weather_data.get('rain_24h', 'N/A')} mm")
else:
    print("   ❌ No weather data available")

# Take a test capture
print("\n3. Taking test capture...")
capture.initialize_camera({
    "exposure_time": 100000,
    "analogue_gain": 1.0,
    "awb_enable": True
})
filename, metadata = capture.capture("verify_overlay.jpg")
capture.close()

if filename and metadata:
    print(f"   ✅ Image captured: {filename}")

    # Apply overlay
    print("\n4. Applying overlay with weather data...")
    overlay.apply_overlay(filename, metadata, mode="day")
    print(f"   ✅ Overlay applied to: {filename}")

    print("\n✅ SUCCESS! Weather overlay should now include:")
    print("   - Line 1: Camera name (left), Camera settings (right)")
    print("   - Line 2: Date/time (left), Debug info (right)")
    print("   - Line 3: Weather data (centered)")
    print(f"\nCheck the image: {filename}")
else:
    print("   ❌ Failed to capture image")

print("\n" + "=" * 60)