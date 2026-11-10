"""What every frame codec offers, whatever the format behind it."""

from io import BytesIO
from typing import Any, Dict, Optional, Tuple

from raspilapse.codecs import exif as frame_exif


class FrameCodec:
    """One still-image format: its name, its extensions and its tuning.

    A subclass names the format (``name`` is both the ``format:`` value and
    the key of its tuning block), lists the extensions it writes and reads
    (the first one is what new files get), and declares its tuning keys with
    their defaults in ``DEFAULTS``. Everything else -- unknown-key checks,
    EXIF handling, saving to a file or to bytes -- lives here, so adding a
    format means writing ``_validate`` and ``_save_params`` and nothing more.
    """

    name: str = ""
    pil_format: str = ""
    suffixes: Tuple[str, ...] = ()
    # Pillow modes the format stores natively; anything else becomes RGB.
    modes: Tuple[str, ...] = ("RGB",)
    DEFAULTS: Dict[str, Any] = {}

    def __init__(self, options: Optional[Dict] = None, where: Optional[str] = None):
        """Merge ``options`` over the defaults and validate the result.

        Args:
            options: The format's tuning block from the config, or None
            where: Config path of that block, e.g. ``output.jpeg``, for messages

        Raises:
            ValueError: For an unknown key or an out-of-range value
        """
        self.where = where or self.name
        options = dict(options or {})
        for key in sorted(options):
            if key not in self.DEFAULTS:
                raise ValueError(
                    f"{self.where} has unknown setting '{key}'; known: "
                    f"{', '.join(self.DEFAULTS)}"
                )
        self.options: Dict[str, Any] = {**self.DEFAULTS, **options}
        self._validate()

    @property
    def suffix(self) -> str:
        """The extension new files of this format get."""
        return self.suffixes[0]

    @classmethod
    def require(cls) -> None:
        """Raise RuntimeError when Pillow cannot read and write this format here."""

    def describe(self) -> Dict[str, Any]:
        """The format and its effective tuning, for the metadata sidecar."""
        return {"format": self.name, **self.options}

    def save(self, image, fp, exif: Optional[bytes] = None) -> None:
        """Write a Pillow image to a path or binary file object.

        Args:
            image: The image to write
            fp: Destination path or binary file object
            exif: EXIF block to embed, as built or read by codecs.exif
        """
        self.require()
        if image.mode not in self.modes:
            image = image.convert("RGB")
        params = self._save_params()
        if exif:
            params["exif"] = frame_exif.normalize(exif)
        image.save(fp, format=self.pil_format, **params)

    def encode(self, image, exif: Optional[bytes] = None) -> bytes:
        """The bytes ``save`` would write."""
        buffer = BytesIO()
        self.save(image, buffer, exif)
        return buffer.getvalue()

    def _validate(self) -> None:
        """Check ``self.options``; raise ValueError naming the offending key."""

    def _save_params(self) -> Dict[str, Any]:
        """Keyword arguments for Pillow's ``Image.save``."""
        return {}

    # Shared checks, so every format reports a bad value in the same words.

    def _check_int(self, key: str, low: int, high: int) -> None:
        value = self.options[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(
                f"{self.where}.{key} is {value!r}; expected a whole number from {low} to {high}"
            )

    def _check_bool(self, key: str) -> None:
        value = self.options[key]
        if not isinstance(value, bool):
            raise ValueError(f"{self.where}.{key} is {value!r}; expected true or false")

    def _check_choice(self, key: str, choices: Tuple[str, ...]) -> None:
        value = self.options[key]
        if value not in choices:
            raise ValueError(
                f"{self.where}.{key} is {value!r}; expected one of {', '.join(choices)}"
            )
