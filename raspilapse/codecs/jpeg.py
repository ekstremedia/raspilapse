"""JPEG, through Pillow's built-in encoder."""

from typing import Any, Dict

from raspilapse.codecs.base import FrameCodec


class JpegCodec(FrameCodec):
    """Baseline or progressive JPEG via libjpeg(-turbo)."""

    name = "jpeg"
    pil_format = "JPEG"
    suffixes = (".jpg", ".jpeg")
    modes = ("RGB", "L")
    DEFAULTS = {
        # 1-100. Above ~95 the file grows fast for no visible gain.
        "quality": 85,
        # Chroma resolution. 4:4:4 keeps coloured edges (overlay text, wires
        # against sky) sharp at roughly 15-25% more bytes than 4:2:0.
        "subsampling": "4:2:0",
        # Progressive files render coarse-to-fine in a browser and are
        # usually a few percent smaller; they cost a little more to encode.
        "progressive": False,
        # An extra Huffman-table pass: a few percent smaller, slower encode.
        "optimize": False,
    }

    def _validate(self) -> None:
        self._check_int("quality", 1, 100)
        self._check_choice("subsampling", ("4:4:4", "4:2:2", "4:2:0"))
        self._check_bool("progressive")
        self._check_bool("optimize")

    def _save_params(self) -> Dict[str, Any]:
        return {
            "quality": self.options["quality"],
            "subsampling": self.options["subsampling"],
            "progressive": self.options["progressive"],
            "optimize": self.options["optimize"],
        }
