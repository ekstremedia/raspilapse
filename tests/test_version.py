"""Tests for version module."""

from pathlib import Path

from raspilapse.__version__ import (
    __author__,
    __description__,
    __email__,
    __license__,
    __url__,
    __version__,
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


def test_version_matches_changelog():
    """The top CHANGELOG entry must be the current version.

    These drifted to three different numbers at once -- 1.3.2 in the CHANGELOG,
    1.1.0 in pyproject and __version__, 0.9.0-beta in CITATION.cff -- because
    nothing checked.
    """
    import re

    changelog = (Path(__file__).parent.parent / "CHANGELOG.md").read_text()
    match = re.search(r"^## \[([0-9]+\.[0-9]+\.[0-9]+[^\]]*)\]", changelog, re.M)
    assert match, "no version heading found in CHANGELOG.md"
    assert match.group(1) == __version__, (
        f"CHANGELOG.md's newest entry is {match.group(1)} but __version__ is "
        f"{__version__}. Add a CHANGELOG entry, or fix the version."
    )


def test_version_matches_citation():
    """CITATION.cff has no dynamic mechanism, so it is on the release checklist."""
    import re

    citation = (Path(__file__).parent.parent / "CITATION.cff").read_text()
    match = re.search(r"^version: (.+)$", citation, re.M)
    assert match, "no version field in CITATION.cff"
    assert match.group(1).strip().strip("'\"") == __version__


def test_pyproject_reads_version_dynamically():
    """pyproject must not hardcode a fourth copy of the version."""
    pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in pyproject
    assert 'version = {attr = "raspilapse.__version__.__version__"}' in pyproject
