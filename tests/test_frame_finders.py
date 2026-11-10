"""Every tool that looks for frames finds them in every format.

A camera switched to output.format: jxl must still get its daily video,
keogram, upload, retention, status and cleanup -- each of those used to
glob for *.jpg only.
"""

import os
import shutil
import subprocess
import time
from datetime import date, datetime
from pathlib import Path

import pytest
from PIL import Image

from raspilapse.codecs import FRAME_SUFFIXES, JpegCodec, JxlCodec, open_frame
from raspilapse.video.daily import find_keogram_file, find_slitscan_file
from raspilapse.video.keogram import create_keogram, find_images
from raspilapse.video.retention import _video_patterns
from raspilapse.video.timelapse import find_images_in_range

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_the_finders_cover_every_format():
    """A new format added to the registry reaches the finders automatically;
    this pins what that currently means."""
    assert FRAME_SUFFIXES == (".jpg", ".jxl")


class TestVideoRange:
    def test_jxl_and_jpeg_frames_of_one_day_are_found_together(self, tmp_path):
        day = tmp_path / "2026" / "11" / "05"
        day.mkdir(parents=True)
        for name in (
            "cam_2026_11_05_10_00_00.jpg",
            "cam_2026_11_05_10_00_30.jxl",
            "cam_2026_11_05_10_01_00.jxl",
            "cam_2026_11_05_10_01_00_metadata.json",
            "cam_2026_11_05_10_01_00.jpg.dng.tmp",
        ):
            (day / name).touch()
        found = find_images_in_range(
            str(tmp_path), "cam", datetime(2026, 11, 5, 9), datetime(2026, 11, 5, 11)
        )
        assert [p.name for p in found] == [
            "cam_2026_11_05_10_00_00.jpg",
            "cam_2026_11_05_10_00_30.jxl",
            "cam_2026_11_05_10_01_00.jxl",
        ]


class TestKeogram:
    def test_find_images_takes_every_frame_format(self, tmp_path):
        for name in ("a_1.jpg", "a_2.jxl", "keogram_x.jxl", "a_1_metadata.json", "a.png"):
            (tmp_path / name).touch()
        assert [p.name for p in find_images(tmp_path)] == ["a_1.jpg", "a_2.jxl"]

    def test_a_keogram_from_jxl_frames_written_as_jxl(self, tmp_path):
        pytest.importorskip("pillow_jxl")
        frames = []
        for i in range(4):
            path = tmp_path / f"cam_2026_11_05_10_0{i}_00.jxl"
            JxlCodec({"effort": 1}).save(Image.new("RGB", (40, 30), (i * 60, 80, 120)), str(path))
            frames.append(path)
        out = tmp_path / "keogram_cam.jxl"
        assert create_keogram(frames, out, JxlCodec({"effort": 1}), crop_top_percent=0.0)
        with open_frame(out) as keogram:
            assert keogram.format == "JXL"
            assert keogram.size == (4, 30)

    def test_the_cli_takes_the_format_from_the_config(self, tmp_path, monkeypatch):
        pytest.importorskip("pillow_jxl")
        from raspilapse.video import keogram as keogram_module

        frames = tmp_path / "2026" / "11" / "05"
        frames.mkdir(parents=True)
        for i in range(3):
            JpegCodec().save(Image.new("RGB", (40, 30)), str(frames / f"cam_{i}.jpg"))
        config = tmp_path / "config.yml"
        config.write_text("video:\n  keogram:\n    format: jxl\n    jxl:\n      effort: 1\n")
        monkeypatch.setattr(
            "sys.argv", ["keogram", "--dir", str(frames), "-c", str(config), "--quality", "70"]
        )
        assert keogram_module.main() == 0
        with open_frame(frames / "keogram_2026-11-05.jxl") as keogram:
            assert keogram.format == "JXL"

    def test_the_cli_refuses_a_format_its_output_contradicts(self, tmp_path, monkeypatch):
        from raspilapse.video import keogram as keogram_module

        JpegCodec().save(Image.new("RGB", (40, 30)), str(tmp_path / "cam_0.jpg"))
        monkeypatch.setattr(
            "sys.argv",
            ["keogram", "--dir", str(tmp_path), "--format", "jxl", "--output", "k.jpg"],
        )
        assert keogram_module.main() == 1


class TestDailyUpload:
    def test_a_jxl_keogram_and_slitscan_are_found(self, tmp_path):
        (tmp_path / "keogram_cam_2026-11-05_0500_to_2026-11-06_0500.jxl").touch()
        (tmp_path / "slitscan_cam_2026-11-05_0500_to_2026-11-06_0500.jxl").touch()
        day = date(2026, 11, 5)
        assert find_keogram_file(tmp_path, "cam", day).suffix == ".jxl"
        assert find_slitscan_file(tmp_path, "cam", day).suffix == ".jxl"

    def test_after_a_format_change_the_newer_render_wins(self, tmp_path):
        old = tmp_path / "keogram_cam_2026-11-05_0500_to_2026-11-06_0500.jxl"
        new = tmp_path / "keogram_cam_2026-11-05_0500_to_2026-11-06_0500.jpg"
        old.touch()
        new.touch()
        earlier = time.time() - 3600
        os.utime(old, (earlier, earlier))
        assert find_keogram_file(tmp_path, "cam", date(2026, 11, 5)) == new


class TestRetention:
    def test_jxl_keograms_and_slitscans_are_covered(self):
        patterns = _video_patterns("cam")
        assert "keogram_cam_*.jxl" in patterns and "slitscan_cam_*.jxl" in patterns
        assert "keogram_cam_*.jpg" in patterns and "slitscan_cam_*.jpg" in patterns


class TestStatus:
    def test_recent_captures_include_jxl(self, tmp_path):
        from raspilapse.cli.status import StatusDisplay

        images = tmp_path / "images" / "2026" / "11" / "05"
        images.mkdir(parents=True)
        (images / "cam_1.jpg").write_bytes(b"x")
        (images / "cam_2.jxl").write_bytes(b"x")
        config = tmp_path / "config.yml"
        config.write_text(f"output:\n  directory: {tmp_path / 'images'}\n")
        captures = StatusDisplay(str(config))._get_recent_captures(limit=5)
        assert sorted(Path(c[0]).name for c in captures) == ["cam_1.jpg", "cam_2.jxl"]


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
class TestCleanupScript:
    def test_old_frames_of_every_format_are_deleted(self, tmp_path):
        day = tmp_path / "2026" / "10" / "01"
        day.mkdir(parents=True)
        old = time.time() - 30 * 86400
        names = ("a.jpg", "b.jxl", "a_metadata.json", "keep.png")
        for name in names:
            (day / name).write_bytes(b"x")
            os.utime(day / name, (old, old))
        fresh = tmp_path / "2026" / "11" / "09"
        fresh.mkdir(parents=True)
        (fresh / "c.jxl").write_bytes(b"x")

        env = dict(os.environ, RASPILAPSE_IMAGE_DIR=str(tmp_path), RASPILAPSE_KEEP_DAYS="7")
        env["PATH"] = f"{tmp_path / 'bin'}:{env['PATH']}"
        (tmp_path / "bin").mkdir()
        # The script logs through logger(1); keep the journal out of tests.
        stub = tmp_path / "bin" / "logger"
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)

        subprocess.run(
            ["bash", str(PROJECT_ROOT / "scripts" / "cleanup_old_images.sh")],
            env=env,
            check=True,
            capture_output=True,
        )
        assert sorted(p.name for p in day.iterdir()) == ["keep.png"]
        assert (fresh / "c.jxl").exists()

    def test_the_script_lists_every_frame_extension(self):
        script = (PROJECT_ROOT / "scripts" / "cleanup_old_images.sh").read_text()
        for suffix in FRAME_SUFFIXES:
            assert f'-name "*{suffix}"' in script, f"cleanup never deletes *{suffix} frames"
