"""Tests for video retention pruning.

The whole risk here is deleting something that has not been uploaded, so most
of these are about what is *kept*.
"""

import os
import sqlite3
import time
from datetime import date, timedelta

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
        "output": {"project_name": "cam"},
    }
    return {"root": tmp_path, "videos": videos, "db": db, "config": config}


def dated_name(age_days, prefix="cam", suffix=".mp4"):
    """A generated-style filename whose covered day is age_days ago.

    Built from today rather than hardcoded: retention now judges files by the
    date in their name, so a literal date would make these tests rot as the
    calendar moves past it.
    """
    covered = date.today() - timedelta(days=age_days)
    return f"{prefix}_{covered.isoformat()}{suffix}"


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
    """What the retention window does and does not reach."""

    def test_an_old_uploaded_video_is_deleted(self, setup):
        old = make(setup, dated_name(30), age_days=30)
        queue(setup, "success", video=old)
        result = prune_videos(setup["config"])
        assert not old.exists()
        assert str(old.resolve()) in result["deleted"]

    def test_a_recent_video_is_kept(self, setup):
        recent = make(setup, dated_name(2), age_days=2)
        queue(setup, "success", video=recent)
        prune_videos(setup["config"])
        assert recent.exists()

    def test_keograms_and_slitscans_go_too(self, setup):
        k = make(setup, dated_name(30, prefix="keogram_cam", suffix=".jpg"), age_days=30)
        s = make(setup, dated_name(30, prefix="slitscan_cam", suffix=".jpg"), age_days=30)
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

    def test_another_projects_mp4_is_untouched(self, setup):
        # A second camera writing into the same web tree, or a hand-parked
        # clip: age alone must never qualify a file the patterns don't own.
        parked = make(setup, dated_name(90, prefix="othercam"), age_days=90)
        prune_videos(setup["config"])
        assert parked.exists()

    def test_a_rerendered_old_day_still_expires(self, setup):
        # Re-rendering an old day refreshes its mtime; the covered day in the
        # name is what the window judges. (It also survives a stale clock
        # stamping fresh files with old mtimes -- no RTC on a Pi.)
        old = make(setup, dated_name(40), age_days=0)
        queue(setup, "success", video=old)
        prune_videos(setup["config"])
        assert not old.exists()


class TestUploadProtection:
    """Nothing still awaiting upload may be deleted, at any age."""

    @pytest.mark.parametrize("status", ["pending", "uploading", "failed"])
    def test_anything_not_uploaded_is_kept_however_old(self, setup, status):
        """Including 'failed': retries are exhausted so the retry timer will not
        touch it again, but the video exists nowhere else, and deleting the one
        copy of a day nobody uploaded is worse than keeping it."""
        old = make(setup, f"cam_{status}.mp4", age_days=365)
        queue(setup, status, video=old)
        result = prune_videos(setup["config"])
        assert old.exists()
        assert str(old.resolve()) in result["kept_protected"]

    def test_a_file_the_queue_never_saw_is_not_protected(self, setup):
        # Ad-hoc partial-day renders have no queue row; cleaning them up is the
        # point of not treating "unknown" as "protected".
        adhoc = make(setup, dated_name(30, suffix="_0500-1552.mp4"), age_days=30)
        prune_videos(setup["config"])
        assert not adhoc.exists()

    def test_an_unreadable_database_deletes_nothing(self, setup):
        old = make(setup, dated_name(30), age_days=30)
        setup["db"].write_text("this is not a database")
        result = prune_videos(setup["config"])
        assert old.exists()
        assert result["deleted"] == []

    def test_a_missing_database_deletes_nothing_when_uploads_are_on(self, setup):
        # With uploads configured, an absent queue file means database.path is
        # mispointed -- and an empty protected set would delete the only copy
        # of every day that never uploaded. Fail closed instead.
        old = make(setup, dated_name(30), age_days=30)
        setup["db"].unlink()
        setup["config"]["video_upload"] = {"enabled": True, "url": "https://example"}
        result = prune_videos(setup["config"])
        assert old.exists()
        assert result["skipped"] == "upload queue unreadable"

    def test_a_missing_database_is_fine_when_uploads_are_off(self, setup):
        # No uploads, no queue: nothing to protect, pruning proceeds.
        old = make(setup, dated_name(30), age_days=30)
        setup["db"].unlink()
        result = prune_videos(setup["config"])
        assert not old.exists()


class TestSafety:
    """The ways a pruner could destroy something it should not."""

    def test_retention_zero_is_a_no_op(self, setup):
        old = make(setup, dated_name(365), age_days=365)
        setup["config"]["video"]["retention_days"] = 0
        prune_videos(setup["config"])
        assert old.exists()

    def test_dry_run_reports_without_deleting(self, setup):
        old = make(setup, dated_name(30), age_days=30)
        result = prune_videos(setup["config"], dry_run=True)
        assert old.exists()
        assert str(old.resolve()) in result["deleted"]

    def test_a_symlink_is_never_followed(self, setup):
        outside = setup["root"] / "precious.mp4"
        outside.write_bytes(b"important")
        link = setup["videos"] / "cam_link.mp4"
        link.symlink_to(outside)
        when = time.time() - 30 * DAY
        # Both have to be aged. With a fresh target the age check skips the
        # file whether or not the guard exists.
        os.utime(outside, (when, when))
        os.utime(link, (when, when), follow_symlinks=False)

        result = prune_videos(setup["config"])

        # Assert on the *link*, not the target. unlink() on a symlink removes
        # the link and leaves what it points at, so `outside.exists()` holds
        # either way and cannot detect the guard going missing.
        assert link.is_symlink(), "the symlink itself was pruned"
        assert result["deleted"] == []
        assert outside.exists()

    def test_emptied_month_directories_are_removed(self, setup):
        make(setup, dated_name(30), age_days=30)
        prune_videos(setup["config"])
        assert not setup["videos"].exists()

    def test_directories_the_prune_did_not_empty_are_kept(self, setup):
        # An empty directory that held nothing this prune deleted is not the
        # prune's to remove -- another camera or a manual mkdir owns it.
        bystander = setup["root"] / "videos" / "2026" / "09"
        bystander.mkdir(parents=True)
        make(setup, dated_name(30), age_days=30)
        prune_videos(setup["config"])
        assert bystander.exists()

    def test_a_missing_video_directory_is_not_an_error(self, setup):
        setup["config"]["video"]["directory"] = str(setup["root"] / "nope")
        assert prune_videos(setup["config"])["deleted"] == []

    def test_the_override_beats_the_config(self, setup):
        old = make(setup, dated_name(3), age_days=3)
        prune_videos(setup["config"], retention_days=1)
        assert not old.exists()


class TestNegativeRetention:
    """A negative window would put the cutoff in the future."""

    def test_a_negative_window_deletes_nothing(self, setup):
        """A negative window puts the cutoff in the future, so every file is
        'older' than it -- including this morning's render. One mistyped
        `--retention-days -7` would take the whole directory."""
        recent = make(setup, "cam_today.mp4", age_days=0)
        old = make(setup, "cam_old.mp4", age_days=30)
        result = prune_videos(setup["config"], retention_days=-7)
        assert recent.exists() and old.exists()
        assert result["deleted"] == []

    def test_a_negative_window_in_the_config_is_rejected_too(self, setup):
        recent = make(setup, "cam_today.mp4", age_days=0)
        setup["config"]["video"]["retention_days"] = -1
        prune_videos(setup["config"])
        assert recent.exists()


class TestPathNormalisation:
    """The queue path and the found path must compare equal."""

    def test_a_queued_path_reaching_the_file_through_a_symlinked_dir_still_protects(
        self, setup, tmp_path
    ):
        """absolute() only prepends the cwd; it leaves symlinks and '..' in
        place. The queue path and the path rglob finds only have to differ
        textually for the lookup to miss and delete a pending upload."""
        real = make(setup, "cam_2026-07-20.mp4", age_days=30)
        # Same file, named through a symlinked parent directory.
        alias_dir = tmp_path / "alias"
        alias_dir.symlink_to(setup["videos"])
        queue(setup, "pending", video=alias_dir / real.name)

        result = prune_videos(setup["config"])
        assert real.exists(), "deleted a file that is still awaiting upload"
        assert result["kept_protected"]

    def test_an_unreadable_queue_says_so_rather_than_reporting_zero(self, setup):
        make(setup, "cam_2026-07-20.mp4", age_days=30)
        setup["db"].write_text("this is not a database")
        assert prune_videos(setup["config"])["skipped"] == "upload queue unreadable"

    def test_a_clean_run_has_nothing_to_report(self, setup):
        make(setup, "cam_2026-07-20.mp4", age_days=30)
        assert prune_videos(setup["config"])["skipped"] is None
