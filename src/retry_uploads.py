#!/usr/bin/env python3
"""
Retry Uploads - Process the upload retry queue.

Usage:
    python3 src/retry_uploads.py           # Process queue (respects backoff timing)
    python3 src/retry_uploads.py --force   # Retry all pending, ignore backoff
    python3 src/retry_uploads.py --status  # Show queue status only
"""

import argparse
import os
import sys

import yaml

# Add project root to path for imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.upload_service import UploadService


def load_config(config_path: str) -> dict:
    """Load configuration from YAML file."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


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
        """,
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Retry all pending uploads regardless of backoff timing",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show queue status without processing",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config/config.yml",
        help="Path to configuration file (default: config/config.yml)",
    )

    args = parser.parse_args()

    # Change to project directory
    os.chdir(project_root)

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
    print(f"Upload Queue Status:")
    print(f"  Pending:  {stats.get('pending', 0)}")
    print(f"  Uploading: {stats.get('uploading', 0)}")
    print(f"  Success:  {stats.get('success', 0)}")
    print(f"  Failed:   {stats.get('failed', 0)}")
    print(f"  Total:    {stats.get('total', 0)}")

    if args.status:
        # Show detailed pending uploads
        pending = service.get_pending_uploads()
        if pending:
            print(f"\nPending Uploads:")
            for upload in pending:
                print(
                    f"  [{upload['id']}] {upload['video_date']} - "
                    f"retries: {upload['retry_count']}/{upload['max_retries']}, "
                    f"next: {upload['next_retry_at'] or 'now'}"
                )
                if upload["last_error"]:
                    print(f"       Error: {upload['last_error'][:80]}")
        return 0

    # Process the queue
    if stats.get("pending", 0) == 0 and stats.get("uploading", 0) == 0:
        print("\nNo pending uploads to process.")
        return 0

    print(f"\nProcessing upload queue (force={args.force})...")
    results = service.process_retry_queue(force=args.force)

    print(f"\nResults:")
    print(f"  Processed: {results['processed']}")
    print(f"  Success:   {results['success']}")
    print(f"  Failed:    {results['failed']}")
    print(f"  Skipped:   {results['skipped']}")

    if results["errors"]:
        print(f"\nErrors:")
        for err in results["errors"]:
            print(f"  [{err['id']}] {err['error'][:80]}")

    # Return non-zero if any failures
    return 1 if results["failed"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
