#!/usr/bin/env python3
"""
Daily timelapse runner -- renders yesterday's video and uploads it.

1. Runs raspilapse.cli.timelapse over the 05:00 -> 05:00 window, which also
   produces the keogram and slitscan.
2. Uploads video, keogram and slitscan when video_upload is configured,
   queueing them for retry on failure.

Runs from raspilapse-daily-video.timer at 05:00; --date renders another day.
"""

import argparse
import errno
import fcntl
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from raspilapse.config import PROJECT_ROOT, load_config
from raspilapse.logging_setup import configure_logging, get_logger
from raspilapse.storage.upload import UploadService


def _find_dated_file(video_dir: Path, patterns: list, date_str: str) -> Path:
    """
    Return the newest file matching the first pattern that hits.

    Patterns are ordered most to least specific, and each is tried both nested
    and flat. Newest by mtime rather than alphabetically, so a regenerated file
    wins over the original.

    Args:
        video_dir: Directory to search
        patterns: Glob patterns, most specific first
        date_str: YYYY-MM-DD, also used to confirm the match really is that day

    Returns:
        The matching path, or None
    """
    for pattern in patterns:
        matches = [m for m in video_dir.glob(pattern) if date_str in m.name]
        if matches:
            return max(matches, key=lambda p: p.stat().st_mtime)
    return None


def _dated_patterns(prefix: str, project_name: str, date_str: str, suffix: str) -> list:
    """Build the glob list for a prefixed, date-stamped file.

    Anchored on prefix, project and date. `**/` also matches the directory
    itself, so these cover flat and nested layouts alike. No loose fallback:
    every generated name carries the window's END date too, so a pattern like
    `keogram*2026-08-08*` happily matches *yesterday's*
    `keogram_..._2026-08-07_0500_to_2026-08-08_0500.jpg` — and a night where
    the keogram failed would silently upload the previous day's file.
    """
    stem = f"{prefix}_{project_name}_{date_str}" if prefix else f"{project_name}_{date_str}"
    return [
        f"**/{stem}_*{suffix}",
        f"**/{stem}*{suffix}",
    ]


def find_video_file(video_dir: Path, project_name: str, date: datetime.date) -> Path:
    """Find the generated video file for a given date."""
    # e.g. project_2026-07-25_0500-0500.mp4
    date_str = date.strftime("%Y-%m-%d")
    return _find_dated_file(
        video_dir, _dated_patterns("", project_name, date_str, ".mp4"), date_str
    )


def find_keogram_file(video_dir: Path, project_name: str, date: datetime.date) -> Path:
    """Find the generated keogram file for a given date."""
    date_str = date.strftime("%Y-%m-%d")
    return _find_dated_file(
        video_dir, _dated_patterns("keogram", project_name, date_str, ".jpg"), date_str
    )


def find_slitscan_file(video_dir: Path, project_name: str, date: datetime.date) -> Path:
    """Find the generated slitscan file for a given date."""
    date_str = date.strftime("%Y-%m-%d")
    return _find_dated_file(
        video_dir, _dated_patterns("slitscan", project_name, date_str, ".jpg"), date_str
    )


def main():
    """Render yesterday's video, keogram and slitscan, then upload them."""
    parser = argparse.ArgumentParser(
        description="Daily timelapse runner - creates video and uploads to server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run for yesterday (default, designed for 5 AM cron job)
  python3 -m raspilapse.cli.daily

  # Run for a specific date
  python3 -m raspilapse.cli.daily --date 2025-12-24

  # Skip upload (just create video)
  python3 -m raspilapse.cli.daily --no-upload

  # Only upload (video already exists)
  python3 -m raspilapse.cli.daily --only-upload --date 2025-12-24
        """,
    )

    parser.add_argument(
        "--date",
        help="Date for timelapse in YYYY-MM-DD format (default: yesterday)",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config/config.yml",
        help="Path to configuration file (default: config/config.yml)",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Skip upload step (just create video and keogram)",
    )
    parser.add_argument(
        "--only-upload",
        action="store_true",
        help="Skip video creation (just upload existing files)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without actually doing it",
    )

    args = parser.parse_args()
    configure_logging(args.config)

    # Relative paths in the config -- the video directory, the database, the
    # log directory -- resolve against the working directory, so this has to
    # run from the project root wherever the timer invoked it from.
    os.chdir(PROJECT_ROOT)

    # systemd already stops the unit overlapping itself -- `systemctl start`
    # during a run joins the existing job rather than starting a second
    # ExecStart. What it cannot see is someone running `python -m
    # raspilapse.cli.daily` in a shell while the 05:00 timer is mid-encode,
    # which is two ffmpegs, ~2.8 GB resident and a starved capture loop.
    #
    # The lock is held for all of main(), upload included, and released when
    # the process exits however it exits.
    #
    # "a", not "w": a contender must not truncate the holder's lock file, and
    # "w" also re-stamps mtime on every attempt -- which made the holder-age
    # report below measure the contender's own open() instead of the holder.
    lock_path = PROJECT_ROOT / "data" / "daily.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "a")  # noqa: SIM115 -- must outlive this scope
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Stamp acquisition time; the contender branch reads it back as the
        # holder's age.
        os.utime(lock_path, None)
    except OSError as e:
        # Contention is EACCES or EAGAIN depending on the platform; anything
        # else (ENOLCK on a filesystem without lock support, EIO) means we do
        # not know whether another run holds it. Reporting those as success
        # would skip the day's video while systemd showed green.
        if e.errno not in (errno.EACCES, errno.EAGAIN):
            print(f"Error: could not acquire {lock_path}: {e}")
            return 1
        # Say how long the holder has been at it: "in progress" for nine hours
        # is a wedged run, and this line is the only place that becomes visible.
        try:
            held_min = (datetime.now().timestamp() - lock_path.stat().st_mtime) / 60
            print(
                "Another daily video run is already in progress "
                f"(lock held ~{held_min:.0f} min); leaving it to finish."
            )
        except OSError:
            print("Another daily video run is already in progress; leaving it to finish.")
        # Deliberately 0, not 1. A second invocation is not a failure, and
        # exiting non-zero would drop the unit into 'failed' -- which is
        # exactly the misleading signal this whole change set is about.
        return 0

    # Load configuration
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(f"Error: Config file not found: {args.config}")
        return 1
    except yaml.YAMLError as e:
        print(f"Error: Invalid YAML in config file: {e}")
        return 1

    # Setup logger
    logger = get_logger("daily_timelapse", args.config)

    # Determine the date for the timelapse
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Error: Invalid date format '{args.date}'. Use YYYY-MM-DD")
            return 1
    else:
        # Default: yesterday
        target_date = datetime.now().date() - timedelta(days=1)

    logger.info(f"Daily timelapse for: {target_date}")
    print(f"Creating daily timelapse for: {target_date}")

    # Get config values
    project_name = config["output"]["project_name"]
    video_dir = Path(config["video"]["directory"])

    # UploadService derives camera_id from the same config itself.
    upload_config = config.get("video_upload", {})

    # Step 1: Create timelapse video, keogram, and slitscan
    if not args.only_upload:
        print("\n=== Creating Timelapse Video ===")
        logger.info("Starting timelapse creation")

        # Build the command for the timelapse renderer, run as a separate
        # process so a crash or an OOM while encoding a day of 4K frames cannot
        # take this runner down with it.
        #
        # Invoked with -m rather than by path: this used to point at
        # src/make_timelapse.py, which stopped existing when the package moved,
        # and would have failed at 05:00 with a file-not-found.
        make_timelapse_cmd = [
            sys.executable,
            "-m",
            "raspilapse.cli.timelapse",
            "--config",
            args.config,
            "--start",
            "05:00",
            "--end",
            "05:00",
            "--start-date",
            target_date.strftime("%Y-%m-%d"),
            "--end-date",
            (target_date + timedelta(days=1)).strftime("%Y-%m-%d"),
            "--slitscan",  # Generate slitscan image alongside keogram
        ]

        if args.dry_run:
            print(f"Would run: {' '.join(make_timelapse_cmd)}")
        else:
            logger.info(f"Running: {' '.join(make_timelapse_cmd)}")
            # A day of 4K takes ~25 minutes; ten times that means ffmpeg is
            # wedged, and an unbounded wait here leaves this Type=oneshot unit
            # 'activating' forever -- the timer merges into the running job and
            # no video is ever built again, with nothing marked failed.
            try:
                result = subprocess.run(make_timelapse_cmd, cwd=PROJECT_ROOT, timeout=4 * 3600)
            except subprocess.TimeoutExpired:
                logger.error("Timelapse renderer exceeded 4 hours; killed")
                print("Error: timelapse renderer exceeded 4 hours and was killed")
                return 1

            # EXIT_NO_IMAGES (10) from the renderer means "no images for this
            # date" - a normal outcome (camera was off, fresh install), not a
            # failure - so return 0 and the systemd unit stays clean. Argparse
            # usage errors exit 2, which used to carry this meaning and made a
            # broken invocation report nightly success.
            if result.returncode == 10:
                msg = f"No images for {target_date.strftime('%Y-%m-%d')} - nothing to do"
                logger.warning(msg)
                print(msg)
                return 0

            if result.returncode != 0:
                logger.error(f"make_timelapse.py failed with code {result.returncode}")
                print("Error: Timelapse creation failed")
                return 1

            logger.info("Timelapse creation completed")

    # Step 2: Upload to server
    if not args.no_upload and upload_config and upload_config.get("enabled", True):
        print("\n=== Uploading to Server ===")
        logger.info("Starting upload")

        # Find the generated video file
        video_path = find_video_file(video_dir, project_name, target_date)
        if not video_path:
            logger.error(f"Could not find video file in {video_dir}")
            print("Error: Video file not found")
            return 1

        logger.info(f"Found video: {video_path}")

        # Find keogram
        keogram_path = find_keogram_file(video_dir, project_name, target_date)
        if keogram_path:
            logger.info(f"Found keogram: {keogram_path}")

        # Find slitscan
        slitscan_path = find_slitscan_file(video_dir, project_name, target_date)
        if slitscan_path:
            logger.info(f"Found slitscan: {slitscan_path}")

        if args.dry_run:
            print("Would upload:")
            print(f"  Video: {video_path}")
            print(f"  Keogram: {keogram_path}")
            print(f"  Slitscan: {slitscan_path}")
            print(f"  To: {upload_config.get('url', 'unknown')}")
        else:
            # Use UploadService for upload with retry queue support
            upload_service = UploadService(config, args.config)
            date_str = target_date.strftime("%Y-%m-%d")

            # Check if already successfully uploaded (e.g. by retry_uploads after a reboot)
            existing = upload_service.get_upload_by_date(date_str)
            if existing and existing["status"] == "success":
                logger.info(f"Upload for {date_str} already completed successfully, skipping")
                print(f"Upload already completed for {date_str}, skipping")
                print("\n=== Done ===")
                return 0

            success, error, _response = upload_service.upload_to_server(
                video_path=video_path,
                keogram_path=keogram_path,
                slitscan_path=slitscan_path,
                date=date_str,
            )

            if success:
                logger.info("Upload completed successfully")
                # Record success in queue so retry_uploads won't re-upload this date
                upload_service.record_upload_success(
                    video_path=str(video_path),
                    keogram_path=str(keogram_path) if keogram_path else None,
                    slitscan_path=str(slitscan_path) if slitscan_path else None,
                    video_date=date_str,
                    server_response=_response,
                )
            else:
                # Queue for retry - don't fail the script since video was created
                queue_id = upload_service.queue_upload(
                    video_path=str(video_path),
                    keogram_path=str(keogram_path) if keogram_path else None,
                    slitscan_path=str(slitscan_path) if slitscan_path else None,
                    video_date=date_str,
                )
                if queue_id:
                    upload_service.mark_upload_failed(queue_id, error or "Unknown error")
                    logger.warning(f"Upload failed, queued for retry (id={queue_id}): {error}")
                    print(f"Warning: Upload failed, queued for retry: {error}")
                else:
                    logger.error(f"Upload failed and could not queue for retry: {error}")
                    print(f"Error: Upload failed: {error}")
                    return 1
    elif args.no_upload:
        print("Skipping upload (--no-upload)")
    else:
        print("Upload disabled in config")

    print("\n=== Done ===")
    logger.info("Daily timelapse completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
