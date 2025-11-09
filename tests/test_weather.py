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

        assert weather._format_temperature(-0.2) == "-0.2°C"
        assert weather._format_temperature(None) == "N/A"

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

        assert weather._format_humidity(82) == "82%"
        assert weather._format_humidity(None) == "N/A"

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

        assert weather._format_wind(None, None) == "N/A"

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

        assert weather._format_wind_direction(0) == "N"
        assert weather._format_wind_direction(90) == "E"
        assert weather._format_wind_direction(180) == "S"
        assert weather._format_wind_direction(270) == "W"
        assert weather._format_wind_direction(185) == "S"
        assert weather._format_wind_direction(None) == "N/A"

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

        assert weather._format_rain(2.3) == "2.3 mm"
        assert weather._format_rain(None) == "N/A"

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

        assert weather._format_pressure(1012) == "1012 hPa"
        assert weather._format_pressure(None) == "N/A"
