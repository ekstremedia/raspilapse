"""Logging configuration for Raspilapse."""

import logging
import logging.handlers
import os
from pathlib import Path
from typing import Dict, Optional
import yaml


class LoggerConfig:
    """Configure and setup logging for Raspilapse scripts."""

    def __init__(self, config_path: str = "config/config.yml", script_name: Optional[str] = None):
        """
        Initialize logger configuration.

        Args:
            config_path: Path to YAML configuration file
            script_name: Name of the script (used for log file naming)
        """
        self.config_path = config_path
        self.script_name = script_name or "raspilapse"
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        """Load configuration from YAML file."""
        config_file = Path(self.config_path)
        if not config_file.exists():
            # Return default config if file not found
            return self._get_default_config()

        try:
            with open(config_file, "r") as f:
                config = yaml.safe_load(f)
                # Ensure logging section exists
                if "logging" not in config:
                    config["logging"] = self._get_default_config()["logging"]
                return config
        except Exception:
            return self._get_default_config()

    def _get_default_config(self) -> Dict:
        """Get default logging configuration."""
        return {
            "logging": {
                "enabled": True,
                "level": "INFO",
                "log_file": "logs/{script}.log",
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "date_format": "%Y-%m-%d %H:%M:%S",
                "console": True,
                "max_size_mb": 10,
                "backup_count": 5,
            }
        }

    def setup_logger(self, name: Optional[str] = None) -> logging.Logger:
        """
        Set up and configure logger.

        Args:
            name: Logger name (defaults to script name)

        Returns:
            Configured logger instance
        """
        log_config = self.config["logging"]

        # Return basic logger if logging is disabled
        if not log_config.get("enabled", True):
            logger = logging.getLogger(name or self.script_name)
            logger.addHandler(logging.NullHandler())
            return logger

        # Create logger
        logger = logging.getLogger(name or self.script_name)
        logger.setLevel(self._get_log_level(log_config.get("level", "INFO")))

        # Remove existing handlers
        logger.handlers.clear()

        # Create formatter
        formatter = logging.Formatter(
            log_config.get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"),
            datefmt=log_config.get("date_format", "%Y-%m-%d %H:%M:%S"),
        )

        # Add file handler if log_file is specified
        log_file_path = log_config.get("log_file")
        if log_file_path:
            log_file_path = log_file_path.format(script=self.script_name)
            log_file = Path(log_file_path)

            # Create log directory if it doesn't exist
            log_file.parent.mkdir(parents=True, exist_ok=True)

            # Use rotating file handler if size limit specified
            max_size_mb = log_config.get("max_size_mb", 10)
            backup_count = log_config.get("backup_count", 5)

            if max_size_mb > 0:
                file_handler = logging.handlers.RotatingFileHandler(
                    log_file,
                    maxBytes=max_size_mb * 1024 * 1024,  # Convert MB to bytes
                    backupCount=backup_count,
                )
            else:
                file_handler = logging.FileHandler(log_file)

            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

        # Add console handler if enabled
        if log_config.get("console", True):
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)

        # Prevent propagation to root logger
        logger.propagate = False

        return logger

    def _get_log_level(self, level_str: str) -> int:
        """
        Convert string log level to logging constant.

        Args:
            level_str: Log level as string (DEBUG, INFO, WARNING, ERROR, CRITICAL)

        Returns:
            Logging level constant
        """
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        return level_map.get(level_str.upper(), logging.INFO)


def get_logger(script_name: str, config_path: str = "config/config.yml") -> logging.Logger:
    """
    Convenience function to get a configured logger.

    Args:
        script_name: Name of the script (used for log file naming)
        config_path: Path to configuration file

    Returns:
        Configured logger instance

    Example:
        >>> from logging_config import get_logger
        >>> logger = get_logger('capture_image')
        >>> logger.info('Starting image capture')
    """
    logger_config = LoggerConfig(config_path, script_name)
    return logger_config.setup_logger()
