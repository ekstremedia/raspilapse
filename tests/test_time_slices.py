"""Tests for the fused keogram/slitscan pass.

create_time_slices decodes each frame once and feeds both canvases. The risk in
that is cross-contamination: the two outputs have *different* resize rules, and
sharing one decoded frame between them could quietly make one of them adopt the
other's. The resize branches had no test coverage at all before this file --
they never fire while the camera resolution is fixed, which is exactly why a
refactor could break them unnoticed.
"""

from pathlib import Path

import pytest
from PIL import Image

from raspilapse.video.keogram import create_keogram, create_slitscan, create_time_slices


def frame(path: Path, width: int, height: int, seed: int):
    """An image whose every column is a different, position-dependent colour."""
    img = Image.new("RGB", (width, height))
    px = img.load()
    for x in range(width):
        for y in range(height):
            px[x, y] = ((x * 7 + seed) % 256, (y * 3 + seed) % 256, (x + y + seed) % 256)
    img.save(path, "JPEG", quality=95)
    return path


@pytest.fixture
def frames(tmp_path):
    return [frame(tmp_path / f"f{i:03d}.jpg", 40, 20, seed=i * 11) for i in range(12)]


class TestFusionMatchesSeparateRuns:
    """Doing both together must equal doing each alone."""

    def test_both_outputs_are_unchanged_by_fusing(self, tmp_path, frames):
        sep_k, sep_s = tmp_path / "sep_k.jpg", tmp_path / "sep_s.jpg"
        create_keogram(frames, sep_k, crop_top_percent=0.0)
        create_slitscan(frames, sep_s, crop_top_percent=0.0)

        fus_k, fus_s = tmp_path / "fus_k.jpg", tmp_path / "fus_s.jpg"
        create_time_slices(frames, keogram_path=fus_k, slitscan_path=fus_s, crop_top_percent=0.0)

        assert sep_k.read_bytes() == fus_k.read_bytes()
        assert sep_s.read_bytes() == fus_s.read_bytes()

    def test_only_the_requested_output_is_written(self, tmp_path, frames):
        k, s = tmp_path / "k.jpg", tmp_path / "s.jpg"
        result = create_time_slices(frames, keogram_path=k, crop_top_percent=0.0)
        assert k.exists() and not s.exists()
        assert result == {"keogram": True}

    def test_asking_for_nothing_does_nothing(self, tmp_path, frames):
        assert create_time_slices(frames) == {}

    def test_no_images_fails_every_requested_output(self, tmp_path):
        result = create_time_slices(
            [], keogram_path=tmp_path / "k.jpg", slitscan_path=tmp_path / "s.jpg"
        )
        assert result == {"keogram": False, "slitscan": False}


class TestResizeRules:
    """The keogram matches height and keeps aspect; the slitscan forces both.

    Only one of those is observable in the output, and it is worth being exact
    about which. A keogram column is the *centre* of the frame, and the centre
    of an image is the centre of that image scaled -- so applying the slitscan's
    rule to the keogram changes its pixels by resampling noise alone (measured:
    44 vs 46 on a synthetic ramp). There is no output assertion that can pin the
    keogram branch down, so this file does not pretend to have one; the branch
    is kept because it is what the split implementation did, not because a test
    here would catch its loss.

    The slitscan's rule is a different matter: its strip is taken at a position,
    so failing to normalise the width takes it from the wrong part of the scene.
    That is asserted concretely below.
    """

    def test_the_slitscan_normalises_width_before_taking_its_strip(self, tmp_path):
        # Middle frame is wider, same height -- so the keogram's rule ("has the
        # height changed?") would leave it alone. If the slitscan inherited
        # that, its strip would come from raw column 13 of a 60-wide frame
        # instead of column 13 of the same frame scaled to 40.
        paths = [
            frame(tmp_path / "a.jpg", 40, 20, seed=0),
            frame(tmp_path / "b.jpg", 60, 20, seed=90),
            frame(tmp_path / "c.jpg", 40, 20, seed=180),
        ]
        out = tmp_path / "s.jpg"
        create_time_slices(paths, slitscan_path=out, crop_top_percent=0.0)

        # Frame 1 owns columns 13..25 (40/3 = 13.33 per frame).
        with Image.open(paths[1]) as wide:
            expected = wide.resize((40, 20), Image.Resampling.LANCZOS).getpixel((13, 5))[0]
            wrong = wide.getpixel((13, 5))[0]

        with Image.open(out) as got:
            actual = got.getpixel((13, 5))[0]

        # JPEG is lossy, so compare by which candidate it is nearer to; the two
        # differ by ~47 levels, far outside quality-95 noise.
        assert abs(actual - expected) < abs(actual - wrong), (
            f"slitscan column 13 is {actual}, nearer the un-normalised {wrong} "
            f"than the expected {expected}"
        )

    def test_a_differently_sized_frame_does_not_abort_either_output(self, tmp_path):
        paths = [
            frame(tmp_path / "a.jpg", 40, 20, seed=0),
            frame(tmp_path / "b.jpg", 80, 40, seed=90),
            frame(tmp_path / "c.jpg", 40, 20, seed=180),
        ]
        result = create_time_slices(
            paths,
            keogram_path=tmp_path / "k.jpg",
            slitscan_path=tmp_path / "s.jpg",
            crop_top_percent=0.0,
        )
        assert result == {"keogram": True, "slitscan": True}

    def test_the_output_keeps_the_first_frames_geometry(self, tmp_path, frames):
        k, s = tmp_path / "k.jpg", tmp_path / "s.jpg"
        create_time_slices(frames, keogram_path=k, slitscan_path=s, crop_top_percent=0.0)
        with Image.open(k) as img:
            assert img.size == (len(frames), 20)  # one column per frame
        with Image.open(s) as img:
            assert img.size == (40, 20)  # full source width


class TestUnreadableFrames:
    def test_a_bad_frame_leaves_a_gap_instead_of_shifting_the_rest(self, tmp_path):
        """The slitscan advances its x position on failure too. Without that,
        every frame after a bad one slides one strip left and the image no
        longer lines up with time."""
        paths = [frame(tmp_path / f"f{i}.jpg", 40, 20, seed=i * 20) for i in range(8)]
        good = tmp_path / "good_s.jpg"
        create_slitscan(paths, good, crop_top_percent=0.0)

        # Corrupt one frame in the middle and rebuild.
        paths[3].write_bytes(b"not a jpeg")
        broken = tmp_path / "broken_s.jpg"
        create_slitscan(paths, broken, crop_top_percent=0.0)

        with Image.open(good) as a, Image.open(broken) as b:
            assert a.size == b.size
            # The frames after the bad one must still occupy their own columns.
            # Sample the last frame's strip: unchanged means nothing shifted.
            assert a.getpixel((39, 10)) == b.getpixel((39, 10))

    def test_a_bad_frame_does_not_abort_either_output(self, tmp_path):
        paths = [frame(tmp_path / f"f{i}.jpg", 40, 20, seed=i * 20) for i in range(6)]
        paths[2].write_bytes(b"not a jpeg")
        result = create_time_slices(
            paths,
            keogram_path=tmp_path / "k.jpg",
            slitscan_path=tmp_path / "s.jpg",
            crop_top_percent=0.0,
        )
        assert result == {"keogram": True, "slitscan": True}

    def test_an_unreadable_first_frame_fails_cleanly(self, tmp_path):
        bad = tmp_path / "bad.jpg"
        bad.write_bytes(b"not a jpeg")
        result = create_time_slices(
            [bad], keogram_path=tmp_path / "k.jpg", slitscan_path=tmp_path / "s.jpg"
        )
        assert result == {"keogram": False, "slitscan": False}


class TestCropping:
    def test_the_overlay_bar_is_cropped_off_the_top(self, tmp_path, frames):
        k = tmp_path / "k.jpg"
        create_time_slices(frames, keogram_path=k, crop_top_percent=10.0)
        with Image.open(k) as img:
            assert img.height == 20 - int(20 * 10 / 100)
