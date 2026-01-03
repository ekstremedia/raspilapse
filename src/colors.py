"""ANSI color codes and formatting utilities for terminal output.

This module provides a unified Colors class for consistent terminal styling
across all Raspilapse CLI tools.
"""


class Colors:
    """ANSI color codes for terminal output."""

    # Standard text colors (dim)
    BLACK = "\033[30m"
    STD_RED = "\033[31m"
    STD_GREEN = "\033[32m"
    STD_YELLOW = "\033[33m"
    STD_BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    STD_CYAN = "\033[36m"
    WHITE = "\033[37m"

    # Bright/intense colors - used as primary colors for visibility
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    # Primary color aliases (bright versions for better terminal visibility)
    # These match the original create_keogram.py colors
    HEADER = "\033[95m"  # Bright magenta
    BLUE = "\033[94m"    # Bright blue
    CYAN = "\033[96m"    # Bright cyan
    GREEN = "\033[92m"   # Bright green
    YELLOW = "\033[93m"  # Bright yellow
    RED = "\033[91m"     # Bright red

    # Styles
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"
    RESET = "\033[0m"
    END = "\033[0m"  # Alias for RESET

    # Background colors
    BG_BLACK = "\033[40m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"
    BG_WHITE = "\033[47m"

    @staticmethod
    def header(text: str) -> str:
        """Format text as a header (bold cyan)."""
        return f"{Colors.BOLD}{Colors.CYAN}{text}{Colors.END}"

    @staticmethod
    def success(text: str) -> str:
        """Format text as success (green)."""
        return f"{Colors.GREEN}{text}{Colors.END}"

    @staticmethod
    def error(text: str) -> str:
        """Format text as error (red)."""
        return f"{Colors.RED}{text}{Colors.END}"

    @staticmethod
    def warning(text: str) -> str:
        """Format text as warning (yellow)."""
        return f"{Colors.YELLOW}{text}{Colors.END}"

    @staticmethod
    def info(text: str) -> str:
        """Format text as info (blue)."""
        return f"{Colors.BLUE}{text}{Colors.END}"

    @staticmethod
    def bold(text: str) -> str:
        """Format text as bold."""
        return f"{Colors.BOLD}{text}{Colors.END}"

    @staticmethod
    def dim(text: str) -> str:
        """Format text as dim."""
        return f"{Colors.DIM}{text}{Colors.END}"


def print_section(title: str):
    """Print a section header with decorative borders."""
    print(f"\n{Colors.header('═' * 70)}")
    print(f"{Colors.header(f'  {title}')}")
    print(f"{Colors.header('═' * 70)}")


def print_info(label: str, value: str):
    """Print an info line with label and value."""
    print(f"  {Colors.BOLD}{label}:{Colors.END} {value}")
