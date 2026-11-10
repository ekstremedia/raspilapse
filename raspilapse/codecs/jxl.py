"""JPEG XL, through the pillow-jxl-plugin package (libjxl underneath)."""

from typing import Any, Dict

from raspilapse.codecs.base import FrameCodec

INSTALL_HINT = "pip3 install --break-system-packages pillow-jxl-plugin"


class JxlCodec(FrameCodec):
    """JPEG XL. Needs pillow-jxl-plugin, which registers the format with Pillow."""

    name = "jxl"
    pil_format = "JXL"
    suffixes = (".jxl",)
    modes = ("RGB", "RGBA", "L", "LA")
    DEFAULTS = {
        # 1-100, on libjxl's own scale: 90 is about "visually lossless".
        # Not comparable to a JPEG quality of the same number.
        "quality": 90,
        # 1 fastest and largest .. 9 slowest and smallest. On a Pi this is
        # the setting that decides whether a 4K frame fits its interval.
        "effort": 7,
        # Mathematically lossless; quality is ignored. Files are several
        # times larger than at quality 90.
        "lossless": False,
        # 0 smallest file .. 4 fastest to decode. Every frame is decoded
        # again by the video and keogram jobs.
        "decoding_speed": 0,
    }

    @classmethod
    def require(cls) -> None:
        try:
            import pillow_jxl  # noqa: F401  (importing registers JXL with Pillow)
        except ImportError as e:
            raise RuntimeError(
                f"jxl images need pillow-jxl-plugin ({e}); install it with: {INSTALL_HINT}"
            ) from e

    def _validate(self) -> None:
        self._check_int("quality", 1, 100)
        self._check_int("effort", 1, 9)
        self._check_bool("lossless")
        self._check_int("decoding_speed", 0, 4)

    def _save_params(self) -> Dict[str, Any]:
        return {
            "quality": self.options["quality"],
            "effort": self.options["effort"],
            "lossless": self.options["lossless"],
            "decoding_speed": self.options["decoding_speed"],
            # Given an image opened from a .jpg file, the plugin would
            # otherwise transcode that file's original bytes and silently
            # drop whatever was drawn on the image since -- the overlay.
            "lossless_jpeg": False,
        }
