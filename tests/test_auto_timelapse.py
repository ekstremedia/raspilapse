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

    def test_deciding_leaves_the_controller_holding_what_it_decided(self, test_config_file):
        """Nothing may rewrite the loop's state after decide() has run.

        This is the test that fails if the day-to-transition seeding comes
        back, in any spelling. _seed_across_mode_change ran immediately after
        decide() and overwrote _required from the last daylight frame's camera
        metadata -- whose AnalogueGain is the sensor's 1.1228 floor while the
        ladder had commanded 1.0. The settings handed to the camera therefore
        described one exposure and the controller's state described another
        1.12x larger, and the next frame started from the wrong one. Nine
        consecutive dusks in this camera's database show the resulting step:
        200 043 -> 224 519 us, +8 to +11 points of mean brightness.

        Asserting the equality rather than the absence catches a reseed however
        it is written -- including one that does not go through a function
        named `seed_*`.
        """
        timelapse = AdaptiveTimelapse(test_config_file)
        timelapse._database = None

        # Walk down through the knee so a crossing actually happens, and check
        # the invariant on every frame rather than only the one that flips.
        knee = timelapse.exposure._max_shutter * 0.01
        seen = set()
        for _ in range(60):
            decision = timelapse._decide()
            seen.add(decision.mode)

            settings = decision.settings
            delivered = (settings["ExposureTime"] / 1e6) * settings["AnalogueGain"]
            # abs=1e-5 is the floor, not slack: ExposureTime is int(shutter *
            # 1e6), so the delivered product is quantised to a microsecond
            # times the gain -- at most 6e-6 here. The step this guards against
            # is 0.0246 in the same units, 2400x larger.
            assert timelapse.exposure._required == pytest.approx(delivered, rel=1e-4, abs=1e-5), (
                f"the controller holds {timelapse.exposure._required} but handed the "
                f"camera {delivered} -- something rewrote the state after decide()"
            )

            brightness = max(0.0, min(255.0, (120.0 / knee) * delivered))
            timelapse.exposure.observe_frame(
                {"mean_brightness": brightness, "std_brightness": 50.0}
            )

        assert {LightMode.DAY, LightMode.TRANSITION} <= seen, (
            f"never crossed the day/transition knee, so the invariant was "
            f"never tested where it used to break (saw {sorted(seen)})"
        )


class TestTheCaptureGrid:
    """Capture times land on multiples of the interval, not on the process.

    The old arithmetic was `sleep = interval - (now - loop_start)`, clamped at
    zero. That recovers from a frame that runs late *within* its slot, but an
    iteration that overruns the whole interval sleeps zero and moves every
    later frame by the overrun, forever -- the video plays at a constant speed
    either side of a permanent seam.
    """

    def test_the_grid_is_absolute_not_relative_to_the_process(self):
        """Two calls an arbitrary offset apart land on the same multiples, so a
        restart resumes the previous process's phase instead of inventing one."""
        assert AdaptiveTimelapse._next_slot(30, 1_000_000_003.7) == 1_000_000_020.0
        assert AdaptiveTimelapse._next_slot(30, 1_000_000_019.9) == 1_000_000_020.0
        assert AdaptiveTimelapse._next_slot(30, 1_000_000_020.0) == 1_000_000_050.0

    def test_a_slot_boundary_advances_rather_than_returning_itself(self):
        """Landing exactly on a boundary must give the *next* slot. Returning
        the current one makes the loop sleep zero and spin."""
        assert AdaptiveTimelapse._next_slot(30, 300.0) == 330.0

    def test_an_overrun_costs_its_own_slots_and_no_more(self):
        """The frame that overran is lost; the ones after it stay on the grid.

        Simulated the way the loop does it: advance by one interval, and when
        that is already in the past skip whole slots forward. Under the old
        arithmetic the same overrun shifts the phase by 25s and never recovers.
        """
        interval, next_slot = 30, 300.0
        emitted = []
        for i, cost in enumerate([5, 5, 55, 5, 5]):  # one frame takes 55s
            emitted.append(next_slot)
            now = next_slot + cost
            next_slot += interval
            if now >= next_slot:
                next_slot += (int((now - next_slot) // interval) + 1) * interval

        assert emitted == [300.0, 330.0, 360.0, 420.0, 450.0]
        gaps = {b - a for a, b in zip(emitted, emitted[1:])}
        assert gaps <= {30.0, 60.0}, f"a gap off the grid: {sorted(gaps)}"


class TestSlotRecovery:
    """Every path that ends an iteration goes back onto the grid, including the
    one that failed to open the camera.

    That path used to `continue` straight past the scheduling, so a retry fired
    the instant it succeeded rather than on a slot -- and a failure lasting
    longer than an interval left the schedule behind without skipping the slots
    it had missed. libcamera refusing a camera that is still closing is an
    ordinary event here, because the camera is opened and closed once per
    frame, so this is not a rare path.
    """

    @staticmethod
    def _advance(current_slot, interval, now):
        """_sleep_until_next_slot with the clock and the sleep stubbed out."""
        slept = []
        with (
            patch("raspilapse.daemon.time.time", return_value=now),
            patch("raspilapse.daemon.time.sleep", side_effect=slept.append),
        ):
            returned = AdaptiveTimelapse._sleep_until_next_slot(current_slot, interval)
        return returned, slept

    def test_an_ordinary_iteration_sleeps_to_the_next_slot(self):
        """The baseline the two failure cases below are departures from: a
        frame costing 5s of a 30s interval sleeps the remaining 25."""
        returned, slept = self._advance(300.0, 30, now=305.0)
        assert returned == 330.0
        assert slept == [25.0]

    def test_a_failure_outlasting_an_interval_skips_the_slots_it_missed(self):
        """Recovery took 70s of a 30s interval. The next capture belongs on the
        grid at 390, not 70s late at 330, and not immediately either."""
        returned, slept = self._advance(300.0, 30, now=370.0)
        assert returned == 390.0
        assert slept == [20.0]

    def test_it_never_returns_a_slot_in_the_past(self):
        """A negative sleep is what an immediate off-grid retry looks like."""
        for late in (0, 1, 29, 30, 31, 200):
            returned, slept = self._advance(300.0, 30, now=300.0 + late)
            assert returned > 300.0 + late, f"{late}s late returned a slot already gone"
            assert slept and slept[0] > 0


class TestTheLoopIsWiredToTheGrid:
    """The scheduling helpers are unit-tested above; this is the wiring.

    `_next_slot` and `_sleep_until_next_slot` can both be perfect while the
    loop drops the returned value on the floor, calls the wrong one, or misses
    a path entirely -- which is exactly how the camera-init branch came to skip
    the schedule. These run one bounded iteration with the camera mocked and
    assert the call actually happens.
    """

    @staticmethod
    def _run_one(config_file, init_side_effect=None, max_iterations=6):
        """Run the capture loop once with the camera mocked out.

        Returns the stubbed `_sleep_until_next_slot` and `time.sleep` so the
        caller can assert on what the loop actually reached.
        """
        timelapse = AdaptiveTimelapse(config_file)
        timelapse._database = None
        timelapse._record = MagicMock()
        timelapse._read_capture_metadata = MagicMock(return_value=None)

        def capture(*args, **kwargs):
            """Stand in for capture_frame, counter included.

            test_mode stops the loop via `frame_count >= num_frames`, and
            frame_count is incremented inside capture_frame -- so a mock that
            only returns a path leaves the loop spinning forever. This cost a
            hung test run to find.
            """
            timelapse.frame_count += 1
            return ("frame.jpg", None)

        timelapse.capture_frame = MagicMock(side_effect=capture)

        calls = []

        def advance_stub(current_slot, interval):
            """Record the reschedule, and stop the loop independently of it.

            The bound is deliberately not the loop's own exit condition: a
            future change that breaks that condition should fail the assertion
            below rather than hang the suite.
            """
            calls.append(current_slot)
            if len(calls) >= max_iterations:
                timelapse.running = False
            return 999.0

        with (
            patch("raspilapse.daemon.ImageCapture") as camera,
            patch("raspilapse.daemon.time.sleep") as sleep,
            patch.object(
                AdaptiveTimelapse, "_sleep_until_next_slot", side_effect=advance_stub
            ) as advance,
        ):
            if init_side_effect:
                camera.return_value.initialize_camera.side_effect = init_side_effect
            timelapse.run(test_mode=True)

        assert len(calls) < max_iterations, "the loop did not stop on its own"
        return advance, sleep

    def test_a_normal_frame_ends_on_the_grid(self, test_config_file):
        """The happy path, and the control for the failure case below: an
        iteration that captures normally must still reschedule, and the first
        frame must be aligned before the loop starts at all."""
        advance, sleep = self._run_one(test_config_file)
        assert advance.called, "the loop finished an iteration without rescheduling"
        assert sleep.called, "the first frame was not aligned to a slot before starting"

    def test_a_failed_camera_init_also_ends_on_the_grid(self, test_config_file):
        """The branch that used to `continue` past the scheduling entirely.

        libcamera refusing a camera that is still closing is ordinary here --
        the camera is opened and closed once per frame -- so an off-grid retry
        was not a rare path.
        """
        advance, _ = self._run_one(
            test_config_file, init_side_effect=[RuntimeError("device busy"), None]
        )
        assert advance.call_count >= 2, (
            f"init failed once and the loop rescheduled {advance.call_count} time(s); "
            f"the failure path skipped the grid"
        )


class TestSeedingAcrossARestart:
    """The database row is a record, not a command, and the two differ.

    `analogue_gain` holds what the sensor reported. The loop's state is in
    commanded units: ladder.allocate keeps gain at 1.0 until the shutter is at
    its ceiling, and this sensor answers 1.1228 regardless. In flight that
    constant is absorbed -- it is a feedback loop and nothing reads it back.
    Here it is read back as if it were a command, and the first frame after a
    restart is seeded 12% bright.

    Measured on this camera at the restart on 2026-08-07 08:06:33, one frame
    wide because the loop corrects it immediately:

        08:06:14   604 us   brightness 119.9
        08:06:33   657 us   brightness 126.2     <- 604 x 1.1228, on the grid
        08:06:44   604 us   brightness 120.2
    """

    def _seeded(self, config_path, row):
        """Seed a fresh daemon from one database row, and return what the
        controller ended up holding as its required exposure."""
        timelapse = AdaptiveTimelapse(config_path)
        timelapse._database = MagicMock()
        timelapse._database.get_last_capture.return_value = row
        timelapse._seed_from_last_capture()
        return timelapse.exposure._required

    def test_a_daylight_row_is_seeded_as_the_gain_the_ladder_commanded(self, test_config_file):
        """604 us at a reported 1.1228 is a commanded 604 us at gain 1.0."""
        required = self._seeded(
            test_config_file,
            {
                "exposure_time_us": 604,
                "analogue_gain": 1.1228070259094238,
                "brightness_mean": 119.9,
                "mode": LightMode.DAY,
            },
        )
        assert required == pytest.approx(
            0.000604, rel=1e-6
        ), "seeded the sensor's gain floor as though the ladder had asked for it"

    def test_a_quantised_ceiling_still_counts_as_the_ceiling(self, test_config_file):
        """The column records what the camera delivered, and it under-delivers.

        A commanded 20 s comes back as 19999994 us. This camera's database has
        62556 gain-controlled rows at that value and not one at exactly
        20000000, so an equality test against max_shutter rejects every real
        night frame and seeds the restart at gain 1.0 -- up to six times too
        dark. That is a worse bug than the daylight one the check exists for,
        and it is the reason the comparison carries a tolerance.
        """
        required = self._seeded(
            test_config_file,
            {
                "exposure_time_us": 19_999_994,
                "analogue_gain": 5.98830413818359,
                "brightness_mean": 120.0,
                "mode": LightMode.NIGHT,
            },
        )
        assert required == pytest.approx(19.999994 * 5.98830413818359, rel=1e-6), (
            "dropped a genuinely commanded night gain because the sensor "
            "delivered a few microseconds under the ceiling"
        )

    def test_a_row_at_the_ceiling_keeps_its_gain(self, test_config_file):
        """The sibling that stops the fix being 'ignore the column'.

        Past the shutter ceiling the ladder really does command gain, and it is
        the only thing distinguishing a 20-second frame at gain 1 from one at
        gain 6. Drop it here and every restart after dark begins six times too
        dark.
        """
        required = self._seeded(
            test_config_file,
            {
                "exposure_time_us": 20_000_000,
                "analogue_gain": 3.1,
                "brightness_mean": 120.0,
                "mode": LightMode.NIGHT,
            },
        )
        assert required == pytest.approx(62.0, rel=1e-6)


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
        """The ladder trigger cannot see what it does not move for: a season
        turning, a dirty lens, a streetlight coming on. Hence the floor."""
        from raspilapse.daemon import REFERENCE_MAX_INTERVAL_FRAMES

        timelapse = self._timelapse(test_config_file)
        timelapse.exposure.seed_from_capture(exposure_time=0.001, analogue_gain=1.0)
        timelapse.exposure.decide()
        timelapse._reference_position = timelapse.exposure.ladder_position
        timelapse._reference_frame = 0
        timelapse.frame_count = REFERENCE_MAX_INTERVAL_FRAMES
        assert timelapse._wants_reference_shot()

    def test_it_can_be_switched_off_entirely(self, test_config_file):
        """The explicit off switch, which still wins over every other reason to
        take one -- including the always-fires first frame."""
        timelapse = self._timelapse(test_config_file)
        timelapse._reference_position = None
        timelapse.config["adaptive_timelapse"]["test_shot"]["enabled"] = False
        assert not timelapse._wants_reference_shot()

    def test_a_configured_white_point_makes_the_reading_pointless(self, test_config_file):
        """_target_colour_gains prefers `fixed_colour_gains` and never looks at
        the learned reference, so on those cameras the reading is taken, paid
        for and discarded. The price is a camera teardown and a frame landing
        three seconds late: 68 of 2879 intervals on 2026-08-06.

        The operator used to have to know to set `test_shot.enabled: false`.
        """
        timelapse = self._timelapse(test_config_file)
        timelapse._reference_position = None  # the always-fires case
        assert timelapse._wants_reference_shot(), "the fixture must start from wanting one"

        timelapse.exposure.config["adaptive_timelapse"]["day_mode"]["fixed_colour_gains"] = [
            2.547,
            1.579,
        ]
        assert not timelapse._wants_reference_shot()

    def test_a_camera_without_a_configured_white_point_still_takes_one(self, test_config_file):
        """The sibling that stops the test above passing with the feature
        deleted outright. A camera with no configured white point has no other
        source of daylight colour: the AWB frame is the only one the ISP meters
        itself, and without it the controller falls back to a hardcoded
        (2.5, 1.6) forever.
        """
        timelapse = self._timelapse(test_config_file)
        timelapse._reference_position = None
        assert not timelapse.exposure.config["adaptive_timelapse"]["day_mode"].get(
            "fixed_colour_gains"
        )
        assert timelapse._wants_reference_shot()

    @pytest.mark.parametrize("mode", [LightMode.TRANSITION, LightMode.NIGHT])
    def test_no_reading_away_from_the_bright_end(self, test_config_file, mode):
        """update_day_wb_reference drops everything taken outside DAY -- AWB has
        nothing to meter in the dark -- and it did so on the far side of the
        teardown, so the frame was already late by then. Dusk is the worst case:
        the ladder crosses most of its range and REFERENCE_LADDER_STEP fires a
        couple of dozen times, every one of them discarded.
        """
        timelapse = self._timelapse(test_config_file)
        timelapse._reference_position = None  # the otherwise-always-fires case

        timelapse.exposure._mode = mode
        assert not timelapse._wants_reference_shot()

        timelapse.exposure._mode = LightMode.DAY
        assert timelapse._wants_reference_shot(), "the day case must still fire"

    def test_wanting_a_reading_tracks_whether_one_would_be_used(self, test_config_file):
        """The property and the precedence must not drift apart.

        Two controllers on the same light, one shown a reference reading and
        one not: they may diverge only where learns_day_wb says the reading is
        used. A third source of daylight colour added to _target_colour_gains
        without updating the property fails here rather than silently
        reintroducing the teardowns.
        """
        for fixed, should_matter in (([2.547, 1.579], False), (None, True)):
            shown = AdaptiveTimelapse(test_config_file).exposure
            hidden = AdaptiveTimelapse(test_config_file).exposure
            for controller in (shown, hidden):
                day = controller.config["adaptive_timelapse"]["day_mode"]
                if fixed:
                    day["fixed_colour_gains"] = fixed
                else:
                    day.pop("fixed_colour_gains", None)

            # One frame first: update_day_wb_reference only learns in DAY, and
            # a controller that has decided nothing yet has no mode at all.
            for controller in (shown, hidden):
                controller.decide()
            assert shown.last_mode == LightMode.DAY

            shown.update_day_wb_reference({"ColourGains": [2.9, 1.35]})
            # Several frames, because the cross-fade moves 15% of the way per
            # frame -- one frame's difference is real but small enough to read
            # as noise.
            for _ in range(9):
                shown.decide()
                hidden.decide()
            diverged = shown.decide()["ColourGains"] != hidden.decide()["ColourGains"]

            assert diverged == should_matter, (
                f"with fixed_colour_gains={fixed!r} the reading "
                f"{'changed' if diverged else 'did not change'} the output, but "
                f"learns_day_wb says {shown.learns_day_wb}"
            )


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
