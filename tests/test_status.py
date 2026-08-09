"""Tests for status display module."""

import json
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml

from raspilapse.cli.status import Colors, StatusDisplay


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


def test_wb_trim_state_path_matches_daemon():
    """status.py duplicates the trim path; this is what stops it drifting.

    The duplication is deliberate (importing the daemon costs picamera2), so
    the cost of that choice has to be paid by a test that fails the moment
    the daemon moves the file and this command starts reading nothing.
    """
    from raspilapse.cli.status import WB_TRIM_STATE
    from raspilapse.daemon import AdaptiveTimelapse

    assert WB_TRIM_STATE == AdaptiveTimelapse.WB_TRIM_STATE


def test_read_wb_trim_missing_file(temp_config, tmp_path):
    """A camera that has never run with feedback on reports nothing, not an error."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    with patch("raspilapse.cli.status.WB_TRIM_STATE", tmp_path / "absent.json"):
        assert status._read_wb_trim() is None


def test_read_wb_trim_malformed(temp_config, tmp_path):
    """A half-written or hand-edited trim file is a fresh start, not a crash."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    state = tmp_path / "wb_trim.json"
    state.write_text('{"wb_trim_r": 0.9')  # truncated mid-write

    with patch("raspilapse.cli.status.WB_TRIM_STATE", state):
        assert status._read_wb_trim() is None


def test_print_adaptive_status_reports_fusion(temp_config, capsys):
    """The fusion bracket and the brightness setpoint both reach the display."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    status.config["adaptive_timelapse"].update(
        {
            "dynamic_range": {
                "method": "fusion",
                "fusion": {"ev_spread": 3.5},
                "tone_map": {"enabled": True, "strength": 0.7},
            },
            "brightness_target": {"base": 132, "overcast_boost": 15, "max_target": 152},
        }
    )

    # cv2 is absent in CI, and DynamicRange would honestly downgrade to 'off'.
    # Pretend it is installed so this test covers the fusion branch either way.
    with patch("importlib.util.find_spec", return_value=object()):
        status.print_adaptive_status()

    captured = capsys.readouterr()
    assert "ADAPTIVE EXPOSURE" in captured.out
    assert "fusion+tm" in captured.out
    assert "3 brackets" in captured.out
    assert "3.5 EV" in captured.out
    assert "11.3x" in captured.out  # 2^3.5, the ratio that means something
    assert "strength 0.70" in captured.out
    assert "132 base" in captured.out


def test_print_adaptive_status_reports_degraded_method(temp_config, capsys):
    """A method that could not load is shown as what runs, plus what was asked for."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    status.config["adaptive_timelapse"]["dynamic_range"] = {"method": "fusion"}

    # find_spec returning None is exactly how DynamicRange detects missing cv2.
    with patch("importlib.util.find_spec", return_value=None):
        status.print_adaptive_status()

    captured = capsys.readouterr()
    assert "config asked for fusion" in captured.out


def test_print_adaptive_status_disabled(temp_config, capsys):
    """Nothing about the pipeline is claimed when adaptive capture is off."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)
    status.config["adaptive_timelapse"]["enabled"] = False

    status.print_adaptive_status()
    captured = capsys.readouterr()
    assert "Disabled" in captured.out
    assert "Fusion" not in captured.out


def test_print_white_balance_shows_effective_gains(temp_config, capsys, tmp_path):
    """The learned trim is applied to the anchor, since that product is the render."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    status.config["adaptive_timelapse"]["day_mode"] = {
        "fixed_colour_gains": [2.26, 1.73],
        "wb_feedback": {"enabled": True},
    }

    state = tmp_path / "wb_trim.json"
    state.write_text(json.dumps({"wb_trim_r": 0.5, "wb_trim_b": 2.0}))

    with patch("raspilapse.cli.status.WB_TRIM_STATE", state):
        status.print_white_balance_status()

    captured = capsys.readouterr()
    assert "WHITE BALANCE" in captured.out
    assert "R 2.26" in captured.out
    assert "x0.500" in captured.out
    assert "R 1.13" in captured.out  # 2.26 * 0.5
    assert "B 3.46" in captured.out  # 1.73 * 2.0


def test_print_white_balance_flags_pinned_trim(temp_config, capsys, tmp_path):
    """A trim sitting on its clamp is the anchor being wrong, and says so."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    status.config["adaptive_timelapse"]["day_mode"] = {
        "fixed_colour_gains": [2.6, 1.73],
        "wb_feedback": {"enabled": True, "max_trim": 0.12},
    }

    state = tmp_path / "wb_trim.json"
    state.write_text(json.dumps({"wb_trim_r": 0.88, "wb_trim_b": 0.95}))

    with patch("raspilapse.cli.status.WB_TRIM_STATE", state):
        status.print_white_balance_status()

    captured = capsys.readouterr()
    assert "pinned" in captured.out
    assert "R trim" in captured.out or "R/" in captured.out
    assert "B trim" not in captured.out  # 0.95 is well inside the clamp


def test_print_white_balance_feedback_off(temp_config, capsys):
    """With feedback off there is no trim to report and none is invented."""
    config_path, _ = temp_config
    status = StatusDisplay(config_path)

    status.config["adaptive_timelapse"]["day_mode"] = {
        "fixed_colour_gains": [2.26, 1.73],
        "wb_feedback": {"enabled": False},
    }

    status.print_white_balance_status()
    captured = capsys.readouterr()
    assert "off" in captured.out
    assert "Learned" not in captured.out
    assert "Effective" not in captured.out


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
    from raspilapse.cli.status import main

    assert callable(main)


def test_main_with_config(temp_config):
    """Test main function with custom config."""
    config_path, _ = temp_config
    from raspilapse.cli.status import main

    with patch("sys.argv", ["status.py", "-c", config_path]):
        with patch.object(StatusDisplay, "display") as mock_display:
            main()
            mock_display.assert_called_once()


def test_main_keyboard_interrupt(temp_config):
    """Test main function handles keyboard interrupt."""
    config_path, _ = temp_config
    from raspilapse.cli.status import main

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
    from raspilapse.cli.status import main

    with patch("sys.argv", ["status.py", "-c", config_path]):
        with patch.object(StatusDisplay, "display") as mock_display:
            mock_display.side_effect = Exception("Test error")
            # Should exit with 1
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
