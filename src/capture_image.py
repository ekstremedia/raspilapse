"""Image capture module for Raspilapse."""

import os
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple
import yaml


class CameraConfig:
    """Camera configuration loaded from YAML file."""

    def __init__(self, config_path: str = "config/config.yml"):
        """
        Initialize camera configuration.

        Args:
            config_path: Path to YAML configuration file
        """
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        """Load configuration from YAML file."""
        config_file = Path(self.config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        with open(config_file, 'r') as f:
            return yaml.safe_load(f)

    def get_resolution(self) -> Tuple[int, int]:
        """Get camera resolution as (width, height) tuple."""
        res = self.config['camera']['resolution']
        return (res['width'], res['height'])

    def get_output_directory(self) -> str:
        """Get output directory path."""
        return self.config['output']['directory']

    def get_filename_pattern(self) -> str:
        """Get filename pattern."""
        return self.config['output']['filename_pattern']

    def get_project_name(self) -> str:
        """Get project name."""
        return self.config['output']['project_name']

    def get_quality(self) -> int:
        """Get JPEG quality setting."""
        return self.config['output']['quality']

    def should_create_directories(self) -> bool:
        """Check if directories should be auto-created."""
        return self.config['system']['create_directories']

    def should_save_metadata(self) -> bool:
        """Check if metadata should be saved."""
        return self.config['system']['save_metadata']

    def get_metadata_pattern(self) -> str:
        """Get metadata filename pattern."""
        return self.config['system']['metadata_filename']

    def get_transforms(self) -> Dict:
        """Get image transform settings."""
        return self.config['camera']['transforms']

    def get_controls(self) -> Optional[Dict]:
        """Get camera control settings if defined."""
        controls = self.config['camera'].get('controls', {})
        return controls if controls else None


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

    def initialize_camera(self):
        """Initialize and configure the camera."""
        try:
            from picamera2 import Picamera2
            import libcamera
        except ImportError as e:
            raise ImportError(
                "Picamera2 not found. Install with: sudo apt install -y python3-picamera2"
            ) from e

        self.picam2 = Picamera2()

        # Create camera configuration
        resolution = self.config.get_resolution()
        camera_config = self.picam2.create_preview_configuration(
            main={"size": resolution}
        )

        # Apply transforms
        transforms = self.config.get_transforms()
        if transforms['horizontal_flip'] or transforms['vertical_flip']:
            import libcamera
            camera_config["transform"] = libcamera.Transform(
                hflip=1 if transforms['horizontal_flip'] else 0,
                vflip=1 if transforms['vertical_flip'] else 0
            )

        self.picam2.configure(camera_config)
        self.picam2.start()

        # Allow camera to stabilize
        time.sleep(2)

        # Apply camera controls if specified
        controls = self.config.get_controls()
        if controls:
            self._apply_controls(controls)

    def _apply_controls(self, controls: Dict):
        """
        Apply camera controls.

        Args:
            controls: Dictionary of control settings
        """
        control_map = {}

        if 'exposure_time' in controls:
            control_map['ExposureTime'] = controls['exposure_time']
        if 'analogue_gain' in controls:
            control_map['AnalogueGain'] = controls['analogue_gain']
        if 'awb_enable' in controls:
            control_map['AwbEnable'] = 1 if controls['awb_enable'] else 0
        if 'colour_gains' in controls:
            control_map['ColourGains'] = tuple(controls['colour_gains'])
        if 'brightness' in controls:
            control_map['Brightness'] = controls['brightness']
        if 'contrast' in controls:
            control_map['Contrast'] = controls['contrast']
        if 'af_mode' in controls:
            control_map['AfMode'] = controls['af_mode']

        if control_map:
            self.picam2.set_controls(control_map)

    def capture(self, output_path: Optional[str] = None) -> Tuple[str, Optional[str]]:
        """
        Capture an image.

        Args:
            output_path: Optional custom output path. If None, uses config pattern.

        Returns:
            Tuple of (image_path, metadata_path)
        """
        if self.picam2 is None:
            raise RuntimeError("Camera not initialized. Call initialize_camera() first.")

        # Prepare output directory
        output_dir = Path(self.config.get_output_directory())
        if self.config.should_create_directories():
            output_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename
        if output_path is None:
            timestamp = datetime.now()
            filename = self.config.get_filename_pattern().format(
                name=self.config.get_project_name(),
                counter=f"{self._counter:04d}",
                timestamp=timestamp.isoformat()
            )
            # Support strftime formatting
            filename = timestamp.strftime(filename)
            output_path = output_dir / filename
        else:
            output_path = Path(output_path)

        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Capture image
        self.picam2.capture_file(str(output_path))

        # Save metadata if enabled
        metadata_path = None
        if self.config.should_save_metadata():
            metadata_path = self._save_metadata(output_path)

        self._counter += 1

        return str(output_path), metadata_path

    def _save_metadata(self, image_path: Path) -> str:
        """
        Save capture metadata.

        Args:
            image_path: Path to captured image

        Returns:
            Path to metadata file
        """
        metadata = self.picam2.capture_metadata()

        # Add custom metadata
        metadata['capture_timestamp'] = datetime.now().isoformat()
        metadata['image_path'] = str(image_path)
        metadata['resolution'] = self.config.get_resolution()
        metadata['quality'] = self.config.get_quality()

        # Generate metadata filename
        timestamp = datetime.now()
        metadata_filename = self.config.get_metadata_pattern().format(
            name=self.config.get_project_name(),
            counter=f"{self._counter:04d}",
            timestamp=timestamp.isoformat()
        )

        metadata_path = image_path.parent / metadata_filename

        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2, default=str)

        return str(metadata_path)

    def close(self):
        """Close and cleanup camera resources."""
        if self.picam2:
            self.picam2.close()
            self.picam2 = None

    def __enter__(self):
        """Context manager entry."""
        self.initialize_camera()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


def capture_single_image(config_path: str = "config/config.yml",
                         output_path: Optional[str] = None) -> Tuple[str, Optional[str]]:
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

    parser = argparse.ArgumentParser(
        description="Capture an image using Raspberry Pi Camera V3"
    )
    parser.add_argument(
        '-c', '--config',
        default='config/config.yml',
        help='Path to configuration file (default: config/config.yml)'
    )
    parser.add_argument(
        '-o', '--output',
        help='Output file path (overrides config pattern)'
    )

    args = parser.parse_args()

    try:
        image_path, metadata_path = capture_single_image(args.config, args.output)
        print(f"Image captured: {image_path}")
        if metadata_path:
            print(f"Metadata saved: {metadata_path}")
    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
