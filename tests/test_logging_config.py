"""Tests for logging_config module."""

import os
import tempfile
import logging
from pathlib import Path
import yaml
import pytest
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.logging_config import LoggerConfig, get_logger


@pytest.fixture
def test_config_with_logging():
    """Create a temporary test configuration file with logging settings."""
    config_data = {
        'logging': {
            'enabled': True,
            'level': 'DEBUG',
            'log_file': 'logs/{script}.log',
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            'date_format': '%Y-%m-%d %H:%M:%S',
            'console': True,
            'max_size_mb': 5,
            'backup_count': 3
        }
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
        yaml.dump(config_data, f)
        config_path = f.name

    yield config_path

    # Cleanup
    os.unlink(config_path)


@pytest.fixture
def test_config_logging_disabled():
    """Create a temporary test configuration file with logging disabled."""
    config_data = {
        'logging': {
            'enabled': False,
            'level': 'INFO',
            'log_file': 'logs/test.log',
            'format': '%(message)s',
            'date_format': '%Y-%m-%d',
            'console': False,
            'max_size_mb': 10,
            'backup_count': 5
        }
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
        yaml.dump(config_data, f)
        config_path = f.name

    yield config_path

    # Cleanup
    os.unlink(config_path)


@pytest.fixture
def temp_log_dir():
    """Create a temporary directory for log files."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    # Cleanup
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestLoggerConfig:
    """Tests for LoggerConfig class."""

    def test_load_config_success(self, test_config_with_logging):
        """Test successful configuration loading."""
        logger_config = LoggerConfig(test_config_with_logging, 'test_script')
        assert logger_config.config is not None
        assert 'logging' in logger_config.config
        assert logger_config.script_name == 'test_script'

    def test_load_config_missing_file(self):
        """Test that missing config file uses defaults."""
        logger_config = LoggerConfig('nonexistent.yml', 'test_script')
        assert logger_config.config is not None
        assert 'logging' in logger_config.config
        # Should use defaults
        assert logger_config.config['logging']['enabled'] is True

    def test_default_config(self):
        """Test default configuration generation."""
        logger_config = LoggerConfig('nonexistent.yml')
        default_config = logger_config._get_default_config()

        assert 'logging' in default_config
        assert default_config['logging']['enabled'] is True
        assert default_config['logging']['level'] == 'INFO'
        assert default_config['logging']['console'] is True

    def test_setup_logger_enabled(self, test_config_with_logging, temp_log_dir):
        """Test logger setup when logging is enabled."""
        # Modify config to use temp directory
        with open(test_config_with_logging, 'r') as f:
            config_data = yaml.safe_load(f)
        config_data['logging']['log_file'] = os.path.join(temp_log_dir, '{script}.log')

        with open(test_config_with_logging, 'w') as f:
            yaml.dump(config_data, f)

        logger_config = LoggerConfig(test_config_with_logging, 'test_script')
        logger = logger_config.setup_logger()

        assert logger is not None
        assert isinstance(logger, logging.Logger)
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) > 0

    def test_setup_logger_disabled(self, test_config_logging_disabled):
        """Test logger setup when logging is disabled."""
        logger_config = LoggerConfig(test_config_logging_disabled, 'test_script')
        logger = logger_config.setup_logger()

        assert logger is not None
        assert isinstance(logger, logging.Logger)
        # Should have NullHandler when disabled
        assert any(isinstance(h, logging.NullHandler) for h in logger.handlers)

    def test_get_log_level(self, test_config_with_logging):
        """Test log level conversion."""
        logger_config = LoggerConfig(test_config_with_logging)

        assert logger_config._get_log_level('DEBUG') == logging.DEBUG
        assert logger_config._get_log_level('INFO') == logging.INFO
        assert logger_config._get_log_level('WARNING') == logging.WARNING
        assert logger_config._get_log_level('ERROR') == logging.ERROR
        assert logger_config._get_log_level('CRITICAL') == logging.CRITICAL

        # Test case insensitivity
        assert logger_config._get_log_level('debug') == logging.DEBUG
        assert logger_config._get_log_level('info') == logging.INFO

        # Test invalid level defaults to INFO
        assert logger_config._get_log_level('INVALID') == logging.INFO

    def test_log_file_creation(self, test_config_with_logging, temp_log_dir):
        """Test that log files are created."""
        # Modify config to use temp directory
        with open(test_config_with_logging, 'r') as f:
            config_data = yaml.safe_load(f)
        config_data['logging']['log_file'] = os.path.join(temp_log_dir, 'test_{script}.log')

        with open(test_config_with_logging, 'w') as f:
            yaml.dump(config_data, f)

        logger_config = LoggerConfig(test_config_with_logging, 'my_script')
        logger = logger_config.setup_logger()

        # Log a message
        logger.info("Test message")

        # Verify log file was created
        expected_log_file = os.path.join(temp_log_dir, 'test_my_script.log')
        assert os.path.exists(expected_log_file)

        # Verify content
        with open(expected_log_file, 'r') as f:
            content = f.read()
            assert 'Test message' in content

    def test_console_handler(self, test_config_with_logging):
        """Test console handler is added when enabled."""
        logger_config = LoggerConfig(test_config_with_logging, 'test_script')
        logger = logger_config.setup_logger()

        # Check for StreamHandler (console handler)
        has_console_handler = any(
            isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
            for h in logger.handlers
        )
        assert has_console_handler

    def test_rotating_file_handler(self, test_config_with_logging, temp_log_dir):
        """Test that rotating file handler is used when max_size_mb > 0."""
        # Modify config to use temp directory
        with open(test_config_with_logging, 'r') as f:
            config_data = yaml.safe_load(f)
        config_data['logging']['log_file'] = os.path.join(temp_log_dir, '{script}.log')
        config_data['logging']['max_size_mb'] = 1

        with open(test_config_with_logging, 'w') as f:
            yaml.dump(config_data, f)

        logger_config = LoggerConfig(test_config_with_logging, 'test_script')
        logger = logger_config.setup_logger()

        # Check for RotatingFileHandler
        has_rotating_handler = any(
            isinstance(h, logging.handlers.RotatingFileHandler)
            for h in logger.handlers
        )
        assert has_rotating_handler

    def test_logger_propagation(self, test_config_with_logging):
        """Test that logger propagation is disabled."""
        logger_config = LoggerConfig(test_config_with_logging, 'test_script')
        logger = logger_config.setup_logger()

        assert logger.propagate is False

    def test_custom_logger_name(self, test_config_with_logging):
        """Test setup with custom logger name."""
        logger_config = LoggerConfig(test_config_with_logging, 'script_name')
        logger = logger_config.setup_logger('custom_logger')

        assert logger.name == 'custom_logger'


class TestGetLogger:
    """Tests for get_logger convenience function."""

    def test_get_logger_basic(self, test_config_with_logging, temp_log_dir):
        """Test get_logger convenience function."""
        # Modify config to use temp directory
        with open(test_config_with_logging, 'r') as f:
            config_data = yaml.safe_load(f)
        config_data['logging']['log_file'] = os.path.join(temp_log_dir, '{script}.log')

        with open(test_config_with_logging, 'w') as f:
            yaml.dump(config_data, f)

        logger = get_logger('my_script', test_config_with_logging)

        assert logger is not None
        assert isinstance(logger, logging.Logger)
        assert logger.name == 'my_script'

    def test_get_logger_uses_script_name(self, test_config_with_logging, temp_log_dir):
        """Test that get_logger uses script name correctly."""
        # Modify config to use temp directory
        with open(test_config_with_logging, 'r') as f:
            config_data = yaml.safe_load(f)
        config_data['logging']['log_file'] = os.path.join(temp_log_dir, '{script}.log')

        with open(test_config_with_logging, 'w') as f:
            yaml.dump(config_data, f)

        logger = get_logger('capture_image', test_config_with_logging)
        logger.info("Test message")

        # Verify log file has correct name
        expected_log = os.path.join(temp_log_dir, 'capture_image.log')
        assert os.path.exists(expected_log)


class TestLoggingIntegration:
    """Integration tests for logging."""

    def test_multiple_loggers(self, test_config_with_logging, temp_log_dir):
        """Test creating multiple loggers with different names."""
        # Modify config to use temp directory
        with open(test_config_with_logging, 'r') as f:
            config_data = yaml.safe_load(f)
        config_data['logging']['log_file'] = os.path.join(temp_log_dir, '{script}.log')

        with open(test_config_with_logging, 'w') as f:
            yaml.dump(config_data, f)

        logger1 = get_logger('script1', test_config_with_logging)
        logger2 = get_logger('script2', test_config_with_logging)

        logger1.info("Message from script1")
        logger2.info("Message from script2")

        # Verify separate log files
        log1_path = os.path.join(temp_log_dir, 'script1.log')
        log2_path = os.path.join(temp_log_dir, 'script2.log')

        assert os.path.exists(log1_path)
        assert os.path.exists(log2_path)

        with open(log1_path, 'r') as f:
            assert 'Message from script1' in f.read()

        with open(log2_path, 'r') as f:
            assert 'Message from script2' in f.read()

    def test_log_levels_filtering(self, test_config_with_logging, temp_log_dir):
        """Test that log levels filter messages correctly."""
        # Modify config to INFO level
        with open(test_config_with_logging, 'r') as f:
            config_data = yaml.safe_load(f)
        config_data['logging']['level'] = 'WARNING'
        config_data['logging']['log_file'] = os.path.join(temp_log_dir, '{script}.log')

        with open(test_config_with_logging, 'w') as f:
            yaml.dump(config_data, f)

        logger = get_logger('test_script', test_config_with_logging)

        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")

        # Read log file
        log_path = os.path.join(temp_log_dir, 'test_script.log')
        with open(log_path, 'r') as f:
            content = f.read()

        # Only WARNING and ERROR should be logged
        assert 'Debug message' not in content
        assert 'Info message' not in content
        assert 'Warning message' in content
        assert 'Error message' in content
