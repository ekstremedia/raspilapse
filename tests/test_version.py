"""Tests for version module."""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from __version__ import (
    __version__,
    __author__,
    __email__,
    __license__,
    __description__,
    __url__,
)


def test_version_format():
    """Test version string format."""
    assert isinstance(__version__, str)
    assert len(__version__) > 0
    # Should be in format X.Y.Z or X.Y.Z-beta
    parts = __version__.split("-")[0].split(".")
    assert len(parts) == 3
    for part in parts:
        assert part.isdigit()


def test_author():
    """Test author information."""
    assert __author__ == "Terje Nesthus"
    assert isinstance(__email__, str)
    assert "@" in __email__


def test_license():
    """Test license information."""
    assert __license__ == "MIT"


def test_metadata():
    """Test package metadata."""
    assert isinstance(__description__, str)
    assert len(__description__) > 0
    assert isinstance(__url__, str)
    assert __url__.startswith("https://")
