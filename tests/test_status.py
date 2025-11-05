"""Tests for status display module."""

import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import tempfile
import shutil
from datetime import datetime, timedelta
import yaml

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from status import StatusDisplay, Colors
import pytest


@pytest.fixture
def temp_config():
    """Create temporary config file for testing."""
    temp_dir = tempfile.mkdtemp()
    config_path = Path(temp_dir) / "config.yml"

    config = {
        "camera": {"resolution": {"width": 1920, "height": 1080}},
        "output": {
            "directory": str(Path(temp_dir) / "images"),
            "organize_by_date": True,
            "date_format": "%Y/%m/%d",
            "symlink_latest": {"enabled": True, "path": "/tmp/latest.jpg"},
        },
        "adaptive_timelapse": {
            "enabled": True,
            "interval": 30,
            "light_thresholds": {"night": 10, "day": 100},
            "night_mode": {"max_exposure_time": 20.0, "analogue_gain": 6},
        },
        "overlay": {
            "enabled": True,
            "position": "top-bar",
            "camera_name": "Test Camera",
            "font": {"family": "default", "size_ratio": 0.02},
            "background": {"enabled": True, "color": [0, 0, 0, 128]},
            "content": {
                "camera_settings": {"enabled": True},
                "debug": {"enabled": False},
            },
        },
        "system": {"save_metadata": True},
    }

    with open(config_path, "w") as f:
        yaml.dump(config, f)

    yield str(config_path), temp_dir

    # Cleanup
    shutil.rmtree(temp_dir)


def test_colors_class():
    """Test Colors class has required attributes."""
    assert hasattr(Colors, "RED")
    assert hasattr(Colors, "GREEN")
    assert hasattr(Colors, "YELLOW")
    assert hasattr(Colors, "BOLD")
    assert hasattr(Colors, "RESET")
    assert isinstance(Colors.RED, str)


def test_status_display_init(temp_config):
    """Test StatusDisplay initialization."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)
    assert status.config is not None
    assert status.config_path == config_path


def test_status_display_missing_config():
    """Test StatusDisplay with missing config file."""
    with pytest.raises(SystemExit):
        StatusDisplay("/nonexistent/config.yml")


def test_get_service_status_running(temp_config):
    """Test getting service status when running."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    with patch("subprocess.run") as mock_run:
        # Mock is-active returns "active"
        mock_run.return_value.stdout = "active"
        mock_run.return_value.returncode = 0

        # Mock status output
        def side_effect(*args, **kwargs):
            result = Mock()
            if "is-active" in args[0]:
                result.stdout = "active"
            else:
                result.stdout = "Active: active (running) since Mon 2025-11-05"
            result.returncode = 0
            return result

        mock_run.side_effect = side_effect

        state, status_str, desc = status._get_service_status()
        assert state == "active"


def test_get_service_status_stopped(temp_config):
    """Test getting service status when stopped."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    with patch("subprocess.run") as mock_run:

        def side_effect(*args, **kwargs):
            result = Mock()
            if "is-active" in args[0]:
                result.stdout = "inactive"
            else:
                result.stdout = "Active: inactive (dead)"
            result.returncode = 3
            return result

        mock_run.side_effect = side_effect

        state, status_str, desc = status._get_service_status()
        assert "inactive" in state.lower() or "stopped" in state.lower()


def test_get_recent_captures_empty(temp_config):
    """Test getting recent captures when no images exist."""
    config_path, temp_dir = temp_config
    status = StatusDisplay(config_path)

    # Output directory doesn't exist yet
    captures = status._get_recent_captures(limit=5)
    assert captures == []


def test_get_recent_captures_with_images(temp_config):
    """Test getting recent captures with images."""
    config_path, temp_dir = temp_config
    status = StatusDisplay(config_path)

    # Create output directory and fake images
    output_dir = Path(temp_dir) / "images" / "2025" / "11" / "05"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create test images
    for i in range(3):
        img_path = output_dir / f"test_{i}.jpg"
        img_path.write_text("fake image data")

    captures = status._get_recent_captures(limit=5)
    assert len(captures) == 3
    assert all(len(cap) == 3 for cap in captures)  # (path, datetime, size)


def test_format_size(temp_config):
    """Test file size formatting."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    assert status._format_size(100) == "100.0 B"
    assert status._format_size(1024) == "1.0 KB"
    assert status._format_size(1024 * 1024) == "1.0 MB"
    assert status._format_size(1024 * 1024 * 1024) == "1.0 GB"


def test_format_time_ago(temp_config):
    """Test time ago formatting."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    now = datetime.now()

    # Seconds ago
    dt = now - timedelta(seconds=30)
    result = status._format_time_ago(dt)
    assert "30s ago" == result or "29s ago" == result  # Account for timing

    # Minutes ago
    dt = now - timedelta(minutes=5)
    result = status._format_time_ago(dt)
    assert "5m ago" == result or "4m ago" == result

    # Hours ago
    dt = now - timedelta(hours=3)
    result = status._format_time_ago(dt)
    assert "3h ago" == result or "2h ago" == result

    # Days ago
    dt = now - timedelta(days=2)
    result = status._format_time_ago(dt)
    assert "2d ago" == result or "1d ago" == result


def test_print_header(temp_config, capsys):
    """Test printing header."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    status.print_header()
    captured = capsys.readouterr()
    assert "RASPILAPSE STATUS" in captured.out
    assert "🎥" in captured.out


def test_print_configuration(temp_config, capsys):
    """Test printing configuration."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    status.print_configuration()
    captured = capsys.readouterr()
    assert "CONFIGURATION" in captured.out
    assert "1920x1080" in captured.out
    assert "30s" in captured.out


def test_print_overlay_status_enabled(temp_config, capsys):
    """Test printing overlay status when enabled."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    status.print_overlay_status()
    captured = capsys.readouterr()
    assert "OVERLAY" in captured.out
    assert "Enabled" in captured.out
    assert "Test Camera" in captured.out


def test_print_overlay_status_disabled(temp_config, capsys):
    """Test printing overlay status when disabled."""
    config_path, temp_dir = temp_config
    status = StatusDisplay(config_path)

    # Disable overlay
    status.config["overlay"]["enabled"] = False

    status.print_overlay_status()
    captured = capsys.readouterr()
    assert "OVERLAY" in captured.out
    assert "Disabled" in captured.out


def test_print_recent_captures_empty(temp_config, capsys):
    """Test printing recent captures when none exist."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    status.print_recent_captures()
    captured = capsys.readouterr()
    assert "RECENT CAPTURES" in captured.out
    assert "No captures found" in captured.out


def test_print_symlink_status_not_enabled(temp_config, capsys):
    """Test printing symlink status when not enabled."""
    config_path, temp_dir = temp_config
    status = StatusDisplay(config_path)

    # Disable symlink
    status.config["output"]["symlink_latest"]["enabled"] = False

    status.print_symlink_status()
    captured = capsys.readouterr()
    # Should print nothing when disabled
    assert "SYMLINK" not in captured.out


def test_print_symlink_status_enabled(temp_config, capsys):
    """Test printing symlink status when enabled."""
    config_path, temp_dir = temp_config
    status = StatusDisplay(config_path)

    # Symlink doesn't exist yet
    status.print_symlink_status()
    captured = capsys.readouterr()
    assert "SYMLINK" in captured.out


def test_print_footer(temp_config, capsys):
    """Test printing footer."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    status.print_footer()
    captured = capsys.readouterr()
    assert "Generated at" in captured.out
    # Check for date format YYYY-MM-DD
    import re

    assert re.search(r"\d{4}-\d{2}-\d{2}", captured.out)


def test_display_full_status(temp_config, capsys):
    """Test displaying full status output."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    with patch.object(status, "_get_service_status") as mock_service:
        mock_service.return_value = ("active", "running", "Service is running")

        status.display()
        captured = capsys.readouterr()

        # Check all sections are present
        assert "RASPILAPSE STATUS" in captured.out
        assert "SERVICE STATUS" in captured.out
        assert "CONFIGURATION" in captured.out
        assert "OVERLAY" in captured.out
        assert "RECENT CAPTURES" in captured.out
        assert "Generated at" in captured.out


def test_main_function():
    """Test main function can be imported."""
    from status import main

    assert callable(main)


def test_main_with_config(temp_config):
    """Test main function with custom config."""
    config_path, _ = temp_config
    from status import main

    with patch("sys.argv", ["status.py", "-c", config_path]):
        with patch.object(StatusDisplay, "display") as mock_display:
            main()
            mock_display.assert_called_once()


def test_main_keyboard_interrupt(temp_config):
    """Test main function handles keyboard interrupt."""
    config_path, _ = temp_config
    from status import main

    with patch("sys.argv", ["status.py", "-c", config_path]):
        with patch.object(StatusDisplay, "display") as mock_display:
            mock_display.side_effect = KeyboardInterrupt()
            # Should exit with 0
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


def test_main_with_error(temp_config):
    """Test main function handles errors."""
    config_path, _ = temp_config
    from status import main

    with patch("sys.argv", ["status.py", "-c", config_path]):
        with patch.object(StatusDisplay, "display") as mock_display:
            mock_display.side_effect = Exception("Test error")
            # Should exit with 1
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
