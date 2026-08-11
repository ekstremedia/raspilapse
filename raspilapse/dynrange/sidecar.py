"""The DNG sidecar: occasional raw negatives kept next to their JPEGs.

For hand-developing the special frames -- a storm front, a midnight-sun sky
-- in a desktop raw workflow, while the timelapse pipeline stays JPEG. At
~15 MB per DNG the retention cap is what makes this safe to leave on: the
oldest negatives are pruned as new ones arrive, so the collection plateaus
at max_files whatever else happens on the disk.
"""

from pathlib import Path

from raspilapse.logging_setup import get_logger

logger = get_logger("dynrange")


def keep_sidecar(frame_index: int, every_n_frames: int) -> bool:
    """Whether this frame's DNG is a keeper. Frame 0 always is, so a fresh
    install produces its first negative immediately rather than in ten
    minutes."""
    if every_n_frames <= 0:
        return False
    return frame_index % every_n_frames == 0


def prune_sidecars(output_directory: str, max_files: int) -> int:
    """Delete the oldest .dng files beyond the cap. Returns how many.

    Sweeps the whole image tree because date subdirectories spread the
    negatives across folders. Only .dng files are candidates -- everything
    else in the tree belongs to other machinery.
    """
    root = Path(output_directory)
    if max_files <= 0 or not root.is_dir():
        return 0

    try:
        negatives = sorted(root.rglob("*.dng"), key=lambda p: p.stat().st_mtime)
    except OSError as e:
        logger.warning(f"Sidecar prune could not scan {root}: {e}")
        return 0

    doomed = negatives[: max(0, len(negatives) - max_files)]
    removed = 0
    for path in doomed:
        try:
            path.unlink()
            removed += 1
        except OSError as e:
            logger.warning(f"Sidecar prune could not remove {path}: {e}")
    if removed:
        logger.info(f"Sidecar prune removed {removed} old negatives (cap {max_files})")
    return removed
