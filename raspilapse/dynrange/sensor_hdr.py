"""The imx708's on-chip HDR: multiple exposures merged on the sensor itself.

Free of CPU cost and of code -- the ISP receives one pre-merged stream --
but not free of trade-offs. The sensor merges in a binned mode, so the
frame arrives at 2304x1296 (a quarter of the pixels) and gets upscaled back
to the configured size here, before the overlay is drawn, so the video
pipeline and the keogram only ever see one frame size. And merged exposures
cannot be long ones: at night the mode is switched off (see
``sensor_hdr.night_off``), where the exposure ladder's 20-second frames
live.

On the Pi 4, libcamera's HdrMode control never reaches this sensor. The
switch is a V4L2 sensor control (``wide_dynamic_range``) that must be set
while the camera is closed -- which costs this pipeline nothing, because
the daemon closes and reopens the camera every frame anyway.
"""

import glob
import os
import subprocess
import tempfile
from typing import Optional, Tuple

from raspilapse.logging_setup import get_logger

logger = get_logger("dynrange")

# The largest frame the imx708 delivers with wide_dynamic_range=1.
HDR_MODE_SIZE = (2304, 1296)

# find_wdr_subdev's cache: None = not looked yet, "" = looked and found
# nothing, anything else = the device path.
_subdev_cache: Optional[str] = None


def find_wdr_subdev() -> Optional[str]:
    """The V4L2 subdevice exposing wide_dynamic_range, or None.

    The imx708's sensor controls live on one of the /dev/v4l-subdev* nodes;
    which one varies by platform, so each is asked for its control list.
    The answer cannot change while the process runs, so it is cached --
    including the negative, which otherwise costs a subprocess sweep every
    frame on cameras without the control.
    """
    global _subdev_cache
    if _subdev_cache is not None:
        return _subdev_cache or None

    for device in sorted(glob.glob("/dev/v4l-subdev*")):
        try:
            listing = subprocess.run(
                ["v4l2-ctl", "-d", device, "--list-ctrls"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.debug(f"Could not list controls on {device}: {e}")
            continue
        if "wide_dynamic_range" in listing.stdout:
            _subdev_cache = device
            logger.debug(f"wide_dynamic_range found on {device}")
            return device

    _subdev_cache = ""
    return None


def set_wdr(enabled: bool) -> bool:
    """Switch the sensor's HDR mode. Camera must be closed for it to stick.

    Returns True when the control was set. False covers every failure --
    no subdevice, no v4l2-ctl, the sensor grabbed by a running camera --
    because the caller's remedy is the same warning either way.
    """
    device = find_wdr_subdev()
    if device is None:
        return False
    try:
        result = subprocess.run(
            ["v4l2-ctl", "-d", device, "--set-ctrl", f"wide_dynamic_range={int(enabled)}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Could not switch sensor HDR: {e}")
        return False
    if result.returncode != 0:
        logger.warning(
            f"Could not switch sensor HDR to {int(enabled)}: {result.stderr.strip() or 'v4l2-ctl failed'}"
        )
        return False
    return True


def hdr_main_size(configured: Tuple[int, int]) -> Tuple[int, int]:
    """The capture size to request while sensor HDR is active.

    The configured size, aspect-fit inside the HDR mode's 2304x1296 --
    asking picamera2 for more than the sensor mode delivers is at best
    undefined on the vc4 ISP, which cannot upscale. Dimensions are rounded
    down to even numbers; video encoders reject odd ones.
    """
    width, height = configured
    scale = min(HDR_MODE_SIZE[0] / width, HDR_MODE_SIZE[1] / height, 1.0)
    return (int(width * scale) // 2 * 2, int(height * scale) // 2 * 2)


def upscale_to(image_path: str, size: Tuple[int, int], quality: int = 85) -> bool:
    """Resize a frame up to the configured size, atomically, in place.

    Runs before the overlay is drawn so its text stays crisp, and before
    anything downstream sees the file, so every frame on disk is the same
    size -- the daily video pipeline concatenates without a scale filter
    and mixed sizes corrupt the encode. A frame already at (or above) the
    target passes through untouched, which is what night frames do when
    HDR is off and capture runs at the configured size natively.
    """
    try:
        from PIL import Image
    except ImportError as e:  # pragma: no cover - Pillow is a core dependency
        logger.warning(f"Upscaling needs Pillow: {e}")
        return False

    target = (int(size[0]), int(size[1]))
    tmp_path: Optional[str] = None
    try:
        with Image.open(image_path) as image:
            if image.size[0] >= target[0] and image.size[1] >= target[1]:
                return True
            resized = image.resize(target, Image.LANCZOS)

        out_dir = os.path.dirname(os.path.abspath(image_path))
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".upscale-", suffix=".jpg", dir=out_dir)
        # mkstemp's 0600 would ride os.replace onto a frame the webserver
        # answers 403 for.
        os.fchmod(tmp_fd, 0o644)
        with os.fdopen(tmp_fd, "wb") as f:
            resized.save(f, "JPEG", quality=int(quality))
        os.replace(tmp_path, image_path)
        tmp_path = None
        logger.debug(f"Upscaled {image_path} to {target[0]}x{target[1]}")
        return True
    except Exception as e:
        logger.warning(f"Upscale failed for {image_path}: {e}")
        return False
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
