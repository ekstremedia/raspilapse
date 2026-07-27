#!/usr/bin/env python3
"""
Retry Uploads - Process the upload retry queue.

Usage:
    raspilapse-retry-uploads                  # Process queue (respects backoff timing)
    raspilapse-retry-uploads --force          # Retry all pending, ignore backoff
    raspilapse-retry-uploads --status         # Show queue status only
    raspilapse-retry-uploads --purge-missing  # Cancel rows whose video is gone
"""

import argparse
import os
import sys
from pathlib import Path

import yaml

from raspilapse.config import PROJECT_ROOT, load_config
from raspilapse.storage.upload import UploadService


def main():
    parser = argparse.ArgumentParser(
        description="Process the upload retry queue",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process queue (respects backoff timing)
  python3 src/retry_uploads.py

  # Retry all pending uploads immediately
  python3 src/retry_uploads.py --force

  # Show queue status without processing
  python3 src/retry_uploads.py --status

  # Cancel queued uploads whose source video has been deleted
  python3 src/retry_uploads.py --purge-missing
        """,
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Retry all pending uploads regardless of backoff timing, "
        "including ones already given up on",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show queue status without processing",
    )
    parser.add_argument(
        "--purge-missing",
        action="store_true",
        help="Cancel queued uploads whose source video no longer exists",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config/config.yml",
        help="Path to configuration file (default: config/config.yml)",
    )

    args = parser.parse_args()

    # Relative paths in the config -- the database, the log directory -- are
    # resolved against the working directory, so this has to run from the
    # project root regardless of where the timer invoked it from. It used to
    # derive the same path from a sys.path bootstrap that no longer exists.
    os.chdir(PROJECT_ROOT)

    # Load configuration
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(f"Error: Config file not found: {args.config}")
        return 1
    except yaml.YAMLError as e:
        print(f"Error: Invalid YAML in config file: {e}")
        return 1

    # Initialize upload service
    service = UploadService(config, args.config)

    # Show status
    stats = service.get_queue_stats()
    print("Upload Queue Status:")
    print(f"  Pending:  {stats.get('pending', 0)}")
    print(f"  Uploading: {stats.get('uploading', 0)}")
    print(f"  Success:  {stats.get('success', 0)}")
    print(f"  Failed:   {stats.get('failed', 0)}")
    print(f"  Total:    {stats.get('total', 0)}")

    if args.status:
        # Show detailed pending uploads
        pending = service.get_pending_uploads(include_failed=True)
        if pending:
            print("\nPending Uploads:")
            for upload in pending:
                missing = "" if Path(upload["video_path"]).exists() else "  [FILE MISSING]"
                print(
                    f"  [{upload['id']}] {upload['video_date']} ({upload['status']}) - "
                    f"retries: {upload['retry_count']}/{upload['max_retries']}, "
                    f"next: {upload['next_retry_at'] or 'now'}{missing}"
                )
                if upload["last_error"]:
                    print(f"       Error: {upload['last_error'][:80]}")
        return 0

    if args.purge_missing:
        cancelled = 0
        for upload in service.get_pending_uploads(include_failed=True):
            if not Path(upload["video_path"]).exists():
                if service.cancel_upload(upload["id"]):
                    cancelled += 1
                    print(f"  Cancelled [{upload['id']}] {upload['video_date']}")
        print(f"\nCancelled {cancelled} upload(s) with a missing source video.")
        return 0

    # Uploading is pointless without credentials, and every attempt still burns
    # a retry slot and writes a log line. Bail out before that happens.
    #
    # Exit 0, not 1: this runs from a timer every 30 minutes, and "uploads are
    # not configured" is a setting, not a fault. Returning non-zero would leave
    # the unit permanently in `failed` on every install that doesn't upload.
    upload_config = config.get("video_upload", {})
    if not upload_config.get("url") or not upload_config.get("api_key"):
        print(
            "\nUploads are not configured (video_upload.url / video_upload.api_key "
            f"are empty in {args.config}) - nothing to do."
        )
        return 0

    # Process the queue
    if stats.get("pending", 0) == 0 and stats.get("uploading", 0) == 0:
        print("\nNo pending uploads to process.")
        return 0

    print(f"\nProcessing upload queue (force={args.force})...")
    results = service.process_retry_queue(force=args.force)

    print("\nResults:")
    print(f"  Processed: {results['processed']}")
    print(f"  Success:   {results['success']}")
    print(f"  Failed:    {results['failed']}")
    print(f"  Skipped:   {results['skipped']}")

    if results["errors"]:
        print("\nErrors:")
        for err in results["errors"]:
            print(f"  [{err['id']}] {err['error'][:80]}")

    # Return non-zero if any failures
    return 1 if results["failed"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
