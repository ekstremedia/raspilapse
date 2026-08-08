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

    def test_explicit_off(self):
        assert DynamicRange.from_config(make_config(method="off")).method == "off"

    def test_case_is_forgiven(self):
        assert DynamicRange.from_config(make_config(method="OFF")).method == "off"

    def test_unknown_method_degrades_to_off(self, dynrange_logs):
        dr = DynamicRange.from_config(make_config(method="magic"))
        assert dr.method == "off"
        assert any("magic" in r.message for r in dynrange_logs.records)

    def test_sensor_hdr_needs_no_optional_packages(self):
        """sensor_hdr talks to V4L2, not to cv2/rawpy; it must parse anywhere."""
        assert DynamicRange.from_config(make_config(method="sensor_hdr")).method == "sensor_hdr"

    @pytest.mark.parametrize("method", METHODS)
    def test_every_advertised_method_parses(self, method, monkeypatch):
        monkeypatch.setattr(dynrange_module.importlib.util, "find_spec", lambda name: object())
        assert DynamicRange.from_config(make_config(method=method)).method == method


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
