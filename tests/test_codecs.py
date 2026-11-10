"""Frame formats: selection, tuning, EXIF, and that every stage keeps both.

The JPEG paths run everywhere. The JPEG XL paths need pillow-jxl-plugin and
skip where it is absent (lean installs, CI's Python 3.9 job).
"""

import sys
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image, ImageDraw
from PIL.JpegImagePlugin import get_sampling

from raspilapse import codecs
from raspilapse.codecs import CODECS, FRAME_SUFFIXES, JpegCodec, JxlCodec
from raspilapse.codecs import exif as frame_exif

METADATA = {"ExposureTime": 20_000_000, "AnalogueGain": 6.0, "DigitalGain": 1.0}
STAMP = datetime(2026, 10, 25, 2, 30, 0, tzinfo=timezone(timedelta(hours=1)))


@pytest.fixture
def jxl():
    pytest.importorskip("pillow_jxl")


@pytest.fixture
def no_jxl_plugin(monkeypatch):
    monkeypatch.setitem(sys.modules, "pillow_jxl", None)


def frame_exif_bytes():
    return frame_exif.build(METADATA, STAMP, "imx708")


def gradient(size=(96, 64)):
    """Not a flat fill: CLAHE and resizing must have something to change."""
    image = Image.new("RGB", size)
    image.putdata(
        [((x * 3) % 256, (y * 4) % 256, 128) for y in range(size[1]) for x in range(size[0])]
    )
    return image


def read_back(path):
    with codecs.open_frame(path) as image:
        exif = image.getexif()
        return image.format, image.size, exif, exif.get_ifd(0x8769)


class TestRegistry:
    def test_every_format_has_a_distinct_name_and_extension(self):
        assert list(CODECS) == ["jpeg", "jxl"]
        assert FRAME_SUFFIXES == (".jpg", ".jxl")

    def test_unknown_format(self):
        with pytest.raises(ValueError, match="video.keogram.format is 'png'; supported: jpeg, jxl"):
            codecs.from_config({"format": "png"}, "video.keogram")

    def test_selected_block_tunes_the_codec(self):
        codec = codecs.from_config({"format": "jpeg", "jpeg": {"quality": 70}}, "output")
        assert codec.options == {**JpegCodec.DEFAULTS, "quality": 70}

    def test_unselected_blocks_are_validated_too(self):
        with pytest.raises(ValueError, match="output.jxl.effort is 12"):
            codecs.from_config({"format": "jpeg", "jxl": {"effort": 12}}, "output")

    @pytest.mark.parametrize(
        ("path", "name"), [("a.jpg", "jpeg"), ("a.JPEG", "jpeg"), ("a.jxl", "jxl")]
    )
    def test_for_path_picks_by_extension(self, path, name):
        assert codecs.for_path(path, {}).name == name

    def test_for_path_tunes_from_the_section(self):
        codec = codecs.for_path("a.jxl", {"format": "jpeg", "jxl": {"effort": 2}})
        assert codec.options["effort"] == 2

    def test_for_path_refuses_an_unknown_extension(self):
        with pytest.raises(ValueError, match="'.png' is not a supported image extension"):
            codecs.for_path("a.png", {})

    def test_output_codec_requires_the_plugin(self, no_jxl_plugin):
        with pytest.raises(RuntimeError, match="jxl images need pillow-jxl-plugin"):
            codecs.output_codec({"format": "jxl", "filename_pattern": "{name}"})

    @pytest.mark.parametrize("pattern", ["{name}_%Y.jpg", "{name}.jxl", "x.JPEG"])
    def test_output_codec_refuses_an_extension_in_the_pattern(self, pattern):
        with pytest.raises(ValueError, match="remove it, output.format sets the extension"):
            codecs.output_codec({"format": "jpeg", "filename_pattern": pattern})

    @pytest.mark.parametrize("pattern", ["{name}_%Y_%m_%d_%H_%M_%S", "{name}_v1.2_%S", "cam"])
    def test_output_codec_accepts_patterns_without_one(self, pattern):
        codecs.output_codec({"format": "jpeg", "filename_pattern": pattern})

    def test_output_codec_names_the_replacement_for_quality(self):
        with pytest.raises(ValueError, match="set output.jxl.quality instead"):
            codecs.output_codec({"format": "jxl", "quality": 85})


class TestValidation:
    @pytest.mark.parametrize(
        ("cls", "options", "message"),
        [
            (JpegCodec, {"quality": 0}, "output.jpeg.quality is 0; expected a whole number"),
            (JpegCodec, {"quality": 85.5}, "output.jpeg.quality is 85.5"),
            (JpegCodec, {"quality": True}, "output.jpeg.quality is True"),
            (JpegCodec, {"subsampling": "4:1:1"}, "expected one of 4:4:4, 4:2:2, 4:2:0"),
            (JpegCodec, {"progressive": "yes"}, "output.jpeg.progressive is 'yes'; expected true"),
            (JpegCodec, {"qualtiy": 80}, "output.jpeg has unknown setting 'qualtiy'"),
            (JxlCodec, {"effort": 0}, "output.jxl.effort is 0"),
            (JxlCodec, {"decoding_speed": 5}, "output.jxl.decoding_speed is 5"),
            (JxlCodec, {"lossless": 1}, "output.jxl.lossless is 1; expected true or false"),
        ],
    )
    def test_bad_values_name_the_key(self, cls, options, message):
        with pytest.raises(ValueError, match=message):
            cls(options, f"output.{cls.name}")

    def test_describe_is_the_effective_tuning(self):
        assert JxlCodec({"effort": 3}).describe() == {
            "format": "jxl",
            "quality": 90,
            "effort": 3,
            "lossless": False,
            "decoding_speed": 0,
        }


class TestJpegTuning:
    def test_quality_reaches_the_encoder(self):
        small = JpegCodec({"quality": 20}).encode(gradient())
        large = JpegCodec({"quality": 95}).encode(gradient())
        assert len(small) < len(large)

    def test_subsampling_reaches_the_encoder(self, tmp_path):
        for setting, sampling in (("4:4:4", 0), ("4:2:0", 2)):
            path = tmp_path / f"s{sampling}.jpg"
            JpegCodec({"subsampling": setting}).save(gradient(), str(path))
            with Image.open(path) as image:
                assert get_sampling(image) == sampling

    def test_progressive_reaches_the_encoder(self, tmp_path):
        path = tmp_path / "p.jpg"
        JpegCodec({"progressive": True}).save(gradient(), str(path))
        with Image.open(path) as image:
            assert image.info.get("progressive") == 1

    def test_rgbx_and_rgba_are_stored_as_rgb(self, tmp_path):
        for mode in ("RGBX", "RGBA"):
            path = tmp_path / f"{mode}.jpg"
            JpegCodec().save(Image.new(mode, (8, 8)), str(path))
            with Image.open(path) as image:
                assert image.mode == "RGB"


class TestJxlTuning:
    def test_writes_jpeg_xl(self, jxl, tmp_path):
        path = tmp_path / "f.jxl"
        JxlCodec().save(gradient(), str(path))
        assert read_back(path)[:2] == ("JXL", (96, 64))

    def test_quality_reaches_the_encoder(self, jxl):
        small = JxlCodec({"quality": 30, "effort": 3}).encode(gradient())
        large = JxlCodec({"quality": 98, "effort": 3}).encode(gradient())
        assert len(small) < len(large)

    def test_lossless_is_lossless(self, jxl, tmp_path):
        path = tmp_path / "l.jxl"
        source = gradient()
        JxlCodec({"lossless": True, "effort": 1}).save(source, str(path))
        with codecs.open_frame(path) as image:
            assert list(image.convert("RGB").getdata()) == list(source.getdata())

    def test_drawing_on_an_opened_jpeg_survives(self, jxl, tmp_path):
        """The plugin's default would transcode the source JPEG's original
        bytes and drop what was drawn since -- the overlay."""
        source = tmp_path / "src.jpg"
        Image.new("RGB", (64, 64), (200, 0, 0)).save(source, quality=95)
        target = tmp_path / "out.jxl"
        with Image.open(source) as opened:
            ImageDraw.Draw(opened).rectangle((0, 0, 31, 63), fill=(0, 0, 200))
            JxlCodec().save(opened, str(target))
        with codecs.open_frame(target) as image:
            r, _, b = image.convert("RGB").getpixel((8, 32))
        assert b > 150 and r < 60

    def test_opening_needs_the_plugin(self, tmp_path, no_jxl_plugin):
        with pytest.raises(RuntimeError, match="pillow-jxl-plugin"):
            codecs.open_frame(tmp_path / "f.jxl")


class TestExif:
    def test_build_carries_the_camera_values(self):
        exif = Image.Exif()
        exif.load(frame_exif_bytes())
        camera = exif.get_ifd(0x8769)
        assert exif[0x010F] == "Raspberry Pi"
        assert exif[0x0110] == "imx708"
        assert exif[0x0131].startswith("Raspilapse ")
        assert exif[0x0132] == "2026:10:25 02:30:00"
        assert camera[0x9003] == "2026:10:25 02:30:00"
        assert camera[0x9011] == "+01:00"
        assert float(camera[0x829A]) == 20.0
        assert camera[0x8827] == 600

    def test_missing_values_are_left_out(self):
        exif = Image.Exif()
        exif.load(frame_exif.build({}, datetime(2026, 1, 1), None))
        camera = exif.get_ifd(0x8769)
        assert 0x0110 not in exif
        assert 0x829A not in camera and 0x8827 not in camera
        assert 0x9011 not in camera, "a naive timestamp has no offset to record"

    def test_normalize(self):
        tiff = b"II*\x00rest"
        assert frame_exif.normalize(tiff) == b"Exif\x00\x00" + tiff
        assert frame_exif.normalize(b"Exif\x00\x00" + tiff) == b"Exif\x00\x00" + tiff

    @pytest.mark.parametrize("cls", [JpegCodec, JxlCodec])
    def test_round_trip_through_a_rewrite(self, cls, tmp_path):
        """What the overlay and every stage do: open, change, save with the
        EXIF read back from the file."""
        if cls is JxlCodec:
            pytest.importorskip("pillow_jxl")
        codec = cls()
        first = tmp_path / f"a{codec.suffix}"
        codec.save(gradient(), str(first), frame_exif_bytes())
        second = tmp_path / f"b{codec.suffix}"
        with codecs.open_frame(first) as image:
            codec.save(image, str(second), frame_exif.read(image))
        _, _, exif, camera = read_back(second)
        assert exif[0x0110] == "imx708"
        assert camera[0x8827] == 600


class TestStagesKeepFormatAndExif:
    """Every stage that rewrites a frame keeps its format and its EXIF."""

    @pytest.fixture(params=["jpeg", "jxl"])
    def frame(self, request, tmp_path):
        if request.param == "jxl":
            pytest.importorskip("pillow_jxl")
        codec = CODECS[request.param]({"effort": 1} if request.param == "jxl" else None)
        path = tmp_path / f"frame{codec.suffix}"
        codec.save(gradient(), str(path), frame_exif_bytes())
        return codec, path

    def assert_kept(self, codec, path, size=(96, 64)):
        fmt, got_size, exif, camera = read_back(path)
        assert fmt == codec.pil_format
        assert got_size == size
        assert exif[0x0110] == "imx708"
        assert camera[0x8827] == 600

    def test_overlay(self, frame):
        from raspilapse.overlay.render import ImageOverlay

        codec, path = frame
        overlay = ImageOverlay(
            {"overlay": {"enabled": True, "position": "top-bar"}, "output": {"jxl": {"effort": 1}}}
        )
        before = path.read_bytes()
        assert overlay.apply_overlay(str(path), dict(METADATA), mode="night") == str(path)
        assert path.read_bytes() != before
        self.assert_kept(codec, path)

    def test_tone_map(self, frame):
        pytest.importorskip("cv2")
        from raspilapse.dynrange.tonemap import tone_map_file

        codec, path = frame
        before = path.read_bytes()
        assert tone_map_file(str(path), 1.0, codec) is True
        assert path.read_bytes() != before
        self.assert_kept(codec, path)

    def test_upscale(self, frame):
        from raspilapse.dynrange.sensor_hdr import upscale_to

        codec, path = frame
        assert upscale_to(str(path), (192, 128), codec) is True
        self.assert_kept(codec, path, size=(192, 128))

    def test_post_process_chain_picks_the_codec_from_the_path(self, frame, monkeypatch):
        import raspilapse.dynrange as dynrange_module
        import raspilapse.dynrange.sensor_hdr as sensor_hdr_module
        from raspilapse.dynrange import DynamicRange

        codec, path = frame
        monkeypatch.setattr(sensor_hdr_module, "find_wdr_subdev", lambda: "/dev/v4l-subdev0")
        monkeypatch.setattr(dynrange_module.importlib.util, "find_spec", lambda name: object())
        config = {
            "camera": {"resolution": {"width": 192, "height": 128}},
            "adaptive_timelapse": {"dynamic_range": {"method": "sensor_hdr"}},
            "output": {"jxl": {"effort": 1}},
        }
        chain = DynamicRange.from_config(config).build_post_process(config)
        assert chain(str(path), dict(METADATA), "day") is True
        self.assert_kept(codec, path, size=(192, 128))


class TestFusedFrames:
    def test_a_fused_frame_is_written_in_the_configured_format(self, jxl, tmp_path):
        """capture_bracketed encodes the fused array itself, EXIF included."""
        from unittest.mock import MagicMock

        import numpy as np

        from raspilapse.camera.capture import ImageCapture

        config = MagicMock()
        config.get_output_directory.return_value = str(tmp_path)
        config.should_organize_by_date.return_value = False
        config.should_create_directories.return_value = True
        config.get_filename_pattern.return_value = "fused"
        config.get_project_name.return_value = "cam"
        config.should_save_metadata.return_value = False
        config.get_codec.return_value = JxlCodec({"effort": 1})
        config.config = {"output": {"format": "jxl", "jxl": {"effort": 1}}}
        capture = ImageCapture(config)
        capture._compute_brightness_from_lores = lambda request: {"mean_brightness": 100.0}

        class Request:
            def __init__(self, exposure_us):
                self.exposure_us = exposure_us

            def get_metadata(self):
                return {"ExposureTime": self.exposure_us, "AnalogueGain": 1.0}

            def make_array(self, stream):
                return np.zeros((48, 64, 3), dtype=np.uint8)

            def release(self):
                pass

        class Camera:
            camera_properties = {"Model": "imx708"}
            requests = [Request(10_000), Request(2_500)]

            def capture_request(self):
                return self.requests.pop(0)

            def set_controls(self, controls):
                pass

        capture.picam2 = Camera()
        image_path, _ = capture.capture_bracketed([10_000, 2_500], lambda frames: frames[0])
        assert image_path.endswith("fused.jxl")
        fmt, size, exif, camera = read_back(image_path)
        assert (fmt, size) == ("JXL", (64, 48))
        assert exif[0x0110] == "imx708"
        assert float(camera[0x829A]) == pytest.approx(0.01)
