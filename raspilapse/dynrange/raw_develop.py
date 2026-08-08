"""Develop the sensor's DNG instead of keeping the ISP's JPEG.

libcamera's DNGs embed the white balance and colour matrices the ISP itself
would have used, so a libraw develop with the camera's own numbers stays
close to the ISP's colour while skipping its 8-bit quantisation -- roughly
two extra stops of shadow latitude from the imx708's 10-bit Bayer data.

The ISP JPEG is always captured alongside and is the built-in fallback: the
developed image replaces it only on success (``os.replace``), so a frame
exists whatever happens here. Long exposures fall back deliberately --
developing 12 megapixels on a Pi 4 costs real seconds, and a night slot has
none spare -- which also means the nightly look shifts between developed
and ISP colour at the handover. That seam is inherent to this method and a
reason the trial may prefer fusion.
"""

from typing import Optional, Tuple

from raspilapse.logging_setup import get_logger

logger = get_logger("dynrange")

# What a 12 MP libraw develop plus resize costs on a Pi 4, conservatively.
# Measured properly by raspilapse-drtest; used only by should_use_raw.
DEVELOP_ESTIMATE_S = 15.0

# Keep this much of the slot free for the close/reopen cycle and observe
# step, matching fusion's reserve.
_SLOT_RESERVE_S = 5.0


def should_use_raw(mode: Optional[str], exposure_s: float, interval_s: float) -> bool:
    """Whether this frame's DNG is worth developing inside its slot."""
    if mode == "night":
        return False
    return exposure_s + DEVELOP_ESTIMATE_S <= interval_s - _SLOT_RESERVE_S


def develop_dng(
    dng_path: str,
    out_jpg_path: str,
    size: Tuple[int, int],
    quality: int = 85,
) -> bool:
    """Develop a DNG over its ISP JPEG. False leaves the JPEG untouched.

    use_camera_wb and no_auto_bright keep the develop deterministic and
    anchored to what the exposure loop commanded -- auto-brightening would
    fight the metering and flicker the timelapse.
    """
    try:
        import cv2
        import rawpy
    except ImportError as e:  # pragma: no cover - config seam checks first
        logger.warning(f"Raw develop needs rawpy and cv2: {e}")
        return False

    import os
    import tempfile

    tmp_path: Optional[str] = None
    try:
        with rawpy.imread(dng_path) as raw:
            rgb = raw.postprocess(
                use_camera_wb=True,
                no_auto_bright=True,
                output_bps=8,
            )
        # rawpy hands back RGB; OpenCV encodes BGR.
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        del rgb
        target = (int(size[0]), int(size[1]))
        if (bgr.shape[1], bgr.shape[0]) != target:
            bgr = cv2.resize(bgr, target, interpolation=cv2.INTER_AREA)

        out_dir = os.path.dirname(os.path.abspath(out_jpg_path))
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".develop-", suffix=".jpg", dir=out_dir)
        # mkstemp's 0600 would ride os.replace onto a frame the webserver
        # answers 403 for.
        os.fchmod(tmp_fd, 0o644)
        os.close(tmp_fd)
        if not cv2.imwrite(tmp_path, bgr, [cv2.IMWRITE_JPEG_QUALITY, int(quality)]):
            logger.warning(f"Raw develop could not encode {out_jpg_path}")
            return False
        os.replace(tmp_path, out_jpg_path)
        tmp_path = None
        logger.debug(f"Developed {dng_path} over {out_jpg_path}")
        return True
    except Exception as e:
        logger.warning(f"Raw develop failed for {dng_path}: {e}")
        return False
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
