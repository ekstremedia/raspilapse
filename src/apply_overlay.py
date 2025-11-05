#!/usr/bin/env python3
"""
Standalone script to apply overlays to existing images.

Can be used to add overlays to previously captured images or batch process
multiple images.
"""

import sys
import argparse
from pathlib import Path
import json

try:
    from src.overlay import apply_overlay_to_image
    from src.logging_config import get_logger
except ImportError:
    from overlay import apply_overlay_to_image
    from logging_config import get_logger

logger = get_logger("apply_overlay")


def main():
    """CLI entry point for overlay application."""
    parser = argparse.ArgumentParser(
        description="Apply text overlay to timelapse images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Apply overlay to a single image
  python3 src/apply_overlay.py test_photos/kringelen_2025_11_05_10_30_45.jpg

  # Apply overlay to multiple images
  python3 src/apply_overlay.py test_photos/*.jpg

  # Specify custom metadata and output
  python3 src/apply_overlay.py image.jpg -m metadata.json -o output.jpg

  # Override mode for overlay
  python3 src/apply_overlay.py image.jpg --mode night

  # Batch process with different output directory
  python3 src/apply_overlay.py test_photos/*.jpg --output-dir overlayed/
        """,
    )

    parser.add_argument(
        "images",
        nargs="+",
        help="Path(s) to image file(s) to process",
    )

    parser.add_argument(
        "-c",
        "--config",
        default="config/config.yml",
        help="Path to configuration file (default: config/config.yml)",
    )

    parser.add_argument(
        "-m",
        "--metadata",
        help="Path to metadata JSON file (if not specified, looks for {image}_metadata.json)",
    )

    parser.add_argument(
        "-o",
        "--output",
        help="Output path for processed image (if not specified, overwrites original)",
    )

    parser.add_argument(
        "--output-dir",
        help="Output directory for batch processing (preserves filenames)",
    )

    parser.add_argument(
        "--mode",
        choices=["day", "night", "transition"],
        help="Override light mode for overlay display",
    )

    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite original images (default behavior)",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Set log level
    if args.verbose:
        logger.setLevel("DEBUG")

    # Check if output and output-dir are both specified
    if args.output and args.output_dir:
        logger.error("Cannot specify both --output and --output-dir")
        return 1

    # Check if multiple images with single output
    if len(args.images) > 1 and args.output:
        logger.error("Cannot specify --output with multiple input images")
        logger.info("Use --output-dir for batch processing")
        return 1

    # Process each image
    success_count = 0
    error_count = 0

    for image_path_str in args.images:
        image_path = Path(image_path_str)

        if not image_path.exists():
            logger.error(f"Image not found: {image_path}")
            error_count += 1
            continue

        # Determine metadata path
        metadata_path = args.metadata
        if metadata_path is None:
            # Look for {image}_metadata.json
            metadata_path = image_path.parent / f"{image_path.stem}_metadata.json"
            if not metadata_path.exists():
                logger.warning(
                    f"Metadata not found: {metadata_path}, using empty metadata"
                )
                metadata_path = None

        # Determine output path
        if args.output_dir:
            output_dir = Path(args.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / image_path.name
        elif args.output:
            output_path = args.output
        else:
            # In-place (overwrite)
            output_path = None

        # Apply overlay
        try:
            logger.info(f"Processing: {image_path}")
            result_path = apply_overlay_to_image(
                str(image_path),
                metadata_path=str(metadata_path) if metadata_path else None,
                config_path=args.config,
                mode=args.mode,
                output_path=str(output_path) if output_path else None,
            )
            logger.info(f"Overlay applied: {result_path}")
            success_count += 1

        except Exception as e:
            logger.error(f"Failed to process {image_path}: {e}")
            if args.verbose:
                logger.exception("Full traceback:")
            error_count += 1

    # Summary
    total = success_count + error_count
    logger.info(f"\n=== Processing Complete ===")
    logger.info(f"Total images: {total}")
    logger.info(f"Successful: {success_count}")
    logger.info(f"Errors: {error_count}")

    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
