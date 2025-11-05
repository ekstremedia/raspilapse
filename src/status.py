#!/usr/bin/env python3
"""Status display script for Raspilapse.

Shows service status, configuration, and recent captures with beautiful colored output.
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import yaml


class Colors:
    """ANSI color codes for terminal output."""

    # Text colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    # Bright colors
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    # Styles
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"
    RESET = "\033[0m"

    # Background colors
    BG_BLACK = "\033[40m"
    BG_GREEN = "\033[42m"
    BG_RED = "\033[41m"
    BG_YELLOW = "\033[43m"


class StatusDisplay:
    """Display system status with colored output."""

    def __init__(self, config_path: str = "config/config.yml"):
        """Initialize status display."""
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        """Load configuration from YAML file."""
        config_file = Path(self.config_path)
        if not config_file.exists():
            print(f"{Colors.RED}Configuration file not found: {self.config_path}{Colors.RESET}")
            sys.exit(1)

        try:
            with open(config_file, "r") as f:
                return yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"{Colors.RED}Failed to parse configuration: {e}{Colors.RESET}")
            sys.exit(1)

    def _get_service_status(self) -> Tuple[str, str, str]:
        """
        Get systemd service status.

        Returns:
            Tuple of (status, state, description)
        """
        try:
            # Check if service is active
            result = subprocess.run(
                ["systemctl", "is-active", "raspilapse.service"],
                capture_output=True,
                text=True,
            )
            status = result.stdout.strip()

            # Get detailed status
            result = subprocess.run(
                ["systemctl", "status", "raspilapse.service"],
                capture_output=True,
                text=True,
            )
            output = result.stdout

            # Parse output for state
            state = "unknown"
            description = ""
            for line in output.split("\n"):
                if "Active:" in line:
                    if "active (running)" in line:
                        state = "running"
                        description = "Service is running normally"
                    elif "inactive" in line:
                        state = "stopped"
                        description = "Service is stopped"
                    elif "failed" in line:
                        state = "failed"
                        description = "Service has failed"
                    break

            return status, state, description

        except Exception as e:
            return "unknown", "error", f"Error checking service: {e}"

    def _get_recent_captures(self, limit: int = 5) -> List[Tuple[str, datetime, int]]:
        """
        Get list of recent captures.

        Args:
            limit: Maximum number of captures to return

        Returns:
            List of (filepath, datetime, size_bytes) tuples
        """
        output_dir = Path(self.config["output"]["directory"])

        if not output_dir.exists():
            return []

        # Find all jpg files recursively
        jpg_files = list(output_dir.rglob("*.jpg"))

        # Filter out metadata directory and symlinks
        jpg_files = [f for f in jpg_files if "metadata" not in f.parts and not f.is_symlink()]

        # Sort by modification time (newest first)
        jpg_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        # Get file info
        captures = []
        for jpg_file in jpg_files[:limit]:
            stat = jpg_file.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime)
            size = stat.st_size
            captures.append((str(jpg_file), mtime, size))

        return captures

    def _format_size(self, size_bytes: int) -> str:
        """Format file size in human-readable format."""
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"

    def _format_time_ago(self, dt: datetime) -> str:
        """Format time difference as human-readable string."""
        now = datetime.now()
        diff = now - dt

        seconds = int(diff.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        elif seconds < 3600:
            minutes = seconds // 60
            return f"{minutes}m ago"
        elif seconds < 86400:
            hours = seconds // 3600
            return f"{hours}h ago"
        else:
            days = seconds // 86400
            return f"{days}d ago"

    def print_header(self):
        """Print status header."""
        print(f"\n{Colors.BOLD}{Colors.BRIGHT_CYAN}{'=' * 60}{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}  🎥  RASPILAPSE STATUS  🎥{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}{'=' * 60}{Colors.RESET}\n")

    def print_service_status(self):
        """Print systemd service status."""
        status, state, description = self._get_service_status()

        print(f"{Colors.BOLD}{Colors.BRIGHT_WHITE}📡 SERVICE STATUS{Colors.RESET}")
        print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}")

        # Status indicator
        if state == "running":
            status_icon = f"{Colors.BG_GREEN}{Colors.BLACK} ● RUNNING {Colors.RESET}"
            status_color = Colors.GREEN
        elif state == "stopped":
            status_icon = f"{Colors.BG_YELLOW}{Colors.BLACK} ○ STOPPED {Colors.RESET}"
            status_color = Colors.YELLOW
        elif state == "failed":
            status_icon = f"{Colors.BG_RED}{Colors.WHITE} ✗ FAILED {Colors.RESET}"
            status_color = Colors.RED
        else:
            status_icon = f"{Colors.BG_BLACK}{Colors.WHITE} ? UNKNOWN {Colors.RESET}"
            status_color = Colors.BRIGHT_BLACK

        print(f"  Status:      {status_icon}")
        print(f"  Description: {status_color}{description}{Colors.RESET}")
        print()

    def print_configuration(self):
        """Print configuration summary."""
        print(f"{Colors.BOLD}{Colors.BRIGHT_WHITE}⚙️  CONFIGURATION{Colors.RESET}")
        print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}")

        adaptive = self.config["adaptive_timelapse"]
        camera = self.config["camera"]
        output = self.config["output"]

        # Camera settings
        res = camera["resolution"]
        print(
            f"  {Colors.BRIGHT_BLUE}Resolution:{Colors.RESET}  {res['width']}x{res['height']} "
            f"{Colors.DIM}({res['width'] * res['height'] / 1_000_000:.1f}MP){Colors.RESET}"
        )

        # Interval
        interval = adaptive["interval"]
        captures_per_min = 60 / interval
        print(
            f"  {Colors.BRIGHT_BLUE}Interval:{Colors.RESET}    {interval}s "
            f"{Colors.DIM}({captures_per_min:.1f} captures/min){Colors.RESET}"
        )

        # Light thresholds
        thresholds = adaptive["light_thresholds"]
        print(
            f"  {Colors.BRIGHT_BLUE}Day Mode:{Colors.RESET}    >{thresholds['day']} lux "
            f"{Colors.YELLOW}☀️{Colors.RESET}"
        )
        print(
            f"  {Colors.BRIGHT_BLUE}Night Mode:{Colors.RESET}  <{thresholds['night']} lux "
            f"{Colors.BRIGHT_MAGENTA}🌙{Colors.RESET}"
        )

        # Night mode settings
        night = adaptive["night_mode"]
        print(
            f"  {Colors.BRIGHT_BLUE}Max Exposure:{Colors.RESET} {night['max_exposure_time']}s "
            f"{Colors.DIM}(ISO {int(night['analogue_gain'] * 100)}){Colors.RESET}"
        )

        # Output directory
        print(f"  {Colors.BRIGHT_BLUE}Output:{Colors.RESET}      {output['directory']}")

        # Organize by date
        if output.get("organize_by_date"):
            date_format = output.get("date_format", "%Y/%m/%d")
            print(
                f"  {Colors.BRIGHT_BLUE}Organization:{Colors.RESET} By date {Colors.DIM}({date_format}){Colors.RESET}"
            )

        print()

    def print_overlay_status(self):
        """Print overlay configuration."""
        print(f"{Colors.BOLD}{Colors.BRIGHT_WHITE}🖼️  OVERLAY{Colors.RESET}")
        print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}")

        overlay = self.config.get("overlay", {})
        enabled = overlay.get("enabled", False)

        if enabled:
            print(f"  {Colors.GREEN}✓ Enabled{Colors.RESET}")
            print(
                f"  {Colors.BRIGHT_BLUE}Position:{Colors.RESET}    {overlay.get('position', 'bottom-left')}"
            )
            print(
                f"  {Colors.BRIGHT_BLUE}Camera Name:{Colors.RESET} {overlay.get('camera_name', 'N/A')}"
            )

            # Font info
            font = overlay.get("font", {})
            print(
                f"  {Colors.BRIGHT_BLUE}Font:{Colors.RESET}        {font.get('family', 'default')} "
                f"{Colors.DIM}(size: {font.get('size_ratio', 0.02):.3f}){Colors.RESET}"
            )

            # Background
            bg = overlay.get("background", {})
            if bg.get("enabled", False):
                bg_color = bg.get("color", [0, 0, 0, 128])
                opacity = (bg_color[3] / 255) * 100 if len(bg_color) > 3 else 100
                print(
                    f"  {Colors.BRIGHT_BLUE}Background:{Colors.RESET}  {Colors.GREEN}✓{Colors.RESET} "
                    f"{Colors.DIM}({opacity:.0f}% opacity){Colors.RESET}"
                )

            # Content
            content = overlay.get("content", {})
            camera_settings = content.get("camera_settings", {})
            debug = content.get("debug", {})
            if camera_settings.get("enabled"):
                print(f"  {Colors.BRIGHT_BLUE}Info:{Colors.RESET}        Camera settings")
            if debug.get("enabled"):
                print(f"  {Colors.BRIGHT_BLUE}Debug:{Colors.RESET}       Enabled")

        else:
            print(f"  {Colors.YELLOW}○ Disabled{Colors.RESET}")

        print()

    def print_recent_captures(self):
        """Print recent captures."""
        print(f"{Colors.BOLD}{Colors.BRIGHT_WHITE}📸 RECENT CAPTURES{Colors.RESET}")
        print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}")

        captures = self._get_recent_captures(limit=10)

        if not captures:
            print(f"  {Colors.YELLOW}No captures found{Colors.RESET}")
        else:
            # Calculate timing between captures
            if len(captures) >= 2:
                time_diffs = []
                for i in range(len(captures) - 1):
                    diff = (captures[i][1] - captures[i + 1][1]).total_seconds()
                    time_diffs.append(diff)

                avg_interval = sum(time_diffs) / len(time_diffs)
                print(
                    f"  {Colors.BRIGHT_BLUE}Average Interval:{Colors.RESET} {avg_interval:.1f}s "
                    f"{Colors.DIM}(target: {self.config['adaptive_timelapse']['interval']}s){Colors.RESET}"
                )
                print()

            # Show recent captures
            for i, (filepath, mtime, size) in enumerate(captures[:5], 1):
                filename = Path(filepath).name
                time_ago = self._format_time_ago(mtime)
                size_str = self._format_size(size)
                time_str = mtime.strftime("%Y-%m-%d %H:%M:%S")

                # Color code based on recency
                if i == 1:
                    color = Colors.BRIGHT_GREEN
                    icon = "●"
                elif i <= 3:
                    color = Colors.GREEN
                    icon = "○"
                else:
                    color = Colors.DIM
                    icon = "·"

                print(
                    f"  {color}{icon}{Colors.RESET} {filename} "
                    f"{Colors.DIM}({time_ago}){Colors.RESET}"
                )
                print(f"    {Colors.DIM}{time_str} · {size_str}{Colors.RESET}")

            # Show total count if more than 5
            if len(captures) > 5:
                print(f"\n  {Colors.DIM}... and {len(captures) - 5} more captures{Colors.RESET}")

        print()

    def print_symlink_status(self):
        """Print symlink status (for web display)."""
        symlink_config = self.config.get("output", {}).get("symlink_latest", {})

        if not symlink_config.get("enabled"):
            return

        symlink_path = symlink_config.get("path")
        if not symlink_path:
            return

        print(f"{Colors.BOLD}{Colors.BRIGHT_WHITE}🔗 SYMLINK STATUS{Colors.RESET}")
        print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}")

        symlink = Path(symlink_path)
        if symlink.exists() or symlink.is_symlink():
            if symlink.is_symlink():
                target = symlink.resolve()
                if target.exists():
                    stat = target.stat()
                    mtime = datetime.fromtimestamp(stat.st_mtime)
                    time_ago = self._format_time_ago(mtime)
                    size_str = self._format_size(stat.st_size)

                    print(f"  {Colors.GREEN}✓ Active{Colors.RESET}")
                    print(f"  {Colors.BRIGHT_BLUE}Path:{Colors.RESET}        {symlink_path}")
                    print(
                        f"  {Colors.BRIGHT_BLUE}Target:{Colors.RESET}      {target.name} "
                        f"{Colors.DIM}({time_ago}, {size_str}){Colors.RESET}"
                    )
                else:
                    print(f"  {Colors.RED}✗ Broken symlink{Colors.RESET}")
                    print(f"  {Colors.BRIGHT_BLUE}Path:{Colors.RESET}        {symlink_path}")
            else:
                print(f"  {Colors.YELLOW}⚠ Exists but not a symlink{Colors.RESET}")
        else:
            print(f"  {Colors.YELLOW}○ Not created yet{Colors.RESET}")
            print(f"  {Colors.BRIGHT_BLUE}Path:{Colors.RESET}        {symlink_path}")

        print()

    def print_footer(self):
        """Print status footer."""
        print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}")
        print(
            f"{Colors.DIM}Generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{Colors.RESET}\n"
        )

    def display(self):
        """Display full status."""
        self.print_header()
        self.print_service_status()
        self.print_configuration()
        self.print_overlay_status()
        self.print_recent_captures()
        self.print_symlink_status()
        self.print_footer()


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Display Raspilapse status")
    parser.add_argument(
        "-c",
        "--config",
        default="config/config.yml",
        help="Path to configuration file (default: config/config.yml)",
    )

    args = parser.parse_args()

    try:
        status = StatusDisplay(args.config)
        status.display()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Interrupted{Colors.RESET}")
        sys.exit(0)
    except Exception as e:
        print(f"{Colors.RED}Error: {e}{Colors.RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
