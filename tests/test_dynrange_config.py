"""The dynamic_range config seam: parsing, degradation, and the off no-op.

Construction must never raise -- a camera with a typo'd or under-provisioned
config keeps photographing as `off`. These tests run everywhere: they exercise
the seam itself, not the methods behind it, so neither OpenCV nor rawpy is
needed.
"""

import logging

import pytest

import raspilapse.dynrange as dynrange_module
from raspilapse.dynrange import METHODS, DynamicRange


def make_config(**dynamic_range):
    config = {"adaptive_timelapse": {}}
    if dynamic_range:
        config["adaptive_timelapse"]["dynamic_range"] = dynamic_range
    return config


@pytest.fixture
def dynrange_logs(caplog):
    """Capture records from the dynrange logger.

    get_logger() sets propagate=False so lines are not duplicated into the
    journal, which also means caplog's root handler never sees them.
    """
    logger = logging.getLogger("dynrange")
    logger.addHandler(caplog.handler)
    previous = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)
        logger.setLevel(previous)


class TestMethodParsing:
    def test_default_is_off(self):
        assert DynamicRange.from_config({}).method == "off"

    def test_empty_block_is_off(self):
        assert DynamicRange.from_config(make_config()).method == "off"

    def test_null_block_is_off(self):
        """A bare `dynamic_range:` line in YAML arrives as None, not a dict."""
        config = {"adaptive_timelapse": {"dynamic_range": None}}
        assert DynamicRange.from_config(config).method == "off"

    def test_scalar_block_degrades_with_a_warning(self, dynrange_logs):
        """`dynamic_range: fusion` is a natural typo for `method: fusion` --
        it must degrade, not crash construction with AttributeError."""
        config = {"adaptive_timelapse": {"dynamic_range": "fusion"}}
        assert DynamicRange.from_config(config).method == "off"
        assert any("mapping" in r.message for r in dynrange_logs.records)

    @pytest.mark.parametrize(
        "config",
        [
            {"adaptive_timelapse": "everything on please"},
            {"adaptive_timelapse": {"dynamic_range": ["fusion", "raw"]}},
            {"adaptive_timelapse": {"dynamic_range": {"tone_map": True}}},
            {"adaptive_timelapse": {"dynamic_range": {"fusion": 3}}},
            {"camera": {"resolution": "4k"}},
            {"output": "images"},
            {"output": {"dng_sidecar": True}},
        ],
    )
    def test_no_malformed_shape_crashes_construction(self, config):
        """The never-raises contract covers malformed YAML shapes, not just
        wrong values: every sub-block read must survive a scalar or a list
        where a mapping belongs."""
        DynamicRange.from_config(config)

    def test_explicit_off(self):
        assert DynamicRange.from_config(make_config(method="off")).method == "off"

    def test_case_is_forgiven(self):
        assert DynamicRange.from_config(make_config(method="OFF")).method == "off"

    def test_unknown_method_degrades_to_off(self, dynrange_logs):
        dr = DynamicRange.from_config(make_config(method="magic"))
        assert dr.method == "off"
        assert any("magic" in r.message for r in dynrange_logs.records)

    def test_sensor_hdr_needs_no_optional_packages(self, monkeypatch):
        """sensor_hdr talks to V4L2, not to cv2/rawpy; it must parse without
        them. The subdev probe is mocked -- these tests run identically on a
        camera-equipped Pi and in CI, and must never touch real hardware."""
        import raspilapse.dynrange.sensor_hdr as sensor_hdr_module

        monkeypatch.setattr(sensor_hdr_module, "find_wdr_subdev", lambda: "/dev/v4l-subdev0")
        assert DynamicRange.from_config(make_config(method="sensor_hdr")).method == "sensor_hdr"

    @pytest.mark.parametrize("method", METHODS)
    def test_every_advertised_method_parses(self, method, monkeypatch):
        import raspilapse.dynrange.sensor_hdr as sensor_hdr_module

        monkeypatch.setattr(dynrange_module.importlib.util, "find_spec", lambda name: object())
        monkeypatch.setattr(sensor_hdr_module, "find_wdr_subdev", lambda: "/dev/v4l-subdev0")
        dr = DynamicRange.from_config(make_config(method=method))
        if method == "tone_map":
            # Sugar: normalises to the off capture path + the tone_map stage,
            # but the label keeps the configured name.
            assert dr.method == "off"
            assert dr.label() == "tone_map"
        else:
            assert dr.method == method


class TestDependencyDegradation:
    @pytest.mark.parametrize("method", ["fusion", "tone_map", "raw"])
    def test_missing_dependency_degrades_to_off(self, method, monkeypatch, dynrange_logs):
        monkeypatch.setattr(dynrange_module.importlib.util, "find_spec", lambda name: None)
        dr = DynamicRange.from_config(make_config(method=method))
        assert dr.method == "off"
        warnings = [r for r in dynrange_logs.records if "apt install" in r.message]
        assert len(warnings) == 1

    def test_warning_names_the_apt_packages(self, monkeypatch, dynrange_logs):
        monkeypatch.setattr(dynrange_module.importlib.util, "find_spec", lambda name: None)
        DynamicRange.from_config(make_config(method="raw"))
        messages = [r.message for r in dynrange_logs.records if "apt install" in r.message]
        assert messages, "expected a dependency warning"
        assert "python3-rawpy" in messages[0]
        assert "python3-opencv" in messages[0]

    def test_label_reports_the_degraded_reality(self, monkeypatch):
        """Frames taken without cv2 must not claim they were fused."""
        monkeypatch.setattr(dynrange_module.importlib.util, "find_spec", lambda name: None)
        dr = DynamicRange.from_config(make_config(method="fusion"))
        assert dr.label() == "off"


class TestLegacyHdrBlock:
    def test_presence_warns_once(self, dynrange_logs):
        config = {"adaptive_timelapse": {"hdr": {"enabled": True}}}
        dr = DynamicRange.from_config(config)
        assert dr.method == "off"
        assert sum("dynamic_range" in r.message for r in dynrange_logs.records) == 1

    def test_absence_stays_quiet(self, dynrange_logs):
        DynamicRange.from_config(make_config(method="off"))
        assert not dynrange_logs.records


class TestOffIsTheOldPipeline:
    def test_post_process_is_build_overlay(self, monkeypatch):
        """With everything off the chain IS build_overlay's callable."""
        sentinel = object()
        monkeypatch.setattr(dynrange_module, "build_overlay", lambda config: sentinel)
        dr = DynamicRange.from_config(make_config(method="off"))
        assert dr.build_post_process({}) is sentinel

    def test_pre_open_asks_for_nothing(self):
        dr = DynamicRange.from_config(make_config(method="off"))
        assert dr.pre_open("day") == {}
        assert dr.pre_open(None) == {}


@pytest.fixture
def all_deps_present(monkeypatch):
    monkeypatch.setattr(dynrange_module.importlib.util, "find_spec", lambda name: object())


class TestToneMapParsing:
    def test_method_tone_map_is_sugar(self, all_deps_present):
        """`method: tone_map` = `method: off` + `tone_map.enabled: true`,
        so the capture path stays the plain single-shot pipeline."""
        dr = DynamicRange.from_config(make_config(method="tone_map"))
        assert dr.method == "off"
        assert dr.tone_map_enabled is True
        assert dr.label() == "tone_map"

    def test_tone_map_combines_with_fusion(self, all_deps_present):
        dr = DynamicRange.from_config(make_config(method="fusion", tone_map={"enabled": True}))
        assert dr.method == "fusion"
        assert dr.tone_map_enabled is True
        assert dr.label() == "fusion+tm"

    def test_disabled_by_default(self, all_deps_present):
        dr = DynamicRange.from_config(make_config(method="off"))
        assert dr.tone_map_enabled is False

    def test_strength_is_clamped(self, all_deps_present):
        over = DynamicRange.from_config(make_config(method="tone_map", tone_map={"strength": 7}))
        under = DynamicRange.from_config(make_config(method="tone_map", tone_map={"strength": -1}))
        assert over._tone_map_strength == 1.0
        assert under._tone_map_strength == 0.0

    def test_missing_cv2_degrades_tone_map(self, monkeypatch, dynrange_logs):
        monkeypatch.setattr(dynrange_module.importlib.util, "find_spec", lambda name: None)
        dr = DynamicRange.from_config(make_config(method="tone_map"))
        assert dr.tone_map_enabled is False
        assert dr.label() == "off"
        warnings = [r for r in dynrange_logs.records if "python3-opencv" in r.message]
        assert len(warnings) == 1


class TestPostProcessComposition:
    def test_tone_map_runs_before_the_overlay(self, all_deps_present, monkeypatch):
        """The overlay bar must be drawn on the mapped image, never mapped."""
        from raspilapse.dynrange import tonemap

        calls = []
        monkeypatch.setattr(
            dynrange_module, "build_overlay", lambda config: lambda *a, **k: calls.append("overlay")
        )
        monkeypatch.setattr(
            tonemap, "tone_map_file", lambda *a, **k: calls.append("tone_map") or True
        )
        dr = DynamicRange.from_config(make_config(method="tone_map"))
        chain = dr.build_post_process({})
        chain("/tmp/frame.jpg", {}, "day")
        assert calls == ["tone_map", "overlay"]

    def test_overlay_result_is_returned(self, all_deps_present, monkeypatch):
        from raspilapse.dynrange import tonemap

        monkeypatch.setattr(
            dynrange_module, "build_overlay", lambda config: lambda *a, **k: "overlaid.jpg"
        )
        monkeypatch.setattr(tonemap, "tone_map_file", lambda *a, **k: True)
        dr = DynamicRange.from_config(make_config(method="tone_map"))
        assert dr.build_post_process({})("/tmp/frame.jpg", {}, "day") == "overlaid.jpg"

    def test_works_without_an_overlay(self, all_deps_present, monkeypatch):
        """Overlay disabled + tone_map on: the chain is still worth having."""
        from raspilapse.dynrange import tonemap

        seen = {}
        monkeypatch.setattr(dynrange_module, "build_overlay", lambda config: None)
        monkeypatch.setattr(
            tonemap,
            "tone_map_file",
            lambda path, strength, quality: seen.update(
                path=path, strength=strength, quality=quality
            )
            or True,
        )
        dr = DynamicRange.from_config(make_config(method="tone_map", tone_map={"strength": 0.8}))
        result = dr.build_post_process({"output": {"quality": 92}})("/tmp/frame.jpg", {}, "day")
        assert result is True
        assert seen == {"path": "/tmp/frame.jpg", "strength": 0.8, "quality": 92}
