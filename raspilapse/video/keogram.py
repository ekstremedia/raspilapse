#!/usr/bin/env python3
"""
Keogram Generator for Raspilapse.

A Keogram (also called "time-slice" image) shows the passage of time by taking
the center vertical slit (1 pixel wide) from each timelapse image and stitching
them together horizontally. The result shows clouds, day/night transitions,
and aurora movement in a single static image.

Usage:
    python3 -m raspilapse.video.keogram --dir /var/www/html/images/2025/12/24/
    python3 -m raspilapse.video.keogram --dir /path/to/images --output keogram.jpg
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PIL import Image

from raspilapse.console import Colors, print_info, print_section
from raspilapse.logging_setup import configure_logging, get_logger


def find_images(directory: Path, pattern: str = "*.jpg") -> List[Path]:
    """
    Find all images in directory matching pattern, sorted by filename.

    Automatically excludes keogram and slitscan files to prevent recursive inclusion.

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
        # Exclude keogram, slitscan files and metadata
        if img.name.startswith(("keogram", "slitscan")) or "_metadata" in img.name:
            continue
        images.append(img)

    images.sort()  # Sort by filename (which includes timestamp)
    return images


def _save_slice(
    canvas: "Image.Image",
    output_path: Path,
    quality: int,
    label: str,
    processed: int,
    skipped: int,
    resized: int,
    logger: Optional[logging.Logger] = None,
    extra: Optional[List[tuple]] = None,
) -> bool:
    """Write one finished canvas and report on it."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(str(output_path), "JPEG", quality=quality, optimize=True)

        size_kb = output_path.stat().st_size / 1024
        print(f"\n  {Colors.success('✓')} {label} saved: {Colors.bold(str(output_path))}")
        print_info("Size", f"{size_kb:.1f} KB")
        print_info("Dimensions", f"{canvas.width} x {canvas.height} pixels")
        print_info("Processed", f"{processed} images")
        for name, value in extra or []:
            print_info(name, value)
        if skipped > 0:
            print(f"  {Colors.warning('⚠')} Skipped: {skipped} images")
        if resized > 0:
            print(f"  {Colors.warning('⚠')} Resized: {resized} images (different resolution)")

        if logger:
            logger.info(
                f"{label} created: {output_path} "
                f"({canvas.width}x{canvas.height}, {size_kb:.1f} KB)"
            )
        return True

    except Exception as e:
        msg = f"Failed to save {label.lower()}: {e}"
        print(Colors.error(f"✗ {msg}"))
        if logger:
            logger.error(msg)
        return False


def create_time_slices(
    image_paths: List[Path],
    keogram_path: Optional[Path] = None,
    slitscan_path: Optional[Path] = None,
    quality: int = 95,
    crop_top_percent: float = 7.0,
    crop_bottom_percent: float = 0.0,
    logger: Optional[logging.Logger] = None,
) -> dict:
    """
    Build a keogram and/or a slitscan, decoding each source frame once.

    Both outputs are a vertical strip taken from every frame; the only
    difference is which strip and where it lands. Generating them separately
    meant reading and JPEG-decoding the whole day twice -- on this camera, 2880
    4K frames at ~123 ms a decode, so about five minutes of the daily job spent
    decoding images it had already decoded a moment earlier.

    The two resize rules are deliberately kept apart rather than unified. A
    keogram matches height and preserves aspect ratio; a slitscan forces both
    dimensions. They only diverge when a frame's dimensions differ from the
    first frame's, which does not happen while the camera resolution is fixed
    -- but unifying them would silently change keogram pixels on the day
    somebody changes it.

    Args:
        image_paths: Image paths, sorted chronologically
        keogram_path: Where to write the keogram, or None to skip it
        slitscan_path: Where to write the slitscan, or None to skip it
        quality: JPEG quality (1-100)
        crop_top_percent: Percentage of height to crop from the top (overlay bar)
        crop_bottom_percent: Percentage of height to crop from the bottom
        logger: Optional logger

    Returns:
        Dict with a bool under 'keogram' and/or 'slitscan' for each output
        that was requested.
    """
    want_keogram = keogram_path is not None
    want_slitscan = slitscan_path is not None
    results = {}
    if want_keogram:
        results["keogram"] = False
    if want_slitscan:
        results["slitscan"] = False

    if not want_keogram and not want_slitscan:
        return results

    if not image_paths:
        msg = "No images to process"
        print(Colors.error(f"✗ {msg}"))
        if logger:
            logger.error(msg)
        return results

    num_images = len(image_paths)
    print(f"  Processing {Colors.bold(str(num_images))} images...")

    # Get dimensions from first image
    try:
        with Image.open(image_paths[0]) as first_img:
            original_height = first_img.height
            original_width = first_img.width
    except Exception as e:
        msg = f"Failed to read first image: {e}"
        print(Colors.error(f"✗ {msg}"))
        if logger:
            logger.error(msg)
        return results

    # Calculate crop amounts
    crop_top_px = int(original_height * crop_top_percent / 100)
    crop_bottom_px = int(original_height * crop_bottom_percent / 100)
    target_height = original_height - crop_top_px - crop_bottom_px

    if crop_top_px > 0 or crop_bottom_px > 0:
        print(f"  Cropping: top={crop_top_px}px, bottom={crop_bottom_px}px (overlay removal)")

    if logger:
        logger.info(f"Source image dimensions: {original_width}x{original_height}")
        if want_keogram:
            logger.info(f"Target dimensions: width={num_images}, height={target_height}")
        if want_slitscan:
            logger.info(f"Slitscan dimensions: {original_width}x{target_height}")
            logger.info(f"Number of frames: {num_images}")
        if crop_top_px > 0 or crop_bottom_px > 0:
            logger.info(f"Cropping: top={crop_top_px}px, bottom={crop_bottom_px}px")

    if target_height <= 0:
        # Reachable from the CLI's crop flags: the two percentages summing to
        # 100 or more leaves nothing. Image.new would raise here, outside the
        # per-frame handler, and take the whole run down with a traceback.
        msg = (
            f"Cropping leaves no image: {crop_top_percent}% top + "
            f"{crop_bottom_percent}% bottom of {original_height}px"
        )
        print(Colors.error(f"✗ {msg}"))
        if logger:
            logger.error(msg)
        return results

    keogram = Image.new("RGB", (num_images, target_height)) if want_keogram else None
    slitscan = Image.new("RGB", (original_width, target_height)) if want_slitscan else None

    # How much of the output width each frame owns. Rarely a whole number --
    # at 3840px over 2880 frames it is 1.333 -- so strips alternate between one
    # and two pixels wide and the position has to be tracked as a float.
    columns_per_frame = original_width / num_images
    if want_slitscan and logger:
        logger.info(f"Columns per frame: {columns_per_frame:.2f}")

    processed = 0
    skipped = 0
    keogram_resized = 0
    slitscan_resized = 0
    current_x = 0.0

    for i, img_path in enumerate(image_paths):
        try:
            with Image.open(img_path) as img:
                img_width, img_height = img.size

                if want_keogram:
                    # Keogram: match height, preserve aspect ratio.
                    frame, frame_width, frame_height = img, img_width, img_height
                    if img_height != original_height:
                        scale = original_height / img_height
                        frame_width = int(img_width * scale)
                        frame_height = original_height
                        frame = img.resize((frame_width, frame_height), Image.Resampling.LANCZOS)
                        keogram_resized += 1
                        if logger and keogram_resized == 1:
                            logger.warning(
                                f"Image {img_path.name} has different height "
                                f"({img_height} vs {original_height}), resizing"
                            )

                    center_x = frame_width // 2
                    strip = frame.crop(
                        (center_x, crop_top_px, center_x + 1, frame_height - crop_bottom_px)
                    )
                    keogram.paste(strip, (i, 0))

                if want_slitscan:
                    # Slitscan: force both dimensions.
                    frame, frame_height = img, img_height
                    if img_width != original_width or img_height != original_height:
                        source_dims = (img_width, img_height)
                        frame = img.resize(
                            (original_width, original_height), Image.Resampling.LANCZOS
                        )
                        frame_height = original_height
                        slitscan_resized += 1
                        if logger and slitscan_resized == 1:
                            logger.warning(
                                f"Image {img_path.name} has different dimensions "
                                f"({source_dims} vs {original_width}x{original_height}), resizing"
                            )

                    start_x = int(current_x)
                    next_x = current_x + columns_per_frame
                    end_x = int(next_x)
                    if end_x <= start_x:
                        end_x = start_x + 1
                    if end_x > original_width:
                        end_x = original_width

                    strip = frame.crop((start_x, crop_top_px, end_x, frame_height - crop_bottom_px))
                    slitscan.paste(strip, (start_x, 0))
                    current_x = next_x

                processed += 1

        except Exception as e:
            skipped += 1
            if logger:
                logger.warning(f"Failed to process {img_path.name}: {e}")
            # Advance regardless of which outputs are enabled. A frame owns its
            # slice of the output width whether or not it could be read, so a
            # skipped frame leaves a gap rather than shifting every frame after
            # it one strip to the left.
            current_x += columns_per_frame
            continue

        # Progress update every 10%
        if (i + 1) % max(1, num_images // 10) == 0:
            pct = (i + 1) * 100 // num_images
            print(f"  {Colors.CYAN}→{Colors.END} Progress: {pct}% ({i + 1}/{num_images})")

    if processed == 0:
        # Image.open only reads the header, so a truncated JPEG gets past the
        # first-frame dimension read and fails later inside crop(). If every
        # frame fails that way both canvases are still blank, and saving them
        # would report success and feed a black image into the upload queue.
        msg = f"No frames could be processed ({skipped} skipped)"
        print(Colors.error(f"✗ {msg}"))
        if logger:
            logger.error(msg)
        return results

    if want_keogram:
        results["keogram"] = _save_slice(
            keogram, keogram_path, quality, "Keogram", processed, skipped, keogram_resized, logger
        )
    if want_slitscan:
        results["slitscan"] = _save_slice(
            slitscan,
            slitscan_path,
            quality,
            "Slitscan",
            processed,
            skipped,
            slitscan_resized,
            logger,
            extra=[("Columns/frame", f"{columns_per_frame:.2f}")],
        )

    return results


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

    Thin wrapper over create_time_slices, which is where the work is. Use that
    directly when you want a slitscan as well, so the frames are decoded once
    instead of twice.

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
    return create_time_slices(
        image_paths,
        keogram_path=output_path,
        quality=quality,
        crop_top_percent=crop_top_percent,
        crop_bottom_percent=crop_bottom_percent,
        logger=logger,
    ).get("keogram", False)


def create_slitscan(
    image_paths: List[Path],
    output_path: Path,
    quality: int = 95,
    crop_top_percent: float = 7.0,
    crop_bottom_percent: float = 0.0,
    logger: Optional[logging.Logger] = None,
) -> bool:
    """
    Create a slitscan image from a list of images.

    Unlike a keogram which takes the center column from each image and creates
    a narrow output, slitscan creates a full-width image where each frame
    contributes columns at progressive positions from left to right.

    For example, with 1920px wide images and 960 frames:
    - Frame 0 contributes columns 0-1 (leftmost)
    - Frame 1 contributes columns 2-3
    - ...
    - Frame 959 contributes columns 1918-1919 (rightmost)

    The result shows the actual scene, but time progresses from left to right.

    Thin wrapper over create_time_slices, which is where the work is. Use that
    directly when you want a keogram as well, so the frames are decoded once
    instead of twice.

    Args:
        image_paths: List of image paths (must be sorted chronologically)
        output_path: Path for the output slitscan image
        quality: JPEG quality (1-100, default 95)
        crop_top_percent: Percentage of image height to crop from top (default 7%)
        crop_bottom_percent: Percentage of image height to crop from bottom
        logger: Optional logger instance

    Returns:
        True if successful, False otherwise
    """
    return create_time_slices(
        image_paths,
        slitscan_path=output_path,
        quality=quality,
        crop_top_percent=crop_top_percent,
        crop_bottom_percent=crop_bottom_percent,
        logger=logger,
    ).get("slitscan", False)


def main():
    """Build a keogram or slitscan from a directory of frames."""
    parser = argparse.ArgumentParser(
        description="Generate keogram or slitscan image from timelapse images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create keogram from a day folder
  python3 -m raspilapse.video.keogram --dir /var/www/html/images/2025/12/24/

  # Create slitscan instead of keogram
  python3 -m raspilapse.video.keogram --dir /var/www/html/images/2025/12/24/ --slitscan

  # Specify output filename
  python3 -m raspilapse.video.keogram --dir /path/to/images --output keogram_custom.jpg

  # Specify output directory (file will be named keogram_YYYY-MM-DD.jpg)
  python3 -m raspilapse.video.keogram --dir /var/www/html/images/2025/12/24/ --output-dir /var/www/html/keograms/

What is a Keogram?
  A keogram shows the passage of time by taking the center vertical slice
  (1 pixel wide) from each timelapse image and combining them horizontally.
  The result is a single image that shows clouds, day/night transitions,
  and aurora movement across the entire day.

What is a Slitscan?
  A slitscan creates a full-width image where each frame contributes columns
  at progressive positions from left to right. Unlike a keogram (narrow strip),
  slitscan shows the actual scene with time progressing across the width.
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
        "--slitscan",
        action="store_true",
        help="Generate slitscan instead of keogram (full-width image with time progressing left to right)",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config/config.yml",
        help="Path to configuration file (for logging)",
    )

    args = parser.parse_args()
    configure_logging(args.config)

    logger = get_logger("create_keogram")

    # Determine mode
    mode = "slitscan" if args.slitscan else "keogram"

    # Print header
    if args.slitscan:
        print_section("🎞️ SLITSCAN GENERATOR")
    else:
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

        filename = f"{mode}_{date_str}.jpg"

        if args.output_dir:
            output_path = Path(args.output_dir) / filename
        else:
            output_path = input_dir / filename

    print(f"\n  Output: {Colors.bold(str(output_path))}")

    # Determine crop values
    crop_top = 0.0 if args.no_crop else args.crop_top
    crop_bottom = 0.0 if args.no_crop else args.crop_bottom

    # Create keogram or slitscan
    if args.slitscan:
        print_section("🎨 Creating Slitscan")
        success = create_slitscan(
            images,
            output_path,
            quality=args.quality,
            crop_top_percent=crop_top,
            crop_bottom_percent=crop_bottom,
            logger=logger,
        )
    else:
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
        print_section(f"✓ {mode.upper()} CREATED SUCCESSFULLY!")
        logger.info(f"{mode.capitalize()} generation completed successfully")
        return 0
    else:
        print_section(f"✗ FAILED TO CREATE {mode.upper()}")
        logger.error(f"{mode.capitalize()} generation failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
