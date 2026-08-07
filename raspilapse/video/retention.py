"""Delete generated videos once they are older than the retention window.

scripts/cleanup_old_images.sh has expired the source JPEGs for a long time, but
nothing has ever expired what is made from them. On this camera that was 3.2 GB
of accumulated video from twelve days, growing forever -- the source images are
bounded and the thing they turn into is not.

This lives in Python rather than in the bash cleanup script for one reason: the
safety rule needs the upload queue, and the queue is SQLite.
"""

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from raspilapse.logging_setup import get_logger

logger = get_logger("video_retention")

# What a day's render leaves behind. Anchored patterns rather than a bare *.jpg
# so nothing unrelated that happens to live in the video directory is caught.
VIDEO_PATTERNS = ("*.mp4", "keogram_*.jpg", "slitscan_*.jpg")

# A row in any other state may still be uploaded, so its files stay put.
UPLOADED_STATUS = "success"


def _protected_paths(db_path: Path) -> Dict[str, str]:
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

    try:
        protected = _protected_paths(Path((config.get("database", {}) or {}).get("path", "")))
    except sqlite3.Error:
        result["skipped"] = "upload queue unreadable"
        return result

    cutoff = (datetime.now() - timedelta(days=retention_days)).timestamp()
    deleted: List[str] = []
    freed = 0

    for pattern in VIDEO_PATTERNS:
        for path in sorted(directory.rglob(pattern)):
            # Never follow a symlink out of the video directory.
            if path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_mtime >= cutoff:
                continue

            key = str(path.resolve())
            if key in protected:
                logger.warning(f"[Retention] Keeping {path.name}: {protected[key]}")
                result["kept_protected"].append(key)
                continue

            size = path.stat().st_size
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
            freed += size

    if not dry_run:
        _remove_empty_dirs(directory)

    result["deleted"] = deleted
    result["bytes"] = freed
    if deleted:
        logger.info(
            f"[Retention] {'Would free' if dry_run else 'Freed'} "
            f"{freed / 1048576:.1f} MB across {len(deleted)} file(s)"
        )
    return result


def _remove_empty_dirs(root: Path) -> None:
    """Drop the YYYY/MM directories a prune has emptied, as the image cleanup does."""
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
            try:
                path.rmdir()
                logger.debug(f"[Retention] Removed empty directory {path}")
            except OSError:
                pass


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
