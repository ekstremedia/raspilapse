"""Tests for auto_timelapse module."""

import json
import os
import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from raspilapse.daemon import AdaptiveTimelapse, LightMode


@pytest.fixture
def test_config_file():
    """Create a temporary test configuration file."""
    config_data = {
        "camera": {
            "resolution": {"width": 1280, "height": 720},
            "transforms": {"horizontal_flip": False, "vertical_flip": False},
            "controls": {},
        },
        "output": {
            "directory": "test_photos",
            "filename_pattern": "{name}_{counter}.jpg",
            "project_name": "test_project",
            "quality": 85,
            "symlink_latest": {"enabled": True, "path": "/tmp/test_status.jpg"},
        },
        "system": {
            "create_directories": True,
            "save_metadata": True,
            "metadata_filename": "{name}_{counter}_metadata.json",
            "metadata_folder": "metadata",
        },
        "overlay": {
            "enabled": False,
        },
        "adaptive_timelapse": {
            "enabled": True,
            "interval": 30,
            "num_frames": 0,
            "light_thresholds": {
                "night": 10,
                "day": 100,
            },
            "night_mode": {
                "max_exposure_time": 20.0,
                "min_exposure_time": 1.0,
                "analogue_gain": 6,
                "awb_enable": False,
            },
            "day_mode": {
                "awb_enable": True,
            },
            "transition_mode": {
                "smooth_transition": True,
                "analogue_gain_min": 1.0,
                "analogue_gain_max": 2.5,
            },
            "test_shot": {
                "enabled": True,
                "exposure_time": 0.1,
                "analogue_gain": 1.0,
            },
        },
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(config_data, f)
        config_path = f.name

    yield config_path

    # Cleanup
    os.unlink(config_path)


class TestSymlinkFunctionality:
    """Test symlink creation for latest image."""

    def test_create_symlink_enabled(self, test_config_file):
        """Test symlink creation when enabled."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Create a test image
        temp_dir = tempfile.mkdtemp()
        image_path = os.path.join(temp_dir, "test_image.jpg")
        with open(image_path, "w") as f:
            f.write("test")

        try:
            # Create symlink
            timelapse._create_latest_symlink(image_path)

            # Verify symlink exists
            symlink_path = Path("/tmp/test_status.jpg")
            assert symlink_path.exists() or symlink_path.is_symlink()

            # Verify it points to the correct file
            if symlink_path.is_symlink():
                target = symlink_path.resolve()
                assert target == Path(image_path).resolve()

            # Cleanup
            if symlink_path.exists():
                symlink_path.unlink()
        finally:
            os.unlink(image_path)
            os.rmdir(temp_dir)

    def test_create_symlink_disabled(self, test_config_file):
        """Test symlink not created when disabled."""
        # Load config and disable symlink
        with open(test_config_file, "r") as f:
            config_data = yaml.safe_load(f)

        config_data["output"]["symlink_latest"]["enabled"] = False

        with open(test_config_file, "w") as f:
            yaml.dump(config_data, f)

        timelapse = AdaptiveTimelapse(test_config_file)

        # Create a test image
        temp_dir = tempfile.mkdtemp()
        image_path = os.path.join(temp_dir, "test_image.jpg")
        with open(image_path, "w") as f:
            f.write("test")

        try:
            # Attempt to create symlink (should do nothing)
            timelapse._create_latest_symlink(image_path)

            # Symlink should not exist (or if it does, it's from another test)
            # We just verify the function doesn't crash
            assert True

        finally:
            os.unlink(image_path)
            os.rmdir(temp_dir)

    def test_symlink_updates_on_new_capture(self, test_config_file):
        """Test symlink updates to point to latest image."""
        timelapse = AdaptiveTimelapse(test_config_file)

        temp_dir = tempfile.mkdtemp()
        symlink_path = Path("/tmp/test_status.jpg")

        try:
            # Create first image
            image1 = os.path.join(temp_dir, "image1.jpg")
            with open(image1, "w") as f:
                f.write("image1")

            timelapse._create_latest_symlink(image1)

            if symlink_path.is_symlink():
                target1 = symlink_path.resolve()
                assert target1 == Path(image1).resolve()

            # Create second image
            image2 = os.path.join(temp_dir, "image2.jpg")
            with open(image2, "w") as f:
                f.write("image2")

            timelapse._create_latest_symlink(image2)

            # Symlink should now point to image2
            if symlink_path.is_symlink():
                target2 = symlink_path.resolve()
                assert target2 == Path(image2).resolve()

        finally:
            # Cleanup
            if symlink_path.exists():
                symlink_path.unlink()
            if os.path.exists(image1):
                os.unlink(image1)
            if os.path.exists(image2):
                os.unlink(image2)
            os.rmdir(temp_dir)

    def test_symlink_permission_error(self, test_config_file):
        """Test handling of permission errors."""
        # Update config to use a restricted path
        with open(test_config_file, "r") as f:
            config_data = yaml.safe_load(f)

        config_data["output"]["symlink_latest"]["path"] = "/root/status.jpg"

        with open(test_config_file, "w") as f:
            yaml.dump(config_data, f)

        timelapse = AdaptiveTimelapse(test_config_file)

        # Create test image
        temp_dir = tempfile.mkdtemp()
        image_path = os.path.join(temp_dir, "test.jpg")
        with open(image_path, "w") as f:
            f.write("test")

        try:
            # This should log an error but not crash
            timelapse._create_latest_symlink(image_path)

            # If we get here without exception, test passes
            assert True
        finally:
            os.unlink(image_path)
            os.rmdir(temp_dir)


class TestLightMode:
    """Test light mode enumeration."""

    def test_light_mode_constants(self):
        """Test light mode constants."""
        assert LightMode.NIGHT == "night"
        assert LightMode.DAY == "day"
        assert LightMode.TRANSITION == "transition"


class TestAdaptiveTimelapse:
    """Test AdaptiveTimelapse class."""

    def test_init(self, test_config_file):
        """Test initialization."""
        timelapse = AdaptiveTimelapse(test_config_file)
        assert timelapse.config is not None
        assert timelapse.running is True
        assert timelapse.frame_count == 0

    def test_calculate_lux(self, test_config_file):
        """Test lux calculation."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Create test image for calculate_lux
        temp_dir = tempfile.mkdtemp()
        test_image = os.path.join(temp_dir, "test.jpg")

        try:
            # Create a dummy image
            from PIL import Image

            img = Image.new("RGB", (100, 100), color=(128, 128, 128))
            img.save(test_image)

            # Test typical metadata
            metadata = {
                "ExposureTime": 10000,  # 10ms
                "AnalogueGain": 2.0,
            }

            lux = timelapse.calculate_lux(test_image, metadata)
            assert isinstance(lux, float)
            assert lux > 0
        finally:
            os.unlink(test_image)
            os.rmdir(temp_dir)

    def test_signal_handler(self, test_config_file):
        """Test signal handler stops the timelapse."""
        timelapse = AdaptiveTimelapse(test_config_file)
        assert timelapse.running is True

        # Simulate SIGTERM
        timelapse._signal_handler(15, None)
        assert timelapse.running is False

    def test_take_test_shot(self, test_config_file):
        """Test taking a test shot."""
        import json
        import tempfile

        # Create metadata file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"ExposureTime": 100000, "AnalogueGain": 1.0}, f)
            metadata_path = f.name

        try:
            # Mock ImageCapture class completely
            with patch("raspilapse.daemon.ImageCapture") as mock_capture_class:
                # Mock the context manager
                mock_instance = MagicMock()
                mock_capture_class.return_value.__enter__.return_value = mock_instance
                mock_capture_class.return_value.__exit__.return_value = None

                # Mock capture_request to return metadata
                mock_request = MagicMock()
                mock_request.get_metadata.return_value = {
                    "ExposureTime": 100000,
                    "AnalogueGain": 1.0,
                }
                mock_instance.picam2.capture_request.return_value = mock_request

                timelapse = AdaptiveTimelapse(test_config_file)
                image_path, metadata = timelapse.take_test_shot()

                assert image_path is not None
                assert isinstance(metadata, dict)
                assert "ExposureTime" in metadata
                # Verify capture_request was called
                mock_instance.picam2.capture_request.assert_called_once()
                # Verify request was released
                mock_request.release.assert_called_once()
        finally:
            os.unlink(metadata_path)

    def test_calculate_lux_no_pil(self, test_config_file):
        """Test lux calculation fallback when PIL not available."""
        timelapse = AdaptiveTimelapse(test_config_file)

        metadata = {
            "ExposureTime": 50000,  # 50ms
            "AnalogueGain": 1.5,
        }

        # Mock PIL.Image.open to raise ImportError
        with patch("PIL.Image.open", side_effect=ImportError("PIL not available")):
            lux = timelapse.calculate_lux("/fake/path.jpg", metadata)
            assert isinstance(lux, float)
            assert lux > 0

    def test_close_camera_fast(self, test_config_file):
        """Test fast camera close method."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Mock capture object
        mock_capture = MagicMock()
        mock_capture.picam2 = MagicMock()

        # Should not raise exception
        timelapse._close_camera_fast(mock_capture, "night")
        mock_capture.close.assert_called_once()

    def test_close_camera_fast_none(self, test_config_file):
        """Test close with None capture."""
        timelapse = AdaptiveTimelapse(test_config_file)
        # Should not raise exception
        timelapse._close_camera_fast(None, "day")

    def test_calculate_lux_error_handling(self, test_config_file):
        """Test lux calculation handles image read errors."""
        timelapse = AdaptiveTimelapse(test_config_file)

        metadata = {
            "ExposureTime": 10000,
            "AnalogueGain": 1.0,
        }

        # Non-existent image
        lux = timelapse.calculate_lux("/nonexistent/image.jpg", metadata)
        assert isinstance(lux, float)
        assert lux > 0  # Should return fallback value


class TestBrightnessAnalysis:
    """Test image brightness analysis."""

    def test_analyze_image_brightness(self, test_config_file):
        """Test brightness analysis returns expected metrics."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Create test image
        temp_dir = tempfile.mkdtemp()
        test_image = os.path.join(temp_dir, "test.jpg")

        try:
            from PIL import Image

            # Create image with known brightness
            img = Image.new("L", (100, 100), color=128)  # Mid-gray
            img.save(test_image)

            result = timelapse._analyze_image_brightness(test_image)

            assert "mean_brightness" in result
            assert "median_brightness" in result
            assert "std_brightness" in result
            assert "underexposed_percent" in result
            assert "overexposed_percent" in result

            # Mid-gray image should have mean ~128
            assert abs(result["mean_brightness"] - 128) < 5
        finally:
            os.unlink(test_image)
            os.rmdir(temp_dir)

    def test_analyze_image_brightness_error(self, test_config_file):
        """Test brightness analysis handles errors gracefully."""
        timelapse = AdaptiveTimelapse(test_config_file)

        result = timelapse._analyze_image_brightness("/nonexistent/image.jpg")
        assert result == {}


class TestTimelapseCaptureFlow:
    """Test the main timelapse capture flow."""

    def test_capture_frame(self, test_config_file):
        """Test single frame capture."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Mock ImageCapture
        mock_capture = MagicMock()
        mock_capture.capture.return_value = ("/tmp/frame.jpg", "/tmp/frame_metadata.json")

        # Test capture
        image_path, metadata_path = timelapse.capture_frame(mock_capture, "night")

        assert image_path == "/tmp/frame.jpg"
        assert timelapse.frame_count == 1
        mock_capture.capture.assert_called_once()

    def test_capture_frame_increments_counter(self, test_config_file):
        """Test that frame counter increments."""
        timelapse = AdaptiveTimelapse(test_config_file)
        mock_capture = MagicMock()
        mock_capture.capture.return_value = ("/tmp/frame.jpg", None)

        # Capture multiple frames
        timelapse.capture_frame(mock_capture, "day")
        timelapse.capture_frame(mock_capture, "day")
        timelapse.capture_frame(mock_capture, "day")

        assert timelapse.frame_count == 3


class TestWhiteBalanceWiring:
    """Which frame each white-balance input is allowed to come from.

    The controller cannot check this for itself -- every value it is handed is
    a plausible pair of gains -- so it has to be checked here, where the wiring
    is. Both defects below shipped, and both were visible in the timelapse:
    a one-frame colour step at dusk, and daylight colour that crept a little
    every day instead of holding at the configured white point.
    """

    def test_the_daylight_reference_is_not_learned_from_an_ordinary_frame(self, test_config_file):
        """An ordinary frame is taken with AWB off, so its ColourGains are the
        ones the controller just chose. Learning from those makes the reference
        its own input: it holds wherever it was pushed, and never corrects."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse._database = None
        timelapse.exposure.update_day_wb_reference = MagicMock()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"ColourGains": [2.9, 1.4], "ExposureTime": 10000}, f)
            metadata_path = f.name

        try:
            decision = MagicMock()
            decision.mode = LightMode.DAY
            timelapse._record(decision, "frame.jpg", metadata_path, None)
        finally:
            os.unlink(metadata_path)

        timelapse.exposure.update_day_wb_reference.assert_not_called()
        # Still kept: the exposure handover starts from the last daylight frame.
        assert timelapse._last_day_capture_metadata["ColourGains"] == [2.9, 1.4]

    def test_the_reference_shot_is_where_it_is_learned(self, test_config_file):
        """The one frame taken with AWB on is the only honest source."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.exposure.update_day_wb_reference = MagicMock()
        timelapse.take_test_shot = MagicMock(return_value=("shot.jpg", {"ColourGains": [2.2, 1.7]}))

        timelapse._take_reference_shot()

        timelapse.exposure.update_day_wb_reference.assert_called_once_with(
            {"ColourGains": [2.2, 1.7]}
        )

    def test_a_reading_the_controller_rejects_does_not_end_the_timelapse(self, test_config_file):
        """Learning the reference moved into the capture loop, whose only
        handler is the one that ends the run. A malformed reading should cost
        the frame's colour and a line in the log, not the timelapse."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.exposure.update_day_wb_reference = MagicMock(
            side_effect=ValueError("malformed ColourGains")
        )
        timelapse.take_test_shot = MagicMock(return_value=("shot.jpg", {"ColourGains": [2.2]}))
        timelapse.frame_count = 40

        timelapse._take_reference_shot()

        # Recorded anyway, for the same reason the outer handler records it: a
        # reading that fails every time must not leave _wants_reference_shot
        # true and tear the camera down once per frame, forever.
        assert timelapse._reference_frame == 40
        assert timelapse._reference_position == timelapse.exposure.ladder_position

    def test_the_handover_cannot_inject_awb_gains(self, test_config_file):
        """seed_from_metadata assigns state directly, bypassing the wb_speed
        cross-fade. Handing it the AWB reference shot is what produced the
        one-frame step; it now takes the daylight frame and no colour at all."""
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse._previous_mode = LightMode.DAY
        timelapse._last_day_capture_metadata = {"ExposureTime": 500_000, "AnalogueGain": 2.0}
        timelapse.exposure.seed_from_metadata = MagicMock()

        timelapse._seed_across_mode_change(LightMode.TRANSITION)

        timelapse.exposure.seed_from_metadata.assert_called_once_with(
            {"ExposureTime": 500_000, "AnalogueGain": 2.0}
        )


class TestPolarAwareness:
    """Test polar day/night awareness functionality."""

    def test_init_location_with_config(self, test_config_file):
        """Test location initialization with valid config."""
        with open(test_config_file, "r") as f:
            config_data = yaml.safe_load(f)
        config_data["location"] = {
            "latitude": 68.7,
            "longitude": 15.4,
            "timezone": "Europe/Oslo",
        }
        with open(test_config_file, "w") as f:
            yaml.dump(config_data, f)

        timelapse = AdaptiveTimelapse(test_config_file)

        # Location is recorded alongside each frame now, not used to decide
        # anything. Without astral there is simply no elevation to record.
        if timelapse._location is None:
            pytest.skip("astral not available")
        assert timelapse._get_sun_elevation() is not None

    def test_init_location_without_config(self, test_config_file):
        """Test location initialization without config."""
        timelapse = AdaptiveTimelapse(test_config_file)

        # Without location config, location should be None
        assert timelapse._location is None

    def test_get_sun_elevation_without_location(self, test_config_file):
        """Test sun elevation returns None without location."""
        timelapse = AdaptiveTimelapse(test_config_file)

        result = timelapse._get_sun_elevation()

        assert result is None

    def test_run_reads_sun_elevation_after_the_call_that_sets_it(self):
        """Static check: the inline form is invisible to every runtime test.

        Passing `self._sun_elevation` and `self._is_polar_day(...)` as arguments
        to the same call is what caused the bug, and it type-checks, imports and
        passes 896 tests. Only the source shape shows it.
        """
        import ast

        src = Path(__file__).resolve().parent.parent / "raspilapse" / "daemon.py"
        for node in ast.walk(ast.parse(src.read_text())):
            if not isinstance(node, ast.Call):
                continue
            reads_attr = any(
                isinstance(a, ast.Attribute) and a.attr == "_sun_elevation" for a in node.args
            )
            calls_polar = any(
                isinstance(a, ast.Call)
                and isinstance(a.func, ast.Attribute)
                and a.func.attr == "_is_polar_day"
                for a in node.args
            )
            assert not (reads_attr and calls_polar), (
                f"daemon.py:{node.lineno} passes self._sun_elevation and "
                "_is_polar_day() to the same call. Arguments evaluate left to "
                "right, so the attribute is read before the call populates it."
            )


class TestDiagnosticEnrichment:
    """Test metadata enrichment with diagnostics."""

    def test_enrich_metadata_with_diagnostics(self, test_config_file):
        """Test diagnostic data is added to metadata."""
        import json

        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse.exposure._smoothed_lux = 500.0
        timelapse.exposure._last_mode = LightMode.DAY
        timelapse._sun_elevation = 15.0

        temp_dir = tempfile.mkdtemp()
        try:
            # Create test metadata file
            metadata_path = os.path.join(temp_dir, "test_meta.json")
            image_path = os.path.join(temp_dir, "test_image.jpg")

            with open(metadata_path, "w") as f:
                json.dump({"ExposureTime": 5000}, f)

            # Create dummy image
            with open(image_path, "wb") as f:
                f.write(b"\xff\xd8\xff\xe0")

            result = timelapse._enrich_metadata_with_diagnostics(
                metadata_path, image_path, LightMode.DAY, lux=500.0, raw_lux=520.0
            )

            assert result is True

            # Read enriched metadata
            with open(metadata_path, "r") as f:
                enriched = json.load(f)

            assert "diagnostics" in enriched
            diag = enriched["diagnostics"]
            assert diag["mode"] == LightMode.DAY
            assert diag["raw_lux"] == 520.0
            assert diag["smoothed_lux"] == 500.0
            assert diag["sun_elevation"] == 15.0
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_enrich_metadata_with_ladder_position(self, test_config_file):
        """Where on the exposure ladder the frame sat is recorded with it.

        Passed in as a snapshot taken the instant decide() returned, rather
        than read back off the controller later. The handover seeding runs
        between those two moments and overwrites the shutter, gain and ladder
        position, so reading late described the seed and not the frame.
        """
        import json

        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse._sun_elevation = 5.0

        # Put the controller somewhere identifiable on the ladder, and take the
        # diagnostics snapshot the loop takes -- the instant decide() returns.
        timelapse.exposure.seed_from_capture(exposure_time=10.0, analogue_gain=3.0)
        timelapse.exposure.decide()
        snapshot = timelapse.exposure.diagnostics()
        expected = timelapse.exposure.ladder_position
        assert 0.0 < expected < 1.0, "the seed should be mid-ladder"

        temp_dir = tempfile.mkdtemp()
        try:
            metadata_path = os.path.join(temp_dir, "test_meta.json")
            image_path = os.path.join(temp_dir, "test_image.jpg")

            with open(metadata_path, "w") as f:
                json.dump({}, f)

            with open(image_path, "wb") as f:
                f.write(b"\xff\xd8\xff\xe0")

            result = timelapse._enrich_metadata_with_diagnostics(
                metadata_path,
                image_path,
                LightMode.TRANSITION,
                lux=100.0,
                controller_diagnostics=snapshot,
            )

            assert result is True

            with open(metadata_path, "r") as f:
                enriched = json.load(f)

            assert "diagnostics" in enriched
            assert enriched["diagnostics"]["ladder_position"] == pytest.approx(expected, abs=1e-4)
        finally:
            import shutil

            shutil.rmtree(temp_dir)


class TestSymlinkCreation:
    """Test latest image symlink creation."""

    def test_create_latest_symlink(self):
        """Test symlink is created to latest image."""
        import yaml

        temp_dir = tempfile.mkdtemp()
        try:
            # Create config with symlink enabled
            symlink_path = os.path.join(temp_dir, "latest.jpg")
            config_path = os.path.join(temp_dir, "config.yml")
            config = {
                "output": {
                    "directory": temp_dir,
                    "symlink_latest": {
                        "enabled": True,
                        "path": symlink_path,
                    },
                },
                "camera": {"resolution": {"width": 640, "height": 480}},
            }
            with open(config_path, "w") as f:
                yaml.dump(config, f)

            timelapse = AdaptiveTimelapse(config_path)

            # Create test image
            image_path = os.path.join(temp_dir, "test_image.jpg")
            with open(image_path, "wb") as f:
                f.write(b"\xff\xd8\xff\xe0")

            # Test symlink creation
            timelapse._create_latest_symlink(image_path)

            assert os.path.islink(symlink_path)
            assert os.path.realpath(symlink_path) == os.path.realpath(image_path)
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_create_latest_symlink_updates_existing(self):
        """Test symlink is updated when already exists."""
        import yaml

        temp_dir = tempfile.mkdtemp()
        try:
            symlink_path = os.path.join(temp_dir, "latest.jpg")
            config_path = os.path.join(temp_dir, "config.yml")
            config = {
                "output": {
                    "directory": temp_dir,
                    "symlink_latest": {
                        "enabled": True,
                        "path": symlink_path,
                    },
                },
                "camera": {"resolution": {"width": 640, "height": 480}},
            }
            with open(config_path, "w") as f:
                yaml.dump(config, f)

            timelapse = AdaptiveTimelapse(config_path)

            # Create test images
            image1 = os.path.join(temp_dir, "image1.jpg")
            image2 = os.path.join(temp_dir, "image2.jpg")
            with open(image1, "wb") as f:
                f.write(b"\xff\xd8\xff\xe0")
            with open(image2, "wb") as f:
                f.write(b"\xff\xd8\xff\xe0")

            # Create initial symlink
            timelapse._create_latest_symlink(image1)
            assert os.path.realpath(symlink_path) == os.path.realpath(image1)

            # Update symlink
            timelapse._create_latest_symlink(image2)
            assert os.path.realpath(symlink_path) == os.path.realpath(image2)
        finally:
            import shutil

            shutil.rmtree(temp_dir)

        # Now it should switch (after hysteresis_frames threshold)
        # Default hysteresis is typically 3 frames


class TestMainFunction:
    """Test main function entry point."""

    def test_main_missing_config(self, monkeypatch, capsys):
        """Test main with missing config file."""
        monkeypatch.setattr(
            "sys.argv",
            ["auto_timelapse.py", "--config", "/nonexistent/config.yml"],
        )

        # Import and run main
        from raspilapse.daemon import main

        result = main()
        assert result == 1

    def test_main_help(self, monkeypatch, capsys):
        """Test main with --help flag."""
        monkeypatch.setattr("sys.argv", ["auto_timelapse.py", "--help"])

        from raspilapse.daemon import main

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0


@pytest.fixture
def direct_control_config_file():
    """Create config file with direct brightness control enabled."""
    config_data = {
        "camera": {
            "resolution": {"width": 1280, "height": 720},
            "transforms": {"horizontal_flip": False, "vertical_flip": False},
            "controls": {},
        },
        "output": {
            "directory": "test_photos",
            "filename_pattern": "{name}_{counter}.jpg",
            "project_name": "test_project",
            "quality": 85,
        },
        "system": {"create_directories": True, "save_metadata": False},
        "overlay": {"enabled": False},
        "adaptive_timelapse": {
            "enabled": True,
            "interval": 30,
            "num_frames": 0,
            "reference_lux": 3.8,
            "direct_brightness_control": True,
            "brightness_damping": 0.5,
            "light_thresholds": {"night": 10, "day": 100},
            "night_mode": {
                "max_exposure_time": 20.0,
                "analogue_gain": 6,
                "awb_enable": False,
            },
            "day_mode": {"awb_enable": True},
            "transition_mode": {
                "smooth_transition": True,
                "target_brightness": 120,
            },
            "test_shot": {
                "enabled": True,
                "exposure_time": 0.1,
                "analogue_gain": 1.0,
            },
        },
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(config_data, f)
        config_path = f.name

    yield config_path
    os.unlink(config_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestFeedbackWiring:
    """The capture loop must actually deliver measurements to the controller.

    A refactor once left run() writing _last_brightness onto AdaptiveTimelapse
    after that state had moved to ExposureController. Nothing raised: the write
    just created a dead attribute, the real one kept its startup seed value, and
    the exposure loop reacted to a measurement from minutes earlier. Unit tests
    of both halves passed; only the camera showed it.
    """

    def test_observe_frame_updates_the_controller(self, direct_control_config_file):
        tl = AdaptiveTimelapse(direct_control_config_file)

        tl.exposure.observe_frame(
            {
                "mean_brightness": 137.0,
                "percentile_95": 231.0,
                "std_brightness": 55.0,
                "overexposed_percent": 0.0,
                "underexposed_percent": 0.0,
            }
        )

        assert tl.exposure.last_brightness == 137.0
        assert tl.exposure.meter.p95 == 231.0

    def test_capture_loop_never_assigns_controller_state_to_self(self, direct_control_config_file):
        """Static check on AdaptiveTimelapse's source.

        Assigning `self._last_brightness = ...` inside AdaptiveTimelapse is
        always a bug now: that name belongs to ExposureController. Python
        happily creates a new attribute instead of raising, so only a check
        like this catches it -- calling observe_frame() directly in a test
        would not, because the broken code path is in run().
        """
        import inspect

        source = inspect.getsource(AdaptiveTimelapse)
        controller_state = {
            name
            for name in vars(AdaptiveTimelapse(direct_control_config_file).exposure)
            if name.startswith("_")
        }

        offenders = sorted(
            name for name in controller_state if re.search(rf"self\.{name}\s*=[^=]", source)
        )
        assert not offenders, (
            "AdaptiveTimelapse assigns state that belongs to ExposureController: "
            f"{offenders}. Route it through the controller instead."
        )

    def test_controller_reacts_to_the_reported_brightness(self, direct_control_config_file):
        """Too bright -> shorter exposure. The sign of the loop."""
        tl = AdaptiveTimelapse(direct_control_config_file)

        tl.exposure.seed_from_capture(exposure_time=0.02, analogue_gain=1.0)
        tl.exposure.observe_frame({"mean_brightness": 200.0, "percentile_95": 210.0})
        assert tl.exposure.decide()["ExposureTime"] < 0.02 * 1_000_000

        tl.exposure.seed_from_capture(exposure_time=0.02, analogue_gain=1.0)
        tl.exposure.observe_frame({"mean_brightness": 60.0, "percentile_95": 90.0})
        assert tl.exposure.decide()["ExposureTime"] > 0.02 * 1_000_000


class TestReferenceShotPolicy:
    """When to interrupt the loop for a white-balance reading.

    This used to happen on every frame, which cost two camera teardowns per
    capture. The shot's other job -- producing a lux figure -- moved onto the
    frame the camera was taking anyway, leaving only the white balance, which
    genuinely cannot be had any other way: it is the only frame the ISP meters
    itself.
    """

    @staticmethod
    def _timelapse(config_file, position=0.0, frame=0):
        from raspilapse.daemon import AdaptiveTimelapse

        timelapse = AdaptiveTimelapse(config_file)
        timelapse._reference_position = position
        timelapse._reference_frame = frame
        timelapse.frame_count = frame
        return timelapse

    def test_the_first_frame_always_needs_one(self, test_config_file):
        timelapse = self._timelapse(test_config_file)
        timelapse._reference_position = None
        assert timelapse._wants_reference_shot()

    def test_a_settled_scene_does_not(self, test_config_file):
        timelapse = self._timelapse(test_config_file)
        timelapse.exposure.seed_from_capture(exposure_time=0.001, analogue_gain=1.0)
        timelapse.exposure.decide()
        timelapse._reference_position = timelapse.exposure.ladder_position
        assert not timelapse._wants_reference_shot()

    def test_moving_light_does(self, test_config_file):
        from raspilapse.daemon import REFERENCE_LADDER_STEP

        timelapse = self._timelapse(test_config_file)
        timelapse.exposure.seed_from_capture(exposure_time=0.001, analogue_gain=1.0)
        timelapse.exposure.decide()
        timelapse._reference_position = (
            timelapse.exposure.ladder_position - REFERENCE_LADDER_STEP * 2
        )
        assert timelapse._wants_reference_shot()

    def test_a_stale_reading_expires_even_in_still_light(self, test_config_file):
        from raspilapse.daemon import REFERENCE_MAX_INTERVAL_FRAMES

        timelapse = self._timelapse(test_config_file)
        timelapse.exposure.seed_from_capture(exposure_time=0.001, analogue_gain=1.0)
        timelapse.exposure.decide()
        timelapse._reference_position = timelapse.exposure.ladder_position
        timelapse._reference_frame = 0
        timelapse.frame_count = REFERENCE_MAX_INTERVAL_FRAMES
        assert timelapse._wants_reference_shot()

    def test_it_can_be_switched_off_entirely(self, test_config_file):
        timelapse = self._timelapse(test_config_file)
        timelapse._reference_position = None
        timelapse.config["adaptive_timelapse"]["test_shot"]["enabled"] = False
        assert not timelapse._wants_reference_shot()


class TestLuxFromTheCapturedFrame:
    """Lux no longer needs a shot of its own."""

    @staticmethod
    def _measure(config_file, brightness, exposure_us, gain):
        from raspilapse.daemon import AdaptiveTimelapse

        timelapse = AdaptiveTimelapse(config_file)
        return timelapse._measure_lux(
            brightness, {"ExposureTime": exposure_us, "AnalogueGain": gain}
        )

    def test_it_follows_the_documented_formula(self, test_config_file):
        """(brightness / 128) * (1 / seconds) * (1 / gain) * calibration."""
        from raspilapse.daemon import LUX_CALIBRATION

        got = self._measure(test_config_file, 128.0, 10_000, 1.0)
        assert got == pytest.approx((128 / 128) * (1 / 0.01) * (1 / 1.0) * LUX_CALIBRATION)

    def test_daylight_lands_in_a_physically_plausible_range(self, test_config_file):
        """A light meter reads roughly 20,000 lux under bright overcast.

        The old figure was measured from a shot pinned at 0.2 s, which
        saturates in daylight -- 368k rows of the database carry the identical
        value 887.19. The scale changed here deliberately.
        """
        daylight = self._measure(test_config_file, 126.0, 210, 1.12)
        assert 5_000 < daylight < 100_000, daylight

    def test_a_dark_night_reads_near_zero(self, test_config_file):
        night = self._measure(test_config_file, 90.0, 20_000_000, 6.0)
        assert 0.0 < night < 1.0, night

    def test_more_light_for_the_same_exposure_reads_higher(self, test_config_file):
        dim = self._measure(test_config_file, 60.0, 10_000, 1.0)
        bright = self._measure(test_config_file, 200.0, 10_000, 1.0)
        assert bright > dim

    def test_the_same_brightness_from_a_longer_exposure_reads_lower(self, test_config_file):
        """A frame that needed more exposure to look the same saw less light."""
        short = self._measure(test_config_file, 120.0, 1_000, 1.0)
        long = self._measure(test_config_file, 120.0, 1_000_000, 1.0)
        assert long < short

    def test_gain_counts_against_it_the_same_way(self, test_config_file):
        low = self._measure(test_config_file, 120.0, 10_000, 1.0)
        high = self._measure(test_config_file, 120.0, 10_000, 6.0)
        assert high == pytest.approx(low / 6.0)

    @pytest.mark.parametrize(
        "brightness,exposure_us,gain",
        [(None, 10_000, 1.0), (120.0, None, 1.0), (120.0, 10_000, None), (120.0, 0, 1.0)],
    )
    def test_missing_or_impossible_inputs_give_nothing(
        self, test_config_file, brightness, exposure_us, gain
    ):
        assert self._measure(test_config_file, brightness, exposure_us, gain) is None
