"""Turning the overlay off must turn off only the overlay.

Not everyone wants text burned into their frames, and `overlay.enabled: false`
is the supported way to say so. These tests pin what that setting is allowed to
affect: the drawing, and nothing else.

The bug they were written for: ImageOverlay.__init__ returned before assigning
self.weather when disabled, and the capture loop read capture.overlay.weather
inside an `except Exception` that logged at DEBUG. Turning off the overlay
therefore silently turned off all database logging, at a log level nobody runs
in production.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from raspilapse.overlay import build_overlay
from raspilapse.overlay.render import ImageOverlay
from raspilapse.overlay.sources.weather import WeatherData

# Every attribute a consumer outside this class reads off it. Adding a data
# source means adding it here, or the disabled path can silently lose it again.
PUBLIC_SOURCES = ("weather", "ships", "tide", "aurora", "system_monitor")


@pytest.fixture
def disabled_config():
    # weather pinned off explicitly rather than relying on WeatherData's own
    # default, so no overlay test can reach the network.
    return {"overlay": {"enabled": False}, "weather": {"enabled": False}}


@pytest.fixture
def enabled_config():
    return {
        "overlay": {"enabled": True, "font": {"family": "default"}},
        "weather": {"enabled": False},
    }


def test_disabled_overlay_still_exposes_its_sources(disabled_config):
    """A disabled overlay is still a complete object, not a half-built one."""
    overlay = ImageOverlay(disabled_config)

    assert overlay.enabled is False
    for name in PUBLIC_SOURCES:
        assert hasattr(overlay, name), (
            f"{name} is missing when the overlay is disabled; "
            "anything reaching through the overlay for it will raise"
        )


def test_disabled_overlay_sources_are_usable_not_just_present(disabled_config):
    """Present-but-None would move the failure rather than remove it."""
    overlay = ImageOverlay(disabled_config)

    weather = overlay.weather.get_weather_data()
    assert weather is None or isinstance(weather, dict)


def test_enabled_and_disabled_expose_the_same_surface(enabled_config, disabled_config):
    """The two paths must not drift apart again."""
    enabled = ImageOverlay(enabled_config)
    disabled = ImageOverlay(disabled_config)

    for name in PUBLIC_SOURCES:
        assert hasattr(enabled, name) and hasattr(disabled, name), name


def test_disabled_overlay_does_not_draw(disabled_config, tmp_path):
    """The one thing it is supposed to switch off."""
    overlay = ImageOverlay(disabled_config)
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"not really a jpeg")

    with patch("raspilapse.overlay.render.Image.open") as mock_open:
        overlay.apply_overlay(str(image), {})

    mock_open.assert_not_called()
    assert image.read_bytes() == b"not really a jpeg", "the frame must be untouched"


class TestWeatherIsNotAnOverlayFeature:
    """The database's weather columns must not depend on the overlay setting.

    Asserted against the daemon's real wiring, not a copy of it. An earlier
    version of this test reproduced the loop's database step inline, which
    meant it went on passing after the loop stopped working that way -- it was
    testing the test.
    """

    @staticmethod
    def _daemon_with_overlay(enabled):
        from raspilapse.daemon import AdaptiveTimelapse

        config = {
            "overlay": {"enabled": enabled},
            "database": {"enabled": True},
            "weather": {"enabled": False},
        }
        timelapse = AdaptiveTimelapse.__new__(AdaptiveTimelapse)
        timelapse.config = config
        timelapse._weather = WeatherData(config)
        timelapse._overlay = build_overlay(config)
        return timelapse

    @pytest.mark.parametrize("overlay_enabled", [True, False])
    def test_weather_source_exists_either_way(self, overlay_enabled):
        timelapse = self._daemon_with_overlay(overlay_enabled)

        assert timelapse._weather is not None
        weather = timelapse._weather.get_weather_data()
        assert weather is None or isinstance(weather, dict)

    def test_overlay_setting_only_controls_drawing(self):
        assert self._daemon_with_overlay(False)._overlay is None
        assert self._daemon_with_overlay(True)._overlay is not None

    def test_daemon_reads_weather_from_its_own_source(self):
        """Pins the wiring: the loop must not reach through the capture object.

        A static check rather than a behavioural one, because the alternative
        is standing up the whole capture loop. `capture.overlay` is exactly the
        expression that used to break database logging when overlays were off.
        """
        source = (Path(__file__).resolve().parent.parent / "raspilapse" / "daemon.py").read_text()
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))

        assert "capture.overlay" not in code, (
            "the capture loop is reaching through the capture object for the "
            "overlay again; weather belongs to the daemon, not the overlay"
        )
        assert "self._weather.get_weather_data()" in code
