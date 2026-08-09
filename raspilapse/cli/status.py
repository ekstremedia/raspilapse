#!/usr/bin/env python3
"""Status display script for Raspilapse.

Shows service status, configuration, and recent captures with beautiful colored output.
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from raspilapse.config import merge_defaults
from raspilapse.console import Colors

# Duplicated from AdaptiveTimelapse.WB_TRIM_STATE rather than imported: that
# import pulls in picamera2, which costs seconds and needs a camera stack this
# command should run without. test_status.py asserts the two stay equal.
WB_TRIM_STATE = Path("data/wb_trim.json")


class StatusDisplay:
    """Display system status with colored output."""

    def __init__(self, config_path: str = "config/config.yml"):
        """Initialize status display."""
        self.config_path = config_path
        self.config = self._load_config()

    # Kept as a method rather than delegating wholesale to config.load_config so
    # the coloured, user-facing error handling below stays where the user sees
    # it. The defaults are merged all the same: without them this command read
    # `adaptive["night_mode"]` straight out of the file and died with
    # `Error: 'night_mode'` against the very config.example.yml the README tells
    # people to copy.
    def _load_config(self) -> Dict:
        """Load configuration from YAML file, filled in from the defaults."""
        config_file = Path(self.config_path)
        if not config_file.exists():
            print(f"{Colors.RED}Configuration file not found: {self.config_path}{Colors.RESET}")
            sys.exit(1)

        try:
            with open(config_file, "r") as f:
                return merge_defaults(yaml.safe_load(f) or {})
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

        # The exposure ladder's ends. This used to print the lux thresholds that
        # selected between three modes; there are no thresholds now, and the
        # camera's own limits are what actually bound what it can do.
        night = adaptive["night_mode"]
        print(
            f"  {Colors.BRIGHT_BLUE}Brightest:{Colors.RESET}   1/10000s at gain 1 "
            f"{Colors.YELLOW}☀️{Colors.RESET}"
        )
        print(
            f"  {Colors.BRIGHT_BLUE}Darkest:{Colors.RESET}     "
            f"{night['max_exposure_time']}s at gain {night['analogue_gain']} "
            f"{Colors.BRIGHT_MAGENTA}🌙{Colors.RESET}"
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

    def _read_wb_trim(self) -> Optional[Tuple[float, float, datetime]]:
        """The trim the daemon last persisted, as (r, b, when written).

        None when the file is absent (a camera that has never run with
        feedback on) or unreadable -- the same tolerance _seed_wb_trim has,
        for the same reason: a missing trim is a fresh start, not an error.
        """
        try:
            with open(WB_TRIM_STATE, "r") as f:
                data = json.load(f)
            mtime = datetime.fromtimestamp(WB_TRIM_STATE.stat().st_mtime)
            return float(data["wb_trim_r"]), float(data["wb_trim_b"]), mtime
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def print_adaptive_status(self):
        """Print the adaptive exposure pipeline: dynamic range, target, fusion."""
        print(f"{Colors.BOLD}{Colors.BRIGHT_WHITE}🌗 ADAPTIVE EXPOSURE{Colors.RESET}")
        print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}")

        adaptive = self.config["adaptive_timelapse"]

        if not adaptive.get("enabled", True):
            print(f"  {Colors.YELLOW}○ Disabled{Colors.RESET}\n")
            return

        # Ask DynamicRange what is actually running rather than reading
        # method straight from the file: it reports the degraded reality (a
        # config asking for fusion without cv2 installed runs as 'off'), and
        # that is the thing worth seeing on a camera that looks wrong.
        block = adaptive.get("dynamic_range") or {}
        try:
            from raspilapse.dynrange import DynamicRange

            dynamic_range = DynamicRange(self.config)
            label = dynamic_range.label()
            method = dynamic_range.method
            tone_strength = dynamic_range._tone_map_strength
            tone_on = dynamic_range.tone_map_enabled
            ev_spread = dynamic_range._fusion_ev_spread
            brackets = dynamic_range._fusion_brackets
            single_shot_above = dynamic_range._fusion_single_shot_above_s
            configured = str(block.get("method", "off")).lower()
            degraded = method != configured and configured != "tone_map"
        except Exception as e:  # pragma: no cover - defensive, never seen
            print(f"  {Colors.RED}Could not read dynamic_range: {e}{Colors.RESET}\n")
            return

        colour = Colors.YELLOW if degraded else Colors.BRIGHT_GREEN
        note = f" {Colors.DIM}(config asked for {configured}){Colors.RESET}" if degraded else ""
        print(
            f"  {Colors.BRIGHT_BLUE}Pipeline:{Colors.RESET}    {colour}{label}{Colors.RESET}{note}"
        )

        if method == "fusion":
            # 2^spread is the ratio between neighbouring brackets, which is
            # the number that means something when looking at a frame: how
            # much more shadow and highlight the merge has to work with.
            ratio = 2.0**ev_spread
            print(
                f"  {Colors.BRIGHT_BLUE}Fusion:{Colors.RESET}      "
                f"{brackets} brackets, ±{ev_spread:.1f} EV "
                f"{Colors.DIM}(±{ratio:.1f}x exposure){Colors.RESET}"
            )
            # The spread is not constant: it ramps to zero as the ladder
            # climbs, so a night frame is a single shot however this is set.
            print(
                f"  {Colors.DIM}               full spread below 1/20s, "
                f"single shot above {single_shot_above:g}s{Colors.RESET}"
            )

        if tone_on:
            print(
                f"  {Colors.BRIGHT_BLUE}Tone Map:{Colors.RESET}    "
                f"{Colors.GREEN}✓{Colors.RESET} strength {tone_strength:.2f} "
                f"{Colors.DIM}(fades out below L=45){Colors.RESET}"
            )
        else:
            print(f"  {Colors.BRIGHT_BLUE}Tone Map:{Colors.RESET}    {Colors.DIM}off{Colors.RESET}")

        # The brightness setpoint. This is what the loop actually drives at,
        # measured on the lores stream before tone mapping and the overlay.
        target = adaptive.get("brightness_target") or {}
        print(
            f"  {Colors.BRIGHT_BLUE}Target:{Colors.RESET}      "
            f"{target.get('base', 120)} base "
            f"{Colors.DIM}(+{target.get('overcast_boost', 15)} overcast, "
            f"max {target.get('max_target', 140)}){Colors.RESET}"
        )
        print(
            f"  {Colors.BRIGHT_BLUE}Damping:{Colors.RESET}     "
            f"{adaptive.get('brightness_damping', 0.5):.2f} "
            f"{Colors.DIM}(correction toward target per frame){Colors.RESET}"
        )

        highlight = adaptive.get("highlight_protection") or {}
        if highlight.get("enabled"):
            night = "on" if highlight.get("apply_in_night") else "day only"
            print(
                f"  {Colors.BRIGHT_BLUE}Highlights:{Colors.RESET}  {Colors.GREEN}✓{Colors.RESET} "
                f"p95 {highlight.get('safe_p95', 200)}/"
                f"{highlight.get('warning_p95', 220)}/"
                f"{highlight.get('critical_p95', 240)} "
                f"{Colors.DIM}(floor {highlight.get('min_scale', 0.70):.2f}, {night}){Colors.RESET}"
            )
        else:
            print(
                f"  {Colors.BRIGHT_BLUE}Highlights:{Colors.RESET}  "
                f"{Colors.DIM}unprotected{Colors.RESET}"
            )

        print()

    def print_white_balance_status(self):
        """Print the day/night white point and the feedback loop's learned trim."""
        print(f"{Colors.BOLD}{Colors.BRIGHT_WHITE}🎨 WHITE BALANCE{Colors.RESET}")
        print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}")

        adaptive = self.config["adaptive_timelapse"]
        day = adaptive.get("day_mode") or {}
        night = adaptive.get("night_mode") or {}

        fixed = day.get("fixed_colour_gains")
        if fixed:
            print(
                f"  {Colors.BRIGHT_BLUE}Day gains:{Colors.RESET}   "
                f"R {fixed[0]:.2f}  B {fixed[1]:.2f} "
                f"{Colors.DIM}(fixed; wins over the learned reference){Colors.RESET}"
            )
        else:
            print(
                f"  {Colors.BRIGHT_BLUE}Day gains:{Colors.RESET}   "
                f"{Colors.DIM}learned from the test shot{Colors.RESET}"
            )

        night_gains = night.get("colour_gains")
        if night_gains:
            print(
                f"  {Colors.BRIGHT_BLUE}Night gains:{Colors.RESET} "
                f"R {night_gains[0]:.2f}  B {night_gains[1]:.2f} "
                f"{Colors.DIM}(dark end of the cross-fade){Colors.RESET}"
            )

        feedback = day.get("wb_feedback") or {}
        if not feedback.get("enabled"):
            print(f"  {Colors.BRIGHT_BLUE}Feedback:{Colors.RESET}    {Colors.DIM}off{Colors.RESET}")
            print()
            return

        max_trim = float(feedback.get("max_trim", 0.12))
        print(
            f"  {Colors.BRIGHT_BLUE}Feedback:{Colors.RESET}    {Colors.GREEN}✓{Colors.RESET} "
            f"strength {float(feedback.get('strength', 0.05)):.2f}, "
            f"max trim ±{max_trim * 100:.0f}%"
        )

        trim = self._read_wb_trim()
        if trim is None:
            print(
                f"  {Colors.BRIGHT_BLUE}Learned:{Colors.RESET}     "
                f"{Colors.DIM}no trim stored yet ({WB_TRIM_STATE}){Colors.RESET}"
            )
            print()
            return

        trim_r, trim_b, written = trim
        # A trim sitting on its clamp means the loop wants a white point the
        # configured gains cannot reach -- the anchor is wrong, not the loop.
        pinned = [
            axis
            for axis, value in (("R", trim_r), ("B", trim_b))
            if abs(abs(1.0 - value) - max_trim) < 0.001
        ]
        print(
            f"  {Colors.BRIGHT_BLUE}Learned:{Colors.RESET}     "
            f"R x{trim_r:.3f}  B x{trim_b:.3f} "
            f"{Colors.DIM}({self._format_time_ago(written)}){Colors.RESET}"
        )
        if fixed:
            print(
                f"  {Colors.BRIGHT_BLUE}Effective:{Colors.RESET}   "
                f"R {fixed[0] * trim_r:.2f}  B {fixed[1] * trim_b:.2f} "
                f"{Colors.DIM}(what daylight frames actually render at){Colors.RESET}"
            )
        if pinned:
            print(
                f"  {Colors.YELLOW}⚠ {'/'.join(pinned)} trim is pinned at the ±"
                f"{max_trim * 100:.0f}% clamp{Colors.RESET}"
            )
            print(
                f"    {Colors.DIM}The loop wants a white point fixed_colour_gains "
                f"cannot reach; move the anchor.{Colors.RESET}"
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
            # Fallbacks mirror render.py's defaults -- this display had its
            # own ("bottom-left", "default") and reported settings the
            # renderer was not using.
            print(
                f"  {Colors.BRIGHT_BLUE}Position:{Colors.RESET}    {overlay.get('position', 'top-bar')}"
            )
            print(
                f"  {Colors.BRIGHT_BLUE}Camera Name:{Colors.RESET} {overlay.get('camera_name', 'N/A')}"
            )

            # Font info
            font = overlay.get("font", {})
            print(
                f"  {Colors.BRIGHT_BLUE}Font:{Colors.RESET}        "
                f"{font.get('family', 'DejaVuSans-Bold.ttf')} "
                f"{Colors.DIM}(size: {font.get('size_ratio', 0.025):.3f}){Colors.RESET}"
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
        self.print_adaptive_status()
        self.print_white_balance_status()
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
