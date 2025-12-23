"""Weather data fetcher for Raspilapse.

Fetches weather data from Netatmo API endpoint for display in overlay.
"""

import json
from typing import Dict, Optional
from datetime import datetime, timedelta
import urllib.request
import urllib.error

try:
    from src.logging_config import get_logger
except ImportError:
    from logging_config import get_logger

logger = get_logger("weather")


class WeatherData:
    """Fetches and caches weather data from Netatmo API."""

    def __init__(self, config: Dict):
        """
        Initialize weather data fetcher.

        Args:
            config: Full configuration dictionary
        """
        self.config = config
        self.weather_config = config.get("weather", {})
        self.enabled = self.weather_config.get("enabled", False)

        # Cache settings
        self.cache_duration = timedelta(
            seconds=self.weather_config.get("cache_duration", 300)
        )  # Default 5 minutes
        self._cached_data: Optional[Dict] = None
        self._cache_time: Optional[datetime] = None

        if self.enabled:
            logger.info("Weather data fetcher initialized")
        else:
            logger.debug("Weather data fetcher disabled")

    def get_weather_data(self) -> Optional[Dict]:
        """
        Get weather data, using cache if available and fresh.

        Returns:
            Weather data dictionary or None if stale/unavailable
        """
        if not self.enabled:
            return None

        # Check cache
        if self._is_cache_valid():
            logger.debug("Using cached weather data")
            return self._cached_data

        # Try to fetch fresh data
        fresh_data = self._fetch_weather_data()

        # If fetch failed and we have stale cached data, return None (will show "-")
        if fresh_data is None and self._cached_data is not None:
            logger.warning("Weather data is stale and refresh failed, showing '-' values")
            return None

        return fresh_data

    def _is_cache_valid(self) -> bool:
        """
        Check if cached data is still valid (within cache duration).

        Returns:
            True if cache is valid, False otherwise
        """
        if self._cached_data is None or self._cache_time is None:
            return False

        age = datetime.now() - self._cache_time
        is_valid = age < self.cache_duration

        if not is_valid:
            logger.debug(
                f"Cache expired (age: {age.total_seconds():.0f}s, limit: {self.cache_duration.total_seconds():.0f}s)"
            )

        return is_valid

    def _fetch_weather_data(self) -> Optional[Dict]:
        """
        Fetch weather data from API endpoint.

        Returns:
            Parsed weather data or None on error
        """
        endpoint = self.weather_config.get("endpoint")
        if not endpoint:
            logger.warning("Weather endpoint not configured")
            return None

        timeout = self.weather_config.get("timeout", 5)

        try:
            logger.debug(f"Fetching weather data from {endpoint}")

            with urllib.request.urlopen(endpoint, timeout=timeout) as response:
                if response.status != 200:
                    logger.error(f"HTTP error {response.status} fetching weather data")
                    return None

                data = json.loads(response.read().decode("utf-8"))

            # Extract relevant data from Netatmo response
            parsed_data = self._parse_netatmo_data(data)

            # Update cache
            self._cached_data = parsed_data
            self._cache_time = datetime.now()

            logger.debug(f"Weather data fetched successfully: {parsed_data}")
            return parsed_data

        except urllib.error.URLError as e:
            logger.error(f"Network error fetching weather data: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from weather API: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching weather data: {e}")
            return None

    def _parse_netatmo_data(self, data: Dict) -> Dict:
        """
        Parse Netatmo API response to extract relevant weather data.

        Args:
            data: Raw Netatmo API response

        Returns:
            Parsed weather data dictionary
        """
        result = {
            "temperature": None,
            "humidity": None,
            "wind_speed": None,
            "wind_gust": None,
            "wind_angle": None,
            "rain": None,
            "rain_1h": None,
            "rain_24h": None,
            "pressure": None,
            "updated_at": None,
        }

        try:
            # Navigate to modules array
            # API can return modules directly at root level or nested under "data"
            if "modules" in data:
                modules = data.get("modules", [])
                station_data = data
            else:
                station_data = data.get("data", {})
                modules = station_data.get("modules", [])

            # Find outdoor module (temperature, humidity)
            for module in modules:
                module_type = module.get("type", "")
                measurements = module.get("measurements", {})

                if module_type == "Outdoor Module":
                    result["temperature"] = measurements.get("Temperature")
                    result["humidity"] = measurements.get("Humidity")

                elif module_type == "Wind Gauge":
                    result["wind_speed"] = measurements.get("WindStrength")
                    result["wind_gust"] = measurements.get("GustStrength")
                    result["wind_angle"] = measurements.get("WindAngle")

                elif module_type == "Rain Gauge":
                    result["rain"] = measurements.get("Rain")
                    result["rain_1h"] = measurements.get("sum_rain_1")
                    result["rain_24h"] = measurements.get("sum_rain_24")

                elif module_type == "Indoor Module":
                    # Get pressure from indoor module if not set
                    if result["pressure"] is None:
                        result["pressure"] = measurements.get("Pressure")

            # Get last updated time
            result["updated_at"] = station_data.get("last_updated")

        except Exception as e:
            logger.error(f"Error parsing Netatmo data: {e}")

        return result

    def format_weather_line(self, template: str) -> str:
        """
        Format weather data according to template string.

        Args:
            template: Template string with placeholders

        Returns:
            Formatted string with weather data
        """
        weather_data = self.get_weather_data()

        if not weather_data:
            return ""

        # Create formatting dictionary
        format_dict = {
            "temp": self._format_temperature(weather_data.get("temperature")),
            "temperature": self._format_temperature(weather_data.get("temperature")),
            "humidity": self._format_humidity(weather_data.get("humidity")),
            "wind": self._format_wind(
                weather_data.get("wind_speed"), weather_data.get("wind_gust")
            ),
            "wind_speed": self._format_wind_speed(weather_data.get("wind_speed")),
            "wind_gust": self._format_wind_speed(weather_data.get("wind_gust")),
            "wind_dir": self._format_wind_direction(weather_data.get("wind_angle")),
            "rain": self._format_rain(weather_data.get("rain")),
            "rain_1h": self._format_rain(weather_data.get("rain_1h")),
            "rain_24h": self._format_rain(weather_data.get("rain_24h")),
            "pressure": self._format_pressure(weather_data.get("pressure")),
        }

        try:
            return template.format(**format_dict)
        except KeyError as e:
            logger.warning(f"Unknown weather placeholder: {e}")
            return template

    def _format_temperature(self, temp: Optional[float]) -> str:
        """Format temperature value with fixed width."""
        if temp is None:
            return "  N/A"
        # Fixed width: -XX.X°C (7 chars total, right-aligned number)
        return f"{temp:5.1f}°C"

    def _format_humidity(self, humidity: Optional[int]) -> str:
        """Format humidity value with fixed width."""
        if humidity is None:
            return " N/A"
        # Fixed width: XXX% (4 chars total, right-aligned)
        return f"{humidity:3d}%"

    def _format_wind(self, speed: Optional[int], gust: Optional[int]) -> str:
        """Format wind speed with gust, fixed width."""
        if speed is None:
            return "  N/A"

        # Convert km/h to m/s for more common metric
        speed_ms = speed / 3.6
        # Fixed width: XX.X m/s
        result = f"{speed_ms:4.1f} m/s"

        if gust is not None and gust > speed:
            gust_ms = gust / 3.6
            # Fixed width for gust too
            result += f" (gust {gust_ms:4.1f})"

        return result

    def _format_wind_speed(self, speed: Optional[int]) -> str:
        """Format wind speed value with fixed width."""
        if speed is None:
            return "  N/A"
        speed_ms = speed / 3.6
        # Fixed width: XX.X m/s
        return f"{speed_ms:4.1f} m/s"

    def _format_wind_direction(self, angle: Optional[int]) -> str:
        """Format wind direction from angle with fixed width."""
        if angle is None:
            return " N/A"

        # Convert angle to compass direction
        directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        index = round(angle / 45) % 8
        # Fixed width: 2 chars, left-aligned (NE, N_, etc)
        return f"{directions[index]:2s}"

    def _format_rain(self, rain: Optional[float]) -> str:
        """Format rain value with fixed width."""
        if rain is None:
            return "  N/A"
        # Fixed width: XX.X mm
        return f"{rain:4.1f} mm"

    def _format_pressure(self, pressure: Optional[float]) -> str:
        """Format pressure value with fixed width."""
        if pressure is None:
            return "  N/A"
        # Fixed width: XXXX hPa (4 digits)
        return f"{pressure:4.0f} hPa"
