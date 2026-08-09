"""The raw method: fallback rules, the develop stage, DNG lifecycle.

A real develop needs a real DNG, which needs a real sensor -- that half is
exercised by raspilapse-drtest on hardware. Everything else is testable
here: the decision table, the disposal guarantees, and that the develop
stage can never cost the frame its JPEG.
"""

from unittest.mock import MagicMock

import pytest

import raspilapse.dynrange as dynrange_module
from raspilapse.dynrange import DynamicRange
from raspilapse.dynrange.raw_develop import DEVELOP_ESTIMATE_S, develop_dng, should_use_raw


class TestShouldUseRaw:
    def test_day_frames_fit(self):
        assert should_use_raw("day", 0.01, 30.0) is True

    def test_transition_frames_fit_while_short(self):
        assert should_use_raw("transition", 0.5, 30.0) is True

    def test_night_never_develops(self):
        assert should_use_raw("night", 0.01, 30.0) is False

    def test_long_exposures_fall_back(self):
        assert should_use_raw("transition", 12.0, 30.0) is False

    def test_short_intervals_fall_back(self):
        # 15s develop estimate cannot fit an 18s slot with 5s reserve.
        assert should_use_raw("day", 0.01, 18.0) is False

    def test_the_boundary_is_the_reserve(self):
        interval = DEVELOP_ESTIMATE_S + 5.0 + 1.0
        assert should_use_raw("day", 1.0, interval) is True
        assert should_use_raw("day", 1.1, interval) is False


class TestDevelopDng:
    def test_missing_rawpy_returns_false(self, tmp_path, monkeypatch):
        import sys

        jpeg = tmp_path / "frame.jpg"
        jpeg.write_bytes(b"isp jpeg")
        monkeypatch.setitem(sys.modules, "rawpy", None)
        assert develop_dng(str(tmp_path / "frame.dng"), str(jpeg), (100, 60)) is False
        assert jpeg.read_bytes() == b"isp jpeg"

    def test_invalid_dng_leaves_the_isp_jpeg(self, tmp_path):
        pytest.importorskip("rawpy")
        pytest.importorskip("cv2")
        jpeg = tmp_path / "frame.jpg"
        jpeg.write_bytes(b"isp jpeg")
        bad_dng = tmp_path / "frame.dng"
        bad_dng.write_bytes(b"not a dng at all")
        assert develop_dng(str(bad_dng), str(jpeg), (100, 60)) is False
        assert jpeg.read_bytes() == b"isp jpeg"
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".develop-")]
        assert leftovers == []


def make_dr(monkeypatch, method="raw", sidecar=None, interval=30):
    monkeypatch.setattr(dynrange_module.importlib.util, "find_spec", lambda name: object())
    config = {
        "adaptive_timelapse": {"interval": interval, "dynamic_range": {"method": method}},
        "output": {"directory": "images"},
    }
    if sidecar:
        config["output"]["dng_sidecar"] = sidecar
    return DynamicRange.from_config(config)


class TestPreOpenRawStream:
    def test_day_enables_the_raw_stream(self, monkeypatch):
        dr = make_dr(monkeypatch)
        assert dr.pre_open("day") == {"enable_raw": True}

    def test_night_does_not(self, monkeypatch):
        dr = make_dr(monkeypatch)
        assert dr.pre_open("night") == {}

    def test_off_method_with_sidecar_keeps_every_nth(self, monkeypatch):
        dr = make_dr(monkeypatch, method="off", sidecar={"enabled": True, "every_n_frames": 2})
        assert dr.pre_open("day") == {"enable_raw": True}  # frame 0: keeper
        assert dr.pre_open("day") == {}  # frame 1
        assert dr.pre_open("day") == {"enable_raw": True}  # frame 2

    def test_disabled_sidecar_never_asks_for_raw(self, monkeypatch):
        dr = make_dr(monkeypatch, method="off")
        for _ in range(3):
            assert dr.pre_open("day") == {}


class TestWantsDng:
    def test_raw_day_frame_saves(self, monkeypatch):
        dr = make_dr(monkeypatch)
        dr.pre_open("day")
        assert dr._wants_dng("day", {"ExposureTime": 10_000}) is True

    def test_never_without_the_stream(self, monkeypatch):
        """save_dng on a stream-less camera raises deep in picamera2."""
        dr = make_dr(monkeypatch)
        assert dr._wants_dng("day", {"ExposureTime": 10_000}) is False

    def test_long_exposure_falls_back_even_with_the_stream_on(self, monkeypatch):
        dr = make_dr(monkeypatch)
        dr.pre_open("transition")
        assert dr._wants_dng("transition", {"ExposureTime": 12_000_000}) is False

    def test_keeper_frame_saves_whatever_the_method(self, monkeypatch):
        dr = make_dr(monkeypatch, method="off", sidecar={"enabled": True, "every_n_frames": 5})
        dr.pre_open("day")
        assert dr._wants_dng("day", None) is True

    def test_save_dng_reaches_plain_capture(self, monkeypatch):
        dr = make_dr(monkeypatch)
        dr.pre_open("day")
        capture = MagicMock()
        capture.capture.return_value = ("img.jpg", None)
        dr.capture_frame(capture, mode="day", settings={"ExposureTime": 10_000})
        assert capture.capture.call_args.kwargs["save_dng"] is True


class TestDevelopStage:
    def stage(self, monkeypatch, tmp_path, method="raw", keeper=False, develop_result=True):
        dr = make_dr(monkeypatch, method=method, sidecar={"enabled": True, "max_files": 10})
        dr._keep_dng_this_frame = keeper
        dr._output_directory = str(tmp_path)
        from raspilapse.dynrange import raw_develop

        calls = []
        monkeypatch.setattr(
            raw_develop,
            "develop_dng",
            lambda dng, out, size, quality: calls.append((dng, out)) or develop_result,
        )
        return dr, calls

    def frame(self, tmp_path, with_dng=True):
        jpeg = tmp_path / "frame.jpg"
        jpeg.write_bytes(b"isp jpeg")
        if with_dng:
            (tmp_path / "frame.jpg.dng.tmp").write_bytes(b"raw bytes")
        return jpeg

    def test_no_dng_passes_through(self, monkeypatch, tmp_path):
        dr, calls = self.stage(monkeypatch, tmp_path)
        jpeg = self.frame(tmp_path, with_dng=False)
        assert dr._develop_stage(str(jpeg)) is True
        assert calls == []

    def test_raw_method_develops_then_discards(self, monkeypatch, tmp_path):
        dr, calls = self.stage(monkeypatch, tmp_path)
        jpeg = self.frame(tmp_path)
        assert dr._develop_stage(str(jpeg)) is True
        assert calls == [(str(jpeg) + ".dng.tmp", str(jpeg))]
        assert not (tmp_path / "frame.jpg.dng.tmp").exists()
        assert not (tmp_path / "frame.dng").exists()

    def test_keeper_is_promoted_not_deleted(self, monkeypatch, tmp_path):
        dr, calls = self.stage(monkeypatch, tmp_path, method="off", keeper=True)
        jpeg = self.frame(tmp_path)
        assert dr._develop_stage(str(jpeg)) is True
        assert calls == []  # method off: dispose only, no develop
        assert (tmp_path / "frame.dng").read_bytes() == b"raw bytes"
        assert not (tmp_path / "frame.jpg.dng.tmp").exists()

    def test_raw_keeper_develops_and_keeps(self, monkeypatch, tmp_path):
        dr, calls = self.stage(monkeypatch, tmp_path, keeper=True)
        jpeg = self.frame(tmp_path)
        assert dr._develop_stage(str(jpeg)) is True
        assert len(calls) == 1
        assert (tmp_path / "frame.dng").exists()

    def test_failed_develop_still_disposes_and_reports(self, monkeypatch, tmp_path):
        """The frame keeps its ISP JPEG; the DNG never lingers."""
        dr, calls = self.stage(monkeypatch, tmp_path, develop_result=False)
        jpeg = self.frame(tmp_path)
        assert dr._develop_stage(str(jpeg)) is False
        assert jpeg.read_bytes() == b"isp jpeg"
        assert not (tmp_path / "frame.jpg.dng.tmp").exists()

    def test_stage_is_wired_for_raw_and_sidecar_only(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(dynrange_module, "build_overlay", lambda config: sentinel)
        plain = make_dr(monkeypatch, method="off")
        assert plain.build_post_process({}) is sentinel
        raw = make_dr(monkeypatch, method="raw")
        assert raw.build_post_process({}) is not sentinel
        keeper = make_dr(monkeypatch, method="off", sidecar={"enabled": True})
        assert keeper.build_post_process({}) is not sentinel
