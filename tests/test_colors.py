"""Tests for colors module."""

import sys
from pathlib import Path
from io import StringIO
from unittest.mock import patch

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from colors import Colors, print_section, print_info


class TestColorsConstants:
    """Test color constant definitions."""

    def test_standard_colors_exist(self):
        """Test standard text colors are defined."""
        assert Colors.BLACK == "\033[30m"
        assert Colors.STD_RED == "\033[31m"
        assert Colors.STD_GREEN == "\033[32m"
        assert Colors.STD_YELLOW == "\033[33m"
        assert Colors.STD_BLUE == "\033[34m"
        assert Colors.MAGENTA == "\033[35m"
        assert Colors.STD_CYAN == "\033[36m"
        assert Colors.WHITE == "\033[37m"

    def test_bright_colors_exist(self):
        """Test bright/intense colors are defined."""
        assert Colors.BRIGHT_BLACK == "\033[90m"
        assert Colors.BRIGHT_RED == "\033[91m"
        assert Colors.BRIGHT_GREEN == "\033[92m"
        assert Colors.BRIGHT_YELLOW == "\033[93m"
        assert Colors.BRIGHT_BLUE == "\033[94m"
        assert Colors.BRIGHT_MAGENTA == "\033[95m"
        assert Colors.BRIGHT_CYAN == "\033[96m"
        assert Colors.BRIGHT_WHITE == "\033[97m"

    def test_primary_color_aliases(self):
        """Test primary color aliases point to bright versions."""
        assert Colors.HEADER == "\033[95m"
        assert Colors.BLUE == "\033[94m"
        assert Colors.CYAN == "\033[96m"
        assert Colors.GREEN == "\033[92m"
        assert Colors.YELLOW == "\033[93m"
        assert Colors.RED == "\033[91m"

    def test_styles_exist(self):
        """Test text styles are defined."""
        assert Colors.BOLD == "\033[1m"
        assert Colors.DIM == "\033[2m"
        assert Colors.UNDERLINE == "\033[4m"
        assert Colors.RESET == "\033[0m"
        assert Colors.END == "\033[0m"

    def test_reset_and_end_are_same(self):
        """Test RESET and END are aliases."""
        assert Colors.RESET == Colors.END

    def test_background_colors_exist(self):
        """Test background colors are defined."""
        assert Colors.BG_BLACK == "\033[40m"
        assert Colors.BG_RED == "\033[41m"
        assert Colors.BG_GREEN == "\033[42m"
        assert Colors.BG_YELLOW == "\033[43m"
        assert Colors.BG_BLUE == "\033[44m"
        assert Colors.BG_MAGENTA == "\033[45m"
        assert Colors.BG_CYAN == "\033[46m"
        assert Colors.BG_WHITE == "\033[47m"


class TestColorsFormatMethods:
    """Test color formatting methods."""

    def test_header_formatting(self):
        """Test header() applies bold cyan formatting."""
        result = Colors.header("Test Header")
        assert result == f"{Colors.BOLD}{Colors.CYAN}Test Header{Colors.END}"
        assert "\033[1m" in result  # BOLD
        assert "\033[96m" in result  # CYAN
        assert "\033[0m" in result  # END

    def test_success_formatting(self):
        """Test success() applies green formatting."""
        result = Colors.success("Success message")
        assert result == f"{Colors.GREEN}Success message{Colors.END}"
        assert "\033[92m" in result  # GREEN
        assert "\033[0m" in result  # END

    def test_error_formatting(self):
        """Test error() applies red formatting."""
        result = Colors.error("Error message")
        assert result == f"{Colors.RED}Error message{Colors.END}"
        assert "\033[91m" in result  # RED
        assert "\033[0m" in result  # END

    def test_warning_formatting(self):
        """Test warning() applies yellow formatting."""
        result = Colors.warning("Warning message")
        assert result == f"{Colors.YELLOW}Warning message{Colors.END}"
        assert "\033[93m" in result  # YELLOW
        assert "\033[0m" in result  # END

    def test_info_formatting(self):
        """Test info() applies blue formatting."""
        result = Colors.info("Info message")
        assert result == f"{Colors.BLUE}Info message{Colors.END}"
        assert "\033[94m" in result  # BLUE
        assert "\033[0m" in result  # END

    def test_bold_formatting(self):
        """Test bold() applies bold formatting."""
        result = Colors.bold("Bold text")
        assert result == f"{Colors.BOLD}Bold text{Colors.END}"
        assert "\033[1m" in result  # BOLD
        assert "\033[0m" in result  # END

    def test_dim_formatting(self):
        """Test dim() applies dim formatting."""
        result = Colors.dim("Dim text")
        assert result == f"{Colors.DIM}Dim text{Colors.END}"
        assert "\033[2m" in result  # DIM
        assert "\033[0m" in result  # END

    def test_empty_string_formatting(self):
        """Test formatting methods handle empty strings."""
        assert Colors.header("") == f"{Colors.BOLD}{Colors.CYAN}{Colors.END}"
        assert Colors.success("") == f"{Colors.GREEN}{Colors.END}"
        assert Colors.error("") == f"{Colors.RED}{Colors.END}"
        assert Colors.warning("") == f"{Colors.YELLOW}{Colors.END}"
        assert Colors.info("") == f"{Colors.BLUE}{Colors.END}"
        assert Colors.bold("") == f"{Colors.BOLD}{Colors.END}"
        assert Colors.dim("") == f"{Colors.DIM}{Colors.END}"

    def test_special_characters_in_text(self):
        """Test formatting methods handle special characters."""
        special_text = "Test: 100% complete! <file.txt>"
        assert "100%" in Colors.success(special_text)
        assert "<file.txt>" in Colors.error(special_text)

    def test_multiline_text(self):
        """Test formatting methods handle multiline text."""
        multiline = "Line 1\nLine 2\nLine 3"
        result = Colors.info(multiline)
        assert "Line 1\nLine 2\nLine 3" in result


class TestPrintFunctions:
    """Test print utility functions."""

    def test_print_section_output(self):
        """Test print_section outputs formatted section header."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            print_section("Test Section")
            output = mock_stdout.getvalue()

            # Should contain the title
            assert "Test Section" in output
            # Should contain decorative borders (═ character)
            assert "═" in output
            # Should have newlines for formatting
            assert "\n" in output

    def test_print_section_with_special_characters(self):
        """Test print_section handles special characters in title."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            print_section("Section: Test & Analysis <100%>")
            output = mock_stdout.getvalue()
            assert "Test & Analysis" in output
            assert "100%" in output

    def test_print_info_output(self):
        """Test print_info outputs formatted label-value pair."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            print_info("Status", "Running")
            output = mock_stdout.getvalue()

            # Should contain the label and value
            assert "Status" in output
            assert "Running" in output
            # Should contain colon separator
            assert ":" in output

    def test_print_info_with_empty_value(self):
        """Test print_info with empty value."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            print_info("Label", "")
            output = mock_stdout.getvalue()
            assert "Label" in output
            assert ":" in output

    def test_print_info_with_numeric_value(self):
        """Test print_info with numeric string value."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            print_info("Count", "42")
            output = mock_stdout.getvalue()
            assert "Count" in output
            assert "42" in output

    def test_print_info_with_path_value(self):
        """Test print_info with path-like value."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            print_info("File", "/home/user/photos/image.jpg")
            output = mock_stdout.getvalue()
            assert "File" in output
            assert "/home/user/photos/image.jpg" in output


class TestColorsIntegration:
    """Integration tests for Colors usage patterns."""

    def test_nested_formatting(self):
        """Test that formatting codes don't interfere with each other."""
        # Common usage pattern: formatted text within formatted section
        header = Colors.header("Title")
        info = Colors.info("details")

        # Both should be independently formatted
        assert Colors.END in header
        assert Colors.END in info

    def test_colors_class_instantiation(self):
        """Test Colors class can be used as a namespace."""
        # Colors is designed as a class with static methods and class attributes
        # It should work without instantiation
        c = Colors()  # Should not raise
        assert c.RED == Colors.RED
        assert c.header("test") == Colors.header("test")

    def test_all_format_methods_return_strings(self):
        """Test all formatting methods return string types."""
        test_text = "test"
        assert isinstance(Colors.header(test_text), str)
        assert isinstance(Colors.success(test_text), str)
        assert isinstance(Colors.error(test_text), str)
        assert isinstance(Colors.warning(test_text), str)
        assert isinstance(Colors.info(test_text), str)
        assert isinstance(Colors.bold(test_text), str)
        assert isinstance(Colors.dim(test_text), str)

    def test_ansi_codes_are_valid_format(self):
        """Test ANSI codes follow the expected format."""
        # All ANSI codes should start with ESC [ and end with m
        ansi_attrs = [
            Colors.BLACK, Colors.STD_RED, Colors.STD_GREEN, Colors.STD_YELLOW,
            Colors.STD_BLUE, Colors.MAGENTA, Colors.STD_CYAN, Colors.WHITE,
            Colors.BRIGHT_BLACK, Colors.BRIGHT_RED, Colors.BRIGHT_GREEN,
            Colors.BRIGHT_YELLOW, Colors.BRIGHT_BLUE, Colors.BRIGHT_MAGENTA,
            Colors.BRIGHT_CYAN, Colors.BRIGHT_WHITE,
            Colors.BOLD, Colors.DIM, Colors.UNDERLINE, Colors.RESET, Colors.END,
            Colors.BG_BLACK, Colors.BG_RED, Colors.BG_GREEN, Colors.BG_YELLOW,
            Colors.BG_BLUE, Colors.BG_MAGENTA, Colors.BG_CYAN, Colors.BG_WHITE,
        ]
        for code in ansi_attrs:
            assert code.startswith("\033["), f"Invalid ANSI code: {repr(code)}"
            assert code.endswith("m"), f"Invalid ANSI code: {repr(code)}"
