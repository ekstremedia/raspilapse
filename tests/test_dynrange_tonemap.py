"""Tone mapping: the fade math runs everywhere, pixel work behind importorskip.

The fade math and the failure paths run everywhere; the tests that need real
CLAHE output are skipped where OpenCV is not installed (CI, lean installs).
"""

import sys

import pytest
from PIL import Image

from raspilapse.dynrange.tonemap import (
    _FADE_CEILING,
    _FADE_FLOOR,
    effective_strength,
    tone_map_file,
)


def write_jpeg(path, color, size=(64, 48)):
    Image.new("RGB", size, color).save(str(path), quality=90)
    return path


class TestEffectiveStrength:
    def test_zero_strength_stays_zero(self):
        assert effective_strength(0.0, 200.0) == 0.0

    def test_dark_frame_fades_to_zero(self):
        assert effective_strength(0.5, _FADE_FLOOR - 1) == 0.0

    def test_bright_frame_gets_full_strength(self):
        assert effective_strength(0.5, _FADE_CEILING + 1) == 0.5

    def test_midpoint_gets_half(self):
        midpoint = (_FADE_FLOOR + _FADE_CEILING) / 2
        assert effective_strength(0.8, midpoint) == pytest.approx(0.4)

    def test_fade_is_monotonic(self):
        """No flicker: strength must never decrease as a scene brightens."""
        values = [effective_strength(0.5, lum) for lum in range(20, 70)]
        assert values == sorted(values)

    def test_fade_is_continuous_at_the_edges(self):
        """The old design skipped dark frames outright; a scene hovering at
        the threshold would toggle the whole effect on and off across
        consecutive timelapse frames."""
        assert effective_strength(1.0, _FADE_FLOOR + 0.1) < 0.02
        assert effective_strength(1.0, _FADE_CEILING - 0.1) > 0.98


class TestNoOpPaths:
    def test_zero_strength_leaves_the_file_alone(self, tmp_path):
        """Strength 0 must not even decode the image -- byte-identical file."""
        image = write_jpeg(tmp_path / "frame.jpg", (128, 128, 128))
        before = image.read_bytes()
        assert tone_map_file(str(image), 0.0) is True
        assert image.read_bytes() == before

    def test_missing_cv2_returns_false(self, tmp_path, monkeypatch):
        """A None entry in sys.modules makes `import cv2` raise ImportError,
        forcing the degradation path even on machines that have OpenCV."""
        image = write_jpeg(tmp_path / "frame.jpg", (128, 128, 128))
        before = image.read_bytes()
        monkeypatch.setitem(sys.modules, "cv2", None)
        assert tone_map_file(str(image), 0.5) is False
        assert image.read_bytes() == before


class TestPixelWork:
    """Real CLAHE output; needs OpenCV."""

    @pytest.fixture(autouse=True)
    def _cv2(self):
        pytest.importorskip("cv2")

    def test_output_is_a_valid_jpeg_of_the_same_size(self, tmp_path):
        image = write_jpeg(tmp_path / "frame.jpg", (180, 160, 140), size=(320, 240))
        assert tone_map_file(str(image), 0.5) is True
        with Image.open(str(image)) as reloaded:
            assert reloaded.size == (320, 240)
            assert reloaded.format == "JPEG"

    def test_bright_frame_is_actually_changed(self, tmp_path):
        # A gradient, not a flat fill: CLAHE on a constant image is identity.
        gradient = Image.new("L", (256, 64))
        gradient.putdata([x % 256 for y in range(64) for x in range(256)])
        image = tmp_path / "frame.jpg"
        gradient.convert("RGB").save(str(image), quality=90)
        before = image.read_bytes()
        assert tone_map_file(str(image), 1.0) is True
        assert image.read_bytes() != before

    def test_night_frame_is_left_untouched(self, tmp_path):
        """Below the fade floor the file must not be re-encoded at all --
        a byte-identical no-op, not a quality-losing rewrite."""
        image = write_jpeg(tmp_path / "night.jpg", (8, 8, 12))
        before = image.read_bytes()
        assert tone_map_file(str(image), 1.0) is True
        assert image.read_bytes() == before

    def test_unreadable_file_returns_false(self, tmp_path):
        garbage = tmp_path / "corrupt.jpg"
        garbage.write_bytes(b"not a jpeg at all")
        assert tone_map_file(str(garbage), 0.5) is False
        assert garbage.read_bytes() == b"not a jpeg at all"

    def test_missing_file_returns_false(self, tmp_path):
        assert tone_map_file(str(tmp_path / "absent.jpg"), 0.5) is False

    def test_no_temp_files_left_behind(self, tmp_path):
        image = write_jpeg(tmp_path / "frame.jpg", (180, 160, 140))
        tone_map_file(str(image), 0.5)
        garbage = tmp_path / "corrupt.jpg"
        garbage.write_bytes(b"junk")
        tone_map_file(str(garbage), 0.5)
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".tonemap-")]
        assert leftovers == []

    def test_result_is_world_readable(self, tmp_path):
        """mkstemp creates 0600; os.replace carries it onto the frame, which
        a webserver answers 403 for. The overlay's save learned this the
        hard way -- the tone map must not reintroduce it."""
        image = write_jpeg(tmp_path / "frame.jpg", (180, 160, 140))
        assert tone_map_file(str(image), 0.5) is True
        assert image.stat().st_mode & 0o644 == 0o644
