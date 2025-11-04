#!/usr/bin/env python3
"""Test script to compare stop/start vs continuous camera for long exposures."""

import time
from pathlib import Path
from src.capture_image import CameraConfig, ImageCapture

def test_stop_start_approach():
    """Test the old code's approach: stop/start camera for each capture."""
    print("=== Testing Stop/Start Approach (like old code) ===\n")

    config = CameraConfig("config/config.yml")

    # Night mode settings
    night_settings = {
        "ExposureTime": 20_000_000,  # 20 seconds
        "AnalogueGain": 2.5,
        "AeEnable": 0,
        "AwbEnable": 1,
    }

    output_dir = Path("test_photos")
    output_dir.mkdir(exist_ok=True)

    # Capture 3 frames with stop/start pattern
    for i in range(3):
        print(f"\n--- Frame {i+1} ---")
        frame_start = time.time()

        # Initialize camera (like old code does each time)
        print("Initializing camera...")
        init_start = time.time()
        capture = ImageCapture(config)
        capture.initialize_camera(manual_controls=night_settings)
        init_time = time.time() - init_start
        print(f"  Camera initialized in {init_time:.1f}s")

        # Capture
        print("Capturing...")
        capture_start = time.time()
        output_path = output_dir / f"stop_start_{i:04d}.jpg"
        image_path, _ = capture.capture(str(output_path))
        capture_time = time.time() - capture_start
        print(f"  Captured in {capture_time:.1f}s")

        # Close camera (like old code does each time)
        print("Closing camera...")
        close_start = time.time()
        capture.close()
        close_time = time.time() - close_start
        print(f"  Closed in {close_time:.1f}s")

        frame_time = time.time() - frame_start
        print(f"Total frame time: {frame_time:.1f}s")
        print(f"  Breakdown: init={init_time:.1f}s, capture={capture_time:.1f}s, close={close_time:.1f}s")

        if i < 2:  # Don't sleep after last frame
            print("Waiting 5 seconds before next frame...")
            time.sleep(5)

    print("\n=== Stop/Start Test Complete ===")


def test_continuous_approach():
    """Test our current approach: keep camera running."""
    print("\n\n=== Testing Continuous Approach (our current code) ===\n")

    config = CameraConfig("config/config.yml")

    # Night mode settings
    night_settings = {
        "ExposureTime": 20_000_000,  # 20 seconds
        "AnalogueGain": 2.5,
        "AeEnable": 0,
        "AwbEnable": 1,
    }

    output_dir = Path("test_photos")
    output_dir.mkdir(exist_ok=True)

    # Initialize camera once
    print("Initializing camera (one time)...")
    init_start = time.time()
    capture = ImageCapture(config)
    capture.initialize_camera(manual_controls=night_settings)
    init_time = time.time() - init_start
    print(f"  Camera initialized in {init_time:.1f}s\n")

    try:
        # Capture 3 frames without reinitializing
        for i in range(3):
            print(f"\n--- Frame {i+1} ---")
            frame_start = time.time()

            print("Capturing...")
            output_path = output_dir / f"continuous_{i:04d}.jpg"
            image_path, _ = capture.capture(str(output_path))

            frame_time = time.time() - frame_start
            print(f"Frame captured in {frame_time:.1f}s")

            if i < 2:  # Don't sleep after last frame
                print("Waiting 5 seconds before next frame...")
                time.sleep(5)

    finally:
        print("\nClosing camera...")
        close_start = time.time()
        capture.close()
        close_time = time.time() - close_start
        print(f"  Closed in {close_time:.1f}s")

    print("\n=== Continuous Test Complete ===")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] == "stop-start":
            test_stop_start_approach()
        elif sys.argv[1] == "continuous":
            test_continuous_approach()
        else:
            print("Usage: python test_stop_start.py [stop-start|continuous]")
            sys.exit(1)
    else:
        # Run both tests
        test_stop_start_approach()
        test_continuous_approach()
