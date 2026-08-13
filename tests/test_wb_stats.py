"""Tests for the white-balance statistics read off the lores stream.

The buffer under test is the packed planar I420 layout picamera2's
make_array("lores") returns: full-height Y rows, then quarter-height U and V,
all at the configured width. Buffers here are painted from RGB through the
same full-range BT.601 the decode uses, so the expectations can be written in
RGB -- the space the assertion actually cares about -- and the test exercises
the round trip rather than restating the implementation's constants.
"""

import os
import tempfile

import numpy as np
import pytest
import yaml

from raspilapse.camera.capture import CameraConfig, ImageCapture

W, H = 320, 240


@pytest.fixture
def capture():
    config_data = {
        "camera": {
            "resolution": {"width": 1280, "height": 720},
            "transforms": {"horizontal_flip": False, "vertical_flip": False},
            "controls": {},
        },
        "output": {
            "directory": "test_photos",
            "filename_pattern": "{name}_{counter}.jpg",
            "project_name": "test_project",
            "quality": 85,
            "organize_by_date": False,
            "date_format": "%Y-%m-%d",
        },
        "system": {"create_directories": False, "save_metadata": False},
        "overlay": {"enabled": False},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(config_data, f)
        path = f.name
    try:
        yield ImageCapture(CameraConfig(path))
    finally:
        os.unlink(path)


def to_yuv(rgb):
    """Full-range BT.601, the inverse of the decode under test."""
    r, g, b = (float(c) for c in rgb)
    y = 0.299 * r + 0.587 * g + 0.114 * b
    return y, (b - y) / 1.772 + 128.0, (r - y) / 1.402 + 128.0


def srgb_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def i420(regions, w=W, h=H):
    """A planar I420 buffer painted from RGB fills.

    regions: list of (col_start, col_end, rgb) vertical stripes.
    """
    buf = np.zeros((h * 3 // 2, w), np.uint8)
    quarter = h // 4
    for c0, c1, rgb in regions:
        y, u, v = to_yuv(rgb)
        buf[:h, c0:c1] = int(round(y))
        # U and V planes are (h/2, w/2), folded into quarter-height rows of
        # full width -- exactly the packed layout the decode unfolds.
        u_plane = buf[h : h + quarter].reshape(h // 2, w // 2)
        v_plane = buf[h + quarter : h + 2 * quarter].reshape(h // 2, w // 2)
        u_plane[:, c0 // 2 : c1 // 2] = int(round(u))
        v_plane[:, c0 // 2 : c1 // 2] = int(round(v))
    return buf


def stats(capture, buf, w=W, h=H):
    gray = buf[:h, :w].astype(np.float32)
    return capture._wb_stats_from_lores(buf, gray, w, h)


class TestNeutralSelection:
    def test_a_grey_frame_reads_unity(self, capture):
        result = stats(capture, i420([(0, W, (128, 128, 128))]))
        assert result["wb_neutral_fraction"] == pytest.approx(1.0)
        assert result["wb_gr"] == pytest.approx(1.0, abs=0.01)
        assert result["wb_gb"] == pytest.approx(1.0, abs=0.01)

    def test_a_cast_comes_back_as_the_rgb_ratios(self, capture):
        # The khaki cast this loop exists for: red high, blue low.
        rgb = (140, 128, 118)
        result = stats(capture, i420([(0, W, rgb)]))
        expected_gr = srgb_linear(rgb[1]) / srgb_linear(rgb[0])
        expected_gb = srgb_linear(rgb[1]) / srgb_linear(rgb[2])
        assert result["wb_gr"] == pytest.approx(expected_gr, rel=0.02)
        assert result["wb_gb"] == pytest.approx(expected_gb, rel=0.02)

    def test_saturated_colour_is_not_a_grey_card(self, capture):
        result = stats(capture, i420([(0, W, (200, 80, 60))]))
        assert result["wb_neutral_fraction"] == pytest.approx(0.0)
        assert "wb_gr" not in result

    def test_the_mask_ignores_the_colourful_half(self, capture):
        cast = (140, 128, 118)
        mixed = stats(capture, i420([(0, W // 2, cast), (W // 2, W, (80, 200, 60))]))
        pure = stats(capture, i420([(0, W, cast)]))
        assert mixed["wb_neutral_fraction"] == pytest.approx(0.5, abs=0.02)
        assert mixed["wb_gr"] == pytest.approx(pure["wb_gr"], abs=0.005)
        assert mixed["wb_gb"] == pytest.approx(pure["wb_gb"], abs=0.005)

    def test_blown_highlights_carry_no_cast_information(self, capture):
        result = stats(capture, i420([(0, W, (250, 250, 250))]))
        assert result["wb_neutral_fraction"] == pytest.approx(0.0)
        assert "wb_gr" not in result

    def test_pale_sky_dimmer_than_cloud_is_not_a_grey_card(self, capture):
        # Clear sky and sky-lit water pass the chroma gate on sunny days but
        # render mid-luma; reading them as grey once railed the blue trim.
        # With bright cloud in frame, the cloud alone must set the reading.
        cloud = (200, 200, 200)
        sky = (140, 150, 170)  # inside the chroma gate, distinctly blue
        mixed = stats(capture, i420([(0, W // 2, cloud), (W // 2, W, sky)]))
        pure = stats(capture, i420([(0, W, cloud)]))
        assert mixed["wb_gr"] == pytest.approx(pure["wb_gr"], abs=0.005)
        assert mixed["wb_gb"] == pytest.approx(pure["wb_gb"], abs=0.005)

    def test_too_few_bright_neutrals_reports_only_the_fraction(self, capture):
        # Enough candidates to attempt the luma cut, but the brightest
        # quartile lands under WB_MIN_SAMPLES: the stats must hold the trim
        # (no ratios) while still reporting how much grey the gates saw.
        buf = np.zeros((H * 3 // 2, W), np.uint8)
        quarter = H // 4
        buf[H : H + quarter] = 128
        buf[H + quarter : H + 2 * quarter] = 128
        # 120 neutral candidates in the top chroma row: 90 dim, 30 bright.
        # The 75th-percentile cut lands between the two, keeping only 30.
        buf[0:2, 0:180] = 120
        buf[0:2, 180:240] = 200
        result = stats(capture, buf)
        assert result["wb_neutral_fraction"] > 0
        assert "wb_gr" not in result
        assert "wb_gb" not in result

    def test_a_uniform_cast_still_reads_through_the_luma_cut(self, capture):
        # On a flat overcast frame every candidate shares one luma, so the
        # brightest-quartile cut keeps them all and the cast still reports.
        rgb = (140, 128, 118)
        result = stats(capture, i420([(0, W, rgb)]))
        expected_gr = srgb_linear(rgb[1]) / srgb_linear(rgb[0])
        assert result["wb_neutral_fraction"] == pytest.approx(1.0)
        assert result["wb_gr"] == pytest.approx(expected_gr, rel=0.02)


class TestBufferLayout:
    def test_a_stride_padded_buffer_is_refused(self, capture):
        buf = np.zeros((H * 3 // 2, 384), np.uint8)
        assert stats(capture, buf) == {}

    def test_a_truncated_buffer_is_refused(self, capture):
        buf = i420([(0, W, (128, 128, 128))])[: H + H // 4]
        assert stats(capture, buf) == {}
