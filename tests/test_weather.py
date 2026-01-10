"""Tests for weather data fetcher module."""

import json
import sys
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
import urllib.error

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from weather import WeatherData


@pytest.fixture
def weather_config():
    """Create test weather configuration."""
    return {
        "weather": {
            "enabled": True,
            "endpoint": "http://test.local/api/netatmo/stations/test-id",
            "cache_duration": 300,
            "timeout": 5,
        }
    }


@pytest.fixture
def sample_netatmo_response():
    """Sample Netatmo API response."""
    return {
        "data": {
            "id": "test-station",
            "name": "Test Station",
            "modules": [
                {
                    "id": "outdoor-module",
                    "name": "Outdoor",
                    "type": "Outdoor Module",
                    "measurements": {
                        "time_utc": 1762654144,
                        "Temperature": -0.2,
                        "Humidity": 82,
                    },
                },
                {
                    "id": "wind-module",
                    "name": "Wind",
                    "type": "Wind Gauge",
                    "measurements": {
                        "time_utc": 1762654157,
                        "WindStrength": 18,  # km/h
                        "WindAngle": 185,
                        "GustStrength": 26,
                    },
                },
                {
                    "id": "rain-module",
                    "name": "Rain",
                    "type": "Rain Gauge",
                    "measurements": {
                        "time_utc": 1762654157,
                        "Rain": 0,
                        "sum_rain_1": 0.5,
                        "sum_rain_24": 2.3,
                    },
                },
                {
                    "id": "indoor-module",
                    "name": "Indoor",
                    "type": "Indoor Module",
                    "measurements": {
                        "Pressure": 1012,
                    },
                },
            ],
            "last_updated": "2025-11-09T03:10:21+01:00",
        }
    }


class TestWeatherDataInit:
    """Test WeatherData initialization."""

    def test_init_enabled(self, weather_config):
        """Test initialization with weather enabled."""
        weather = WeatherData(weather_config)
        assert weather.enabled is True
        assert weather.cache_duration == timedelta(seconds=300)
        assert weather._cached_data is None
        assert weather._cache_time is None

    def test_init_disabled(self):
        """Test initialization with weather disabled."""
        config = {"weather": {"enabled": False}}
        weather = WeatherData(config)
        assert weather.enabled is False

    def test_init_missing_config(self):
        """Test initialization with missing weather config."""
        config = {}
        weather = WeatherData(config)
        assert weather.enabled is False


class TestWeatherDataFetching:
    """Test weather data fetching."""

    def test_get_weather_data_disabled(self):
        """Test that disabled weather returns None."""
        config = {"weather": {"enabled": False}}
        weather = WeatherData(config)
        assert weather.get_weather_data() is None

    @patch("urllib.request.urlopen")
    def test_fetch_weather_success(self, mock_urlopen, weather_config, sample_netatmo_response):
        """Test successful weather data fetch."""
        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(sample_netatmo_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        data = weather.get_weather_data()

        assert data is not None
        assert data["temperature"] == -0.2
        assert data["humidity"] == 82
        assert data["wind_speed"] == 18
        assert data["wind_gust"] == 26
        assert data["wind_angle"] == 185
        assert data["rain"] == 0
        assert data["rain_1h"] == 0.5
        assert data["rain_24h"] == 2.3
        assert data["pressure"] == 1012

    @patch("urllib.request.urlopen")
    def test_fetch_weather_http_error(self, mock_urlopen, weather_config):
        """Test handling of HTTP errors."""
        mock_response = MagicMock()
        mock_response.status = 404
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        data = weather.get_weather_data()

        assert data is None

    @patch("urllib.request.urlopen")
    def test_fetch_weather_network_error(self, mock_urlopen, weather_config):
        """Test handling of network errors."""
        mock_urlopen.side_effect = urllib.error.URLError("Network error")

        weather = WeatherData(weather_config)
        data = weather.get_weather_data()

        assert data is None

    @patch("urllib.request.urlopen")
    def test_fetch_weather_invalid_json(self, mock_urlopen, weather_config):
        """Test handling of invalid JSON response."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b"Invalid JSON"
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        data = weather.get_weather_data()

        assert data is None


class TestWeatherDataCaching:
    """Test weather data caching."""

    @patch("urllib.request.urlopen")
    def test_cache_valid(self, mock_urlopen, weather_config, sample_netatmo_response):
        """Test that cache is used when valid."""
        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(sample_netatmo_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)

        # First call should fetch data
        data1 = weather.get_weather_data()
        assert mock_urlopen.call_count == 1

        # Second call should use cache
        data2 = weather.get_weather_data()
        assert mock_urlopen.call_count == 1  # No additional call
        assert data1 == data2

    @patch("urllib.request.urlopen")
    def test_cache_expired(self, mock_urlopen, weather_config, sample_netatmo_response):
        """Test that expired cache triggers new fetch."""
        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(sample_netatmo_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)

        # First call should fetch data
        data1 = weather.get_weather_data()
        assert mock_urlopen.call_count == 1

        # Manually expire cache
        weather._cache_time = datetime.now() - timedelta(seconds=400)

        # Second call should fetch again
        data2 = weather.get_weather_data()
        assert mock_urlopen.call_count == 2


class TestWeatherDataParsing:
    """Test weather data parsing from different API response formats."""

    @patch("urllib.request.urlopen")
    def test_parse_modules_at_root_level(self, mock_urlopen, weather_config):
        """Test parsing when modules are at root level (not nested under 'data')."""
        # API response with modules directly at root level
        root_level_response = {
            "modules": [
                {
                    "id": "outdoor-module",
                    "name": "Outdoor",
                    "type": "Outdoor Module",
                    "measurements": {
                        "Temperature": 15.5,
                        "Humidity": 65,
                    },
                },
                {
                    "id": "wind-module",
                    "name": "Wind",
                    "type": "Wind Gauge",
                    "measurements": {
                        "WindStrength": 12,
                        "WindAngle": 90,
                        "GustStrength": 20,
                    },
                },
            ],
            "last_updated": "2025-12-23T10:00:00+01:00",
        }

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(root_level_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        data = weather.get_weather_data()

        assert data is not None
        assert data["temperature"] == 15.5
        assert data["humidity"] == 65
        assert data["wind_speed"] == 12
        assert data["wind_angle"] == 90
        assert data["wind_gust"] == 20

    @patch("urllib.request.urlopen")
    def test_parse_modules_nested_under_data(
        self, mock_urlopen, weather_config, sample_netatmo_response
    ):
        """Test parsing when modules are nested under 'data' key."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(sample_netatmo_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        data = weather.get_weather_data()

        assert data is not None
        assert data["temperature"] == -0.2
        assert data["humidity"] == 82

    @patch("urllib.request.urlopen")
    def test_parse_empty_modules_array(self, mock_urlopen, weather_config):
        """Test parsing when modules array is empty."""
        empty_modules_response = {
            "modules": [],
            "last_updated": "2025-12-23T10:00:00+01:00",
        }

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(empty_modules_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        data = weather.get_weather_data()

        assert data is not None
        assert data["temperature"] is None
        assert data["humidity"] is None

    @patch("urllib.request.urlopen")
    def test_parse_missing_endpoint(self, mock_urlopen):
        """Test that missing endpoint returns None."""
        config = {
            "weather": {
                "enabled": True,
                # No endpoint configured
            }
        }
        weather = WeatherData(config)
        data = weather.get_weather_data()
        assert data is None


class TestWeatherDataFormatWeatherLine:
    """Test format_weather_line method."""

    @patch("urllib.request.urlopen")
    def test_format_weather_line_basic(self, mock_urlopen, weather_config, sample_netatmo_response):
        """Test formatting a weather line with template."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(sample_netatmo_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        result = weather.format_weather_line("Temp: {temp} | Humidity: {humidity}")

        assert "°C" in result
        assert "%" in result

    @patch("urllib.request.urlopen")
    def test_format_weather_line_with_wind(
        self, mock_urlopen, weather_config, sample_netatmo_response
    ):
        """Test formatting weather line with wind data."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(sample_netatmo_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        result = weather.format_weather_line("Wind: {wind_speed} from {wind_dir}")

        assert "m/s" in result

    def test_format_weather_line_disabled(self):
        """Test format_weather_line when weather is disabled."""
        config = {"weather": {"enabled": False}}
        weather = WeatherData(config)
        result = weather.format_weather_line("Temp: {temp}")
        assert result == ""

    @patch("urllib.request.urlopen")
    def test_format_weather_line_invalid_placeholder(
        self, mock_urlopen, weather_config, sample_netatmo_response
    ):
        """Test format_weather_line with invalid placeholder."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(sample_netatmo_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        # Template with an invalid placeholder
        result = weather.format_weather_line("Test: {invalid_key}")
        # Should return original template when key is unknown
        assert result == "Test: {invalid_key}"


class TestWeatherDataStaleCache:
    """Test stale cache behavior."""

    @patch("urllib.request.urlopen")
    def test_stale_cache_returns_none(self, mock_urlopen, weather_config, sample_netatmo_response):
        """Test that stale cache + failed refresh returns None."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(sample_netatmo_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)

        # First call succeeds
        data1 = weather.get_weather_data()
        assert data1 is not None
        assert mock_urlopen.call_count == 1

        # Expire the cache
        weather._cache_time = datetime.now() - timedelta(seconds=400)

        # Second call fails (network error)
        mock_urlopen.side_effect = urllib.error.URLError("Network error")
        data2 = weather.get_weather_data()

        # Should return None (showing "-" values) instead of stale data
        assert data2 is None


class TestWeatherDataFormatting:
    """Test weather data formatting methods."""

    @patch("urllib.request.urlopen")
    def test_format_temperature(self, mock_urlopen, weather_config, sample_netatmo_response):
        """Test temperature formatting."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(sample_netatmo_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        weather.get_weather_data()  # Fetch data

        assert weather._format_temperature(-0.2) == " -0.2°C"  # Fixed-width: 5.1f + °C
        assert weather._format_temperature(None) == "  N/A"  # Fixed-width

    @patch("urllib.request.urlopen")
    def test_format_humidity(self, mock_urlopen, weather_config, sample_netatmo_response):
        """Test humidity formatting."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(sample_netatmo_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        weather.get_weather_data()

        assert weather._format_humidity(82) == " 82%"  # Fixed-width: 3d + %
        assert weather._format_humidity(None) == " N/A"  # Fixed-width

    @patch("urllib.request.urlopen")
    def test_format_wind(self, mock_urlopen, weather_config, sample_netatmo_response):
        """Test wind formatting (km/h to m/s conversion)."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(sample_netatmo_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        weather.get_weather_data()

        # 18 km/h = 5.0 m/s, 26 km/h = 7.2 m/s
        formatted = weather._format_wind(18, 26)
        assert "5.0 m/s" in formatted
        assert "7.2" in formatted

        assert weather._format_wind(None, None) == "  N/A"  # Fixed-width

    @patch("urllib.request.urlopen")
    def test_format_wind_direction(self, mock_urlopen, weather_config, sample_netatmo_response):
        """Test wind direction formatting."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(sample_netatmo_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        weather.get_weather_data()

        assert weather._format_wind_direction(0) == "N "  # Fixed-width: 2 chars
        assert weather._format_wind_direction(90) == "E "  # Fixed-width: 2 chars
        assert weather._format_wind_direction(180) == "S "  # Fixed-width: 2 chars
        assert weather._format_wind_direction(270) == "W "  # Fixed-width: 2 chars
        assert weather._format_wind_direction(185) == "S "  # Fixed-width: 2 chars
        assert weather._format_wind_direction(None) == " N/A"  # Fixed-width

    @patch("urllib.request.urlopen")
    def test_format_rain(self, mock_urlopen, weather_config, sample_netatmo_response):
        """Test rain formatting."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(sample_netatmo_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        weather.get_weather_data()

        assert weather._format_rain(2.3) == " 2.3 mm"  # Fixed-width: 4.1f + mm
        assert weather._format_rain(None) == "  N/A"  # Fixed-width

    @patch("urllib.request.urlopen")
    def test_format_pressure(self, mock_urlopen, weather_config, sample_netatmo_response):
        """Test pressure formatting."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(sample_netatmo_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        weather.get_weather_data()

        assert weather._format_pressure(1012) == "1012 hPa"  # Fixed-width: 4.0f + hPa
        assert weather._format_pressure(None) == "  N/A"  # Fixed-width


class TestWindFormattingEdgeCases:
    """Test wind formatting edge cases."""

    def test_format_wind_no_gust(self, weather_config):
        """Test wind formatting without gust data."""
        weather = WeatherData(weather_config)
        # Speed only, no gust
        result = weather._format_wind(36, None)  # 36 km/h = 10.0 m/s
        assert "10.0 m/s" in result
        assert "gust" not in result

    def test_format_wind_gust_equals_speed(self, weather_config):
        """Test wind formatting when gust equals speed (no gust shown)."""
        weather = WeatherData(weather_config)
        # When gust equals speed, gust should not be shown
        result = weather._format_wind(18, 18)
        assert "5.0 m/s" in result
        assert "gust" not in result

    def test_format_wind_gust_less_than_speed(self, weather_config):
        """Test wind formatting when gust is less than speed (anomaly)."""
        weather = WeatherData(weather_config)
        # When gust < speed (shouldn't happen but handle gracefully)
        result = weather._format_wind(36, 18)
        assert "10.0 m/s" in result
        assert "gust" not in result  # Gust not shown if less than speed

    def test_format_wind_zero_speed(self, weather_config):
        """Test wind formatting with zero speed."""
        weather = WeatherData(weather_config)
        result = weather._format_wind(0, 0)
        assert "0.0 m/s" in result


class TestWindDirectionEdgeCases:
    """Test wind direction edge cases."""

    def test_format_wind_direction_boundary_angles(self, weather_config):
        """Test wind direction at boundary angles."""
        weather = WeatherData(weather_config)

        # Test all cardinal and intercardinal directions
        directions_map = {
            0: "N",
            45: "NE",
            90: "E",
            135: "SE",
            180: "S",
            225: "SW",
            270: "W",
            315: "NW",
            360: "N",  # Full circle back to N
        }

        for angle, expected in directions_map.items():
            result = weather._format_wind_direction(angle)
            assert expected in result, f"Angle {angle} should be {expected}, got {result}"

    def test_format_wind_direction_rounding(self, weather_config):
        """Test wind direction rounding at boundaries."""
        weather = WeatherData(weather_config)

        # 22.5 is exactly between N and NE - should round to NE (index 1)
        result = weather._format_wind_direction(22)
        assert "N" in result

        # 23 should also round to NE
        result = weather._format_wind_direction(23)
        assert "NE" in result

        # Test at 337.5 (between NW and N)
        result = weather._format_wind_direction(338)
        assert "N" in result


class TestTemperatureFormattingEdgeCases:
    """Test temperature formatting edge cases."""

    def test_format_temperature_extreme_cold(self, weather_config):
        """Test temperature formatting with extreme cold values."""
        weather = WeatherData(weather_config)
        result = weather._format_temperature(-40.0)
        assert "-40.0°C" in result

    def test_format_temperature_extreme_hot(self, weather_config):
        """Test temperature formatting with extreme hot values."""
        weather = WeatherData(weather_config)
        result = weather._format_temperature(50.0)
        assert "50.0°C" in result

    def test_format_temperature_zero(self, weather_config):
        """Test temperature formatting at exactly zero."""
        weather = WeatherData(weather_config)
        result = weather._format_temperature(0.0)
        assert "0.0°C" in result


class TestWeatherDataParsingEdgeCases:
    """Test weather data parsing edge cases."""

    @patch("urllib.request.urlopen")
    def test_parse_empty_modules(self, mock_urlopen, weather_config):
        """Test parsing response with empty modules list."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "data": {
                "id": "test-station",
                "name": "Test Station",
                "modules": [],
            }
        }).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        data = weather.get_weather_data()

        # Should not crash, return None values
        assert data is not None
        assert data.get("temperature") is None

    @patch("urllib.request.urlopen")
    def test_parse_unknown_module_type(self, mock_urlopen, weather_config):
        """Test parsing response with unknown module type."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "data": {
                "id": "test-station",
                "name": "Test Station",
                "modules": [
                    {
                        "id": "unknown-module",
                        "name": "Unknown",
                        "type": "Unknown Type",
                        "measurements": {
                            "SomeValue": 123,
                        },
                    },
                ],
            }
        }).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        data = weather.get_weather_data()

        # Should not crash with unknown module type
        assert data is not None

    @patch("urllib.request.urlopen")
    def test_parse_missing_measurements(self, mock_urlopen, weather_config):
        """Test parsing response with missing measurements dict."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "data": {
                "id": "test-station",
                "name": "Test Station",
                "modules": [
                    {
                        "id": "outdoor-module",
                        "name": "Outdoor",
                        "type": "Outdoor Module",
                        # Missing "measurements" key
                    },
                ],
            }
        }).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        data = weather.get_weather_data()

        # Should not crash with missing measurements
        assert data is not None


class TestHTTPErrorHandling:
    """Test HTTP error handling."""

    @patch("urllib.request.urlopen")
    def test_http_500_error(self, mock_urlopen, weather_config):
        """Test handling of HTTP 500 server error."""
        mock_response = MagicMock()
        mock_response.status = 500
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        data = weather.get_weather_data()

        assert data is None

    @patch("urllib.request.urlopen")
    def test_http_502_bad_gateway(self, mock_urlopen, weather_config):
        """Test handling of HTTP 502 bad gateway error."""
        mock_response = MagicMock()
        mock_response.status = 502
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        data = weather.get_weather_data()

        assert data is None

    @patch("urllib.request.urlopen")
    def test_json_decode_error(self, mock_urlopen, weather_config):
        """Test handling of invalid JSON response."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b"not valid json {{"
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        data = weather.get_weather_data()

        # Should return None on JSON decode error
        assert data is None


class TestWeatherTemplateFormatting:
    """Test weather template formatting."""

    @patch("urllib.request.urlopen")
    def test_format_weather_line_with_all_values(
        self, mock_urlopen, weather_config, sample_netatmo_response
    ):
        """Test formatting weather line with all values present."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(sample_netatmo_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        weather.get_weather_data()

        template = "{temperature} | {humidity} | {wind}"
        result = weather.format_weather_line(template)

        assert "°C" in result
        assert "%" in result
        assert "m/s" in result

    @patch("urllib.request.urlopen")
    def test_format_weather_line_unknown_placeholder(self, mock_urlopen, weather_config, sample_netatmo_response):
        """Test formatting with unknown placeholder."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(sample_netatmo_response).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        weather = WeatherData(weather_config)
        weather.get_weather_data()

        template = "{unknown_variable}"
        result = weather.format_weather_line(template)

        # Should return template as-is when variable unknown
        assert "{unknown_variable}" in result

    @patch("urllib.request.urlopen")
    def test_format_weather_line_no_data(self, mock_urlopen, weather_config):
        """Test formatting when no weather data available."""
        mock_urlopen.side_effect = urllib.error.URLError("Network error")

        weather = WeatherData(weather_config)
        weather.get_weather_data()  # This will fail

        template = "{temperature} | {humidity}"
        result = weather.format_weather_line(template)

        # Should return empty string when no data
        assert result == ""


class TestRainFormatting:
    """Test rain formatting edge cases."""

    def test_format_rain_zero(self, weather_config):
        """Test rain formatting with zero rainfall."""
        weather = WeatherData(weather_config)
        result = weather._format_rain(0.0)
        assert "0.0 mm" in result

    def test_format_rain_small_value(self, weather_config):
        """Test rain formatting with small value."""
        weather = WeatherData(weather_config)
        result = weather._format_rain(0.1)
        assert "0.1 mm" in result

    def test_format_rain_large_value(self, weather_config):
        """Test rain formatting with large value."""
        weather = WeatherData(weather_config)
        result = weather._format_rain(99.9)
        assert "99.9 mm" in result


class TestHumidityFormatting:
    """Test humidity formatting edge cases."""

    def test_format_humidity_zero(self, weather_config):
        """Test humidity formatting at 0%."""
        weather = WeatherData(weather_config)
        result = weather._format_humidity(0)
        assert "0%" in result

    def test_format_humidity_100(self, weather_config):
        """Test humidity formatting at 100%."""
        weather = WeatherData(weather_config)
        result = weather._format_humidity(100)
        assert "100%" in result


class TestPressureFormatting:
    """Test pressure formatting edge cases."""

    def test_format_pressure_low(self, weather_config):
        """Test pressure formatting with low pressure."""
        weather = WeatherData(weather_config)
        result = weather._format_pressure(950)
        assert "950 hPa" in result

    def test_format_pressure_high(self, weather_config):
        """Test pressure formatting with high pressure."""
        weather = WeatherData(weather_config)
        result = weather._format_pressure(1050)
        assert "1050 hPa" in result
