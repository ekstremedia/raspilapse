"""System monitoring module for Raspberry Pi.

Provides system health metrics for display in timelapse overlays.
"""

import os
import subprocess
from typing import Any, Dict, Optional

try:
    from src.logging_config import get_logger
except ImportError:
    from logging_config import get_logger

logger = get_logger("system_monitor")


class SystemMonitor:
    """Monitor Raspberry Pi system metrics."""

    def __init__(self):
        """Initialize system monitor."""
        logger.debug("SystemMonitor initialized")

    def get_cpu_temperature(self) -> Optional[float]:
        """
        Get CPU temperature in Celsius.

        Returns:
            Temperature in Celsius, or None if unavailable
        """
        try:
            # Try vcgencmd first (Raspberry Pi specific)
            result = subprocess.run(
                ["vcgencmd", "measure_temp"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if result.returncode == 0:
                # Output format: "temp=42.8'C"
                temp_str = result.stdout.strip()
                temp = float(temp_str.split("=")[1].split("'")[0])
                return temp
        except Exception as e:
            logger.debug(f"vcgencmd failed, trying thermal_zone: {e}")

        try:
            # Fallback to thermal_zone (works on most Linux systems)
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp = float(f.read().strip()) / 1000.0
                return temp
        except Exception as e:
            logger.warning(f"Could not read CPU temperature: {e}")
            return None

    def get_disk_space(self, path: str = "/") -> Optional[Dict[str, float]]:
        """
        Get disk space information for a given path.

        Args:
            path: Filesystem path to check (default: root)

        Returns:
            Dict with 'total', 'used', 'free', 'percent' in GB, or None if unavailable
        """
        try:
            stat = os.statvfs(path)
            total = (stat.f_blocks * stat.f_frsize) / (1024**3)  # GB
            free = (stat.f_bavail * stat.f_frsize) / (1024**3)  # GB
            used = total - free
            percent = (used / total) * 100 if total > 0 else 0

            return {
                "total": total,
                "used": used,
                "free": free,
                "percent": percent,
            }
        except Exception as e:
            logger.warning(f"Could not read disk space: {e}")
            return None

    def get_memory_usage(self) -> Optional[Dict[str, float]]:
        """
        Get memory usage information.

        Returns:
            Dict with 'total', 'used', 'free', 'percent' in MB, or None if unavailable
        """
        try:
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()

            mem_info = {}
            for line in lines:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = int(parts[1].strip().split()[0])  # Remove 'kB' unit
                    mem_info[key] = value

            total = mem_info.get("MemTotal", 0) / 1024  # MB
            available = mem_info.get("MemAvailable", 0) / 1024  # MB
            used = total - available
            percent = (used / total) * 100 if total > 0 else 0

            return {
                "total": total,
                "used": used,
                "free": available,
                "percent": percent,
            }
        except Exception as e:
            logger.warning(f"Could not read memory usage: {e}")
            return None

    def get_cpu_load(self) -> Optional[Dict[str, float]]:
        """
        Get CPU load averages.

        Returns:
            Dict with '1min', '5min', '15min' load averages, or None if unavailable
        """
        try:
            load1, load5, load15 = os.getloadavg()
            return {
                "1min": load1,
                "5min": load5,
                "15min": load15,
            }
        except Exception as e:
            logger.warning(f"Could not read CPU load: {e}")
            return None

    def get_uptime(self) -> Optional[float]:
        """
        Get system uptime in seconds.

        Returns:
            Uptime in seconds, or None if unavailable
        """
        try:
            with open("/proc/uptime", "r") as f:
                uptime = float(f.read().split()[0])
                return uptime
        except Exception as e:
            logger.warning(f"Could not read uptime: {e}")
            return None

    def get_all_metrics(self, disk_path: str = "/") -> Dict[str, Any]:
        """
        Get all system metrics at once.

        Args:
            disk_path: Path to check disk space for

        Returns:
            Dict with all available metrics
        """
        metrics = {
            "cpu_temp": self.get_cpu_temperature(),
            "disk": self.get_disk_space(disk_path),
            "memory": self.get_memory_usage(),
            "load": self.get_cpu_load(),
            "uptime": self.get_uptime(),
        }

        logger.debug(f"System metrics collected: {metrics}")
        return metrics

    @staticmethod
    def format_cpu_temp(temp: Optional[float]) -> str:
        """
        Format CPU temperature for display.

        Args:
            temp: Temperature in Celsius

        Returns:
            Formatted string (e.g., "42.8°C" or "N/A")
        """
        if temp is None:
            return "N/A"
        return f"{temp:.1f}°C"

    @staticmethod
    def format_disk_space(disk: Optional[Dict[str, float]]) -> str:
        """
        Format disk space for display.

        Args:
            disk: Disk space dict from get_disk_space()

        Returns:
            Formatted string (e.g., "50.2 GB free (42%)" or "N/A")
        """
        if disk is None:
            return "N/A"
        return f"{disk['free']:.1f} GB free ({disk['percent']:.0f}% used)"

    @staticmethod
    def format_memory(memory: Optional[Dict[str, float]]) -> str:
        """
        Format memory usage for display.

        Args:
            memory: Memory dict from get_memory_usage()

        Returns:
            Formatted string (e.g., "1.2 GB / 4.0 GB (30%)" or "N/A")
        """
        if memory is None:
            return "N/A"
        return f"{memory['used']/1024:.1f} GB / {memory['total']/1024:.1f} GB ({memory['percent']:.0f}%)"

    @staticmethod
    def format_cpu_load(load: Optional[Dict[str, float]]) -> str:
        """
        Format CPU load for display.

        Args:
            load: Load dict from get_cpu_load()

        Returns:
            Formatted string (e.g., "0.52, 0.48, 0.45" or "N/A")
        """
        if load is None:
            return "N/A"
        return f"{load['1min']:.2f}, {load['5min']:.2f}, {load['15min']:.2f}"

    @staticmethod
    def format_uptime(uptime: Optional[float]) -> str:
        """
        Format uptime for display.

        Args:
            uptime: Uptime in seconds

        Returns:
            Formatted string (e.g., "2d 5h 30m" or "N/A")
        """
        if uptime is None:
            return "N/A"

        days = int(uptime // 86400)
        hours = int((uptime % 86400) // 3600)
        minutes = int((uptime % 3600) // 60)

        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"


def main():
    """CLI entry point for testing system monitor."""
    import json

    monitor = SystemMonitor()

    print("=== Raspberry Pi System Metrics ===\n")

    # CPU Temperature
    temp = monitor.get_cpu_temperature()
    print(f"CPU Temperature: {SystemMonitor.format_cpu_temp(temp)}")

    # Disk Space (root)
    disk = monitor.get_disk_space("/")
    print(f"Disk Space (/): {SystemMonitor.format_disk_space(disk)}")

    # Disk Space (images directory if different)
    if os.path.exists("/var/www/html/images"):
        disk_images = monitor.get_disk_space("/var/www/html/images")
        print(f"Disk Space (images): {SystemMonitor.format_disk_space(disk_images)}")

    # Memory Usage
    memory = monitor.get_memory_usage()
    print(f"Memory Usage: {SystemMonitor.format_memory(memory)}")

    # CPU Load
    load = monitor.get_cpu_load()
    print(f"CPU Load (1m, 5m, 15m): {SystemMonitor.format_cpu_load(load)}")

    # Uptime
    uptime = monitor.get_uptime()
    print(f"Uptime: {SystemMonitor.format_uptime(uptime)}")

    # All metrics (JSON)
    print("\n=== Raw Metrics (JSON) ===")
    metrics = monitor.get_all_metrics("/var/www/html/images")
    print(json.dumps(metrics, indent=2, default=str))


if __name__ == "__main__":
    main()
