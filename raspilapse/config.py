"""Shared configuration and CLI helpers.

Six modules had their own ``load_config``, three had a byte-identical
``get_db_path``, and ``parse_time_arg`` / ``format_duration`` existed twice with
subtly different defaults. This is the one copy.

Imports nothing else from the project. logging_config depends on this module,
so the reverse would be a circular import at load time.
"""

import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Dict, Optional, Union

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yml"


def resolve_config_path(config_path: Optional[Union[str, Path]] = None) -> Path:
    """
    Resolve a config path to an absolute one.

    Relative paths are tried against the working directory first, then against
    the project root, so ``config/config.yml`` works from anywhere -- not only
    from the directory the systemd units happen to set.
    """
    if config_path is None:
        return DEFAULT_CONFIG_PATH

    path = Path(config_path)
    if path.is_absolute():
        return path
    if path.exists():
        return path.resolve()
    return (PROJECT_ROOT / path).resolve()


def load_config(config_path: Optional[Union[str, Path]] = None) -> Dict:
    """
    Load and parse a YAML config file.

    Args:
        config_path: Path to the config; defaults to config/config.yml

    Returns:
        The parsed config, or {} for an empty file

    Raises:
        FileNotFoundError: The file does not exist
        yaml.YAMLError: The file is not valid YAML
    """
    path = resolve_config_path(config_path)
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def get_db_path(
    config: Optional[Dict] = None, config_path: Optional[Union[str, Path]] = None
) -> str:
    """
    Get the absolute path to the SQLite database.

    Args:
        config: An already-loaded config; read from disk if omitted
        config_path: Where to read it from, when config is omitted

    Returns:
        Absolute path. Falls back to <project>/data/timelapse.db.
    """
    default = str(PROJECT_ROOT / "data" / "timelapse.db")

    if config is None:
        try:
            config = load_config(config_path)
        except (OSError, yaml.YAMLError):
            return default

    db_path = (config.get("database") or {}).get("path") or default
    if not os.path.isabs(db_path):
        db_path = str(PROJECT_ROOT / db_path)
    return db_path


def parse_time_arg(time_str: str, default: timedelta = timedelta(hours=24)) -> timedelta:
    """
    Parse a duration like '5m', '1h', '24h' or '7d'.

    A bare number is read as hours. A leading '-' is ignored, so both '24h' and
    '-24h' mean "the last 24 hours".

    Args:
        time_str: The duration string
        default: Returned for empty input. Callers differ: db_stats wants 1h,
            db_graphs wants 24h, so this is deliberately explicit.

    Returns:
        The parsed timedelta

    Raises:
        ValueError: The string is not a recognised duration
    """
    if not time_str:
        return default

    text = time_str.lower().strip().lstrip("-")
    units = {"m": "minutes", "h": "hours", "d": "days"}

    try:
        if text and text[-1] in units:
            return timedelta(**{units[text[-1]]: int(text[:-1])})
        return timedelta(hours=int(text))
    except ValueError:
        raise ValueError(f"Invalid time format: {time_str}. Use e.g. 5m, 1h, 24h, 7d") from None


def parse_time_arg_or_exit(time_str: str, default: timedelta = timedelta(hours=24)) -> timedelta:
    """parse_time_arg for CLI use: print the error and exit rather than raise."""
    try:
        return parse_time_arg(time_str, default)
    except ValueError as e:
        print(e)
        sys.exit(1)


def format_duration(seconds: float, precision: int = 1) -> str:
    """
    Format a number of seconds as a human-readable duration.

    Args:
        seconds: Duration in seconds
        precision: Decimal places. db_stats uses 1, db_graphs uses 0, and
            changing either silently would alter both tools' output.

    Returns:
        e.g. "45.0s", "3.5m", "2.0h", "1.5d"
    """
    for limit, divisor, suffix in ((60, 1, "s"), (3600, 60, "m"), (86400, 3600, "h")):
        if seconds < limit:
            return f"{seconds / divisor:.{precision}f}{suffix}"
    return f"{seconds / 86400:.{precision}f}d"
