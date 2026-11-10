"""Still-image formats for frames and the images made from them.

``output.format`` picks the frame format and ``video.keogram.format`` the
keogram's; each format is tuned in a block named after it (``output.jpeg``,
``output.jxl``, ...). A file's extension says which format it holds, so
every stage that rewrites a frame asks ``for_path`` and keeps the format the
capture chose.

To add a format: subclass FrameCodec in a module of its own, list it in
CODECS, document its block in docs/CONFIG-REFERENCE.yml and add its
extension to scripts/cleanup_old_images.sh.
"""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Dict, Tuple, Type, Union

from PIL import Image

from raspilapse.codecs.base import FrameCodec
from raspilapse.codecs.jpeg import JpegCodec
from raspilapse.codecs.jxl import JxlCodec

CODECS: Dict[str, Type[FrameCodec]] = {codec.name: codec for codec in (JpegCodec, JxlCodec)}

# The extension each format gives new files. The finders (video, keogram,
# status, retention) look for exactly these.
FRAME_SUFFIXES: Tuple[str, ...] = tuple(codec.suffixes[0] for codec in CODECS.values())

_BY_SUFFIX: Dict[str, Type[FrameCodec]] = {
    suffix: codec for codec in CODECS.values() for suffix in codec.suffixes
}

# A filename pattern ending in an extension of its own: ".jpg", ".jxl", ...
_EXTENSION_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9]{1,4}$")

__all__ = [
    "CODECS",
    "FRAME_SUFFIXES",
    "FrameCodec",
    "JpegCodec",
    "JxlCodec",
    "codec_class",
    "for_path",
    "from_config",
    "open_frame",
    "output_codec",
]


def codec_class(name: str, where: str = "format") -> Type[FrameCodec]:
    """The codec class for a ``format:`` value; ValueError for an unknown one."""
    try:
        return CODECS[name]
    except (KeyError, TypeError):
        raise ValueError(f"{where} is {name!r}; supported: {', '.join(CODECS)}") from None


def from_config(section: Mapping, where: str) -> FrameCodec:
    """The codec a config section selects with ``format:``, tuned by its block.

    Every format block present in the section is validated, not just the
    selected one, so a typo in a block that is switched off today is found
    today rather than on the day it is switched on.

    Args:
        section: e.g. config["output"] or config["video"]["keogram"]
        where: That section's config path, for messages

    Raises:
        ValueError: Unknown format, unknown tuning key or out-of-range value
    """
    cls = codec_class(section["format"], f"{where}.format")
    for name, other in CODECS.items():
        if name != cls.name and section.get(name) is not None:
            other(section[name], f"{where}.{name}")
    return cls(section.get(cls.name), f"{where}.{cls.name}")


def output_codec(output: Mapping) -> FrameCodec:
    """Validate the output section and return the frame codec, ready to write.

    Raises:
        ValueError: A key this version no longer reads, or a bad format block
        RuntimeError: The selected format's Pillow plugin is not installed
    """
    if "quality" in output:
        raise ValueError(
            f"output.quality is no longer read; set output.{output.get('format', 'jpeg')}"
            ".quality instead"
        )
    match = _EXTENSION_RE.search(str(output.get("filename_pattern", "")))
    if match:
        raise ValueError(
            f"output.filename_pattern ends in '{match.group()}'; remove it, "
            "output.format sets the extension"
        )
    codec = from_config(output, "output")
    codec.require()
    return codec


def for_path(path: Union[str, Path], section: Mapping, where: str = "output") -> FrameCodec:
    """The codec for a file, by its extension, tuned from ``section``.

    Args:
        path: The file to be written
        section: The config section holding the format blocks (e.g. output)
        where: That section's config path, for messages

    Raises:
        ValueError: The extension belongs to no supported format
    """
    suffix = Path(str(path)).suffix.lower()
    cls = _BY_SUFFIX.get(suffix)
    if cls is None:
        raise ValueError(
            f"'{suffix}' is not a supported image extension; supported: " f"{', '.join(_BY_SUFFIX)}"
        )
    return cls((section or {}).get(cls.name), f"{where}.{cls.name}")


def open_frame(path: Union[str, Path]):
    """``Image.open`` that first loads the plugin the file's format needs.

    Raises:
        RuntimeError: The format's plugin is not installed
    """
    cls = _BY_SUFFIX.get(Path(str(path)).suffix.lower())
    if cls is not None:
        cls.require()
    return Image.open(str(path))
