"""Weather data fetcher for Raspilapse.

Fetches weather data from a Netatmo API endpoint for display in the overlay.

The cache lives at module level, keyed by endpoint, rather than on the
instance. ImageOverlay -- and therefore WeatherData -- is rebuilt inside every
ImageCapture, and ImageCapture is constructed twice per capture cycle, so a
per-instance cache started empty every single time and the configured
cache_duration never took effect.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional

from raspilapse.logging_setup import get_logger

logger = get_logger("weather")

# How long to suppress repeats of an unchanged error message.
ERROR_LOG_INTERVAL = timedelta(minutes=10)

# Smallest retry delay, so cache_duration: 0 cannot mean "retry immediately".
MIN_BACKOFF = timedelta(seconds=30)


@dataclass
class _CacheEntry:
    """Per-endpoint fetch state, shared by every WeatherData in the process."""

    data: Optional[Dict] = None
    fetched_at: Optional[datetime] = None
    failures: int = 0
    next_attempt_at: Optional[datetime] = None
    last_error: str = ""
    last_error_logged_at: Optional[datetime] = None
    suppressed: int = field(default=0)


_CACHE: Dict[str, _CacheEntry] = {}


def reset_cache() -> None:
    """Drop all cached weather state. For tests."""
    _CACHE.clear()


class WeatherData:
    """Fetches and caches weather data from a Netatmo API endpoint."""

    def __init__(self, config: Dict):
        """
        Initialize weather data fetcher.

        Args:
            config: Full configuration dictionary
        """
        self.config = config
        self.weather_config = config.get("weather", {})
        self.enabled = self.weather_config.get("enabled", False)

        self.cache_duration = timedelta(seconds=self.weather_config.get("cache_duration", 300))
        # Cap on the exponential backoff applied after consecutive failures.
        self.max_backoff = timedelta(seconds=self.weather_config.get("max_backoff_seconds", 900))

        if self.enabled:
            logger.debug("Weather data fetcher initialized")
        else:
            logger.debug("Weather data fetcher disabled")

    @property
    def _entry(self) -> _CacheEntry:
        endpoint = self.weather_config.get("endpoint") or ""
        return _CACHE.setdefault(endpoint, _CacheEntry())

    # Kept as properties so existing callers and tests that poke at the cache
    # keep working now that the storage moved to module level.
    @property
    def _cached_data(self) -> Optional[Dict]:
        return self._entry.data

    @_cached_data.setter
    def _cached_data(self, value: Optional[Dict]) -> None:
        self._entry.data = value

    @property
    def _cache_time(self) -> Optional[datetime]:
        return self._entry.fetched_at

    @_cache_time.setter
    def _cache_time(self, value: Optional[datetime]) -> None:
        self._entry.fetched_at = value

    def get_weather_data(self) -> Optional[Dict]:
        """
        Get weather data, using cache if available and fresh.

        Returns:
            Weather data dictionary, or None if nothing has ever been fetched
        """
        if not self.enabled:
            return None

        entry = self._entry

        if self._is_cache_valid():
            logger.debug("Using cached weather data")
            return entry.data

        # After a failure, don't touch the network again until the backoff
        # expires. Without this, a DNS outage produced one error line per call,
        # twice per 30-second cycle -- 72,000 identical lines in one log file.
        now = datetime.now()
        if entry.next_attempt_at and now < entry.next_attempt_at:
            return entry.data

        fresh_data = self._fetch_weather_data()

        # Serve stale data rather than blanking the overlay.
        if fresh_data is None and entry.data is not None:
            age = (now - entry.fetched_at).total_seconds() if entry.fetched_at else None
            logger.debug(
                "Weather fetch failed, using stale cached data"
                + (f" (age: {age:.0f}s)" if age is not None else "")
            )
            return entry.data

        return fresh_data

    def _is_cache_valid(self) -> bool:
        """
        Check if cached data is still valid (within cache duration).

        Returns:
            True if cache is valid, False otherwise
        """
        entry = self._entry
        if entry.data is None or entry.fetched_at is None:
            return False

        age = datetime.now() - entry.fetched_at
        is_valid = age < self.cache_duration

        if not is_valid:
            logger.debug(
                f"Cache expired (age: {age.total_seconds():.0f}s, "
                f"limit: {self.cache_duration.total_seconds():.0f}s)"
            )

        return is_valid

    def _record_failure(self, message: str) -> None:
        """Apply backoff and log at most one line per distinct error per interval."""
        entry = self._entry
        now = datetime.now()
        entry.failures += 1

        # 300s, 600s, 1200s, ... capped at max_backoff. The floor matters
        # because cache_duration: 0 would otherwise give a zero delay and
        # restore the every-call retry this exists to prevent.
        base = max(self.cache_duration, MIN_BACKOFF)
        delay = min(base * (2 ** (entry.failures - 1)), self.max_backoff)
        entry.next_attempt_at = now + delay

        changed = message != entry.last_error
        due = (
            entry.last_error_logged_at is None
            or now - entry.last_error_logged_at >= ERROR_LOG_INTERVAL
        )

        if changed or due:
            if entry.suppressed:
                logger.warning(
                    f"{message} (attempt {entry.failures}, "
                    f"{entry.suppressed} identical error(s) suppressed; "
                    f"retrying in {delay.total_seconds():.0f}s)"
                )
            else:
                logger.warning(f"{message} (retrying in {delay.total_seconds():.0f}s)")
            entry.last_error = message
            entry.last_error_logged_at = now
            entry.suppressed = 0
        else:
            entry.suppressed += 1

    def _fetch_weather_data(self) -> Optional[Dict]:
        """
        Fetch weather data from the API endpoint.

        Returns:
            Parsed weather data or None on error
        """
        endpoint = self.weather_config.get("endpoint")
        if not endpoint:
            self._record_failure("Weather endpoint not configured")
            return None

        timeout = self.weather_config.get("timeout", 5)
        entry = self._entry

        try:
            logger.debug(f"Fetching weather data from {endpoint}")

            with urllib.request.urlopen(endpoint, timeout=timeout) as response:
                if response.status != 200:
                    self._record_failure(f"HTTP error {response.status} fetching weather data")
                    return None

                data = json.loads(response.read().decode("utf-8"))

            parsed_data = self._parse_netatmo_data(data)

            entry.data = parsed_data
            entry.fetched_at = datetime.now()
            if entry.failures:
                logger.info(f"Weather data recovered after {entry.failures} failed attempt(s)")
            entry.failures = 0
            entry.next_attempt_at = None
            entry.last_error = ""
            entry.suppressed = 0

            logger.debug(f"Weather data fetched successfully: {parsed_data}")
            return parsed_data

        except urllib.error.URLError as e:
            self._record_failure(f"Network error fetching weather data: {e}")
            return None
        except json.JSONDecodeError as e:
            self._record_failure(f"Invalid JSON response from weather API: {e}")
            return None
        except Exception as e:
            self._record_failure(f"Unexpected error fetching weather data: {e}")
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
            # The API returns modules either at the root or nested under "data".
            # `or {}` rather than a .get default: the API sends an explicit
            # "data": null when the station is offline, and a default only
            # applies when the key is absent. That produced 2,204 logged
            # AttributeErrors on one Pi.
            if "modules" in data:
                station_data = data
                modules = data.get("modules") or []
            else:
                station_data = data.get("data") or {}
                modules = station_data.get("modules") or []

            if not isinstance(modules, list):
                modules = []

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

    # Placeholders emitted by format_fields().
    #
    # "temperature" is deliberately absent: in overlay templates that name
    # means the *camera sensor* temperature, which comes from capture metadata.
    # Only format_weather_line(), whose templates are weather-only, aliases it
    # to the outdoor reading.
    PLACEHOLDERS = (
        "temp",
        "temperature_outdoor",
        "humidity",
        "wind",
        "wind_speed",
        "wind_gust",
        "wind_dir",
        "rain",
        "rain_1h",
        "rain_24h",
        "pressure",
    )

    def format_fields(self, weather_data: Optional[Dict] = None) -> Dict[str, str]:
        """
        Build the overlay placeholder dictionary for some weather data.

        Args:
            weather_data: Data to format; fetched (or taken from cache) if omitted

        Returns:
            Placeholder name -> formatted string. Every key in PLACEHOLDERS is
            always present, filled with "-" when there is no data at all.
        """
        if weather_data is None:
            weather_data = self.get_weather_data()

        if not weather_data:
            return {name: "-" for name in self.PLACEHOLDERS}

        temperature = self._format_temperature(weather_data.get("temperature"))
        return {
            "temp": temperature,
            # Alias used by the top-bar overlay templates.
            "temperature_outdoor": temperature,
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

        fields = self.format_fields(weather_data)
        # Weather-only templates: here "temperature" means the outdoor reading.
        fields["temperature"] = fields["temp"]

        try:
            return template.format(**fields)
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
