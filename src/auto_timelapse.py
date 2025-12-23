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

        # Transition smoothing state
        self._lux_history: list = []  # Rolling history for EMA
        self._smoothed_lux: float = None  # Exponential moving average of lux
        self._last_mode: str = None  # Previous mode for hysteresis
        self._mode_hold_count: int = 0  # Counter for hysteresis
        self._day_wb_reference: tuple = None  # AWB gains from bright daylight
        self._last_colour_gains: tuple = None  # Previous frame's color gains for smooth transition

        # Load transition smoothing config with defaults
        transition_config = self.config.get("adaptive_timelapse", {}).get("transition_mode", {})
        self._lux_smoothing_factor = transition_config.get("lux_smoothing_factor", 0.3)
        self._hysteresis_frames = transition_config.get("hysteresis_frames", 3)
        self._wb_transition_speed = transition_config.get("wb_transition_speed", 0.15)

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

    def _smooth_lux(self, raw_lux: float) -> float:
        """
        Apply exponential moving average smoothing to lux values.

        This prevents sudden jumps in lux readings from causing mode flips.

        Args:
            raw_lux: Raw calculated lux value

        Returns:
            Smoothed lux value
        """
        if self._smoothed_lux is None:
            # First reading - initialize
            self._smoothed_lux = raw_lux
        else:
            # Exponential moving average: new = alpha * raw + (1 - alpha) * old
            alpha = self._lux_smoothing_factor
            self._smoothed_lux = alpha * raw_lux + (1 - alpha) * self._smoothed_lux

        logger.debug(f"Lux smoothing: raw={raw_lux:.2f} → smoothed={self._smoothed_lux:.2f}")
        return self._smoothed_lux

    def _apply_hysteresis(self, new_mode: str) -> str:
        """
        Apply hysteresis to mode transitions to prevent rapid flipping.

        Mode only changes after N consecutive frames request the same new mode.

        Args:
            new_mode: The mode determined by current lux

        Returns:
            The actual mode to use (may be held at previous)
        """
        if self._last_mode is None:
            # First frame - accept the mode
            self._last_mode = new_mode
            self._mode_hold_count = 0
            return new_mode

        if new_mode == self._last_mode:
            # Same mode - reset counter
            self._mode_hold_count = 0
            return new_mode

        # Different mode requested
        self._mode_hold_count += 1

        if self._mode_hold_count >= self._hysteresis_frames:
            # Enough consecutive frames - accept the change
            logger.info(
                f"Mode transition: {self._last_mode} → {new_mode} "
                f"(after {self._mode_hold_count} frames)"
            )
            self._last_mode = new_mode
            self._mode_hold_count = 0
            return new_mode
        else:
            # Hold at previous mode
            logger.debug(
                f"Hysteresis: holding {self._last_mode}, "
                f"requested {new_mode} ({self._mode_hold_count}/{self._hysteresis_frames})"
            )
            return self._last_mode

    def _interpolate_colour_gains(self, target_gains: tuple, position: float = None) -> tuple:
        """
        Smoothly interpolate colour gains to prevent sudden white balance shifts.

        Uses gradual transition towards target gains rather than instant switching.

        Args:
            target_gains: Target (red, blue) colour gains
            position: Optional transition position (0.0=night, 1.0=day) for
                     calculating intermediate gains between night and day references

        Returns:
            Interpolated colour gains tuple
        """
        if target_gains is None:
            return self._last_colour_gains

        if self._last_colour_gains is None:
            # First frame - accept target gains
            self._last_colour_gains = target_gains
            return target_gains

        # Gradual transition towards target
        speed = self._wb_transition_speed
        new_red = self._last_colour_gains[0] + speed * (
            target_gains[0] - self._last_colour_gains[0]
        )
        new_blue = self._last_colour_gains[1] + speed * (
            target_gains[1] - self._last_colour_gains[1]
        )

        interpolated = (new_red, new_blue)
        self._last_colour_gains = interpolated

        logger.debug(
            f"WB interpolation: target=[{target_gains[0]:.2f}, {target_gains[1]:.2f}] "
            f"→ actual=[{new_red:.2f}, {new_blue:.2f}]"
        )
        return interpolated

    def _update_day_wb_reference(self, metadata: Dict):
        """
        Update day white balance reference from camera's AWB in bright conditions.

        This captures what the camera considers correct WB for daylight,
        which we use to smoothly transition from/to night manual WB.

        Args:
            metadata: Camera metadata containing ColourGains
        """
        colour_gains = metadata.get("ColourGains")
        lux = metadata.get("Lux", 0)

        # Only update reference in bright daylight (>200 lux) with valid gains
        if colour_gains and lux > 200:
            # Validate gains are reasonable (not extreme values)
            if 1.0 < colour_gains[0] < 4.0 and 1.0 < colour_gains[1] < 4.0:
                self._day_wb_reference = tuple(colour_gains)
                logger.debug(
                    f"Updated day WB reference: [{colour_gains[0]:.2f}, {colour_gains[1]:.2f}] "
                    f"at {lux:.0f} lux"
                )

    def _get_target_colour_gains(self, mode: str, position: float = None) -> tuple:
        """
        Get target colour gains based on mode and transition position.

        For smooth transitions, interpolates between night manual gains
        and day AWB reference gains.

        Args:
            mode: Current light mode
            position: Transition position (0.0=night, 1.0=day), only for transition mode

        Returns:
            Target colour gains tuple (red, blue)
        """
        night_config = self.config["adaptive_timelapse"]["night_mode"]
        night_gains = tuple(night_config.get("colour_gains", [1.83, 2.02]))

        if mode == LightMode.NIGHT:
            return night_gains

        # For day and transition, we need day reference
        # Use stored reference or a reasonable default for daylight
        day_gains = self._day_wb_reference or (2.5, 1.6)

        if mode == LightMode.DAY:
            return day_gains

        # Transition mode - interpolate based on position
        if position is not None:
            # position: 0.0 = at night threshold, 1.0 = at day threshold
            red = night_gains[0] + position * (day_gains[0] - night_gains[0])
            blue = night_gains[1] + position * (day_gains[1] - night_gains[1])
            return (red, blue)

        # Default to midpoint
        return ((night_gains[0] + day_gains[0]) / 2, (night_gains[1] + day_gains[1]) / 2)

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

            # Use smooth WB interpolation even in night mode for seamless transitions
            target_gains = self._get_target_colour_gains(mode)
            smooth_gains = self._interpolate_colour_gains(target_gains)
            settings["ColourGains"] = smooth_gains

            logger.info(
                f"Night mode: exposure={night['max_exposure_time']}s, gain={night['analogue_gain']}, "
                f"WB=[{smooth_gains[0]:.2f}, {smooth_gains[1]:.2f}]"
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

            # For smooth transitions, use manual WB with interpolated gains
            # AWB is only used internally to learn good daylight WB values
            # (captured via _update_day_wb_reference from actual capture metadata)
            transition_config = adaptive_config.get("transition_mode", {})
            if transition_config.get("smooth_wb_in_day_mode", True):
                settings["AwbEnable"] = 0
                target_gains = self._get_target_colour_gains(mode)
                smooth_gains = self._interpolate_colour_gains(target_gains)
                settings["ColourGains"] = smooth_gains
            else:
                # Legacy behavior: use AWB in day mode
                settings["AwbEnable"] = 1 if day.get("awb_enable", True) else 0

            # Apply brightness adjustment if specified
            if "brightness" in day:
                settings["Brightness"] = day["brightness"]

            wb_info = (
                f"WB=[{settings.get('ColourGains', ('auto', 'auto'))[0]:.2f}, {settings.get('ColourGains', ('auto', 'auto'))[1]:.2f}]"
                if "ColourGains" in settings
                else "WB=auto"
            )
            logger.info(
                f"Day mode: auto_exposure={'on' if settings.get('AeEnable', 1) else 'off'}, "
                f"brightness={day.get('brightness', 0.0)}, {wb_info}"
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

                # ALWAYS use manual WB during transitions to prevent flickering
                # AWB causes sudden color shifts - instead we smoothly interpolate
                settings["AwbEnable"] = 0

                # Get smoothly interpolated colour gains
                target_gains = self._get_target_colour_gains(mode, position)
                smooth_gains = self._interpolate_colour_gains(target_gains, position)
                settings["ColourGains"] = smooth_gains

                logger.info(
                    f"Transition mode: lux={lux:.2f}, position={position:.2f}, "
                    f"exposure={interpolated_exposure:.2f}s, gain={interpolated_gain:.2f}, "
                    f"WB=[{smooth_gains[0]:.2f}, {smooth_gains[1]:.2f}]"
                )
            else:
                # Use middle values
                exposure_seconds = 5.0
                settings["ExposureTime"] = int(exposure_seconds * 1_000_000)  # 5 seconds
                settings["AnalogueGain"] = transition["analogue_gain_max"]
                settings["AwbEnable"] = 0
                # Use interpolated colour gains
                target_gains = self._get_target_colour_gains(mode, 0.5)
                smooth_gains = self._interpolate_colour_gains(target_gains)
                settings["ColourGains"] = smooth_gains

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
        original_save_metadata = self.camera_config.config["system"]["save_metadata"]

        # Set test shot controls
        self.camera_config.config["camera"]["controls"] = {
            "exposure_time": int(test_config["exposure_time"] * 1_000_000),
            "analogue_gain": test_config["analogue_gain"],
            "awb_enable": True,
        }

        # CRITICAL: Disable metadata saving for test shots to prevent timestamped
        # metadata files from accumulating in metadata/ folder
        # Test shots are only for measuring light levels, not part of timelapse
        self.camera_config.config["system"]["save_metadata"] = False

        # Create metadata directory (files get overwritten, not accumulated)
        metadata_dir = Path(self.config.get("system", {}).get("metadata_folder", "metadata"))
        metadata_dir.mkdir(exist_ok=True)

        # Capture test image (overwritten each time - no timestamps)
        # Since save_metadata=False, this won't create timestamped metadata files
        metadata = {}
        with ImageCapture(self.camera_config) as capture:
            test_path = metadata_dir / "test_shot.jpg"

            # Capture test image using capture_request to get metadata directly
            import json

            try:
                request = capture.picam2.capture_request()
                try:
                    # Save image
                    request.save("main", str(test_path))
                    # Get metadata from request
                    metadata = request.get_metadata()
                    # Save test shot metadata manually with fixed filename (overwritten each time)
                    test_metadata_path = metadata_dir / "test_shot_metadata.json"
                    with open(test_metadata_path, "w") as f:
                        json.dump(metadata, f, indent=2, default=str)
                    logger.debug(f"Test shot metadata saved: {test_metadata_path}")
                finally:
                    request.release()
            except Exception as e:
                logger.warning(f"Could not capture test shot with metadata: {e}")
                metadata = {}

            # Set image_path for return value
            image_path = str(test_path)

        # Restore original settings
        self.camera_config.config["camera"]["controls"] = original_controls
        self.camera_config.config["system"]["save_metadata"] = original_save_metadata

        logger.debug(f"Test shot saved: {image_path}")
        return image_path, metadata

    def _create_latest_symlink(self, image_path: str):
        """
        Create a symlink to the latest captured image.

        Args:
            image_path: Path to the latest image
        """
        symlink_config = self.config.get("output", {}).get("symlink_latest", {})
        if not symlink_config.get("enabled", False):
            return

        symlink_path = symlink_config.get("path")
        if not symlink_path:
            logger.warning("Symlink enabled but no path specified")
            return

        try:
            symlink_path = Path(symlink_path)
            image_path = Path(image_path).resolve()  # Get absolute path

            # Remove existing symlink/file if it exists
            if symlink_path.exists() or symlink_path.is_symlink():
                symlink_path.unlink()

            # Create new symlink
            symlink_path.symlink_to(image_path)
            logger.debug(f"Created symlink: {symlink_path} -> {image_path}")

        except PermissionError:
            logger.error(
                f"Permission denied creating symlink at {symlink_path}. "
                f"You may need to run with sudo or adjust permissions."
            )
        except Exception as e:
            logger.error(f"Failed to create symlink: {e}")

    def capture_frame(self, capture: ImageCapture, mode: str) -> Tuple[str, Optional[str]]:
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
        # Pass mode so overlay knows the light mode
        image_path, metadata_path = capture.capture(mode=mode)

        # Create symlink to latest image if enabled
        self._create_latest_symlink(image_path)

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

                        # Calculate raw lux from test shot
                        raw_lux = self.calculate_lux(test_image_path, test_metadata)

                        # Apply exponential moving average smoothing
                        lux = self._smooth_lux(raw_lux)

                        # Determine raw mode from smoothed lux
                        raw_mode = self.determine_mode(lux)

                        # Apply hysteresis to prevent rapid mode flipping
                        mode = self._apply_hysteresis(raw_mode)

                        # Get settings for this mode (with smooth WB interpolation)
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

                    # Update day WB reference from actual capture metadata
                    # This allows us to learn good daylight WB values for smooth transitions
                    if metadata_path and mode == LightMode.DAY:
                        try:
                            import json

                            with open(metadata_path, "r") as f:
                                capture_metadata = json.load(f)
                            self._update_day_wb_reference(capture_metadata)
                        except Exception as e:
                            logger.debug(f"Could not read capture metadata for WB reference: {e}")

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

            logger.info(f"=== Adaptive Timelapse Stopped ({self.frame_count} frames) ===")


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
