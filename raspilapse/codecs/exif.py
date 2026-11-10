"""The EXIF block every frame carries, whatever its format.

picamera2 used to write a few tags itself, but only into JPEGs it saved
directly; the overlay's re-encode dropped them. Building the block here and
handing it to every save keeps the camera values in the file through the
whole post-process chain, for every format.
"""

from datetime import datetime
from typing import Dict, Optional

from PIL import Image, TiffImagePlugin

from raspilapse.__version__ import __version__

# Pillow expects EXIF bytes with this APP1 header; JPEG XL stores the TIFF
# structure without it.
_HEADER = b"Exif\x00\x00"

_MAKE = 0x010F
_MODEL = 0x0110
_SOFTWARE = 0x0131
_DATETIME = 0x0132
_EXIF_IFD = 0x8769
_EXPOSURE_TIME = 0x829A
_ISO = 0x8827
_DATETIME_ORIGINAL = 0x9003
_OFFSET_TIME_ORIGINAL = 0x9011


def build(metadata: Dict, timestamp: datetime, model: Optional[str] = None) -> bytes:
    """EXIF for a frame from its libcamera metadata.

    The same tags picamera2 wrote -- make, model, software, capture time,
    exposure time, ISO from the total gain -- plus the capture's UTC offset,
    which the DST fall-back hour otherwise makes ambiguous.

    Args:
        metadata: The request's metadata (ExposureTime, AnalogueGain, DigitalGain)
        timestamp: The capture's own timestamp, timezone-aware when possible
        model: Sensor model, e.g. "imx708"

    Returns:
        EXIF bytes, header included, ready for FrameCodec.save
    """
    exif = Image.Exif()
    exif[_MAKE] = "Raspberry Pi"
    if model:
        exif[_MODEL] = model
    exif[_SOFTWARE] = f"Raspilapse {__version__}"
    stamp = timestamp.strftime("%Y:%m:%d %H:%M:%S")
    exif[_DATETIME] = stamp

    camera: Dict[int, object] = {_DATETIME_ORIGINAL: stamp}
    offset = timestamp.strftime("%z")
    if offset:
        camera[_OFFSET_TIME_ORIGINAL] = f"{offset[:3]}:{offset[3:5]}"
    exposure_us = metadata.get("ExposureTime")
    if exposure_us:
        camera[_EXPOSURE_TIME] = TiffImagePlugin.IFDRational(int(exposure_us), 1_000_000)
    analogue = metadata.get("AnalogueGain")
    if analogue:
        total_gain = float(analogue) * float(metadata.get("DigitalGain", 1.0))
        camera[_ISO] = int(total_gain * 100)
    exif[_EXIF_IFD] = camera
    return exif.tobytes()


def read(image) -> Optional[bytes]:
    """The EXIF block of an opened image, normalized, or None when it has none."""
    raw = image.info.get("exif")
    return normalize(raw) if raw else None


def normalize(raw: bytes) -> bytes:
    """Give TIFF-only EXIF bytes (as JPEG XL stores them) the header Pillow expects."""
    if raw.startswith(_HEADER):
        return raw
    if raw[:2] in (b"II", b"MM"):
        return _HEADER + raw
    return raw
