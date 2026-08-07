"""Tests for video retention pruning.

The whole risk here is deleting something that has not been uploaded, so most
of these are about what is *kept*.
"""

import os
import sqlite3
import time

import pytest

from raspilapse.video.retention import prune_videos

DAY = 86400


@pytest.fixture
def setup(tmp_path):
    """A video directory, a queue database, and a config pointing at both."""
    videos = tmp_path / "videos" / "2026" / "08"
    videos.mkdir(parents=True)
    db = tmp_path / "timelapse.db"

    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE upload_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_date DATE, video_path TEXT, keogram_path TEXT,
            slitscan_path TEXT, status TEXT)"""
    )
    conn.commit()
    conn.close()

    config = {
        "video": {"directory": str(tmp_path / "videos"), "retention_days": 7},
        "database": {"path": str(db)},
    }
    return {"root": tmp_path, "videos": videos, "db": db, "config": config}


def make(setup, name, age_days, size=1024):
    p = setup["videos"] / name
    p.write_bytes(b"x" * size)
    when = time.time() - age_days * DAY
    os.utime(p, (when, when))
    return p


def queue(setup, status, video=None, keogram=None, slitscan=None, date="2026-08-01"):
    conn = sqlite3.connect(setup["db"])
    conn.execute(
        "INSERT INTO upload_queue (video_date, video_path, keogram_path, slitscan_path, status)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            date,
            str(video) if video else None,
            str(keogram) if keogram else None,
            str(slitscan) if slitscan else None,
            status,
        ),
    )
    conn.commit()
    conn.close()


class TestAgeWindow:
    def test_an_old_uploaded_video_is_deleted(self, setup):
        old = make(setup, "cam_2026-07-20.mp4", age_days=30)
        queue(setup, "success", video=old)
        result = prune_videos(setup["config"])
        assert not old.exists()
        assert str(old.absolute()) in result["deleted"]

    def test_a_recent_video_is_kept(self, setup):
        recent = make(setup, "cam_2026-08-06.mp4", age_days=2)
        queue(setup, "success", video=recent)
        prune_videos(setup["config"])
        assert recent.exists()

    def test_keograms_and_slitscans_go_too(self, setup):
        k = make(setup, "keogram_cam_2026-07-20.jpg", age_days=30)
        s = make(setup, "slitscan_cam_2026-07-20.jpg", age_days=30)
        queue(setup, "success", keogram=k, slitscan=s)
        prune_videos(setup["config"])
        assert not k.exists() and not s.exists()

    def test_unrelated_files_are_untouched(self, setup):
        other = setup["videos"] / "notes.txt"
        other.write_text("keep me")
        when = time.time() - 90 * DAY
        os.utime(other, (when, when))
        prune_videos(setup["config"])
        assert other.exists()


class TestUploadProtection:
    @pytest.mark.parametrize("status", ["pending", "uploading", "failed"])
    def test_anything_not_uploaded_is_kept_however_old(self, setup, status):
        """Including 'failed': retries are exhausted so the retry timer will not
        touch it again, but the video exists nowhere else, and deleting the one
        copy of a day nobody uploaded is worse than keeping it."""
        old = make(setup, f"cam_{status}.mp4", age_days=365)
        queue(setup, status, video=old)
        result = prune_videos(setup["config"])
        assert old.exists()
        assert str(old.absolute()) in result["kept_protected"]

    def test_a_file_the_queue_never_saw_is_not_protected(self, setup):
        # Ad-hoc partial-day renders have no queue row; cleaning them up is the
        # point of not treating "unknown" as "protected".
        adhoc = make(setup, "cam_2026-07-20_0500-1552.mp4", age_days=30)
        prune_videos(setup["config"])
        assert not adhoc.exists()

    def test_an_unreadable_database_deletes_nothing(self, setup):
        old = make(setup, "cam_2026-07-20.mp4", age_days=30)
        setup["db"].write_text("this is not a database")
        result = prune_videos(setup["config"])
        assert old.exists()
        assert result["deleted"] == []


class TestSafety:
    def test_retention_zero_is_a_no_op(self, setup):
        old = make(setup, "cam_2026-07-20.mp4", age_days=365)
        setup["config"]["video"]["retention_days"] = 0
        prune_videos(setup["config"])
        assert old.exists()

    def test_dry_run_reports_without_deleting(self, setup):
        old = make(setup, "cam_2026-07-20.mp4", age_days=30)
        result = prune_videos(setup["config"], dry_run=True)
        assert old.exists()
        assert str(old.absolute()) in result["deleted"]

    def test_a_symlink_is_never_followed(self, setup):
        outside = setup["root"] / "precious.mp4"
        outside.write_bytes(b"important")
        link = setup["videos"] / "cam_link.mp4"
        link.symlink_to(outside)
        when = time.time() - 30 * DAY
        os.utime(link, (when, when), follow_symlinks=False)
        prune_videos(setup["config"])
        assert outside.exists()

    def test_emptied_month_directories_are_removed(self, setup):
        make(setup, "cam_2026-07-20.mp4", age_days=30)
        prune_videos(setup["config"])
        assert not setup["videos"].exists()

    def test_a_missing_video_directory_is_not_an_error(self, setup):
        setup["config"]["video"]["directory"] = str(setup["root"] / "nope")
        assert prune_videos(setup["config"])["deleted"] == []

    def test_the_override_beats_the_config(self, setup):
        old = make(setup, "cam_2026-08-04.mp4", age_days=3)
        prune_videos(setup["config"], retention_days=1)
        assert not old.exists()
