"""Image capture module for Raspilapse."""

import os
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple
import yaml

# Handle imports for both module and script execution
try:
    from src.logging_config import get_logger
    from src.overlay import ImageOverlay
except ImportError:
    from logging_config import get_logger
    from overlay import ImageOverlay

# Initialize logger
logger = get_logger("capture_image")


class CameraConfig:
    """Camera configuration loaded from YAML file."""

    def __init__(self, config_path: str = "config/config.yml"):
        """
        Initialize camera configuration.

        Args:
            config_path: Path to YAML configuration file
        """
        self.config_path = config_path
        logger.info(f"Loading configuration from: {config_path}")
        self.config = self._load_config()
        logger.debug(f"Configuration loaded successfully")

    def _load_config(self) -> Dict:
        """Load configuration from YAML file."""
        config_file = Path(self.config_path)
        if not config_file.exists():
            logger.error(f"Configuration file not found: {self.config_path}")
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        try:
            with open(config_file, "r") as f:
                config = yaml.safe_load(f)
                logger.debug(f"Successfully parsed YAML configuration")
                return config
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse configuration file: {e}")
            raise

    def get_resolution(self) -> Tuple[int, int]:
        """Get camera resolution as (width, height) tuple."""
        res = self.config["camera"]["resolution"]
        return (res["width"], res["height"])

    def get_output_directory(self) -> str:
        """Get output directory path."""
        return self.config["output"]["directory"]

    def get_filename_pattern(self) -> str:
        """Get filename pattern."""
        return self.config["output"]["filename_pattern"]

    def get_project_name(self) -> str:
        """Get project name."""
        return self.config["output"]["project_name"]

    def get_quality(self) -> int:
        """Get JPEG quality setting."""
        return self.config["output"]["quality"]

    def should_create_directories(self) -> bool:
        """Check if directories should be auto-created."""
        return self.config["system"]["create_directories"]

    def should_save_metadata(self) -> bool:
        """Check if metadata should be saved."""
        return self.config["system"]["save_metadata"]

    def get_metadata_pattern(self) -> str:
        """Get metadata filename pattern."""
        return self.config["system"]["metadata_filename"]

    def get_transforms(self) -> Dict:
        """Get image transform settings."""
        return self.config["camera"]["transforms"]

    def get_controls(self) -> Optional[Dict]:
        """Get camera control settings if defined."""
        controls = self.config["camera"].get("controls", {})
        return controls if controls else None

    def should_organize_by_date(self) -> bool:
        """Check if images should be organized by date."""
        return self.config["output"].get("organize_by_date", False)

    def get_date_format(self) -> str:
        """Get date format for subdirectories."""
        return self.config["output"].get("date_format", "%Y-%m-%d")


class ImageCapture:
    """Handles image capture using Picamera2."""

    def __init__(self, config: CameraConfig):
        """
        Initialize image capture.

        Args:
            config: Camera configuration object
        """
        self.config = config
        self.picam2 = None
        self._counter = 0

        # Initialize overlay handler
        self.overlay = ImageOverlay(config.config)

        # Store last brightness metrics from lores stream (avoids disk I/O)
        self.last_brightness_metrics: Optional[Dict] = None

        logger.debug("ImageCapture instance created")

    def initialize_camera(self, manual_controls: Optional[Dict] = None):
        """
        Initialize and configure the camera.

        Args:
            manual_controls: Optional dict of controls to apply during configuration.
                           These override config file controls.
        """
        logger.info("Initializing camera...")

        try:
            from picamera2 import Picamera2
            import libcamera
        except ImportError as e:
            logger.error(
                "Picamera2 library not found. Install with: sudo apt install -y python3-picamera2"
            )
            raise ImportError(
                "Picamera2 not found. Install with: sudo apt install -y python3-picamera2"
            ) from e

        try:
            self.picam2 = Picamera2()
            logger.debug("Picamera2 object created")

            # Create camera configuration
            resolution = self.config.get_resolution()
            logger.info(f"Setting camera resolution to {resolution[0]}x{resolution[1]}")

            # Prepare controls - merge manual_controls with config controls
            controls_to_apply = {}
            config_controls = self.config.get_controls()
            if config_controls:
                controls_to_apply = self._prepare_control_map(config_controls)

            if manual_controls:
                # Manual controls override config controls
                manual_map = self._prepare_control_map(manual_controls)
                controls_to_apply.update(manual_map)
                logger.debug(f"Applying manual controls: {manual_controls}")

            # Create configuration with controls embedded
            # CRITICAL: Set buffer_count=3 and queue=False for long exposures
            # Set FrameDurationLimits to match exposure time for fast long exposures
            if controls_to_apply:
                # Add FrameDurationLimits if ExposureTime is set (REQUIRED for fast long exposures!)
                if "ExposureTime" in controls_to_apply:
                    exposure_us = controls_to_apply["ExposureTime"]
                    # Frame period = exposure + 100ms slack
                    frame_duration_us = exposure_us + 100_000
                    controls_to_apply["FrameDurationLimits"] = (
                        frame_duration_us,
                        frame_duration_us,
                    )
                    controls_to_apply["NoiseReductionMode"] = 0  # Keep pipeline light
                    logger.debug(
                        f"Set FrameDurationLimits to {frame_duration_us}µs for {exposure_us}µs exposure"
                    )

                camera_config = self.picam2.create_still_configuration(
                    # Use an RGB format that PIL / Picamera2 helpers support
                    main={"size": resolution, "format": "RGB888"},
                    # Low-res stream for fast brightness measurement (avoids disk I/O)
                    lores={"size": (320, 240), "format": "YUV420"},
                    raw=None,  # Disable RAW for performance
                    buffer_count=3,  # CRITICAL: prevents frame queuing delays
                    queue=False,  # Ensures fresh frame after request
                    display=None,
                    controls=controls_to_apply,
                )
                logger.debug(f"Camera configured with controls: {controls_to_apply}")
            else:
                camera_config = self.picam2.create_still_configuration(
                    main={"size": resolution},
                    # Low-res stream for fast brightness measurement
                    lores={"size": (320, 240), "format": "YUV420"},
                    display=None,
                )

            # Apply transforms
            transforms = self.config.get_transforms()
            if transforms["horizontal_flip"] or transforms["vertical_flip"]:
                import libcamera

                logger.debug(
                    f"Applying transforms: hflip={transforms['horizontal_flip']}, vflip={transforms['vertical_flip']}"
                )
                camera_config["transform"] = libcamera.Transform(
                    hflip=1 if transforms["horizontal_flip"] else 0,
                    vflip=1 if transforms["vertical_flip"] else 0,
                )

            self.picam2.configure(camera_config)
            logger.debug("Camera configured")

            self.picam2.start()
            logger.info("Camera started")

            # Allow camera to stabilize
            logger.debug("Waiting for camera to stabilize (2 seconds)...")
            time.sleep(2)

            logger.info("Camera initialization complete")

        except Exception as e:
            logger.error(f"Failed to initialize camera: {e}")
            raise

    def _prepare_control_map(self, controls: Dict) -> Dict:
        """
        Prepare control map for libcamera.

        Converts both snake_case and PascalCase keys to proper libcamera format.

        Args:
            controls: Dictionary of control settings

        Returns:
            Dictionary ready for libcamera
        """
        control_map = {}

        # Handle snake_case keys (from config file)
        if "exposure_time" in controls:
            control_map["ExposureTime"] = controls["exposure_time"]
        if "analogue_gain" in controls:
            control_map["AnalogueGain"] = controls["analogue_gain"]
        if "awb_enable" in controls:
            control_map["AwbEnable"] = 1 if controls["awb_enable"] else 0
        if "ae_enable" in controls:
            control_map["AeEnable"] = 1 if controls["ae_enable"] else 0
        if "colour_gains" in controls:
            control_map["ColourGains"] = tuple(controls["colour_gains"])
        if "brightness" in controls:
            control_map["Brightness"] = controls["brightness"]
        if "contrast" in controls:
            control_map["Contrast"] = controls["contrast"]
        if "af_mode" in controls:
            control_map["AfMode"] = controls["af_mode"]
        if "lens_position" in controls:
            control_map["LensPosition"] = controls["lens_position"]
        if "exposure_value" in controls:
            control_map["ExposureValue"] = controls["exposure_value"]

        # Handle PascalCase keys (direct libcamera controls)
        if "ExposureTime" in controls:
            control_map["ExposureTime"] = controls["ExposureTime"]
        if "AnalogueGain" in controls:
            control_map["AnalogueGain"] = controls["AnalogueGain"]
        if "AwbEnable" in controls:
            control_map["AwbEnable"] = controls["AwbEnable"]
        if "AeEnable" in controls:
            control_map["AeEnable"] = controls["AeEnable"]
        if "ColourGains" in controls:
            control_map["ColourGains"] = controls["ColourGains"]
        if "Brightness" in controls:
            control_map["Brightness"] = controls["Brightness"]
        if "Contrast" in controls:
            control_map["Contrast"] = controls["Contrast"]
        if "AfMode" in controls:
            control_map["AfMode"] = controls["AfMode"]
        if "LensPosition" in controls:
            control_map["LensPosition"] = controls["LensPosition"]
        if "ExposureValue" in controls:
            control_map["ExposureValue"] = controls["ExposureValue"]

        return control_map

    def _apply_controls(self, controls: Dict):
        """
        Apply camera controls to an already-started camera (legacy method).

        Args:
            controls: Dictionary of control settings
        """
        control_map = self._prepare_control_map(controls)
        if control_map:
            logger.debug(f"Applying controls to camera: {control_map}")
            self.picam2.set_controls(control_map)

    def _compute_brightness_from_lores(self, request) -> Dict:
        """
        Compute brightness metrics from the lores stream.

        This avoids disk I/O and overlay contamination by analyzing
        the raw low-resolution image buffer directly from the camera.

        Args:
            request: Picamera2 capture request with lores stream

        Returns:
            Dictionary with brightness metrics
        """
        try:
            import numpy as np

            # Get lores array from request (YUV420 format)
            # In YUV420, the Y (luminance) plane comes first, followed by U and V
            lores_array = request.make_array("lores")

            # For YUV420, the array shape is (height * 1.5, width) with Y plane first
            # Extract just the Y (luminance) plane - first 240 rows for 320x240 lores
            # The Y values are already brightness (0-255), no conversion needed
            height = 240
            gray = lores_array[:height, :].astype(np.float32)

            # Compute statistics
            mean_brightness = float(np.mean(gray))
            median_brightness = float(np.median(gray))
            std_brightness = float(np.std(gray))

            # Percentiles for exposure analysis
            p5 = float(np.percentile(gray, 5))
            p10 = float(np.percentile(gray, 10))
            p90 = float(np.percentile(gray, 90))
            p95 = float(np.percentile(gray, 95))

            # Under/overexposure percentages
            total_pixels = gray.size
            underexposed = float(np.sum(gray < 10) / total_pixels * 100)
            overexposed = float(np.sum(gray > 245) / total_pixels * 100)

            metrics = {
                "mean_brightness": round(mean_brightness, 2),
                "median_brightness": round(median_brightness, 2),
                "std_brightness": round(std_brightness, 2),
                "percentile_5": round(p5, 2),
                "percentile_10": round(p10, 2),
                "percentile_90": round(p90, 2),
                "percentile_95": round(p95, 2),
                "underexposed_percent": round(underexposed, 2),
                "overexposed_percent": round(overexposed, 2),
            }

            logger.debug(
                f"Lores brightness: mean={mean_brightness:.1f}, median={median_brightness:.1f}"
            )
            return metrics

        except Exception as e:
            logger.warning(f"Could not compute brightness from lores: {e}")
            return {}

    def update_controls(self, controls: Dict):
        """
        Update camera controls on an already-initialized camera.

        Useful for changing exposure settings between captures without reinitializing.

        Args:
            controls: Dictionary of camera control settings
        """
        if self.picam2 is None:
            logger.error("Camera not initialized")
            raise RuntimeError("Camera not initialized")

        logger.debug(f"Updating camera controls: {controls}")

        # Prepare control map
        control_map = self._prepare_control_map(controls)

        # Add FrameDurationLimits if ExposureTime is being updated (REQUIRED for fast long exposures!)
        if "ExposureTime" in control_map:
            exposure_us = control_map["ExposureTime"]
            frame_duration_us = exposure_us + 100_000
            control_map["FrameDurationLimits"] = (frame_duration_us, frame_duration_us)
            control_map["NoiseReductionMode"] = 0  # Keep pipeline light
            logger.debug(
                f"Updated FrameDurationLimits to {frame_duration_us}µs for {exposure_us}µs exposure"
            )

        if control_map:
            logger.debug(f"Applying controls to camera: {control_map}")
            self.picam2.set_controls(control_map)

    def capture(
        self, output_path: Optional[str] = None, mode: Optional[str] = None
    ) -> Tuple[str, Optional[str]]:
        """
        Capture an image.

        Args:
            output_path: Optional custom output path. If None, uses config pattern.
            mode: Optional light mode (day/night/transition) for overlay display

        Returns:
            Tuple of (image_path, metadata_path)
        """
        if self.picam2 is None:
            logger.error("Camera not initialized. Call initialize_camera() first.")
            raise RuntimeError("Camera not initialized. Call initialize_camera() first.")

        logger.info(f"Starting image capture #{self._counter}")

        try:
            # Prepare output directory
            output_dir = Path(self.config.get_output_directory())

            # Add date subdirectories if organize_by_date is enabled
            if self.config.should_organize_by_date():
                timestamp = datetime.now()
                date_subdir = timestamp.strftime(self.config.get_date_format())
                output_dir = output_dir / date_subdir
                logger.debug(f"Date-organized directory: {output_dir}")

            if self.config.should_create_directories():
                output_dir.mkdir(parents=True, exist_ok=True)
                logger.debug(f"Output directory: {output_dir}")

            # Generate filename
            if output_path is None:
                timestamp = datetime.now()
                filename = self.config.get_filename_pattern().format(
                    name=self.config.get_project_name(),
                    counter=f"{self._counter:04d}",
                    timestamp=timestamp.isoformat(),
                )
                # Support strftime formatting
                filename = timestamp.strftime(filename)
                output_path = output_dir / filename
            else:
                output_path = Path(output_path)

            logger.debug(f"Output path: {output_path}")

            # Ensure output directory exists
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Use capture_request() to get both image and metadata without blocking
            # This avoids the 20-second delay from capture_metadata() with long exposures
            logger.debug("Capturing image...")
            request = self.picam2.capture_request()
            try:
                # Compute brightness from lores BEFORE saving (no overlay contamination)
                self.last_brightness_metrics = self._compute_brightness_from_lores(request)

                # Save the image
                request.save("main", str(output_path))
                logger.info(f"Image captured successfully: {output_path}")

                # Get metadata from request (always, for overlay)
                metadata_dict = request.get_metadata()

                # Save metadata if enabled (from request, no blocking!)
                metadata_path = None
                if self.config.should_save_metadata():
                    logger.debug("Saving metadata...")
                    metadata_path = self._save_metadata_from_dict(output_path, metadata_dict)
                    logger.debug(f"Metadata saved: {metadata_path}")
            finally:
                # Always release the request
                request.release()

            # Apply overlay if enabled (do this after release to avoid holding camera)
            if self.overlay.enabled and metadata_dict is not None:
                logger.info(f"Applying overlay to {output_path} (mode: {mode})...")
                result = self.overlay.apply_overlay(str(output_path), metadata_dict, mode)
                if result:
                    logger.info(f"Overlay applied successfully")
                else:
                    logger.warning(f"Overlay application returned None/False")
            else:
                if not self.overlay.enabled:
                    logger.info("Overlay is disabled")
                if metadata_dict is None:
                    logger.warning("No metadata available for overlay")

            self._counter += 1

            return str(output_path), metadata_path

        except Exception as e:
            logger.error(f"Failed to capture image: {e}")
            raise

    def _save_metadata_from_dict(self, image_path: Path, metadata: Dict) -> str:
        """
        Save capture metadata from a metadata dictionary.

        Args:
            image_path: Path to captured image
            metadata: Metadata dictionary from capture_request

        Returns:
            Path to metadata file
        """
        # Add custom metadata
        metadata["capture_timestamp"] = datetime.now().isoformat()
        metadata["image_path"] = str(image_path)
        metadata["resolution"] = self.config.get_resolution()
        metadata["quality"] = self.config.get_quality()

        # Generate metadata filename
        timestamp = datetime.now()
        metadata_filename = self.config.get_metadata_pattern().format(
            name=self.config.get_project_name(),
            counter=f"{self._counter:04d}",
            timestamp=timestamp.isoformat(),
        )
        # Support strftime formatting (e.g., %Y_%m_%d_%H_%M_%S)
        metadata_filename = timestamp.strftime(metadata_filename)

        metadata_path = image_path.parent / metadata_filename

        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2, default=str)

        return str(metadata_path)

    def _save_metadata(self, image_path: Path) -> str:
        """
        Save capture metadata (legacy method using capture_metadata()).

        Args:
            image_path: Path to captured image

        Returns:
            Path to metadata file
        """
        metadata = self.picam2.capture_metadata()
        return self._save_metadata_from_dict(image_path, metadata)

    def close(self):
        """Close and cleanup camera resources."""
        if self.picam2:
            logger.info("Closing camera...")
            self.picam2.close()
            self.picam2 = None
            logger.debug("Camera closed successfully")

    def __enter__(self):
        """Context manager entry."""
        self.initialize_camera()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


def capture_single_image(
    config_path: str = "config/config.yml", output_path: Optional[str] = None
) -> Tuple[str, Optional[str]]:
    """
    Convenience function to capture a single image.

    Args:
        config_path: Path to configuration file
        output_path: Optional custom output path

    Returns:
        Tuple of (image_path, metadata_path)
    """
    config = CameraConfig(config_path)

    with ImageCapture(config) as capture:
        return capture.capture(output_path)


def main():
    """CLI entry point for capturing a single image."""
    import argparse

    parser = argparse.ArgumentParser(description="Capture an image using Raspberry Pi Camera V3")
    parser.add_argument(
        "-c",
        "--config",
        default="config/config.yml",
        help="Path to configuration file (default: config/config.yml)",
    )
    parser.add_argument("-o", "--output", help="Output file path (overrides config pattern)")

    args = parser.parse_args()

    logger.info("=== Raspilapse Image Capture Started ===")
    logger.debug(f"Config file: {args.config}")
    if args.output:
        logger.debug(f"Custom output path: {args.output}")

    try:
        image_path, metadata_path = capture_single_image(args.config, args.output)
        print(f"Image captured: {image_path}")
        logger.info(f"Image captured: {image_path}")
        if metadata_path:
            print(f"Metadata saved: {metadata_path}")
            logger.info(f"Metadata saved: {metadata_path}")
        logger.info("=== Capture Complete ===")
    except Exception as e:
        print(f"Error: {e}")
        logger.error(f"Capture failed: {e}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
