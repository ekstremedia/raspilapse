"""Logging configuration for Raspilapse.

Modules call ``get_logger(name)`` at import time and get a logger back
immediately. CLI entry points then call ``configure_logging(args.config)`` once, after
parsing arguments, which retroactively applies that config to every logger
handed out so far and to every one handed out afterwards. Without that second
step, ``-c/--config`` could never influence logging: by the time argparse has
run, the module-level ``get_logger`` calls have already happened.

All paths resolve against the project root rather than the current working
directory. A relative default meant that running a script from anywhere else
silently fell back to the built-in defaults -- INFO level with the console
handler on, i.e. ten times the volume, duplicated into the journal.
"""

import logging
import logging.handlers
import os
from pathlib import Path
from typing import Dict, Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yml"

# script_name -> logger, so repeated get_logger() calls return the same object.
# The old implementation called logger.handlers.clear() on every call, which
# silently detached handlers another module was still holding.
_registry: Dict[str, logging.Logger] = {}

# Absolute config path -> parsed logging section. Six modules importing this
# used to mean six YAML parses at startup.
_config_cache: Dict[Path, Dict] = {}

# Set by configure(); None means "use DEFAULT_CONFIG_PATH".
_active_config_path: Optional[Path] = None

DEFAULT_LOGGING_CONFIG: Dict = {
    "enabled": True,
    "level": "INFO",
    "log_file": "logs/{script}.log",
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "date_format": "%Y-%m-%d %H:%M:%S",
    # "auto" means: no console handler when systemd is already capturing
    # stdout. See _console_enabled().
    "console": "auto",
    "max_size_mb": 5,
    "backup_count": 2,
}

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _resolve_config_path(config_path: Optional[str]) -> Path:
    """Resolve a config path to an absolute one.

    Relative paths are tried against the current directory first, then against
    the project root, so ``config/config.yml`` works from anywhere.
    """
    if config_path is None:
        return DEFAULT_CONFIG_PATH

    path = Path(config_path)
    if path.is_absolute():
        return path
    if path.exists():
        return path.resolve()
    return (PROJECT_ROOT / path).resolve()


def _load_logging_config(config_path: Path) -> Dict:
    """Read the ``logging:`` section of a config file, with defaults filled in."""
    if config_path in _config_cache:
        return _config_cache[config_path]

    settings = dict(DEFAULT_LOGGING_CONFIG)
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                # `or {}` matters: an empty file parses to None, which used to
                # raise TypeError into a bare except and land on the defaults
                # by accident rather than by design.
                config = yaml.safe_load(f) or {}
            settings.update(config.get("logging") or {})
        except (OSError, yaml.YAMLError):
            pass  # Defaults are a usable fallback; logging isn't up yet to say so.

    _config_cache[config_path] = settings
    return settings


def _console_enabled(setting) -> bool:
    """Decide whether to attach a console handler.

    Under systemd every unit has StandardOutput=journal, so a console handler
    writes each line a second time -- once to logs/, once to the journal. The
    journal had reached 3.6 GB that way.

    JOURNAL_STREAM (not INVOCATION_ID) is the right signal: it is set only when
    stdout/stderr are actually connected to the journal, which is exactly the
    don't-duplicate condition. INVOCATION_ID is set for any unit, including
    ones writing to a file or a tty. It is inherited by children, so
    subprocesses are covered too.
    """
    if isinstance(setting, str) and setting.lower() == "auto":
        return not os.environ.get("JOURNAL_STREAM")
    return bool(setting)


def _resolve_log_file(log_file: str, script_name: str) -> Path:
    """Resolve the log file path.

    An absolute path in the config always wins. A relative one lands in
    RASPILAPSE_LOG_DIR if that is set (the test suite and tmpfs setups use
    this), otherwise under the project root -- never relative to the current
    working directory, which is how stray log files ended up scattered around.
    """
    path = Path(log_file.format(script=script_name))
    if path.is_absolute():
        return path

    log_dir = os.environ.get("RASPILAPSE_LOG_DIR")
    # RASPILAPSE_LOG_DIR replaces the directory, it is not prepended to it.
    return Path(log_dir) / path.name if log_dir else PROJECT_ROOT / path


def _apply(logger: logging.Logger, script_name: str, settings: Dict) -> logging.Logger:
    """Configure ``logger`` from a parsed logging section."""
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    if not settings.get("enabled", True):
        # NullHandler alone does not stop propagation, so a "disabled" logger
        # would still emit through any root handler someone else configured.
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        return logger

    logger.setLevel(_LEVELS.get(str(settings.get("level", "INFO")).upper(), logging.INFO))

    formatter = logging.Formatter(
        settings.get("format", DEFAULT_LOGGING_CONFIG["format"]),
        datefmt=settings.get("date_format", DEFAULT_LOGGING_CONFIG["date_format"]),
    )

    log_file = settings.get("log_file")
    if log_file:
        path = _resolve_log_file(log_file, script_name)
        path.parent.mkdir(parents=True, exist_ok=True)

        max_size_mb = settings.get("max_size_mb", DEFAULT_LOGGING_CONFIG["max_size_mb"])
        if max_size_mb and max_size_mb > 0:
            handler = logging.handlers.RotatingFileHandler(
                path,
                maxBytes=int(max_size_mb * 1024 * 1024),
                backupCount=settings.get("backup_count", DEFAULT_LOGGING_CONFIG["backup_count"]),
            )
        else:
            handler = logging.FileHandler(path)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    if _console_enabled(settings.get("console", DEFAULT_LOGGING_CONFIG["console"])):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        logger.addHandler(console)

    logger.propagate = False
    return logger


def get_logger(script_name: str, config_path: Optional[str] = None) -> logging.Logger:
    """Get a configured logger, creating it on first use.

    Safe to call at module import time. If a later ``configure_logging()`` names a
    different config file, this logger is reconfigured to match.

    Args:
        script_name: Used as the logger name and in the log filename
        config_path: Config to read; defaults to the project's config/config.yml

    Example:
        >>> from logging_config import get_logger
        >>> logger = get_logger("capture_image")
        >>> logger.info("Starting image capture")
    """
    if script_name in _registry:
        return _registry[script_name]

    resolved = _resolve_config_path(config_path) if config_path else _active_or_default()
    logger = _apply(logging.getLogger(script_name), script_name, _load_logging_config(resolved))
    _registry[script_name] = logger
    return logger


def configure_logging(config_path: Optional[str] = None) -> None:
    """Point logging at ``config_path`` and reconfigure existing loggers.

    Call once from ``main()``, straight after parsing arguments.
    """
    global _active_config_path
    _active_config_path = _resolve_config_path(config_path)

    settings = _load_logging_config(_active_config_path)
    for script_name, logger in _registry.items():
        _apply(logger, script_name, settings)


def reset() -> None:
    """Drop all cached state. For tests."""
    global _active_config_path
    for logger in _registry.values():
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
    _registry.clear()
    _config_cache.clear()
    _active_config_path = None


def _active_or_default() -> Path:
    return _active_config_path or DEFAULT_CONFIG_PATH


class LoggerConfig:
    """Backwards-compatible wrapper around the module-level functions."""

    def __init__(self, config_path: str = None, script_name: Optional[str] = None):
        self.config_path = config_path if config_path is not None else str(DEFAULT_CONFIG_PATH)
        self.script_name = script_name or "raspilapse"
        self.config = {"logging": _load_logging_config(_resolve_config_path(self.config_path))}

    def _load_config(self) -> Dict:
        return self.config

    def _get_default_config(self) -> Dict:
        return {"logging": dict(DEFAULT_LOGGING_CONFIG)}

    def _get_log_level(self, level_str: str) -> int:
        return _LEVELS.get(str(level_str).upper(), logging.INFO)

    def setup_logger(self, name: Optional[str] = None) -> logging.Logger:
        script_name = name or self.script_name
        # One name throughout: registering under `script_name` while formatting
        # the filename from self.script_name meant a later configure_logging()
        # silently moved the logger to a different file.
        logger = _apply(logging.getLogger(script_name), script_name, self.config["logging"])
        _registry[script_name] = logger
        return logger
