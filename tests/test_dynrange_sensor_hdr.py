"""sensor_hdr: subdev discovery, the WDR toggle, size maths, the upscale.

All hardware access is mocked -- these tests must not touch a real sensor,
least of all on a Pi where the daemon owns the camera. The upscale stage is
pure Pillow and runs everywhere, CI included.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

import raspilapse.dynrange as dynrange_module
import raspilapse.dynrange.sensor_hdr as sensor_hdr_module
from raspilapse.dynrange import DynamicRange
from raspilapse.dynrange.sensor_hdr import find_wdr_subdev, hdr_main_size, set_wdr, upscale_to


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    """Discovery caches per process; every test starts unlooked."""
    monkeypatch.setattr(sensor_hdr_module, "_subdev_cache", None)


def completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


WDR_LISTING = "wide_dynamic_range 0x009a0915 (bool) : default=0 value=0"


class TestFindWdrSubdev:
    def test_finds_the_first_subdev_with_the_control(self, monkeypatch):
        monkeypatch.setattr(
            sensor_hdr_module.glob, "glob", lambda pattern: ["/dev/v4l-subdev1", "/dev/v4l-subdev0"]
        )
        with patch.object(sensor_hdr_module.subprocess, "run") as run:
            run.side_effect = [completed("brightness"), completed(WDR_LISTING)]
            assert find_wdr_subdev() == "/dev/v4l-subdev1"
        # Sorted order: subdev0 was asked first and lacked the control.
        assert run.call_args_list[0].args[0][2] == "/dev/v4l-subdev0"

    def test_no_subdevs_means_none(self, monkeypatch):
        monkeypatch.setattr(sensor_hdr_module.glob, "glob", lambda pattern: [])
        assert find_wdr_subdev() is None

    def test_missing_v4l2ctl_means_none(self, monkeypatch):
        monkeypatch.setattr(sensor_hdr_module.glob, "glob", lambda pattern: ["/dev/v4l-subdev0"])
        with patch.object(
            sensor_hdr_module.subprocess, "run", side_effect=FileNotFoundError("v4l2-ctl")
        ):
            assert find_wdr_subdev() is None

    def test_positive_answer_is_cached(self, monkeypatch):
        monkeypatch.setattr(sensor_hdr_module.glob, "glob", lambda pattern: ["/dev/v4l-subdev0"])
        with patch.object(
            sensor_hdr_module.subprocess, "run", return_value=completed(WDR_LISTING)
        ) as run:
            find_wdr_subdev()
            find_wdr_subdev()
        assert run.call_count == 1

    def test_negative_answer_is_cached_too(self, monkeypatch):
        """Cameras without the control must not pay a subprocess sweep per
        frame."""
        monkeypatch.setattr(sensor_hdr_module.glob, "glob", lambda pattern: ["/dev/v4l-subdev0"])
        with patch.object(
            sensor_hdr_module.subprocess, "run", return_value=completed("nothing here")
        ) as run:
            assert find_wdr_subdev() is None
            assert find_wdr_subdev() is None
        assert run.call_count == 1


class TestSetWdr:
    def test_builds_the_v4l2_command(self, monkeypatch):
        monkeypatch.setattr(sensor_hdr_module, "_subdev_cache", "/dev/v4l-subdev0")
        with patch.object(sensor_hdr_module.subprocess, "run", return_value=completed()) as run:
            assert set_wdr(True) is True
            assert set_wdr(False) is True
        commands = [call.args[0] for call in run.call_args_list]
        assert commands[0] == [
            "v4l2-ctl",
            "-d",
            "/dev/v4l-subdev0",
            "--set-ctrl",
            "wide_dynamic_range=1",
        ]
        assert commands[1][-1] == "wide_dynamic_range=0"

    def test_no_subdev_returns_false(self, monkeypatch):
        monkeypatch.setattr(sensor_hdr_module.glob, "glob", lambda pattern: [])
        assert set_wdr(True) is False

    def test_grabbed_sensor_returns_false(self, monkeypatch):
        """A running camera holds the subdev; the set fails, not the daemon."""
        monkeypatch.setattr(sensor_hdr_module, "_subdev_cache", "/dev/v4l-subdev0")
        with patch.object(
            sensor_hdr_module.subprocess,
            "run",
            return_value=completed(
                returncode=1, stderr="VIDIOC_S_EXT_CTRLS: Device or resource busy"
            ),
        ):
            assert set_wdr(True) is False


class TestHdrMainSize:
    def test_4k_caps_to_the_hdr_mode(self):
        assert hdr_main_size((3840, 2160)) == (2304, 1296)

    def test_full_sensor_caps_to_the_hdr_mode(self):
        assert hdr_main_size((4608, 2592)) == (2304, 1296)

    def test_small_configured_sizes_pass_through(self):
        assert hdr_main_size((1920, 1080)) == (1920, 1080)
        assert hdr_main_size((2304, 1296)) == (2304, 1296)

    def test_tall_aspect_fits_by_height(self):
        width, height = hdr_main_size((4000, 3000))
        assert height <= 1296 and width <= 2304
        assert width / height == pytest.approx(4000 / 3000, abs=0.01)

    def test_dimensions_come_out_even(self):
        """Video encoders reject odd dimensions."""
        for configured in [(4000, 3000), (3841, 2161), (2305, 1297)]:
            width, height = hdr_main_size(configured)
            assert width % 2 == 0 and height % 2 == 0


class TestUpscaleTo:
    def test_upscales_and_stays_world_readable(self, tmp_path):
        small = tmp_path / "frame.jpg"
        Image.new("RGB", (230, 130), (90, 120, 150)).save(str(small), quality=90)
        assert upscale_to(str(small), (460, 260), quality=85) is True
        with Image.open(str(small)) as result:
            assert result.size == (460, 260)
        assert small.stat().st_mode & 0o644 == 0o644

    def test_frame_already_at_size_is_untouched(self, tmp_path):
        """Night frames captured natively must not be re-encoded."""
        native = tmp_path / "frame.jpg"
        Image.new("RGB", (460, 260), (20, 20, 30)).save(str(native), quality=90)
        before = native.read_bytes()
        assert upscale_to(str(native), (460, 260)) is True
        assert native.read_bytes() == before

    def test_unreadable_frame_returns_false(self, tmp_path):
        garbage = tmp_path / "corrupt.jpg"
        garbage.write_bytes(b"not a jpeg")
        assert upscale_to(str(garbage), (460, 260)) is False
        assert garbage.read_bytes() == b"not a jpeg"

    def test_no_temp_files_left_behind(self, tmp_path):
        small = tmp_path / "frame.jpg"
        Image.new("RGB", (230, 130)).save(str(small), quality=90)
        upscale_to(str(small), (460, 260))
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".upscale-")]
        assert leftovers == []


def make_dr(monkeypatch, method="sensor_hdr", subdev="/dev/v4l-subdev0", **block):
    monkeypatch.setattr(sensor_hdr_module, "find_wdr_subdev", lambda: subdev)
    config = {
        "camera": {"resolution": {"width": 3840, "height": 2160}},
        "adaptive_timelapse": {"dynamic_range": {"method": method, **block}},
    }
    return DynamicRange.from_config(config)


class TestFacadeWiring:
    def test_missing_subdev_degrades_to_off(self, monkeypatch, caplog):
        import logging

        logger = logging.getLogger("dynrange")
        logger.addHandler(caplog.handler)
        try:
            dr = make_dr(monkeypatch, subdev=None)
        finally:
            logger.removeHandler(caplog.handler)
        assert dr.method == "off"
        assert dr.label() == "off"

    def test_day_frame_enables_wdr_and_caps_the_size(self, monkeypatch):
        dr = make_dr(monkeypatch)
        with patch.object(sensor_hdr_module, "set_wdr", return_value=True) as set_wdr_mock:
            kwargs = dr.pre_open("day")
        set_wdr_mock.assert_called_once_with(True)
        assert kwargs == {"main_size_override": (2304, 1296)}

    @pytest.mark.parametrize("mode", ["transition", "night", None])
    def test_other_modes_disable_wdr_with_day_only(self, monkeypatch, mode):
        dr = make_dr(monkeypatch)
        with patch.object(sensor_hdr_module, "set_wdr", return_value=True) as set_wdr_mock:
            kwargs = dr.pre_open(mode)
        set_wdr_mock.assert_called_once_with(False)
        assert kwargs == {}

    def test_day_only_false_keeps_wdr_on_at_night(self, monkeypatch):
        dr = make_dr(monkeypatch, sensor_hdr={"day_only": False})
        with patch.object(sensor_hdr_module, "set_wdr", return_value=True) as set_wdr_mock:
            kwargs = dr.pre_open("night")
        set_wdr_mock.assert_called_once_with(True)
        assert "main_size_override" in kwargs

    def test_reference_shot_drops_wdr(self, monkeypatch):
        """The WB reference shot opens at full resolution; HDR must yield."""
        dr = make_dr(monkeypatch)
        with patch.object(sensor_hdr_module, "set_wdr", return_value=True) as set_wdr_mock:
            dr.pre_reference_shot()
        set_wdr_mock.assert_called_once_with(False)

    def test_shutdown_leaves_the_sensor_clean(self, monkeypatch):
        dr = make_dr(monkeypatch)
        with patch.object(sensor_hdr_module, "set_wdr", return_value=True) as set_wdr_mock:
            dr.shutdown()
        set_wdr_mock.assert_called_once_with(False)

    def test_other_methods_never_touch_the_sensor(self, monkeypatch):
        monkeypatch.setattr(dynrange_module.importlib.util, "find_spec", lambda name: object())
        dr = DynamicRange.from_config(
            {"adaptive_timelapse": {"dynamic_range": {"method": "fusion"}}}
        )
        with patch.object(sensor_hdr_module, "set_wdr") as set_wdr_mock:
            dr.pre_open("day")
            dr.pre_reference_shot()
            dr.shutdown()
        set_wdr_mock.assert_not_called()

    def test_upscale_runs_before_tone_map_and_overlay(self, monkeypatch):
        monkeypatch.setattr(dynrange_module.importlib.util, "find_spec", lambda name: object())
        from raspilapse.dynrange import tonemap

        calls = []
        monkeypatch.setattr(
            dynrange_module, "build_overlay", lambda config: lambda *a, **k: calls.append("overlay")
        )
        monkeypatch.setattr(
            sensor_hdr_module, "upscale_to", lambda *a, **k: calls.append("upscale") or True
        )
        monkeypatch.setattr(
            tonemap, "tone_map_file", lambda *a, **k: calls.append("tone_map") or True
        )
        dr = make_dr(monkeypatch, tone_map={"enabled": True})
        chain = dr.build_post_process({})
        chain("/tmp/frame.jpg", {}, "day")
        assert calls == ["upscale", "tone_map", "overlay"]

    def test_capture_falls_through_to_the_plain_path(self, monkeypatch):
        """sensor_hdr changes how the camera opens, not how it captures."""
        dr = make_dr(monkeypatch)
        capture = MagicMock()
        capture.capture.return_value = ("img.jpg", None)
        dr.capture_frame(capture, mode="day", settings={"ExposureTime": 10_000})
        capture.capture.assert_called_once()
        capture.capture_bracketed.assert_not_called()
