"""Delete generated videos once they are older than the retention window.

scripts/cleanup_old_images.sh has expired the source JPEGs for a long time, but
nothing has ever expired what is made from them. On this camera that was 3.2 GB
of accumulated video from twelve days, growing forever -- the source images are
bounded and the thing they turn into is not.

This lives in Python rather than in the bash cleanup script for one reason: the
safety rule needs the upload queue, and the queue is SQLite.
"""

import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from raspilapse.config import get_db_path
from raspilapse.logging_setup import get_logger

logger = get_logger("video_retention")

# The day a file covers, read from its name: every generated name leads with
# {project}_{YYYY-MM-DD}, and keograms/slitscans carry the same stem.
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _video_patterns(project_name: str) -> tuple:
    """What a day's render leaves behind, anchored to this camera's project.

    Anchored on the project name -- not a bare *.mp4 -- so an mp4 someone
    parked in the web root, or a second camera writing into the same tree,
    is never swept up on age alone.
    """
    return (
        f"{project_name}_*.mp4",
        f"keogram_{project_name}_*.jpg",
        f"slitscan_{project_name}_*.jpg",
    )


# A row in any other state may still be uploaded, so its files stay put.
UPLOADED_STATUS = "success"


def _protected_paths(db_path: Path, upload_configured: bool = False) -> Dict[str, str]:
    """Absolute paths that must not be deleted, mapped to why.

    Anything the upload queue still knows about in a state other than
    'success'. That deliberately includes 'failed' -- a row that exhausted its
    retries will not be uploaded again by the retry timer, but its video also
    exists nowhere else, and silently deleting the one copy of a day nobody
    managed to upload is a worse outcome than keeping it. Retained files are
    logged rather than merely skipped, so this shows up as something to deal
    with instead of an invisible leak.

    Files the queue has never heard of are NOT protected: that is how the
    ad-hoc partial-day renders sitting in the video directory get cleaned up.
    """
    protected: Dict[str, str] = {}
    if not db_path.exists():
        if upload_configured:
            # With uploads in play, a missing file where the queue should be
            # is indistinguishable from a mispointed database.path -- and
            # answering "nothing is protected" to that deletes the only copy
            # of every day that never uploaded. Refuse, like the unreadable
            # case below. Without uploads there is nothing to protect.
            raise sqlite3.OperationalError(f"upload queue database not found at {db_path}")
        return protected

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT video_path, keogram_path, slitscan_path, status, video_date "
                "FROM upload_queue WHERE status != ?",
                (UPLOADED_STATUS,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        # Refusing to delete anything is the safe failure here.
        logger.warning(f"[Retention] Could not read upload queue, keeping everything: {e}")
        raise

    for video, keogram, slitscan, status, video_date in rows:
        for path in (video, keogram, slitscan):
            if path:
                # resolve(), not absolute(): absolute() only prepends the cwd,
                # leaving '..' segments and symlinks in place. The queue path
                # and the path found by rglob only have to differ textually --
                # one going through a symlinked directory is enough -- for the
                # lookup below to miss and delete a video that is still waiting
                # to upload. /var/www/html is a symlink on some installs.
                protected[str(Path(path).resolve())] = f"{status} upload for {video_date}"
    return protected


def prune_videos(
    config: Dict,
    retention_days: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, object]:
    """Delete rendered videos, keograms and slitscans past the retention window.

    Args:
        config: Loaded configuration.
        retention_days: Override for video.retention_days. 0 disables pruning.
        dry_run: Report what would be deleted and delete nothing.

    Returns:
        Dict with 'deleted' (list of paths), 'bytes', 'kept_protected' (list).
    """
    video_cfg = config.get("video", {}) or {}
    if retention_days is None:
        retention_days = video_cfg.get("retention_days", 0)

    # "skipped" carries why nothing happened. Without it every early return
    # prints the same "Deleted 0 file(s)" and an operator cannot tell a clean
    # no-op from a run that bailed out of its own safety check.
    result: Dict[str, object] = {"deleted": [], "bytes": 0, "kept_protected": [], "skipped": None}

    if not retention_days:
        logger.debug("[Retention] video.retention_days is 0, nothing to do")
        result["skipped"] = "retention disabled"
        return result

    if retention_days < 0:
        # A negative window puts the cutoff in the future, so every file in the
        # directory is older than it -- including this morning's render. One
        # mistyped `--retention-days -7` would take the lot.
        logger.error(
            f"[Retention] Refusing to run with retention_days={retention_days}: "
            "a negative window would delete every file"
        )
        result["skipped"] = f"invalid retention_days={retention_days}"
        return result

    directory = Path(video_cfg.get("directory", "videos"))
    if not directory.is_dir():
        logger.warning(f"[Retention] Video directory does not exist: {directory}")
        result["skipped"] = f"no video directory at {directory}"
        return result

    upload_cfg = config.get("video_upload") or {}
    upload_configured = bool(upload_cfg) and upload_cfg.get("enabled", True)
    try:
        # get_db_path resolves a relative database.path against the project
        # root, so this no longer depends on the caller's cwd.
        protected = _protected_paths(Path(get_db_path(config)), upload_configured)
    except sqlite3.Error:
        result["skipped"] = "upload queue unreadable"
        return result

    now = datetime.now()
    cutoff_ts = (now - timedelta(days=retention_days)).timestamp()
    cutoff_date = (now - timedelta(days=retention_days)).date()
    project_name = (config.get("output", {}) or {}).get("project_name", "timelapse")
    deleted: List[str] = []
    deleted_parents = set()
    freed = 0

    for pattern in _video_patterns(project_name):
        for path in sorted(directory.rglob(pattern)):
            try:
                # Never follow a symlink out of the video directory.
                if path.is_symlink() or not path.is_file():
                    continue
                if not _past_retention(path, cutoff_date, cutoff_ts):
                    continue
                size = path.stat().st_size
            except OSError:
                # Vanished mid-prune; the next run settles it.
                continue

            key = str(path.resolve())
            if key in protected:
                logger.warning(f"[Retention] Keeping {path.name}: {protected[key]}")
                result["kept_protected"].append(key)
                continue

            if dry_run:
                logger.info(f"[Retention] Would delete {path} ({size / 1048576:.1f} MB)")
            else:
                try:
                    path.unlink()
                except OSError as e:
                    logger.warning(f"[Retention] Could not delete {path}: {e}")
                    continue
                logger.info(f"[Retention] Deleted {path} ({size / 1048576:.1f} MB)")
            deleted.append(key)
            deleted_parents.add(path.parent)
            freed += size

    if not dry_run and deleted_parents:
        _remove_empty_dirs(directory, deleted_parents)

    result["deleted"] = deleted
    result["bytes"] = freed
    if deleted:
        logger.info(
            f"[Retention] {'Would free' if dry_run else 'Freed'} "
            f"{freed / 1048576:.1f} MB across {len(deleted)} file(s)"
        )
    return result


def _past_retention(path: Path, cutoff_date, cutoff_ts: float) -> bool:
    """Whether a file has aged out of the window.

    By the date in its name when there is one: the filename carries the day
    the file covers, while mtime does not survive clock steps (no RTC --
    fake-hwclock plus a late NTP sync can stamp a fresh render with a
    month-old time) and resets whenever an old day is re-rendered. Names
    without a parsable date fall back to mtime.
    """
    m = _DATE_RE.search(path.name)
    if m:
        try:
            covered = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            return covered < cutoff_date
        except ValueError:
            pass
    return path.stat().st_mtime < cutoff_ts


def _remove_empty_dirs(root: Path, parents: Iterable[Path]) -> None:
    """Drop the YYYY/MM directories this prune emptied -- and only those.

    Walking everything under the root removed empty directories the prune had
    nothing to do with, in a tree other things share. Instead climb from the
    parents of the files actually deleted, stopping at the first non-empty
    directory or at the video root.
    """
    root = root.resolve()
    for parent in sorted(set(parents), key=lambda p: len(p.parts), reverse=True):
        current = parent
        while True:
            try:
                resolved = current.resolve()
                if resolved == root or root not in resolved.parents:
                    break
                if current.is_symlink() or not current.is_dir() or any(current.iterdir()):
                    break
                current.rmdir()
                logger.debug(f"[Retention] Removed empty directory {current}")
            except OSError:
                break
            current = current.parent


def main() -> int:
    """CLI entry point."""
    import argparse

    from raspilapse.config import PROJECT_ROOT, load_config

    parser = argparse.ArgumentParser(description="Delete videos past the retention window")
    parser.add_argument("--config", default="config/config.yml")
    parser.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="Override video.retention_days for this run",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="List what would be deleted, delete nothing"
    )
    args = parser.parse_args()

    # Relative paths in the config resolve against the project root, wherever
    # the timer invoked this from.
    os.chdir(PROJECT_ROOT)

    result = prune_videos(
        load_config(args.config), retention_days=args.retention_days, dry_run=args.dry_run
    )
    if result["skipped"]:
        # Not a silent zero: "upload queue unreadable" in particular means the
        # safety check never ran, which is a different thing from there being
        # nothing to delete.
        print(f"Skipped: {result['skipped']}")
        return 0

    deleted = result["deleted"]
    print(
        f"{'Would delete' if args.dry_run else 'Deleted'} {len(deleted)} file(s), "
        f"{result['bytes'] / 1048576:.1f} MB"
    )
    if result["kept_protected"]:
        print(f"Kept {len(result['kept_protected'])} file(s) still awaiting upload")
    return 0
