"""Tests for system monitoring module."""

import sys
from pathlib import Path
from unittest.mock import Mock, patch, mock_open
import subprocess

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from system_monitor import SystemMonitor


@pytest.fixture
def monitor():
    """Create a SystemMonitor instance."""
    return SystemMonitor()


class TestSystemMonitor:
    """Test suite for SystemMonitor class."""

    def test_initialization(self, monitor):
        """Test SystemMonitor initializes correctly."""
        assert monitor is not None
        assert isinstance(monitor, SystemMonitor)

    @patch("subprocess.run")
    def test_get_cpu_temperature_vcgencmd_success(self, mock_run, monitor):
        """Test CPU temperature retrieval via vcgencmd."""
        # Mock successful vcgencmd response
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "temp=42.8'C\n"
        mock_run.return_value = mock_result

        temp = monitor.get_cpu_temperature()

        assert temp == 42.8
        mock_run.assert_called_once()

    @patch("subprocess.run")
    @patch("builtins.open", new_callable=mock_open, read_data="42800\n")
    def test_get_cpu_temperature_thermal_zone_fallback(self, mock_file, mock_run, monitor):
        """Test CPU temperature fallback to thermal_zone."""
        # Mock vcgencmd failure
        mock_run.side_effect = Exception("vcgencmd not found")

        temp = monitor.get_cpu_temperature()

        assert temp == 42.8
        mock_file.assert_called_once_with("/sys/class/thermal/thermal_zone0/temp", "r")

    @patch("subprocess.run")
    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_get_cpu_temperature_failure(self, mock_file, mock_run, monitor):
        """Test CPU temperature when both methods fail."""
        # Mock both methods failing
        mock_run.side_effect = Exception("vcgencmd not found")

        temp = monitor.get_cpu_temperature()

        assert temp is None

    @patch("os.statvfs")
    def test_get_disk_space_success(self, mock_statvfs, monitor):
        """Test disk space retrieval."""
        # Mock statvfs result
        mock_stat = Mock()
        mock_stat.f_blocks = 30_000_000  # Total blocks
        mock_stat.f_bavail = 20_000_000  # Available blocks
        mock_stat.f_frsize = 4096  # Block size
        mock_statvfs.return_value = mock_stat

        disk = monitor.get_disk_space("/")

        assert disk is not None
        assert "total" in disk
        assert "used" in disk
        assert "free" in disk
        assert "percent" in disk
        assert disk["free"] < disk["total"]
        assert 0 <= disk["percent"] <= 100

    @patch("os.statvfs")
    def test_get_disk_space_failure(self, mock_statvfs, monitor):
        """Test disk space when statvfs fails."""
        mock_statvfs.side_effect = OSError("Permission denied")

        disk = monitor.get_disk_space("/")

        assert disk is None

    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data="MemTotal:        8000000 kB\nMemAvailable:    4000000 kB\n",
    )
    def test_get_memory_usage_success(self, mock_file, monitor):
        """Test memory usage retrieval."""
        memory = monitor.get_memory_usage()

        assert memory is not None
        assert "total" in memory
        assert "used" in memory
        assert "free" in memory
        assert "percent" in memory
        assert memory["used"] < memory["total"]
        assert 0 <= memory["percent"] <= 100

    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_get_memory_usage_failure(self, mock_file, monitor):
        """Test memory usage when /proc/meminfo is unavailable."""
        memory = monitor.get_memory_usage()

        assert memory is None

    @patch("os.getloadavg")
    def test_get_cpu_load_success(self, mock_loadavg, monitor):
        """Test CPU load average retrieval."""
        mock_loadavg.return_value = (0.52, 0.48, 0.45)

        load = monitor.get_cpu_load()

        assert load is not None
        assert "1min" in load
        assert "5min" in load
        assert "15min" in load
        assert load["1min"] == 0.52
        assert load["5min"] == 0.48
        assert load["15min"] == 0.45

    @patch("os.getloadavg")
    def test_get_cpu_load_failure(self, mock_loadavg, monitor):
        """Test CPU load when getloadavg fails."""
        mock_loadavg.side_effect = OSError("Not supported")

        load = monitor.get_cpu_load()

        assert load is None

    @patch("builtins.open", new_callable=mock_open, read_data="123456.78 234567.89\n")
    def test_get_uptime_success(self, mock_file, monitor):
        """Test uptime retrieval."""
        uptime = monitor.get_uptime()

        assert uptime is not None
        assert uptime == 123456.78

    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_get_uptime_failure(self, mock_file, monitor):
        """Test uptime when /proc/uptime is unavailable."""
        uptime = monitor.get_uptime()

        assert uptime is None

    @patch.object(SystemMonitor, "get_cpu_temperature")
    @patch.object(SystemMonitor, "get_disk_space")
    @patch.object(SystemMonitor, "get_memory_usage")
    @patch.object(SystemMonitor, "get_cpu_load")
    @patch.object(SystemMonitor, "get_uptime")
    def test_get_all_metrics(
        self,
        mock_uptime,
        mock_load,
        mock_memory,
        mock_disk,
        mock_temp,
        monitor,
    ):
        """Test getting all metrics at once."""
        # Mock all methods
        mock_temp.return_value = 42.5
        mock_disk.return_value = {"total": 100.0, "used": 30.0, "free": 70.0, "percent": 30.0}
        mock_memory.return_value = {
            "total": 8000.0,
            "used": 3000.0,
            "free": 5000.0,
            "percent": 37.5,
        }
        mock_load.return_value = {"1min": 0.5, "5min": 0.4, "15min": 0.3}
        mock_uptime.return_value = 86400.0

        metrics = monitor.get_all_metrics("/var/www/html")

        assert "cpu_temp" in metrics
        assert "disk" in metrics
        assert "memory" in metrics
        assert "load" in metrics
        assert "uptime" in metrics
        assert metrics["cpu_temp"] == 42.5
        assert metrics["disk"]["free"] == 70.0
        assert metrics["memory"]["percent"] == 37.5


class TestFormatters:
    """Test suite for SystemMonitor formatting methods."""

    def test_format_cpu_temp_valid(self):
        """Test CPU temperature formatting with valid value."""
        formatted = SystemMonitor.format_cpu_temp(42.5)
        assert formatted == "42.5°C"

    def test_format_cpu_temp_none(self):
        """Test CPU temperature formatting with None."""
        formatted = SystemMonitor.format_cpu_temp(None)
        assert formatted == "N/A"

    def test_format_disk_space_valid(self):
        """Test disk space formatting with valid dict."""
        disk = {"total": 116.7, "used": 36.8, "free": 79.9, "percent": 31.5}
        formatted = SystemMonitor.format_disk_space(disk)
        assert "79.9 GB free" in formatted
        assert "31% used" in formatted or "32% used" in formatted

    def test_format_disk_space_none(self):
        """Test disk space formatting with None."""
        formatted = SystemMonitor.format_disk_space(None)
        assert formatted == "N/A"

    def test_format_memory_valid(self):
        """Test memory formatting with valid dict."""
        memory = {"total": 4000.0, "used": 1200.0, "free": 2800.0, "percent": 30.0}
        formatted = SystemMonitor.format_memory(memory)
        assert "1.2 GB" in formatted
        assert "3.9 GB" in formatted or "4.0 GB" in formatted
        assert "30%" in formatted

    def test_format_memory_none(self):
        """Test memory formatting with None."""
        formatted = SystemMonitor.format_memory(None)
        assert formatted == "N/A"

    def test_format_cpu_load_valid(self):
        """Test CPU load formatting with valid dict."""
        load = {"1min": 0.52, "5min": 0.48, "15min": 0.45}
        formatted = SystemMonitor.format_cpu_load(load)
        assert "0.52" in formatted
        assert "0.48" in formatted
        assert "0.45" in formatted

    def test_format_cpu_load_none(self):
        """Test CPU load formatting with None."""
        formatted = SystemMonitor.format_cpu_load(None)
        assert formatted == "N/A"

    def test_format_uptime_days(self):
        """Test uptime formatting with days."""
        uptime = 2 * 86400 + 5 * 3600 + 30 * 60  # 2 days, 5 hours, 30 minutes
        formatted = SystemMonitor.format_uptime(uptime)
        assert "2d" in formatted
        assert "5h" in formatted
        assert "30m" in formatted

    def test_format_uptime_hours(self):
        """Test uptime formatting with hours only."""
        uptime = 5 * 3600 + 30 * 60  # 5 hours, 30 minutes
        formatted = SystemMonitor.format_uptime(uptime)
        assert "5h" in formatted
        assert "30m" in formatted
        assert "d" not in formatted

    def test_format_uptime_minutes(self):
        """Test uptime formatting with minutes only."""
        uptime = 30 * 60  # 30 minutes
        formatted = SystemMonitor.format_uptime(uptime)
        assert "30m" in formatted
        assert "h" not in formatted
        assert "d" not in formatted

    def test_format_uptime_none(self):
        """Test uptime formatting with None."""
        formatted = SystemMonitor.format_uptime(None)
        assert formatted == "N/A"


class TestIntegration:
    """Integration tests for SystemMonitor."""

    def test_real_system_metrics(self, monitor):
        """Test getting real system metrics (integration test)."""
        # This test runs on actual hardware
        metrics = monitor.get_all_metrics("/")

        # CPU temp should be available on Pi
        # (might be None on some systems, but that's ok)
        assert metrics["cpu_temp"] is None or isinstance(metrics["cpu_temp"], float)

        # Disk should always be available
        assert metrics["disk"] is not None
        assert metrics["disk"]["total"] > 0
        assert 0 <= metrics["disk"]["percent"] <= 100

        # Memory should always be available
        assert metrics["memory"] is not None
        assert metrics["memory"]["total"] > 0
        assert 0 <= metrics["memory"]["percent"] <= 100

        # Load should always be available
        assert metrics["load"] is not None
        assert isinstance(metrics["load"]["1min"], float)

        # Uptime should always be available
        assert metrics["uptime"] is not None
        assert metrics["uptime"] > 0

    def test_formatted_output(self, monitor):
        """Test that formatted output is reasonable."""
        metrics = monitor.get_all_metrics("/")

        cpu_temp_str = SystemMonitor.format_cpu_temp(metrics["cpu_temp"])
        assert cpu_temp_str is not None
        assert len(cpu_temp_str) > 0

        disk_str = SystemMonitor.format_disk_space(metrics["disk"])
        assert disk_str is not None
        assert "GB" in disk_str

        memory_str = SystemMonitor.format_memory(metrics["memory"])
        assert memory_str is not None
        assert "GB" in memory_str

        load_str = SystemMonitor.format_cpu_load(metrics["load"])
        assert load_str is not None

        uptime_str = SystemMonitor.format_uptime(metrics["uptime"])
        assert uptime_str is not None
