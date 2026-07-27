"""The substitution table the overlay templates draw from.

A template names placeholders -- `{tide_level}`, `{ships_line_3}`, `{cpu_temp}`
-- and `_prepare_overlay_data` fills them. The contract is that *every* key
exists on every frame, whatever is switched off or unreachable, because a
missing one raises KeyError from inside `apply_overlay`, which catches it and
returns None: one silently un-overlaid frame in the middle of a timelapse.

These tests were written because the field groups had no coverage at all. When
`_prepare_overlay_data` was split into per-source helpers, four deliberate
breakages -- dropping the ship fields, dropping the system fields, changing the
disabled-tide placeholders, and querying the tide source while it was disabled
-- all passed the entire overlay suite. Each has a test below.
"""

from unittest.mock import patch

import pytest

from raspilapse.overlay.render import ImageOverlay

METADATA = {
    "ExposureTime": 1_000_000,
    "AnalogueGain": 2.0,
    "Lux": 100.5,
    "ColourGains": [1.8, 1.5],
    "SensorTemperature": 40.0,
    "resolution": [1920, 1080],
}

TIDE_READING = {
    "level_str": "142cm",
    "arrow": "→",
    "trend": "rising",
    "target_level_str": "38cm",
    "high_time_str": "23:55",
    "high_level_str": "168cm",
    "low_time_str": "06:10",
    "low_level_str": "38cm",
}

# Placeholders every tide key takes when there is nothing to show.
TIDE_BLANKS = {
    "tide": "",
    "tide_level": "-",
    "tide_arrow": "",
    "tide_trend": "-",
    "tide_target": "-",
    "tide_high_time": "-",
    "tide_high_level": "-",
    "tide_low_time": "-",
    "tide_low_level": "-",
}


def make_overlay(**sources):
    """An enabled overlay with each named source forced on or off."""
    config = {
        "overlay": {
            "enabled": True,
            "position": "top-bar",
            "camera_name": "Test Cam",
            "font": {"family": "default", "size_ratio": 0.025},
            "datetime": {"localized": False},
        },
    }
    overlay = ImageOverlay(config)
    for name, enabled in sources.items():
        getattr(overlay, name).enabled = enabled
    return overlay


class TestTideFields:
    """Tide keys, present whether or not there is a tide to report."""

    def test_disabled_tide_still_defines_every_key(self):
        overlay = make_overlay(tide=False)
        data = overlay._prepare_overlay_data(METADATA, "day")
        for key, blank in TIDE_BLANKS.items():
            assert data[key] == blank, f"{key} should be {blank!r} when tide is disabled"

    def test_enabled_tide_with_no_reading_defines_every_key(self):
        """A configured station that has never answered must look like no station."""
        overlay = make_overlay(tide=True)
        with patch.object(overlay.tide, "get_widget_data", return_value=None):
            data = overlay._prepare_overlay_data(METADATA, "day")
        for key, blank in TIDE_BLANKS.items():
            assert data[key] == blank, f"{key} should be {blank!r} when the reading is missing"

    def test_enabled_tide_with_a_reading_uses_it(self):
        overlay = make_overlay(tide=True)
        with (
            patch.object(overlay.tide, "get_widget_data", return_value=TIDE_READING),
            patch.object(overlay.tide, "format_tide_compact", return_value="142cm →"),
        ):
            data = overlay._prepare_overlay_data(METADATA, "day")
        assert data["tide"] == "142cm →"
        assert data["tide_level"] == "142cm"
        assert data["tide_trend"] == "rising"
        assert data["tide_high_time"] == "23:55"
        assert data["tide_low_level"] == "38cm"

    def test_disabled_tide_is_never_queried(self):
        """Disabled means no call, not a call whose answer is discarded.

        get_widget_data reads a cache file and can refresh it over the network.
        Doing that for a source the user turned off costs a file read per frame
        -- 2,880 a day -- for output that is thrown away.
        """
        overlay = make_overlay(tide=False)
        with patch.object(overlay.tide, "get_widget_data") as query:
            overlay._prepare_overlay_data(METADATA, "day")
        query.assert_not_called()


class TestShipFields:
    """Ship keys, including the five line slots a template may reference."""

    def test_disabled_ships_define_all_five_line_slots(self):
        overlay = make_overlay(ships=False)
        data = overlay._prepare_overlay_data(METADATA, "day")
        assert data["ships"] == ""
        assert data["ships_count"] == "0"
        assert data["ships_moving"] == "0"
        for i in range(1, 6):
            assert data[f"ships_line_{i}"] == "", f"ships_line_{i} missing or non-empty"

    def test_enabled_ships_fill_the_slots_they_have_and_blank_the_rest(self):
        """Two vessels must still leave slots 3, 4 and 5 defined."""
        overlay = make_overlay(ships=True)
        with (
            patch.object(overlay.ships, "format_ships_lines", return_value=["NORDLYS, POLARLYS"]),
            patch.object(overlay.ships, "get_ships_count", return_value=2),
            patch.object(overlay.ships, "get_moving_ships_count", return_value=1),
        ):
            data = overlay._prepare_overlay_data(METADATA, "day")

        assert data["ships"] == "NORDLYS, POLARLYS"
        assert data["ships_count"] == "2"
        assert data["ships_moving"] == "1"
        assert data["ships_line_1"] == "NORDLYS, POLARLYS"
        for i in range(2, 6):
            assert data[f"ships_line_{i}"] == "", f"ships_line_{i} should be blank"


class TestSystemFields:
    """Host metrics, which are absent on any machine that is not a Pi."""

    def test_metrics_are_formatted_when_present(self):
        overlay = make_overlay()
        metrics = {
            "cpu_temp": 48.25,
            "disk": {"free": 12.3, "used": 40.1, "total": 58.0, "percent": 69.0},
            "memory": {"used": 2048, "free": 6144, "total": 8192, "percent": 25.0},
            "load": {"1min": 0.42, "5min": 0.31, "15min": 0.25},
            "uptime": 90061,
        }
        with patch.object(overlay.system_monitor, "get_all_metrics", return_value=metrics):
            data = overlay._prepare_overlay_data(METADATA, "day")

        assert data["cpu_temp_raw"] == "48.2"
        assert data["disk_free"] == "12.3 GB"
        assert data["disk_percent"] == "69%"
        assert data["memory_used"] == "2.0 GB"
        assert data["load_1min"] == "0.42"

    def test_absent_metrics_become_na_rather_than_missing(self):
        """Every key still has to exist, or a template naming it kills the frame."""
        overlay = make_overlay()
        with patch.object(overlay.system_monitor, "get_all_metrics", return_value={}):
            data = overlay._prepare_overlay_data(METADATA, "day")

        for key in (
            "cpu_temp",
            "cpu_temp_raw",
            "disk_free",
            "disk_percent",
            "memory_used",
            "memory_percent",
            "load_1min",
            "uptime",
        ):
            assert data[key] == "N/A", f"{key} should be N/A when metrics are unavailable"


class TestEveryDocumentedPlaceholder:
    """The whole point of the table: no template can raise KeyError."""

    @pytest.mark.parametrize(
        "placeholder",
        [
            "date",
            "time",
            "datetime",
            "datetime_localized",
            "camera_name",
            "mode",
            "exposure",
            "exposure_ms",
            "exposure_us",
            "iso",
            "gain",
            "wb",
            "wb_gains",
            "color_gains",
            "lux",
            "resolution",
            "temperature",
            "af_mode",
            "lens_position",
            "focus_distance",
            "cpu_temp",
            "disk",
            "memory",
            "load",
            "uptime",
            "ships",
            "ships_count",
            "ships_moving",
            "ships_line_1",
            "ships_line_5",
            "tide",
            "tide_level",
            "tide_trend",
        ],
    )
    def test_placeholder_is_defined_with_everything_switched_off(self, placeholder):
        """The worst case for a missing key: nothing enabled, nothing reachable."""
        overlay = make_overlay(tide=False, ships=False, aurora=False)
        with (
            patch.object(overlay.system_monitor, "get_all_metrics", return_value={}),
            patch.object(overlay.weather, "get_weather_data", return_value=None),
        ):
            data = overlay._prepare_overlay_data(METADATA, "day")
        assert placeholder in data
