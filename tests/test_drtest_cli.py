"""raspilapse-drtest: method parsing, config building, the daemon guard."""

import subprocess
from unittest.mock import patch

import pytest
from PIL import Image

import raspilapse.cli.drtest as drtest
from raspilapse.cli.drtest import (
    brightness_stats,
    build_method_config,
    daemon_is_active,
    light_mode_for,
    parse_methods,
)


class TestParseMethods:
    def test_default_list_parses_in_order(self):
        assert parse_methods(drtest.DEFAULT_METHODS) == [
            "off",
            "tone_map",
            "fusion",
            "sensor_hdr",
            "raw",
        ]

    def test_off_is_always_first(self):
        """Every comparison needs its reference frame."""
        assert parse_methods("fusion,off") == ["off", "fusion"]
        assert parse_methods("fusion") == ["off", "fusion"]

    def test_duplicates_collapse(self):
        assert parse_methods("fusion,fusion,off,off") == ["off", "fusion"]

    def test_fusion_plus_tm_is_a_method(self):
        assert parse_methods("fusion+tm") == ["off", "fusion+tm"]

    def test_unknown_method_raises(self):
        with pytest.raises(ValueError, match="magic"):
            parse_methods("fusion,magic")


class TestBuildMethodConfig:
    BASE = {"adaptive_timelapse": {"interval": 30}, "output": {"quality": 85}}

    def test_sets_the_method(self):
        config = build_method_config(self.BASE, "fusion")
        assert config["adaptive_timelapse"]["dynamic_range"]["method"] == "fusion"
        assert not config["adaptive_timelapse"]["dynamic_range"]["tone_map"]["enabled"]

    def test_fusion_plus_tm_enables_both(self):
        config = build_method_config(self.BASE, "fusion+tm")
        assert config["adaptive_timelapse"]["dynamic_range"]["method"] == "fusion"
        assert config["adaptive_timelapse"]["dynamic_range"]["tone_map"]["enabled"]

    def test_base_config_is_never_mutated(self):
        build_method_config(self.BASE, "raw")
        assert "dynamic_range" not in self.BASE["adaptive_timelapse"]


class TestLightModeFor:
    def test_fast_exposures_are_day(self):
        assert light_mode_for(10_000) == "day"

    def test_slow_exposures_are_transition_never_night(self):
        """Night is where every method deliberately stands down -- useless
        for a tool whose whole point is exercising them."""
        assert light_mode_for(500_000) == "transition"


class TestBrightnessStats:
    def test_flat_gray_frame(self, tmp_path):
        frame = tmp_path / "gray.jpg"
        Image.new("RGB", (64, 48), (128, 128, 128)).save(str(frame), quality=95)
        mean, p5, p95 = brightness_stats(str(frame))
        assert mean == pytest.approx(128, abs=3)
        assert p5 == pytest.approx(128, abs=3)
        assert p95 == pytest.approx(128, abs=3)


class TestDaemonGuard:
    def test_active_service_is_detected(self):
        with patch.object(drtest.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            assert daemon_is_active() is True

    def test_stopped_service_is_not(self):
        with patch.object(drtest.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=3)
            assert daemon_is_active() is False

    def test_no_systemd_is_not(self):
        with patch.object(drtest.subprocess, "run", side_effect=FileNotFoundError):
            assert daemon_is_active() is False

    def test_main_refuses_while_the_daemon_runs(self, capsys):
        with patch.object(drtest, "daemon_is_active", return_value=True):
            assert drtest.main([]) == 1
        out = capsys.readouterr().out
        assert "sudo systemctl stop raspilapse" in out

    def test_unknown_method_is_an_argparse_error(self):
        with pytest.raises(SystemExit) as excinfo:
            drtest.main(["--methods", "magic"])
        assert excinfo.value.code == 2
