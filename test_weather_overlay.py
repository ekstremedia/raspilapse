#!/usr/bin/env python3
"""Test weather overlay integration."""

import sys
from pathlib import Path
import yaml
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from overlay import ImageOverlay
from PIL import Image, ImageDraw

# Load config
with open("config/config.yml", "r") as f:
    config = yaml.safe_load(f)

print("=" * 60)
print("WEATHER OVERLAY TEST")
print("=" * 60)

# Create a test image
test_img_path = Path("test_weather_overlay.jpg")
img = Image.new("RGB", (1920, 1080), color=(50, 50, 50))
draw = ImageDraw.Draw(img)
draw.text((960, 540), "Test Image", fill=(255, 255, 255), anchor="mm")
img.save(test_img_path)

print(f"\nCreated test image: {test_img_path}")

# Initialize overlay
overlay = ImageOverlay(config)
print(f"Overlay enabled: {overlay.enabled}")

# Prepare test metadata
metadata = {
    "ExposureTime": 10000,  # 10ms
    "AnalogueGain": 1.5,
    "Lux": 150.0,
    "ColourGains": [1.8, 1.5],
    "SensorTemperature": 22.5,
    "resolution": [1920, 1080],
    "capture_timestamp": datetime.now().isoformat(),
}

print("\nApplying overlay with weather data...")

# Get overlay data to see what values are being used
data = overlay._prepare_overlay_data(metadata, mode="day")

print("\nWeather values in overlay data:")
print(f"  temp: {data.get('temp')}")
print(f"  humidity: {data.get('humidity')}")
print(f"  wind: {data.get('wind')}")
print(f"  rain_24h: {data.get('rain_24h')}")

# Apply overlay
result_path = overlay.apply_overlay(test_img_path, metadata, mode="day")

if result_path:
    print(f"\n✅ SUCCESS! Overlay applied to: {result_path}")
    print(f"\nOpen the image to verify weather data is displayed:")
    print(f"  {result_path}")
else:
    print("\n❌ FAILED: Could not apply overlay")

print("\n" + "=" * 60)
