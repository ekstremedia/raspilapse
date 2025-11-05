"""Adaptive timelapse module for Raspilapse.

Automatically adjusts exposure settings based on ambient light conditions.
Perfect for 24/7 timelapses that capture both daylight and nighttime scenes,
including stars and aurora activity.
"""

import os
import sys
import time
import signal
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple
import yaml

# Handle imports for both module and script execution
try:
    from src.logging_config import get_logger
    from src.capture_image import CameraConfig, ImageCapture
except ImportError:
    from logging_config import get_logger
    from capture_image import CameraConfig, ImageCapture

# Initialize logger
logger = get_logger("auto_timelapse")


class LightMode:
    """Light mode enumeration."""

    NIGHT = "night"
    DAY = "day"
    TRANSITION = "transition"


class AdaptiveTimelapse:
    """Handles adaptive timelapse capture with automatic exposure adjustment."""

    def __init__(self, config_path: str = "config/config.yml"):
        """
        Initialize adaptive timelapse.

        Args:
            config_path: Path to configuration file
        """
        self.config_path = config_path
        self.config = self._load_config()
        self.camera_config = CameraConfig(config_path)
        self.running = True
        self.frame_count = 0

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False

    def _load_config(self) -> Dict:
        """Load configuration from YAML file."""
        config_file = Path(self.config_path)
        if not config_file.exists():
            logger.error(f"Configuration file not found: {self.config_path}")
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        try:
            with open(config_file, "r") as f:
                config = yaml.safe_load(f)
                logger.debug("Configuration loaded successfully")
                return config
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse configuration file: {e}")
            raise

    def calculate_lux(self, test_image_path: str, metadata: Dict) -> float:
        """
        Calculate approximate lux from camera metadata and image brightness.

        This method analyzes the actual image brightness rather than just
        relying on camera metadata, which can be misleading due to auto-exposure.

        Args:
            test_image_path: Path to test shot image
            metadata: Camera metadata from capture

        Returns:
            Estimated lux value
        """
        exposure_time = metadata.get("ExposureTime", 10000)  # microseconds
        analogue_gain = metadata.get("AnalogueGain", 1.0)

        # Convert exposure time to seconds
        exposure_seconds = exposure_time / 1_000_000

        # Analyze image brightness
        try:
            from PIL import Image
            import numpy as np

            # Open image and convert to grayscale
            img = Image.open(test_image_path)
            img_gray = img.convert("L")  # Convert to grayscale
            img_array = np.array(img_gray)

            # Calculate mean brightness (0-255)
            mean_brightness = np.mean(img_array)

            # Calculate lux based on brightness and camera settings
            # The brighter the image with less exposure time/gain, the more ambient light
            # Formula: lux = (mean_brightness / 128) * (1 / exposure_seconds) * (1 / gain) * calibration_factor
            calibration_factor = 100.0

            if exposure_seconds > 0 and analogue_gain > 0:
                # Normalized brightness (0.0 to 2.0, where 1.0 is mid-gray)
                brightness_factor = mean_brightness / 128.0

                lux = (
                    brightness_factor
                    * (1.0 / exposure_seconds)
                    * (1.0 / analogue_gain)
                    * calibration_factor
                )
            else:
                lux = 1000.0  # Very bright

            logger.debug(
                f"Image analysis: brightness={mean_brightness:.1f}/255, "
                f"exposure={exposure_time}µs, gain={analogue_gain:.2f} → lux={lux:.2f}"
            )

        except ImportError:
            # Fall back to metadata-only calculation if PIL not available
            logger.warning("PIL not available, using metadata-only lux calculation")
            if exposure_seconds > 0:
                lux = (100.0 / exposure_seconds) / analogue_gain
            else:
                lux = 1000.0

            logger.debug(
                f"Metadata-only lux: {lux:.2f} (exposure: {exposure_time}µs, gain: {analogue_gain})"
            )

        except Exception as e:
            logger.error(f"Error analyzing image brightness: {e}")
            # Fallback calculation
            lux = 50.0

        return lux

    def determine_mode(self, lux: float) -> str:
        """
        Determine light mode based on lux value.

        Args:
            lux: Calculated lux value

        Returns:
            Light mode (night, day, or transition)
        """
        thresholds = self.config["adaptive_timelapse"]["light_thresholds"]
        night_threshold = thresholds["night"]
        day_threshold = thresholds["day"]

        if lux < night_threshold:
            mode = LightMode.NIGHT
        elif lux > day_threshold:
            mode = LightMode.DAY
        else:
            mode = LightMode.TRANSITION

        logger.info(f"Light level: {lux:.2f} lux → Mode: {mode}")
        return mode

    def get_camera_settings(self, mode: str, lux: float = None) -> Dict:
        """
        Get camera settings for the specified light mode.

        Args:
            mode: Light mode (night, day, or transition)
            lux: Current lux value (used for transition mode)

        Returns:
            Dictionary of camera control settings
        """
        adaptive_config = self.config["adaptive_timelapse"]
        settings = {}

        if mode == LightMode.NIGHT:
            night = adaptive_config["night_mode"]
            # Disable auto-exposure, auto-gain, and auto-white-balance for manual control
            settings["AeEnable"] = 0
            settings["ExposureTime"] = int(night["max_exposure_time"] * 1_000_000)
            settings["AnalogueGain"] = night["analogue_gain"]
            # Lock AWB for long exposures - AWB causes 5x slowdown!
            settings["AwbEnable"] = 0

            if "colour_gains" in night:
                settings["ColourGains"] = tuple(night["colour_gains"])

            logger.info(
                f"Night mode: exposure={night['max_exposure_time']}s, gain={night['analogue_gain']}"
            )

        elif mode == LightMode.DAY:
            day = adaptive_config["day_mode"]

            if "exposure_time" in day:
                # Manual exposure mode
                settings["AeEnable"] = 0
                settings["ExposureTime"] = int(day["exposure_time"] * 1_000_000)
                if "analogue_gain" in day:
                    settings["AnalogueGain"] = day["analogue_gain"]
            else:
                # Auto exposure mode for day (let camera optimize)
                settings["AeEnable"] = 1
                # Don't set AnalogueGain - let auto exposure handle it

            settings["AwbEnable"] = 1 if day.get("awb_enable", True) else 0

            # Apply brightness adjustment if specified
            if "brightness" in day:
                settings["Brightness"] = day["brightness"]

            logger.info(
                f"Day mode: auto_exposure={'on' if settings.get('AeEnable', 1) else 'off'}, gain={'auto' if settings.get('AeEnable', 1) else day.get('analogue_gain', 'auto')}, brightness={day.get('brightness', 0.0)}"
            )

        elif mode == LightMode.TRANSITION:
            transition = adaptive_config["transition_mode"]
            thresholds = adaptive_config["light_thresholds"]

            # Disable auto-exposure for manual control
            settings["AeEnable"] = 0

            if transition.get("smooth_transition", True) and lux is not None:
                # Interpolate gain and exposure based on lux value
                night_threshold = thresholds["night"]
                day_threshold = thresholds["day"]
                lux_range = day_threshold - night_threshold

                # Calculate position in transition range (0.0 to 1.0)
                position = (lux - night_threshold) / lux_range
                position = max(0.0, min(1.0, position))

                # Interpolate gain
                gain_min = transition["analogue_gain_min"]
                gain_max = transition["analogue_gain_max"]
                interpolated_gain = gain_max - (position * (gain_max - gain_min))

                # Interpolate exposure time (from night max to shorter exposure)
                night_exposure = adaptive_config["night_mode"]["max_exposure_time"]
                day_exposure = 0.05  # 50ms for transition->day
                interpolated_exposure = night_exposure - (
                    position * (night_exposure - day_exposure)
                )

                settings["ExposureTime"] = int(interpolated_exposure * 1_000_000)
                settings["AnalogueGain"] = interpolated_gain
                settings["AwbEnable"] = 1

                logger.info(
                    f"Transition mode: lux={lux:.2f}, exposure={interpolated_exposure:.2f}s, gain={interpolated_gain:.2f}"
                )
            else:
                # Use middle values
                settings["ExposureTime"] = int(5.0 * 1_000_000)  # 5 seconds
                settings["AnalogueGain"] = transition["analogue_gain_max"]
                settings["AwbEnable"] = 1

        return settings

    def take_test_shot(self) -> Tuple[str, Dict]:
        """
        Take a quick test shot to measure light levels.

        Returns:
            Tuple of (image_path, metadata)
        """
        logger.info("Taking test shot to measure light levels...")

        test_config = self.config["adaptive_timelapse"]["test_shot"]

        # Temporarily modify camera config for test shot
        original_controls = self.camera_config.config["camera"].get("controls", {})

        # Set test shot controls
        self.camera_config.config["camera"]["controls"] = {
            "exposure_time": int(test_config["exposure_time"] * 1_000_000),
            "analogue_gain": test_config["analogue_gain"],
            "awb_enable": True,
        }

        # Create temporary output directory for test shots
        test_dir = Path("test_shots")
        test_dir.mkdir(exist_ok=True)

        # Capture test image
        with ImageCapture(self.camera_config) as capture:
            test_path = test_dir / f"test_{datetime.now():%Y%m%d_%H%M%S}.jpg"
            image_path, metadata_path = capture.capture(str(test_path))

            # Read metadata
            if metadata_path and Path(metadata_path).exists():
                import json

                with open(metadata_path, "r") as f:
                    metadata = json.load(f)
            else:
                metadata = {}

        # Restore original controls
        self.camera_config.config["camera"]["controls"] = original_controls

        logger.debug(f"Test shot saved: {image_path}")
        return image_path, metadata

    def capture_frame(
        self, capture: ImageCapture, mode: str
    ) -> Tuple[str, Optional[str]]:
        """
        Capture a single frame with the camera's current settings.

        Args:
            capture: ImageCapture instance with initialized camera
            mode: Light mode

        Returns:
            Tuple of (image_path, metadata_path)
        """
        logger.info(f"Capturing frame #{self.frame_count} in {mode} mode...")

        # Capture the image (controls were set during initialization)
        image_path, metadata_path = capture.capture()

        self.frame_count += 1
        return image_path, metadata_path

    def _close_camera_fast(self, capture: ImageCapture, last_mode: str):
        """
        Close camera properly.

        Args:
            capture: ImageCapture instance to close
            last_mode: Last light mode used (for logging)
        """
        if capture is None or capture.picam2 is None:
            return

        try:
            # Close the camera
            capture.close()
            logger.debug("Camera closed successfully")

        except Exception as e:
            logger.error(f"Error during close: {e}")

    def run(self, test_mode: bool = False):
        """Run the adaptive timelapse capture loop.

        Args:
            test_mode: If True, capture one image then exit
        """
        adaptive_config = self.config["adaptive_timelapse"]

        if not adaptive_config.get("enabled", True):
            logger.warning("Adaptive timelapse is disabled in configuration")
            return

        interval = adaptive_config["interval"]
        num_frames = 1 if test_mode else adaptive_config["num_frames"]

        logger.info("=== Adaptive Timelapse Started ===")
        logger.info(f"Interval: {interval} seconds")
        logger.info(f"Frames: {'unlimited' if num_frames == 0 else num_frames}")

        # Initialize camera once at the start
        capture = None
        last_mode = None

        try:
            while self.running:
                loop_start = time.time()

                # Check if we've reached the frame limit
                if num_frames > 0 and self.frame_count >= num_frames:
                    logger.info(f"Reached frame limit: {num_frames}")
                    break

                # CRITICAL: Close camera before taking test shot to avoid "Camera in Running state" error
                # Test shot uses its own context-managed camera instance
                if capture is not None and adaptive_config["test_shot"]["enabled"]:
                    logger.debug("Closing camera before test shot...")
                    self._close_camera_fast(capture, last_mode)
                    capture = None
                    last_mode = None

                # Take test shot if enabled
                if adaptive_config["test_shot"]["enabled"]:
                    try:
                        test_image_path, test_metadata = self.take_test_shot()

                        # Calculate lux from test shot
                        lux = self.calculate_lux(test_image_path, test_metadata)

                        # Determine mode
                        mode = self.determine_mode(lux)

                        # Get settings for this mode
                        settings = self.get_camera_settings(mode, lux)

                    except Exception as e:
                        logger.error(f"Test shot failed: {e}")
                        # Fall back to day mode
                        mode = LightMode.DAY
                        settings = self.get_camera_settings(mode)
                else:
                    # No test shot, use day mode
                    mode = LightMode.DAY
                    settings = self.get_camera_settings(mode)

                # Initialize camera on first frame or if it was closed
                if capture is None:
                    logger.info("Initializing camera for timelapse...")
                    capture = ImageCapture(self.camera_config)
                    capture.initialize_camera(manual_controls=settings)
                    last_mode = mode

                # Capture actual frame
                try:
                    image_path, metadata_path = self.capture_frame(capture, mode)
                    logger.info(f"Frame captured: {image_path}")

                except Exception as e:
                    logger.error(f"Frame capture failed: {e}", exc_info=True)

                # Calculate time to sleep
                elapsed = time.time() - loop_start
                sleep_time = max(0, interval - elapsed)

                if sleep_time > 0:
                    logger.debug(f"Sleeping for {sleep_time:.1f} seconds...")
                    time.sleep(sleep_time)
                else:
                    logger.warning(
                        f"Capture took longer than interval ({elapsed:.1f}s > {interval}s)"
                    )

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
        finally:
            # Close camera if it was initialized
            if capture is not None:
                logger.info("Closing camera...")
                self._close_camera_fast(capture, last_mode)

            logger.info(
                f"=== Adaptive Timelapse Stopped ({self.frame_count} frames) ==="
            )


def main():
    """CLI entry point for adaptive timelapse."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Adaptive timelapse for Raspberry Pi Camera - automatically adjusts exposure for day/night"
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config/config.yml",
        help="Path to configuration file (default: config/config.yml)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test mode: capture one image then exit",
    )

    args = parser.parse_args()

    logger.info("Starting Raspilapse Adaptive Timelapse")

    try:
        timelapse = AdaptiveTimelapse(args.config)
        if args.test:
            logger.info("TEST MODE: Capturing single image then exiting")
            timelapse.run(test_mode=True)
        else:
            timelapse.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
