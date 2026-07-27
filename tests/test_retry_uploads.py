"""Tests for the upload retry CLI.

Covers the three behaviours introduced when the queue was fixed: --purge-missing
for rows whose video is gone, --status showing which those are, and exiting 0
rather than 1 when uploads simply are not configured (this runs from a timer
every 30 minutes; a config state must not paint the unit red forever).
"""

import os
import sys
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from src.retry_uploads import main  # noqa: E402
from src.upload_service import UploadService  # noqa: E402


@pytest.fixture
def config_file(tmp_path):
    db = tmp_path / "t.db"
    cfg = tmp_path / "config" / "config.yml"
    cfg.parent.mkdir()
    cfg.write_text(
        yaml.dump(
            {
                "database": {"enabled": True, "path": str(db)},
                "video_upload": {
                    "enabled": True,
                    "url": "https://example.com/upload",
                    "api_key": "k",
                    "camera_id": "cam",
                },
                "output": {"project_name": "cam"},
            }
        )
    )
    return cfg


def _run(cfg, *args):
    # main() chdirs to the project root; leaking that into whatever runs next
    # makes tests order-dependent.
    cwd = os.getcwd()
    try:
        with patch("sys.argv", ["retry_uploads.py", "-c", str(cfg), *args]):
            return main()
    finally:
        os.chdir(cwd)


class TestUnconfigured:
    def test_exits_zero_when_uploads_are_not_configured(self, tmp_path, capsys):
        """A timer-driven unit must not go to `failed` because a feature is off."""
        cfg = tmp_path / "config" / "config.yml"
        cfg.parent.mkdir()
        cfg.write_text(
            yaml.dump(
                {
                    "database": {"enabled": True, "path": str(tmp_path / "t.db")},
                    "video_upload": {"enabled": True, "url": "", "api_key": ""},
                    "output": {"project_name": "cam"},
                }
            )
        )
        assert _run(cfg) == 0
        assert "not configured" in capsys.readouterr().out

    def test_missing_config_file_is_an_error(self, tmp_path, capsys):
        assert _run(tmp_path / "nope.yml") == 1
        assert "not found" in capsys.readouterr().out

    def test_invalid_yaml_is_an_error(self, tmp_path, capsys):
        bad = tmp_path / "bad.yml"
        bad.write_text("{ not: valid: [[[")
        assert _run(bad) == 1
        assert "Invalid YAML" in capsys.readouterr().out


class TestStatus:
    def test_empty_queue(self, config_file, capsys):
        assert _run(config_file, "--status") == 0
        assert "Total:    0" in capsys.readouterr().out

    def test_flags_rows_whose_video_is_gone(self, config_file, capsys):
        cfg = yaml.safe_load(config_file.read_text())
        UploadService(cfg, str(config_file)).queue_upload(
            "/gone/video.mp4", None, None, "2026-01-01"
        )
        assert _run(config_file, "--status") == 0
        assert "[FILE MISSING]" in capsys.readouterr().out


class TestPurgeMissing:
    def test_cancels_rows_whose_source_is_gone(self, config_file, capsys, tmp_path):
        cfg = yaml.safe_load(config_file.read_text())
        svc = UploadService(cfg, str(config_file))

        present = tmp_path / "here.mp4"
        present.write_bytes(b"x")
        svc.queue_upload("/gone/a.mp4", None, None, "2026-01-01")
        svc.queue_upload(str(present), None, None, "2026-01-02")

        assert _run(config_file, "--purge-missing") == 0
        assert "Cancelled 1 upload" in capsys.readouterr().out

        remaining = UploadService(cfg, str(config_file)).get_pending_uploads(include_failed=True)
        assert [r["video_date"] for r in remaining] == ["2026-01-02"]

    def test_is_a_no_op_when_every_source_exists(self, config_file, capsys, tmp_path):
        present = tmp_path / "here.mp4"
        present.write_bytes(b"x")
        cfg = yaml.safe_load(config_file.read_text())
        UploadService(cfg, str(config_file)).queue_upload(str(present), None, None, "2026-01-02")

        assert _run(config_file, "--purge-missing") == 0
        assert "Cancelled 0 upload" in capsys.readouterr().out
