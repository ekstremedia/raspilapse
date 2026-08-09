"""Fusion: the convergence maths, the budget guard, the bracket capture flow.

Everything except the Mertens merge itself runs without OpenCV -- the plan
maths is pure, and capture_bracketed takes its fuse function by injection,
so the camera flow is tested with a scripted fake picam2 and a trivial fuser.
"""

from unittest.mock import MagicMock

import pytest

from raspilapse.camera.capture import ImageCapture
from raspilapse.dynrange import DynamicRange
from raspilapse.dynrange.fusion import (
    FULL_SPREAD_BELOW_S,
    build_fuse_fn,
    estimated_cost_s,
    plan_brackets,
    spread_ev,
)

EV = 2.0
T_OFF = 0.5


class TestSpreadEv:
    def test_full_spread_at_short_exposures(self):
        assert spread_ev(0.001, EV, T_OFF) == EV
        assert spread_ev(FULL_SPREAD_BELOW_S, EV, T_OFF) == EV

    def test_zero_at_and_beyond_the_single_shot_point(self):
        assert spread_ev(T_OFF, EV, T_OFF) == 0.0
        assert spread_ev(20.0, EV, T_OFF) == 0.0

    def test_zero_when_spread_is_disabled(self):
        assert spread_ev(0.001, 0.0, T_OFF) == 0.0

    def test_monotonically_decreasing(self):
        """The anti-flicker property: longer exposure never means more
        spread, so consecutive timelapse frames cannot jump between looks."""
        exposures = [0.01, 0.05, 0.1, 0.2, 0.3, 0.4, 0.49, 0.5, 1.0]
        spreads = [spread_ev(t, EV, T_OFF) for t in exposures]
        assert spreads == sorted(spreads, reverse=True)

    def test_continuous_at_the_convergence_point(self):
        """No cliff where fusion hands over to single-shot."""
        assert spread_ev(0.499, EV, T_OFF) < 0.02

    def test_midpoint_of_the_log_ramp(self):
        # sqrt(0.05 * 0.5) is halfway between the knees in log space.
        halfway = (FULL_SPREAD_BELOW_S * T_OFF) ** 0.5
        assert spread_ev(halfway, EV, T_OFF) == pytest.approx(EV / 2)


class TestPlanBrackets:
    def plan(self, base_s, brackets=3, interval=30.0, settle=8):
        return plan_brackets(base_s, brackets, EV, T_OFF, interval, settle)

    def test_three_brackets_are_base_under_over(self):
        plan = self.plan(0.01)
        assert plan[0] == 0.01
        assert plan[1] == pytest.approx(0.01 / 4)  # -2 EV
        assert plan[2] == pytest.approx(0.01 * 4)  # +2 EV

    def test_two_brackets_are_base_then_under(self):
        """When only one extra shot is allowed, rescue the highlights --
        blown ones are unrecoverable downstream, dark shadows are merely
        dark."""
        plan = self.plan(0.01, brackets=2)
        assert plan == [0.01, pytest.approx(0.01 / 4)]

    def test_converged_exposure_gets_a_single_entry(self):
        assert self.plan(0.5) == [0.5]
        assert self.plan(20.0) == [20.0]

    def test_budget_drops_the_over_bracket_first(self):
        """The over bracket is the longest; it goes before the under one."""
        generous = self.plan(0.1, interval=30.0)
        tight = self.plan(0.1, interval=12.0)
        assert len(generous) == 3
        assert len(tight) == 2
        assert tight[1] < 0.1  # the survivor is the under bracket

    def test_hopeless_budget_degrades_to_single_shot(self):
        assert len(self.plan(0.3, interval=6.0)) == 1

    def test_day_exposures_fit_comfortably_in_a_30s_slot(self):
        """The whole point: at real daytime exposures nothing is dropped."""
        for base_s in (0.001, 0.005, 0.02, 0.1):
            assert len(self.plan(base_s)) == 3

    def test_cost_estimate_charges_settle_frames_to_extra_brackets_only(self):
        base_only = estimated_cost_s([0.1], 8)
        with_bracket = estimated_cost_s([0.1, 0.025], 8)
        assert base_only == pytest.approx(0.2)
        # 9 frame periods of 0.125s, plus fuse and encode estimates.
        assert with_bracket == pytest.approx(0.2 + 9 * 0.125 + 4.0)


class FakeRequest:
    """A capture_request whose metadata scripts how the sensor settles."""

    def __init__(self, exposure_us, array="frame"):
        self._exposure_us = exposure_us
        self._array = array
        self.released = False

    def get_metadata(self):
        return {"ExposureTime": self._exposure_us}

    def make_array(self, stream):
        assert stream == "main"
        return self._array

    def release(self):
        self.released = True


class FakePicam2:
    """Serves scripted requests and mimics the settle lag of set_controls."""

    def __init__(self, requests):
        self._requests = list(requests)
        self.served = []
        self.control_calls = []

    def capture_request(self):
        request = self._requests.pop(0)
        self.served.append(request)
        return request

    def set_controls(self, controls):
        self.control_calls.append(controls)


@pytest.fixture
def capture(tmp_path):
    """An ImageCapture around a fake camera, saving into tmp_path."""
    config = MagicMock()
    config.get_output_directory.return_value = str(tmp_path)
    config.should_organize_by_date.return_value = False
    config.should_create_directories.return_value = True
    config.get_filename_pattern.return_value = "test_%Y_%m_%d_%H_%M_%S.jpg"
    config.get_project_name.return_value = "test"
    config.should_save_metadata.return_value = False
    instance = ImageCapture(config)
    instance._compute_brightness_from_lores = lambda request: {"mean_brightness": 100.0}
    return instance


def first_frame_fuser(frames):
    """A fuse_fn that proves which arrays arrived, without any cv2."""
    return ("+".join(frames)).encode()


class TestCaptureBracketed:
    def test_happy_path_fuses_base_and_settled_brackets(self, capture, tmp_path):
        capture.picam2 = FakePicam2(
            [
                FakeRequest(10_000, "base"),
                # The under bracket: one stale frame, then the command lands.
                FakeRequest(10_000, "stale"),
                FakeRequest(2_520, "under"),  # within 10% of 2500
                # The over bracket lands immediately.
                FakeRequest(39_000, "over"),  # within 10% of 40000
            ]
        )
        image_path, metadata_path = capture.capture_bracketed(
            [10_000, 2_500, 40_000], first_frame_fuser, mode="day"
        )
        content = (tmp_path / image_path.split("/")[-1]).read_bytes()
        assert content == b"base+under+over"
        assert capture.last_settle_frames == [1, 0]
        assert all(r.released for r in capture.picam2.served)

    def test_settle_cap_uses_the_frame_anyway(self, capture):
        """A sensor that never settles costs a warning, not a stuck loop."""
        stubborn = [FakeRequest(10_000, "base")] + [
            FakeRequest(10_000, f"stale{i}") for i in range(12)
        ]
        capture.picam2 = FakePicam2(stubborn)
        capture.capture_bracketed([10_000, 2_500], first_frame_fuser, settle_frames_max=3)
        # Base + 3 discards + the give-up frame.
        assert len(capture.picam2.served) == 5
        assert capture.last_settle_frames == [3]

    def test_base_metrics_feed_the_exposure_loop(self, capture):
        capture.picam2 = FakePicam2([FakeRequest(10_000, "base"), FakeRequest(2_500, "under")])
        capture.capture_bracketed([10_000, 2_500], first_frame_fuser)
        assert capture.last_brightness_metrics == {"mean_brightness": 100.0}

    def test_gain_is_never_touched_between_brackets(self, capture):
        capture.picam2 = FakePicam2([FakeRequest(10_000, "base"), FakeRequest(2_500, "under")])
        capture.capture_bracketed([10_000, 2_500], first_frame_fuser)
        for controls in capture.picam2.control_calls:
            assert "AnalogueGain" not in controls

    def test_fewer_than_two_exposures_is_a_programming_error(self, capture):
        capture.picam2 = FakePicam2([])
        with pytest.raises(ValueError):
            capture.capture_bracketed([10_000], first_frame_fuser)

    def test_uninitialized_camera_raises(self, capture):
        with pytest.raises(RuntimeError):
            capture.capture_bracketed([10_000, 2_500], first_frame_fuser)

    def test_extra_metadata_reaches_the_sidecar(self, capture, tmp_path):
        capture.config.should_save_metadata.return_value = True
        capture.config.get_metadata_pattern.return_value = "test_%Y_%m_%d_%H_%M_%S_metadata.json"
        capture.config.get_resolution.return_value = [64, 48]
        capture.config.get_quality.return_value = 85
        capture.picam2 = FakePicam2([FakeRequest(10_000, "base"), FakeRequest(2_500, "under")])
        _, metadata_path = capture.capture_bracketed(
            [10_000, 2_500],
            first_frame_fuser,
            extra_metadata={"dr_method": "fusion", "fusion_exposures_us": [10_000, 2_500]},
        )
        import json

        sidecar = json.loads(open(metadata_path).read())
        assert sidecar["dr_method"] == "fusion"
        assert sidecar["fusion_exposures_us"] == [10_000, 2_500]
        assert sidecar["ExposureTime"] == 10_000  # the base shot's metadata


class TestDispatcher:
    def make_dr(self, monkeypatch, method="fusion", interval=30):
        import raspilapse.dynrange as dynrange_module

        monkeypatch.setattr(dynrange_module.importlib.util, "find_spec", lambda name: object())
        return DynamicRange.from_config(
            {"adaptive_timelapse": {"interval": interval, "dynamic_range": {"method": method}}}
        )

    def test_day_decision_goes_through_capture_bracketed(self, monkeypatch):
        dr = self.make_dr(monkeypatch)
        capture = MagicMock()
        capture.last_settle_frames = [2]
        capture.capture_bracketed.return_value = ("img.jpg", None)
        settings = {"ExposureTime": 10_000, "AnalogueGain": 1.0}
        result = dr.capture_frame(capture, mode="day", settings=settings)
        assert result == ("img.jpg", None)
        exposures = capture.capture_bracketed.call_args.args[0]
        assert exposures[0] == 10_000
        assert len(exposures) == 3
        capture.capture.assert_not_called()

    def test_night_decision_falls_through_to_plain_capture(self, monkeypatch):
        """Converged fusion IS the old pipeline -- no fusion code runs."""
        dr = self.make_dr(monkeypatch)
        capture = MagicMock()
        capture.capture.return_value = ("img.jpg", None)
        settings = {"ExposureTime": 20_000_000, "AnalogueGain": 6.0}
        result = dr.capture_frame(capture, mode="night", settings=settings)
        assert result == ("img.jpg", None)
        capture.capture_bracketed.assert_not_called()

    def test_off_method_never_plans(self, monkeypatch):
        dr = self.make_dr(monkeypatch, method="off")
        capture = MagicMock()
        capture.capture.return_value = ("img.jpg", None)
        dr.capture_frame(capture, mode="day", settings={"ExposureTime": 10_000})
        capture.capture_bracketed.assert_not_called()

    def test_missing_settings_falls_through(self, monkeypatch):
        dr = self.make_dr(monkeypatch)
        capture = MagicMock()
        capture.capture.return_value = ("img.jpg", None)
        dr.capture_frame(capture, mode="day", settings=None)
        capture.capture_bracketed.assert_not_called()

    def test_fusion_details_are_recorded_in_metadata(self, monkeypatch):
        dr = self.make_dr(monkeypatch)
        capture = MagicMock()
        capture.last_settle_frames = [1]
        capture.capture_bracketed.return_value = ("img.jpg", None)
        dr.capture_frame(
            capture,
            mode="day",
            settings={"ExposureTime": 10_000},
            extra_metadata={"dr_method": "fusion"},
        )
        merged = capture.capture_bracketed.call_args.kwargs["extra_metadata"]
        assert merged["dr_method"] == "fusion"
        assert merged["fusion_exposures_us"][0] == 10_000

    def test_settle_observations_update_the_estimate(self, monkeypatch):
        dr = self.make_dr(monkeypatch)
        capture = MagicMock()
        capture.last_settle_frames = [2, 3]
        capture.capture_bracketed.return_value = ("img.jpg", None)
        before = dr._settle_ema
        dr.capture_frame(capture, mode="day", settings={"ExposureTime": 10_000})
        assert dr._settle_ema < before  # observed 3 < seeded 8


class TestFuseFn:
    """The Mertens merge itself; needs OpenCV."""

    @pytest.fixture(autouse=True)
    def _cv2(self):
        pytest.importorskip("cv2")

    def test_fuses_a_synthetic_triptych(self):
        import numpy as np

        shape = (48, 64, 3)
        under = np.full(shape, 40, dtype=np.uint8)
        base = np.full(shape, 120, dtype=np.uint8)
        over = np.full(shape, 220, dtype=np.uint8)
        encoded = build_fuse_fn(90)([base, under, over])
        import cv2

        decoded = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR)
        assert decoded.shape == shape
        # The fused result sits between the extremes, nearer the base.
        assert 40 < decoded.mean() < 220

    def test_output_survives_saturated_inputs(self):
        """Mertens overshoots slightly on saturated pixels; without the clip
        they wrap to black when cast to uint8."""
        import numpy as np

        white = np.full((32, 32, 3), 255, dtype=np.uint8)
        encoded = build_fuse_fn(90)([white, white, white])
        import cv2

        decoded = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR)
        assert decoded.min() > 200  # no wrapped-to-black pixels
