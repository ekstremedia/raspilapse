#!/usr/bin/env python3
"""
Keogram Generator for Raspilapse.

A Keogram (also called "time-slice" image) shows the passage of time by taking
the center vertical slit (1 pixel wide) from each timelapse image and stitching
them together horizontally. The result shows clouds, day/night transitions,
and aurora movement in a single static image.

Usage:
    python3 src/create_keogram.py --dir /var/www/html/images/2025/12/24/
    python3 src/create_keogram.py --dir /path/to/images --output keogram.jpg
"""

import os
import sys
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
import logging

from PIL import Image

# Add project root to path for imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from src.logging_config import get_logger
except ModuleNotFoundError:
    from logging_config import get_logger


# ANSI color codes for pretty output
class Colors:
    """ANSI color codes for terminal output."""

    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    END = "\033[0m"

    @staticmethod
    def header(text: str) -> str:
        return f"{Colors.BOLD}{Colors.CYAN}{text}{Colors.END}"

    @staticmethod
    def success(text: str) -> str:
        return f"{Colors.GREEN}{text}{Colors.END}"

    @staticmethod
    def error(text: str) -> str:
        return f"{Colors.RED}{text}{Colors.END}"

    @staticmethod
    def warning(text: str) -> str:
        return f"{Colors.YELLOW}{text}{Colors.END}"

    @staticmethod
    def info(text: str) -> str:
        return f"{Colors.BLUE}{text}{Colors.END}"

    @staticmethod
    def bold(text: str) -> str:
        return f"{Colors.BOLD}{text}{Colors.END}"


def print_section(title: str):
    """Print a section header."""
    print(f"\n{Colors.header('═' * 70)}")
    print(f"{Colors.header(f'  {title}')}")
    print(f"{Colors.header('═' * 70)}")


def print_info(label: str, value: str):
    """Print an info line with label and value."""
    print(f"  {Colors.BOLD}{label}:{Colors.END} {value}")


def find_images(directory: Path, pattern: str = "*.jpg") -> List[Path]:
    """
    Find all images in directory matching pattern, sorted by filename.

    Automatically excludes keogram files to prevent recursive inclusion.

    Args:
        directory: Directory to search
        pattern: Glob pattern for images (default: *.jpg)

    Returns:
        List of image paths sorted by filename (chronological by timestamp)
    """
    if not directory.exists():
        raise ValueError(f"Directory not found: {directory}")

    images = []
    for img in directory.glob(pattern):
        # Exclude keogram files and metadata
        if img.name.startswith("keogram") or "_metadata" in img.name:
            continue
        images.append(img)

    images.sort()  # Sort by filename (which includes timestamp)
    return images


def create_keogram(
    image_paths: List[Path],
    output_path: Path,
    quality: int = 95,
    crop_top_percent: float = 7.0,
    crop_bottom_percent: float = 0.0,
    logger: Optional[logging.Logger] = None,
) -> bool:
    """
    Create a keogram from a list of images.

    Takes the center vertical column (1 pixel wide) from each image and
    stitches them together horizontally to show the passage of time.

    Args:
        image_paths: List of image paths (must be sorted chronologically)
        output_path: Path for the output keogram image
        quality: JPEG quality (1-100, default 95)
        crop_top_percent: Percentage of image height to crop from top (default 7% for overlay bar)
        crop_bottom_percent: Percentage of image height to crop from bottom
        logger: Optional logger instance

    Returns:
        True if successful, False otherwise
    """
    if not image_paths:
        msg = "No images to process"
        print(Colors.error(f"✗ {msg}"))
        if logger:
            logger.error(msg)
        return False

    num_images = len(image_paths)
    print(f"  Processing {Colors.bold(str(num_images))} images...")

    # Get dimensions from first image
    try:
        with Image.open(image_paths[0]) as first_img:
            original_height = first_img.height
            first_width = first_img.width
    except Exception as e:
        msg = f"Failed to read first image: {e}"
        print(Colors.error(f"✗ {msg}"))
        if logger:
            logger.error(msg)
        return False

    # Calculate crop amounts
    crop_top_px = int(original_height * crop_top_percent / 100)
    crop_bottom_px = int(original_height * crop_bottom_percent / 100)
    target_height = original_height - crop_top_px - crop_bottom_px

    if crop_top_px > 0 or crop_bottom_px > 0:
        print(f"  Cropping: top={crop_top_px}px, bottom={crop_bottom_px}px (overlay removal)")

    if logger:
        logger.info(f"Target dimensions: width={num_images}, height={target_height}")
        logger.info(f"Source image dimensions: {first_width}x{original_height}")
        if crop_top_px > 0 or crop_bottom_px > 0:
            logger.info(f"Cropping: top={crop_top_px}px, bottom={crop_bottom_px}px")

    # Create the keogram canvas
    # Width = number of images (1 pixel per image)
    # Height = height of source images
    keogram = Image.new("RGB", (num_images, target_height))

    # Process each image
    processed = 0
    skipped = 0
    resized = 0

    for i, img_path in enumerate(image_paths):
        try:
            with Image.open(img_path) as img:
                img_width, img_height = img.size

                # Handle resolution changes - resize if height differs from original
                if img_height != original_height:
                    # Resize to match original height while preserving aspect ratio
                    scale = original_height / img_height
                    new_width = int(img_width * scale)
                    img = img.resize((new_width, original_height), Image.Resampling.LANCZOS)
                    img_width = new_width
                    img_height = original_height
                    resized += 1
                    if logger and resized == 1:
                        logger.warning(
                            f"Image {img_path.name} has different height "
                            f"({img_height} vs {original_height}), resizing"
                        )

                # Extract center vertical column (1 pixel wide), with crop applied
                center_x = img_width // 2
                # Crop box: (left, top, right, bottom)
                strip = img.crop((center_x, crop_top_px, center_x + 1, img_height - crop_bottom_px))

                # Paste into keogram at position i
                keogram.paste(strip, (i, 0))
                processed += 1

        except Exception as e:
            skipped += 1
            if logger:
                logger.warning(f"Failed to process {img_path.name}: {e}")
            continue

        # Progress update every 10%
        if (i + 1) % max(1, num_images // 10) == 0:
            pct = (i + 1) * 100 // num_images
            print(f"  {Colors.CYAN}→{Colors.END} Progress: {pct}% ({i + 1}/{num_images})")

    # Save the keogram
    try:
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        keogram.save(str(output_path), "JPEG", quality=quality, optimize=True)

        size_kb = output_path.stat().st_size / 1024
        print(f"\n  {Colors.success('✓')} Keogram saved: {Colors.bold(str(output_path))}")
        print_info("Size", f"{size_kb:.1f} KB")
        print_info("Dimensions", f"{num_images} x {target_height} pixels")
        print_info("Processed", f"{processed} images")
        if skipped > 0:
            print(f"  {Colors.warning('⚠')} Skipped: {skipped} images")
        if resized > 0:
            print(f"  {Colors.warning('⚠')} Resized: {resized} images (different resolution)")

        if logger:
            logger.info(
                f"Keogram created: {output_path} "
                f"({num_images}x{target_height}, {size_kb:.1f} KB)"
            )

        return True

    except Exception as e:
        msg = f"Failed to save keogram: {e}"
        print(Colors.error(f"✗ {msg}"))
        if logger:
            logger.error(msg)
        return False


def create_keogram_from_images(
    images: List[Path],
    output_path: Path,
    quality: int = 95,
    crop_top_percent: float = 7.0,
    crop_bottom_percent: float = 0.0,
    logger: Optional[logging.Logger] = None,
) -> bool:
    """
    Convenience function to create keogram from a list of image paths.

    This is the main entry point for integration with make_timelapse.py.

    Args:
        images: List of image paths (sorted chronologically)
        output_path: Output path for the keogram
        quality: JPEG quality (1-100)
        crop_top_percent: Percentage to crop from top (default 7% for overlay bar)
        crop_bottom_percent: Percentage to crop from bottom
        logger: Optional logger

    Returns:
        True if successful
    """
    return create_keogram(
        images,
        output_path,
        quality=quality,
        crop_top_percent=crop_top_percent,
        crop_bottom_percent=crop_bottom_percent,
        logger=logger,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Generate keogram (time-slice) image from timelapse images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create keogram from a day folder
  python3 src/create_keogram.py --dir /var/www/html/images/2025/12/24/

  # Specify output filename
  python3 src/create_keogram.py --dir /path/to/images --output keogram_custom.jpg

  # Specify output directory (file will be named keogram_YYYY-MM-DD.jpg)
  python3 src/create_keogram.py --dir /var/www/html/images/2025/12/24/ --output-dir /var/www/html/keograms/

What is a Keogram?
  A keogram shows the passage of time by taking the center vertical slice
  (1 pixel wide) from each timelapse image and combining them horizontally.
  The result is a single image that shows clouds, day/night transitions,
  and aurora movement across the entire day.
        """,
    )

    parser.add_argument(
        "--dir",
        "-d",
        required=True,
        help="Directory containing timelapse images",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output filename (default: keogram_YYYY-MM-DD.jpg based on directory date)",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory (default: same as input directory)",
    )
    parser.add_argument(
        "--pattern",
        default="*.jpg",
        help="Glob pattern for finding images (default: *.jpg)",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=95,
        help="JPEG quality 1-100 (default: 95)",
    )
    parser.add_argument(
        "--crop-top",
        type=float,
        default=7.0,
        help="Percentage of image height to crop from top (default: 7%% for overlay bar removal)",
    )
    parser.add_argument(
        "--crop-bottom",
        type=float,
        default=0.0,
        help="Percentage of image height to crop from bottom (default: 0%%)",
    )
    parser.add_argument(
        "--no-crop",
        action="store_true",
        help="Disable automatic top cropping (include overlay in keogram)",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config/config.yml",
        help="Path to configuration file (for logging)",
    )

    args = parser.parse_args()

    # Setup logger
    try:
        logger = get_logger("create_keogram", args.config)
    except Exception:
        logger = logging.getLogger("create_keogram")
        logger.setLevel(logging.INFO)

    # Print header
    print_section("🌅 KEOGRAM GENERATOR")

    # Find images
    input_dir = Path(args.dir)
    print(f"\n  Scanning: {Colors.bold(str(input_dir))}")

    try:
        images = find_images(input_dir, args.pattern)
    except ValueError as e:
        print(Colors.error(f"✗ {e}"))
        logger.error(str(e))
        return 1

    if not images:
        msg = f"No images found matching '{args.pattern}' in {input_dir}"
        print(Colors.error(f"✗ {msg}"))
        logger.error(msg)
        return 1

    print(f"  {Colors.success('✓')} Found {Colors.bold(str(len(images)))} images")
    print(f"  {Colors.CYAN}→{Colors.END} First: {Colors.bold(images[0].name)}")
    print(f"  {Colors.CYAN}→{Colors.END} Last:  {Colors.bold(images[-1].name)}")

    # Determine output path
    if args.output:
        if args.output_dir:
            output_path = Path(args.output_dir) / args.output
        else:
            output_path = input_dir / args.output
    else:
        # Generate filename from directory date or today's date
        try:
            # Try to extract date from directory path (e.g., /images/2025/12/24/)
            parts = input_dir.parts
            if len(parts) >= 3:
                year, month, day = parts[-3], parts[-2], parts[-1]
                if year.isdigit() and month.isdigit() and day.isdigit():
                    date_str = f"{year}-{month}-{day}"
                else:
                    date_str = datetime.now().strftime("%Y-%m-%d")
            else:
                date_str = datetime.now().strftime("%Y-%m-%d")
        except Exception:
            date_str = datetime.now().strftime("%Y-%m-%d")

        filename = f"keogram_{date_str}.jpg"

        if args.output_dir:
            output_path = Path(args.output_dir) / filename
        else:
            output_path = input_dir / filename

    print(f"\n  Output: {Colors.bold(str(output_path))}")

    # Determine crop values
    crop_top = 0.0 if args.no_crop else args.crop_top
    crop_bottom = 0.0 if args.no_crop else args.crop_bottom

    # Create keogram
    print_section("🎨 Creating Keogram")
    success = create_keogram(
        images,
        output_path,
        quality=args.quality,
        crop_top_percent=crop_top,
        crop_bottom_percent=crop_bottom,
        logger=logger,
    )

    if success:
        print_section("✓ KEOGRAM CREATED SUCCESSFULLY!")
        logger.info("Keogram generation completed successfully")
        return 0
    else:
        print_section("✗ FAILED TO CREATE KEOGRAM")
        logger.error("Keogram generation failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
