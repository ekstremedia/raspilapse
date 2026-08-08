"""Image capture module for Raspilapse."""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import yaml

from raspilapse.config import merge_defaults
from raspilapse.logging_setup import configure_logging, get_logger
from raspilapse.overlay import build_overlay

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
        logger.debug("Configuration loaded successfully")

    def _load_config(self) -> Dict:
        """Load configuration from YAML file."""
        config_file = Path(self.config_path)
        if not config_file.exists():
            logger.error(f"Configuration file not found: {self.config_path}")
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        try:
            with open(config_file, "r") as f:
                config = yaml.safe_load(f) or {}
                logger.debug("Successfully parsed YAML configuration")
                # The accessors below index without a fallback, which is what
                # forced every config file to spell out the whole schema.
                return merge_defaults(config)
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
        # merge_defaults() always fills these keys; the fallbacks only exist
        # for dicts that bypass it, and match DEFAULTS so both paths agree.
        return self.config["output"].get("organize_by_date", True)

    def get_date_format(self) -> str:
        """Get date format for subdirectories."""
        return self.config["output"].get("date_format", "%Y/%m/%d")


class ImageCapture:
    """Handles image capture using Picamera2."""

    def __init__(self, config: CameraConfig, post_process: Optional[Callable] = None):
        """
        Initialize image capture.

        Args:
            config: Camera configuration object
            post_process: Optional callable(image_path, metadata, mode) applied
                to each frame after it is written. This is how the overlay is
                attached. Leaving it None is the whole of "no overlay" -- this
                module used to import the renderer itself, which made Pillow a
                hard requirement of taking a photo even with the overlay off.
        """
        self.config = config
        self.picam2 = None
        self._counter = 0
        self.post_process = post_process

        # Store last brightness metrics from lores stream (avoids disk I/O)
        self.last_brightness_metrics: Optional[Dict] = None

        # Frames discarded waiting for each bracket's exposure to land in the
        # last capture_bracketed call; the fusion planner reads this to keep
        # its slot-budget estimate honest.
        self.last_settle_frames: list = []

        logger.debug("ImageCapture instance created")

    def initialize_camera(
        self,
        manual_controls: Optional[Dict] = None,
        main_size_override: Optional[Tuple[int, int]] = None,
    ):
        """
        Initialize and configure the camera.

        Args:
            manual_controls: Optional dict of controls to apply during configuration.
                           These override config file controls.
            main_size_override: Capture size to request instead of the
                configured resolution. Used while sensor HDR is active,
                whose binned mode cannot deliver the configured size.
        """
        logger.debug("Initializing camera...")

        try:
            import libcamera
            from picamera2 import Picamera2
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
            if main_size_override is not None:
                resolution = (int(main_size_override[0]), int(main_size_override[1]))
            logger.debug(f"Setting camera resolution to {resolution[0]}x{resolution[1]}")

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
            logger.debug("Camera started")

            # Allow camera to stabilize
            logger.debug("Waiting for camera to stabilize (2 seconds)...")
            time.sleep(2)

            # request.save() encodes at picamera2's own default (90) unless
            # told otherwise -- without this line output.quality only ever
            # applied to the overlay's re-encode, and not at all with the
            # overlay off.
            self.picam2.options["quality"] = self.config.get_quality()

            logger.debug("Camera initialization complete")

        except Exception as e:
            logger.error(f"Failed to initialize camera: {e}")
            # Release whatever was allocated before the failure. Relying on
            # __del__ to close a half-opened camera is what left the device
            # "in use" for a retry that would otherwise have succeeded.
            if self.picam2 is not None:
                try:
                    self.picam2.close()
                except Exception:
                    pass
                self.picam2 = None
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

            # For YUV420, the array shape is (height * 1.5, stride) with the Y
            # plane first; Y values are already brightness (0-255). Slice to
            # the size libcamera actually granted rather than assuming
            # 320x240: an adjusted stream would silently average chroma rows
            # (or stride padding) into every exposure decision.
            try:
                lores_w, lores_h = self.picam2.camera_config["lores"]["size"]
            except Exception:
                # The configured size is the truth; if the lookup shape ever
                # changes, fall back to the size we request rather than losing
                # brightness feedback entirely.
                lores_w, lores_h = 320, 240
            gray = lores_array[:lores_h, :lores_w].astype(np.float32)

            # Compute statistics
            mean_brightness = float(np.mean(gray))
            median_brightness = float(np.median(gray))
            std_brightness = float(np.std(gray))

            # Percentiles for exposure analysis. p5/p95 drive the shadow and
            # highlight checks; p25/p75 are the interquartile range, and are
            # what the brightness_p25/p75 database columns expect -- this used
            # to emit p10/p90, so both columns were NULL on every row ever
            # written, and nothing else read them.
            p5, p25, p75, p95 = (float(v) for v in np.percentile(gray, [5, 25, 75, 95]))

            # Under/overexposure percentages
            total_pixels = gray.size
            underexposed = float(np.sum(gray < 10) / total_pixels * 100)
            overexposed = float(np.sum(gray > 245) / total_pixels * 100)

            metrics = {
                "mean_brightness": round(mean_brightness, 2),
                "median_brightness": round(median_brightness, 2),
                "std_brightness": round(std_brightness, 2),
                "percentile_5": round(p5, 2),
                "percentile_25": round(p25, 2),
                "percentile_75": round(p75, 2),
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

    def _resolve_output_path(self, timestamp, output_path: Optional[str] = None) -> Path:
        """Where this capture's image belongs, directories created.

        Shared by every capture flavour so the date-subdirectory, filename
        and DST-collision rules cannot drift apart between them.
        """
        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            return output_path

        output_dir = Path(self.config.get_output_directory())

        # Add date subdirectories if organize_by_date is enabled
        if self.config.should_organize_by_date():
            date_subdir = timestamp.strftime(self.config.get_date_format())
            output_dir = output_dir / date_subdir
            logger.debug(f"Date-organized directory: {output_dir}")

        if self.config.should_create_directories():
            output_dir.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Output directory: {output_dir}")

        filename = self.config.get_filename_pattern().format(
            name=self.config.get_project_name(),
            counter=f"{self._counter:04d}",
            timestamp=timestamp.isoformat(),
        )
        # Support strftime formatting
        filename = timestamp.strftime(filename)
        output_path = output_dir / filename
        if output_path.exists():
            # Only reachable when local wall time repeats (the DST
            # fall-back hour): the second pass would silently
            # overwrite the first. A _dst suffix keeps both files;
            # the video renderer skips the suffixed name (its
            # trailing fields no longer parse as a timestamp), so the
            # repeated hour is absent from the video but not from disk.
            logger.warning(f"{output_path} already exists; keeping both")
            output_path = output_path.with_name(f"{output_path.stem}_dst{output_path.suffix}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        return output_path

    def capture(
        self,
        output_path: Optional[str] = None,
        mode: Optional[str] = None,
        extra_metadata: Optional[Dict] = None,
    ) -> Tuple[str, Optional[str]]:
        """
        Capture an image.

        Args:
            output_path: Optional custom output path. If None, uses config pattern.
            mode: Optional light mode (day/night/transition) for overlay display
            extra_metadata: Optional dict of extra metadata to merge (e.g., calculated lux)

        Returns:
            Tuple of (image_path, metadata_path)
        """
        if self.picam2 is None:
            logger.error("Camera not initialized. Call initialize_camera() first.")
            raise RuntimeError("Camera not initialized. Call initialize_camera() first.")

        logger.debug(f"Starting image capture #{self._counter}")

        try:
            # One timestamp for the date subdirectory, the filename and the
            # metadata sidecar. Separate now() calls let a frame straddling
            # midnight land in yesterday's directory under today's filename,
            # where find_images_in_range() never looks. Timezone-aware so the
            # sidecar's capture_timestamp carries the UTC offset: during the
            # DST fall-back hour a naive string repeats, and the database's
            # INSERT OR REPLACE destroyed the first pass's rows.
            timestamp = datetime.now().astimezone()
            output_path = self._resolve_output_path(timestamp, output_path)

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

                # Merge extra metadata (e.g., calculated lux) - overrides camera values
                if extra_metadata:
                    metadata_dict.update(extra_metadata)

                # Save metadata if enabled (from request, no blocking!)
                metadata_path = None
                if self.config.should_save_metadata():
                    logger.debug("Saving metadata...")
                    metadata_path = self._save_metadata_from_dict(
                        output_path, metadata_dict, timestamp
                    )
                    logger.debug(f"Metadata saved: {metadata_path}")
            finally:
                # Always release the request
                request.release()

            # Post-process after releasing the request, so the camera is not
            # held while the frame is re-encoded.
            if self.post_process is not None and metadata_dict is not None:
                logger.debug(f"Post-processing {output_path} (mode: {mode})...")
                if self.post_process(str(output_path), metadata_dict, mode):
                    logger.debug("Post-processing applied")
                else:
                    logger.warning("Post-processing returned nothing")
            elif self.post_process is not None:
                logger.warning("No metadata available, skipping post-processing")

            self._counter += 1

            return str(output_path), metadata_path

        except Exception as e:
            logger.error(f"Failed to capture image: {e}")
            raise

    def _capture_at_exposure(self, exposure_us: int, settle_frames_max: int):
        """One frame after the commanded exposure has actually landed.

        set_controls on a running camera takes effect frames later, so
        requests are discarded until the sensor reports an ExposureTime
        within 10% of the command -- whole-line quantisation means an exact
        match never happens. The cap turns a sensor that never settles into
        a warning and a slightly-off bracket instead of a stuck loop.

        Returns:
            Tuple of (frame array, frames discarded while settling)
        """
        discarded = 0
        while True:
            request = self.picam2.capture_request()
            try:
                reported = request.get_metadata().get("ExposureTime", 0)
                close_enough = abs(reported - exposure_us) <= exposure_us * 0.1
                if close_enough or discarded >= settle_frames_max:
                    if not close_enough:
                        logger.warning(
                            f"Bracket never settled: commanded {exposure_us}us, sensor "
                            f"reports {reported}us after {discarded} discarded frames"
                        )
                    return request.make_array("main"), discarded
                discarded += 1
            finally:
                request.release()

    def capture_bracketed(
        self,
        bracket_exposures_us: list,
        fuse_fn: Callable[[list], bytes],
        mode: Optional[str] = None,
        extra_metadata: Optional[Dict] = None,
        settle_frames_max: int = 10,
    ) -> Tuple[str, Optional[str]]:
        """Capture a bracket of exposures and save their fusion as the frame.

        The base exposure -- the one the exposure loop commanded -- comes
        first: its lores metrics and metadata are the frame's, so metering,
        lux and the database keep describing the exposure that was actually
        decided, untouched by fusion. The remaining brackets only differ in
        ExposureTime; gain is never changed mid-bracket.

        fuse_fn turns the list of frame arrays into encoded JPEG bytes.
        Injected so this module needs neither cv2 nor Pillow -- the caller
        (the dynrange package) owns the pixel mathematics.

        Args:
            bracket_exposures_us: Exposure times in microseconds, base first
            fuse_fn: Callable merging the captured arrays into JPEG bytes
            mode: Light mode, passed through to post-processing
            extra_metadata: Extra keys merged into the metadata sidecar
            settle_frames_max: Discard cap per bracket while controls land

        Returns:
            Tuple of (image_path, metadata_path)
        """
        if self.picam2 is None:
            logger.error("Camera not initialized. Call initialize_camera() first.")
            raise RuntimeError("Camera not initialized. Call initialize_camera() first.")
        if len(bracket_exposures_us) < 2:
            raise ValueError("capture_bracketed needs at least two exposures")

        logger.debug(
            f"Starting bracketed capture #{self._counter}: "
            f"{[int(e) for e in bracket_exposures_us]}us"
        )
        self.last_settle_frames = []

        try:
            # Same timestamp discipline as capture(): one clock reading for
            # directory, filename and sidecar.
            timestamp = datetime.now().astimezone()
            output_path = self._resolve_output_path(timestamp)

            # Base shot first, straight off the already-settled camera.
            request = self.picam2.capture_request()
            try:
                self.last_brightness_metrics = self._compute_brightness_from_lores(request)
                frames = [request.make_array("main")]
                metadata_dict = request.get_metadata()
            finally:
                request.release()

            for exposure_us in bracket_exposures_us[1:]:
                self.update_controls({"ExposureTime": int(exposure_us)})
                frame, discarded = self._capture_at_exposure(int(exposure_us), settle_frames_max)
                frames.append(frame)
                self.last_settle_frames.append(discarded)

            encoded = fuse_fn(frames)
            bracket_count = len(frames)
            # Release ~25 MB per 4K bracket before post-processing decodes
            # the JPEG on top of them.
            del frames

            with open(output_path, "wb") as f:
                f.write(encoded)
            logger.info(
                f"Image captured successfully: {output_path} ({bracket_count} brackets fused)"
            )

            if extra_metadata:
                metadata_dict.update(extra_metadata)

            metadata_path = None
            if self.config.should_save_metadata():
                metadata_path = self._save_metadata_from_dict(output_path, metadata_dict, timestamp)
                logger.debug(f"Metadata saved: {metadata_path}")

            if self.post_process is not None and metadata_dict is not None:
                logger.debug(f"Post-processing {output_path} (mode: {mode})...")
                if self.post_process(str(output_path), metadata_dict, mode):
                    logger.debug("Post-processing applied")
                else:
                    logger.warning("Post-processing returned nothing")

            self._counter += 1

            return str(output_path), metadata_path

        except Exception as e:
            logger.error(f"Failed to capture bracketed image: {e}")
            raise

    def _save_metadata_from_dict(
        self, image_path: Path, metadata: Dict, timestamp: Optional[datetime] = None
    ) -> str:
        """
        Save capture metadata from a metadata dictionary.

        Args:
            image_path: Path to captured image
            metadata: Metadata dictionary from capture_request
            timestamp: The capture's own timestamp, so the sidecar's filename
                and capture_timestamp match the image filename exactly

        Returns:
            Path to metadata file
        """
        timestamp = timestamp or datetime.now()

        # Add custom metadata
        metadata["capture_timestamp"] = timestamp.isoformat()
        metadata["image_path"] = str(image_path)
        metadata["resolution"] = self.config.get_resolution()
        metadata["quality"] = self.config.get_quality()

        # Generate metadata filename
        metadata_filename = self.config.get_metadata_pattern().format(
            name=self.config.get_project_name(),
            counter=f"{self._counter:04d}",
            timestamp=timestamp.isoformat(),
        )
        # Support strftime formatting (e.g., %Y_%m_%d_%H_%M_%S)
        metadata_filename = timestamp.strftime(metadata_filename)

        metadata_path = image_path.parent / metadata_filename
        if image_path.stem.endswith("_dst"):
            # The image kept both DST-fold passes under a _dst suffix; the
            # sidecar must follow, or the second pass overwrites the first
            # pass's sidecar and the {stem}_metadata.json pairing that
            # apply_overlay relies on breaks for the suffixed image.
            metadata_path = image_path.parent / f"{image_path.stem}_metadata.json"

        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2, default=str)

        return str(metadata_path)

    def close(self):
        """Close and cleanup camera resources."""
        if self.picam2:
            logger.debug("Closing camera...")
            try:
                self.picam2.close()
            finally:
                # Null the handle even when close() raises: a half-dead
                # Picamera2 kept here pins the device in picamera2's global
                # registry, and every later open fails with "Camera in use".
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

    # build_overlay returns None unless the overlay is switched on, and only
    # imports Pillow in the case where it is.
    with ImageCapture(config, post_process=build_overlay(config.config)) as capture:
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
    configure_logging(args.config)

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
