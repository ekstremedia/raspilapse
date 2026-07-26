"""Tests for the pure overlay drawing helpers.

These exist mainly for their failure paths: an overlay that is slightly
misaligned beats an overlay that is not drawn, so every helper degrades rather
than raising.
"""

import os
import sys

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.overlay_draw import (  # noqa: E402
    draw_divider,
    draw_gradient_bar,
    format_slot,
    measure_widest,
    text_height,
    text_width,
)


@pytest.fixture
def draw():
    return ImageDraw.Draw(Image.new("RGBA", (400, 100), (0, 0, 0, 0)))


class TestTextWidth:
    def test_measures_a_real_font(self, draw):
        assert text_width(draw, "hello", None) > 0

    def test_longer_string_is_wider(self, draw):
        assert text_width(draw, "wwwwwwwwww", None) > text_width(draw, "w", None)

    def test_empty_string_is_zero_width(self, draw):
        assert text_width(draw, "", None) == 0

    def test_falls_back_when_the_font_cannot_be_measured(self):
        """Some bitmap fallbacks raise from textbbox; estimate rather than crash."""

        class Unmeasurable:
            def textbbox(self, *a, **k):
                raise OSError("no metrics")

        assert text_width(Unmeasurable(), "abcde", None, font_size=20) == pytest.approx(60, abs=1)

    def test_returns_an_int(self, draw):
        assert isinstance(text_width(draw, "hello", None), int)


class TestTextHeight:
    def test_measures_a_real_font(self, draw):
        assert text_height(draw, None) > 0

    def test_is_reference_based_not_content_based(self, draw):
        """Every line must come out the same height regardless of its glyphs."""
        assert text_height(draw, None) == text_height(draw, None)

    def test_falls_back_on_failure(self):
        class Unmeasurable:
            def textbbox(self, *a, **k):
                raise OSError("no metrics")

        assert text_height(Unmeasurable(), None, fallback=17) == 17


class TestFormatSlot:
    def test_substitutes(self):
        assert format_slot("{a}-{b}", {"a": "1", "b": "2"}) == "1-2"

    def test_unknown_placeholder_returns_the_raw_template(self):
        """A typo in one config template must not abort the whole overlay."""
        assert format_slot("{nope}", {"a": "1"}, "line_1_left") == "{nope}"

    def test_malformed_template_returns_the_raw_template(self):
        assert format_slot("{", {"a": "1"}) == "{"

    def test_template_without_placeholders_is_passed_through(self):
        assert format_slot("static text", {}) == "static text"


class TestMeasureWidest:
    def test_picks_the_widest(self, draw):
        widest = measure_widest(draw, ("i", "wwwwwwww", "ww"), None)
        assert widest == text_width(draw, "wwwwwwww", None)

    def test_empty_input_is_zero(self, draw):
        assert measure_widest(draw, (), None) == 0


class TestDrawing:
    def test_divider_marks_its_column(self):
        img = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
        draw_divider(ImageDraw.Draw(img), x=20, y_top=2, y_bottom=18, color=(255, 0, 0, 255))
        assert img.getpixel((20, 10))[3] > 0
        assert img.getpixel((5, 10))[3] == 0

    def test_gradient_fades_downward(self):
        """The bar fades toward the image so it does not read as a hard band."""
        img = Image.new("RGBA", (10, 50), (0, 0, 0, 0))
        draw_gradient_bar(ImageDraw.Draw(img), 10, 50, (0, 0, 0, 200))
        assert img.getpixel((5, 0))[3] > img.getpixel((5, 49))[3]

    def test_gradient_covers_the_full_width(self):
        img = Image.new("RGBA", (30, 10), (0, 0, 0, 0))
        draw_gradient_bar(ImageDraw.Draw(img), 30, 10, (0, 0, 0, 255))
        assert img.getpixel((0, 0))[3] > 0
        assert img.getpixel((29, 0))[3] > 0
