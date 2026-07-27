"""Cached JSON data sources for the overlay.

Ships, tide and aurora all arrive the same way: a JSON file written by a
separate service, re-read on a timer, with the last good copy kept so a missing
or half-written file does not blank the overlay. That skeleton was written out
three times; CachedJsonSource is the one copy.

Everything past the loading -- tide extremes, ship formatting, aurora arrows --
is genuine per-source domain logic and stays with its class.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from src.logging_config import get_logger
except ImportError:
    from logging_config import get_logger

logger = get_logger("overlay")


class CachedJsonSource:
    """A JSON file re-read on a timer, serving the last good copy on failure.

    Subclasses set the four class attributes and, if their file wraps the
    payload in an envelope, override _extract.
    """

    section_key: str = ""  # config section, e.g. "tide"
    path_key: str = ""  # key within it holding the file path
    cache_duration: float = 60.0  # seconds
    label: str = "data"  # used in log messages

    def __init__(self, config: Dict):
        """
        Args:
            config: Full configuration dictionary
        """
        self.config = config
        self.section = config.get(self.section_key, {}) or {}
        self.enabled = bool(self.section.get("enabled", False))
        self.path = self.section.get(self.path_key, "")
        self._cache: Optional[Dict] = None
        self._cache_time: Optional[datetime] = None
        self._missing_logged = False

    def _extract(self, raw: Dict) -> Dict:
        """Pull the payload out of the loaded file. Override to unwrap."""
        return raw

    def load(self) -> Optional[Dict]:
        """
        Return the data, re-reading the file if the cache has expired.

        Returns:
            The parsed payload, the stale cache if the file is missing or
            unreadable, or None if there is nothing at all.
        """
        if not self.enabled or not self.path:
            return None

        now = datetime.now()
        # Not gated on _cache: a source that has never loaded successfully is
        # exactly the one that needs the interval most. Gating on the cache
        # made the stamp below dead for that case, so a missing file still cost
        # a stat() on every render -- twice per capture cycle, forever.
        if self._cache_time is not None:
            if (now - self._cache_time).total_seconds() < self.cache_duration:
                return self._cache

        try:
            path = Path(self.path)
            if not path.exists():
                # Stamp the attempt so a permanently absent file is re-checked
                # on the normal interval instead of on every render, and warn
                # once rather than every time the cache expires. An overlay is
                # rebuilt twice per capture cycle; without this an unconfigured
                # ships_file floods the log exactly the way weather.py used to.
                self._cache_time = now
                if not self._missing_logged:
                    logger.warning(f"{self.label.capitalize()} file not found: {self.path}")
                    self._missing_logged = True
                return self._cache

            with open(path, "r") as f:
                data = self._extract(json.load(f))

            self._missing_logged = False
            self._cache = data
            self._cache_time = now
            return data

        except Exception as e:
            self._cache_time = now
            logger.warning(f"Failed to load {self.label} data: {e}")
            return self._cache


class ShipsData(CachedJsonSource):
    """Handles loading and formatting ship data from pi-overlay-data."""

    section_key = "barentswatch"
    path_key = "ships_file"
    label = "ships"
    cache_duration = 60

    def get_ships_data(self) -> Optional[Dict]:
        """Load the ships data. Alias for load()."""
        return self.load()

    def _format_ship(self, ship: Dict) -> str:
        """Format a single ship compactly: NAME (category speed dir) or NAME (category stationary)"""
        name = ship.get("name", "Unknown")
        speed = ship.get("speed", 0)
        direction = ship.get("direction", "")
        category = ship.get("category", "")

        # Show "(category, stationary)" for ships not moving (speed <= 0.5 kts)
        if speed <= 0.5:
            if category:
                return f"{name} ({category}, stationary)"
            return f"{name} (stationary)"

        # Abbreviate direction
        dir_abbrev = {
            "north": "N",
            "north-east": "NE",
            "east": "E",
            "south-east": "SE",
            "south": "S",
            "south-west": "SW",
            "west": "W",
            "north-west": "NW",
            "unknown": "",
        }
        dir_short = dir_abbrev.get(direction, direction[:2].upper() if direction else "")

        if category:
            if dir_short:
                return f"{name} ({category}, {speed:.1f} kts {dir_short})"
            else:
                return f"{name} ({category}, {speed:.1f} kts)"
        else:
            if dir_short:
                return f"{name} ({speed:.1f} kts {dir_short})"
            else:
                return f"{name} ({speed:.1f} kts)"

    def get_moving_ships_list(self) -> List[Dict]:
        """Get list of moving ships sorted by speed descending."""
        data = self.get_ships_data()
        if data is None:
            return []

        items = data.get("items", [])
        # Filter to moving ships only (speed > 0.5 kts to ignore drift)
        moving_ships = [s for s in items if s.get("speed", 0) > 0.5]
        # Sort by speed descending (fastest first)
        moving_ships.sort(key=lambda s: s.get("speed", 0), reverse=True)
        return moving_ships

    def get_all_ships_list(self) -> List[Dict]:
        """Get list of all ships sorted by speed descending."""
        data = self.get_ships_data()
        if data is None:
            return []

        items = data.get("items", [])
        # Sort by speed descending (fastest/moving ships first)
        ships = sorted(items, key=lambda s: s.get("speed", 0), reverse=True)
        return ships

    def format_ships_lines(self, ships_per_line: int = 4) -> List[str]:
        """
        Format ships data as multiple lines for overlay display.

        Args:
            ships_per_line: Number of ships per line

        Returns:
            List of formatted lines (first line includes count header)
        """
        all_ships = self.get_all_ships_list()

        if not all_ships:
            return ["0 Ships"]

        # Format all ships
        ship_strings = [self._format_ship(ship) for ship in all_ships]
        ship_count = len(all_ships)

        # Split into chunks
        lines = []
        for i in range(0, len(ship_strings), ships_per_line):
            chunk = ship_strings[i : i + ships_per_line]
            if i == 0:
                # First line includes count header
                lines.append(f"{ship_count} Ships: " + ", ".join(chunk))
            else:
                # Continuation lines - no indent, align with left margin
                lines.append(", ".join(chunk))

        return lines

    def get_ship_boxes_data(self) -> List[str]:
        """
        Get list of formatted ship strings for individual box rendering.

        Returns:
            List of formatted strings, one per ship (e.g., "NORDLYS 14.1 kts SE")
        """
        moving_ships = self.get_moving_ships_list()
        return [self._format_ship(ship) for ship in moving_ships]

    def get_ships_count(self) -> int:
        """Get total number of ships in the area."""
        data = self.get_ships_data()
        if data is None:
            return 0
        return data.get("count", len(data.get("items", [])))

    def get_moving_ships_count(self) -> int:
        """Get number of moving ships (speed > 0.5 kts)."""
        data = self.get_ships_data()
        if data is None:
            return 0
        items = data.get("items", [])
        return len([s for s in items if s.get("speed", 0) > 0.5])


def _level_cm(point: Optional[Dict], default: float = 0.0) -> float:
    """
    Read a tide point's level as a number.

    A plain `.get("level_cm", 0)` looks safe but returns None when the JSON
    holds an explicit null -- the default only covers a *missing* key.
    That None then reaches the interpolation arithmetic as a TypeError, taking
    the whole overlay down over one bad forecast point.
    """
    if not point:
        return default
    value = point.get("level_cm")
    return default if value is None else value


class TideData(CachedJsonSource):
    """Handles loading and formatting tide data from pi-overlay-data."""

    section_key = "tide"
    path_key = "tide_file"
    label = "tide"
    cache_duration = 600

    def _extract(self, raw: Dict) -> Dict:
        """The tide file wraps its payload in a cache envelope."""
        return raw.get("tide_data", raw)

    def get_tide_data(self) -> Optional[Dict]:
        """Load the tide data. Alias for load()."""
        return self.load()

    def get_current_level(self) -> Optional[float]:
        """
        Get current tide level in meters, interpolated from points array.

        Uses the points array to find the level for the current time,
        interpolating between the two nearest points.
        """
        data = self.get_tide_data()
        if data is None:
            return None

        points = data.get("points", [])
        if not points:
            # Fallback to static current level if no points
            current = data.get("current", {})
            level_cm = current.get("level_cm")
            if level_cm is not None:
                return level_cm / 100.0
            return None

        now = datetime.now().astimezone()

        # Find the two points surrounding the current time
        prev_point = None
        next_point = None

        for point in points:
            point_time = self._parse_time(point.get("time"))
            if point_time is None:
                continue

            if point_time <= now:
                prev_point = point
            elif next_point is None:
                next_point = point
                break

        # If we have both points, interpolate
        if prev_point and next_point:
            prev_time = self._parse_time(prev_point["time"])
            next_time = self._parse_time(next_point["time"])
            prev_level = _level_cm(prev_point)
            next_level = _level_cm(next_point)

            # Calculate interpolation factor (0.0 to 1.0)
            total_diff = (next_time - prev_time).total_seconds()
            current_diff = (now - prev_time).total_seconds()

            if total_diff > 0:
                factor = current_diff / total_diff
                level_cm = prev_level + (next_level - prev_level) * factor
                return level_cm / 100.0

        # If we only have previous point, use it
        if prev_point:
            return _level_cm(prev_point) / 100.0

        # If we only have next point, use it
        if next_point:
            return _level_cm(next_point) / 100.0

        # Fallback to static current level
        current = data.get("current", {})
        level_cm = current.get("level_cm")
        if level_cm is not None:
            return level_cm / 100.0
        return None

    def get_trend(self) -> str:
        """
        Get tide trend (rising, falling, stable) based on points array.

        Calculates trend from the current interpolated position in the points array.
        """
        data = self.get_tide_data()
        if data is None:
            return "unknown"

        points = data.get("points", [])
        if len(points) < 2:
            # Fallback to static trend
            current = data.get("current", {})
            return current.get("trend", "unknown")

        now = datetime.now().astimezone()

        # Find the two points surrounding current time
        prev_point = None
        next_point = None

        for point in points:
            point_time = self._parse_time(point.get("time"))
            if point_time is None:
                continue

            if point_time <= now:
                prev_point = point
            elif next_point is None:
                next_point = point
                break

        # Determine trend from the two surrounding points
        if prev_point and next_point:
            prev_level = _level_cm(prev_point)
            next_level = _level_cm(next_point)

            diff = next_level - prev_level
            if diff > 2:  # Rising threshold
                return "rising"
            elif diff < -2:  # Falling threshold
                return "falling"
            else:
                return "stable"

        # Fallback to static trend
        current = data.get("current", {})
        return current.get("trend", "unknown")

    def get_trend_arrow(self) -> str:
        """Get arrow character for trend direction."""
        trend = self.get_trend()
        if trend == "rising":
            return "↑"
        elif trend == "falling":
            return "↓"
        else:
            return "→"

    def _find_extremes_from_points(self) -> Tuple[List[Dict], List[Dict]]:
        """
        Find all high and low tides from the points array.

        Walks the series tracking direction (rising/falling). An extreme is
        emitted only when direction actually reverses; plateaus on a slope
        (e.g. 149,149,149 then 150) are NOT extremes — only plateaus where
        the direction before differs from the direction after qualify, and
        the midpoint of such a plateau is reported as the extreme.

        Returns:
            Tuple of (highs_list, lows_list) where each item is
            {"time": iso_string, "level_cm": int}
        """
        data = self.get_tide_data()
        if data is None:
            return [], []

        points = data.get("points", [])
        if len(points) < 3:
            return [], []

        min_amplitude_cm = 5  # reject reversals smaller than this vs. previous extreme

        highs: List[Dict] = []
        lows: List[Dict] = []

        # Track the previous accepted opposite extreme for amplitude filtering.
        last_extreme_level: Optional[int] = None  # level of previous accepted extreme
        last_extreme_kind: Optional[str] = None  # "high" or "low"

        # Direction leading into the current point: 1 rising, -1 falling,
        # 0 not yet known.
        prev_dir = 0

        def emit(kind: str, idx: int) -> None:
            nonlocal last_extreme_level, last_extreme_kind
            level = _level_cm(points[idx])
            if last_extreme_level is not None and last_extreme_kind != kind:
                if abs(level - last_extreme_level) < min_amplitude_cm:
                    return
            entry = {"time": points[idx].get("time"), "level_cm": level}
            if kind == "high":
                highs.append(entry)
            else:
                lows.append(entry)
            last_extreme_level = level
            last_extreme_kind = kind

        for i in range(1, len(points)):
            prev_level = _level_cm(points[i - 1])
            curr_level = _level_cm(points[i])

            if curr_level > prev_level:
                cur_dir = 1
            elif curr_level < prev_level:
                cur_dir = -1
            else:
                cur_dir = 0  # plateau

            if cur_dir == 0:
                # Plateau: wait to see which way it breaks.
                continue

            if prev_dir == 0:
                # First strict move; nothing to emit yet.
                prev_dir = cur_dir
                continue

            if cur_dir == prev_dir:
                # Same direction: the plateau resolved as a continuation, so
                # there is no extremum here.
                continue

            # Direction reversed between the previous strict move and this one.
            # The extreme is at the midpoint of the plateau (or the single peak
            # point if no plateau). prev_dir was the direction up to the
            # plateau; cur_dir is the direction leaving it.
            plateau_end = i - 1  # last index at the plateau level
            plateau_start = plateau_end
            plateau_level = _level_cm(points[plateau_end])
            while plateau_start > 0 and _level_cm(points[plateau_start - 1]) == plateau_level:
                plateau_start -= 1
            mid_idx = (plateau_start + plateau_end) // 2

            if prev_dir == 1 and cur_dir == -1:
                emit("high", mid_idx)
            elif prev_dir == -1 and cur_dir == 1:
                emit("low", mid_idx)

            prev_dir = cur_dir

        return highs, lows

    def get_next_high(self) -> Optional[Dict]:
        """
        Get next high tide info by calculating from points array.

        Always calculates from the points array to ensure accuracy,
        filtering to only return future events.
        """
        now = datetime.now().astimezone()

        highs, _ = self._find_extremes_from_points()
        for high in highs:
            high_time = self._parse_time(high.get("time"))
            if high_time and high_time > now:
                return high

        return None

    def get_next_low(self) -> Optional[Dict]:
        """
        Get next low tide info by calculating from points array.

        Always calculates from the points array to ensure accuracy,
        filtering to only return future events.
        """
        now = datetime.now().astimezone()

        _, lows = self._find_extremes_from_points()
        for low in lows:
            low_time = self._parse_time(low.get("time"))
            if low_time and low_time > now:
                return low

        return None

    def _parse_time(self, time_str: str) -> Optional[datetime]:
        """Parse ISO format time string, always returning timezone-aware datetime."""
        if not time_str:
            return None
        try:
            dt = datetime.fromisoformat(time_str)
            # Ensure timezone-aware for comparison with datetime.now().astimezone()
            if dt.tzinfo is None:
                dt = dt.astimezone()
            return dt
        except (ValueError, TypeError):
            return None

    def get_next_event(self) -> Tuple[str, Optional[datetime], Optional[float]]:
        """
        Get the next tide event (whichever is sooner).

        Returns:
            Tuple of (event_type, event_time, level_m)
            event_type is "high" or "low"
        """
        next_high = self.get_next_high()
        next_low = self.get_next_low()

        high_time = None
        low_time = None

        if next_high:
            high_time = self._parse_time(next_high.get("time"))
        if next_low:
            low_time = self._parse_time(next_low.get("time"))

        if high_time and low_time:
            if high_time < low_time:
                level = _level_cm(next_high) / 100.0
                return ("high", high_time, level)
            else:
                level = _level_cm(next_low) / 100.0
                return ("low", low_time, level)
        elif high_time:
            level = _level_cm(next_high) / 100.0
            return ("high", high_time, level)
        elif low_time:
            level = _level_cm(next_low) / 100.0
            return ("low", low_time, level)

        return ("unknown", None, None)

    def format_time(self, dt: Optional[datetime]) -> str:
        """Format datetime as HH:MM in local time."""
        if dt is None:
            return "--:--"
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%H:%M")

    def format_tide_compact(self) -> str:
        """
        Format tide info in compact form for text overlay.

        Returns:
            String like "1.4m ↑ (high 18:30)"
        """
        level = self.get_current_level()
        if level is None:
            return ""

        arrow = self.get_trend_arrow()
        event_type, event_time, _ = self.get_next_event()
        time_str = self.format_time(event_time)

        return f"{level:.1f}m {arrow} ({event_type} {time_str})"

    def get_widget_data(self) -> Optional[Dict]:
        """
        Get formatted data for the tide widget display.

        Returns:
            Dictionary with widget display data or None
        """
        level = self.get_current_level()
        if level is None:
            return None

        trend = self.get_trend()
        arrow = self.get_trend_arrow()
        event_type, event_time, target_level = self.get_next_event()

        next_high = self.get_next_high()
        next_low = self.get_next_low()

        high_time = self._parse_time(next_high.get("time")) if next_high else None
        low_time = self._parse_time(next_low.get("time")) if next_low else None
        high_level = _level_cm(next_high) / 100.0 if next_high else None
        low_level = _level_cm(next_low) / 100.0 if next_low else None

        return {
            "level": level,
            "level_str": f"{int(level * 100)}cm",
            "trend": trend,
            "arrow": arrow,
            "next_event_type": event_type,
            "next_event_time": event_time,
            "next_event_time_str": self.format_time(event_time),
            "target_level": target_level,
            "target_level_str": f"{int(target_level * 100)}cm" if target_level is not None else "",
            "high_time": high_time,
            "high_time_str": self.format_time(high_time),
            "high_level": high_level,
            "high_level_str": f"{int(high_level * 100)}cm" if high_level is not None else "",
            "low_time": low_time,
            "low_time_str": self.format_time(low_time),
            "low_level": low_level,
            "low_level_str": f"{int(low_level * 100)}cm" if low_level is not None else "",
        }


class AuroraData(CachedJsonSource):
    """Handles loading and formatting aurora data from pi-overlay-data."""

    section_key = "aurora"
    path_key = "aurora_file"
    label = "aurora"
    cache_duration = 60

    def _extract(self, raw: Dict) -> Dict:
        """The aurora file wraps its payload in a cache envelope."""
        return raw.get("aurora_data", raw)

    def get_aurora_data(self) -> Optional[Dict]:
        """Load the aurora data. Alias for load()."""
        return self.load()

    def get_bz_arrow(self, bz_status: str) -> str:
        """Get arrow for Bz direction (south is good for aurora)."""
        if "south" in bz_status:
            return "↓"
        elif "north" in bz_status:
            return "↑"
        return "→"

    def get_widget_data(self) -> Optional[Dict]:
        """
        Get formatted data for the aurora widget display.

        Returns:
            Dictionary with widget display data or None
        """
        data = self.get_aurora_data()
        if data is None:
            return None

        kp = data.get("kp", 0)
        bz = data.get("bz", 0)
        bz_status = data.get("bz_status", "unknown")
        speed = data.get("speed", 0)
        storm = data.get("storm", "G0")
        favorable = data.get("favorable", False)

        return {
            "kp": kp,
            "kp_str": f"{kp:.1f}" if isinstance(kp, (int, float)) else "N/A",
            "bz": bz,
            "bz_str": f"{bz:.1f}" if isinstance(bz, (int, float)) else "N/A",
            "bz_status": bz_status,
            "bz_arrow": self.get_bz_arrow(bz_status),
            "speed": speed,
            "speed_str": f"{speed}",
            "storm": storm,
            "favorable": favorable,
        }
