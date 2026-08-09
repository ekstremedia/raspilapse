"""Tests for the white-balance trim's survival across restarts.

The controller side of the trim is covered in test_exposure.py; this file
covers the daemon's half: reading the state file into the controller at
startup, and writing it back only when the trim has actually moved. The
state path is patched per test -- the class default points at the live
data/ directory, and these tests must never read a running camera's trim.
"""

import json
import os
import tempfile
from unittest.mock import patch

import pytest
import yaml

from raspilapse.daemon import AdaptiveTimelapse


def make_timelapse(tmp_path, enabled=True, state=None):
    """An AdaptiveTimelapse with the trim state redirected into tmp_path."""
    config_data = {
        "camera": {
            "resolution": {"width": 1280, "height": 720},
            "transforms": {"horizontal_flip": False, "vertical_flip": False},
            "controls": {},
        },
        "output": {
            "directory": str(tmp_path / "photos"),
            "filename_pattern": "{name}_{counter}.jpg",
            "project_name": "test_project",
            "quality": 85,
        },
        "system": {"create_directories": False, "save_metadata": False},
        "overlay": {"enabled": False},
        "adaptive_timelapse": {
            "enabled": True,
            "interval": 30,
            "day_mode": {
                "fixed_colour_gains": [2.26, 1.73],
                "wb_feedback": {"enabled": enabled},
            },
        },
    }
    state_path = tmp_path / "wb_trim.json"
    if state is not None:
        state_path.write_text(state if isinstance(state, str) else json.dumps(state))

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(config_data, f)
        config_path = f.name
    try:
        with patch.object(AdaptiveTimelapse, "WB_TRIM_STATE", state_path):
            timelapse = AdaptiveTimelapse(config_path)
        # The instance keeps using the patched path after construction.
        timelapse.WB_TRIM_STATE = state_path
        return timelapse, state_path
    finally:
        os.unlink(config_path)


class TestSeeding:
    def test_a_saved_trim_is_restored(self, tmp_path):
        timelapse, _ = make_timelapse(
            tmp_path, state={"wb_trim_r": 1.05, "wb_trim_b": 0.95}
        )
        assert timelapse.exposure.wb_trim == (1.05, 0.95)

    def test_restoration_clamps_to_the_current_max_trim(self, tmp_path):
        """The file may hold a trim from a config with a wider max_trim."""
        timelapse, _ = make_timelapse(
            tmp_path, state={"wb_trim_r": 0.5, "wb_trim_b": 1.5}
        )
        assert timelapse.exposure.wb_trim == (0.88, 1.12)

    def test_no_file_means_the_configured_gains_exactly(self, tmp_path):
        timelapse, _ = make_timelapse(tmp_path)
        assert timelapse.exposure.wb_trim == (1.0, 1.0)

    def test_a_malformed_file_is_ignored(self, tmp_path):
        timelapse, _ = make_timelapse(tmp_path, state="not json {")
        assert timelapse.exposure.wb_trim == (1.0, 1.0)

    def test_a_file_missing_a_key_is_ignored(self, tmp_path):
        timelapse, _ = make_timelapse(tmp_path, state={"wb_trim_r": 1.05})
        assert timelapse.exposure.wb_trim == (1.0, 1.0)

    def test_disabled_feedback_never_reads_the_file(self, tmp_path):
        """A disabled loop must not apply a trim it can no longer correct."""
        timelapse, _ = make_timelapse(
            tmp_path, enabled=False, state={"wb_trim_r": 1.05, "wb_trim_b": 0.95}
        )
        assert timelapse.exposure.wb_trim == (1.0, 1.0)


class TestPersistence:
    def test_a_moved_trim_is_written(self, tmp_path):
        timelapse, state_path = make_timelapse(tmp_path)
        timelapse.exposure.seed_from_capture(wb_trim=(1.05, 0.95))
        timelapse._persist_wb_trim()
        assert json.loads(state_path.read_text()) == {
            "wb_trim_r": 1.05,
            "wb_trim_b": 0.95,
        }

    def test_a_write_leaves_no_temporary_behind(self, tmp_path):
        timelapse, state_path = make_timelapse(tmp_path)
        timelapse.exposure.seed_from_capture(wb_trim=(1.05, 0.95))
        timelapse._persist_wb_trim()
        assert not state_path.with_suffix(".json.tmp").exists()

    def test_a_step_below_the_threshold_is_not_rewritten(self, tmp_path):
        timelapse, state_path = make_timelapse(tmp_path)
        timelapse.exposure.seed_from_capture(wb_trim=(1.05, 0.95))
        timelapse._persist_wb_trim()
        timelapse.exposure.seed_from_capture(wb_trim=(1.0505, 0.95))
        timelapse._persist_wb_trim()
        assert json.loads(state_path.read_text())["wb_trim_r"] == 1.05

    def test_a_step_past_the_threshold_is(self, tmp_path):
        timelapse, state_path = make_timelapse(tmp_path)
        timelapse.exposure.seed_from_capture(wb_trim=(1.05, 0.95))
        timelapse._persist_wb_trim()
        timelapse.exposure.seed_from_capture(wb_trim=(1.06, 0.95))
        timelapse._persist_wb_trim()
        assert json.loads(state_path.read_text())["wb_trim_r"] == 1.06

    def test_disabled_feedback_writes_nothing(self, tmp_path):
        timelapse, state_path = make_timelapse(tmp_path, enabled=False)
        timelapse.exposure.seed_from_capture(wb_trim=(1.05, 0.95))
        timelapse._persist_wb_trim()
        assert not state_path.exists()


class TestRoundTrip:
    def test_what_one_run_writes_the_next_run_reads(self, tmp_path):
        first, state_path = make_timelapse(tmp_path)
        first.exposure.seed_from_capture(wb_trim=(1.031, 0.978))
        first._persist_wb_trim()

        second, _ = make_timelapse(tmp_path, state=state_path.read_text())
        assert second.exposure.wb_trim == pytest.approx((1.031, 0.978))
