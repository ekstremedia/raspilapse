"""Tests for the shared config and CLI helpers.

The two parameterised helpers matter most: parse_time_arg and format_duration
existed twice before this module, with quietly different defaults (1h vs 24h,
.1f vs .0f). Merging them without those parameters would have silently changed
both tools' output.
"""

import os
import sys
from datetime import timedelta

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config_utils import (  # noqa: E402
    PROJECT_ROOT,
    format_duration,
    get_db_path,
    load_config,
    parse_time_arg,
    resolve_config_path,
)


class TestParseTimeArg:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("5m", timedelta(minutes=5)),
            ("90m", timedelta(minutes=90)),
            ("1h", timedelta(hours=1)),
            ("24h", timedelta(hours=24)),
            ("7d", timedelta(days=7)),
            ("6", timedelta(hours=6)),
        ],
    )
    def test_units(self, text, expected):
        assert parse_time_arg(text) == expected

    def test_leading_dash_is_ignored(self):
        """Both '24h' and '-24h' mean the last 24 hours."""
        assert parse_time_arg("-24h") == parse_time_arg("24h")

    def test_case_and_whitespace_insensitive(self):
        assert parse_time_arg("  7D  ") == timedelta(days=7)

    def test_empty_uses_the_callers_default(self):
        """db_stats wants 1h and db_graphs wants 24h, so this is not shared."""
        assert parse_time_arg("", timedelta(hours=1)) == timedelta(hours=1)
        assert parse_time_arg("", timedelta(hours=24)) == timedelta(hours=24)

    @pytest.mark.parametrize("bad", ["abc", "5x", "h", "1.5h"])
    def test_rejects_nonsense(self, bad):
        with pytest.raises(ValueError, match="Invalid time format"):
            parse_time_arg(bad)


class TestFormatDuration:
    @pytest.mark.parametrize(
        "seconds,expected",
        [(45, "45.0s"), (90, "1.5m"), (7200, "2.0h"), (129600, "1.5d")],
    )
    def test_units(self, seconds, expected):
        assert format_duration(seconds) == expected

    def test_precision_is_a_parameter(self):
        """db_stats uses 1 decimal, db_graphs uses 0. Changing either silently
        would alter that tool's output."""
        assert format_duration(90, precision=1) == "1.5m"
        assert format_duration(90, precision=0) == "2m"

    def test_boundaries(self):
        assert format_duration(59).endswith("s")
        assert format_duration(60).endswith("m")
        assert format_duration(3600).endswith("h")
        assert format_duration(86400).endswith("d")


class TestResolveConfigPath:
    def test_none_gives_the_project_default(self):
        assert resolve_config_path() == PROJECT_ROOT / "config" / "config.yml"

    def test_absolute_is_returned_as_is(self, tmp_path):
        p = tmp_path / "x.yml"
        assert resolve_config_path(str(p)) == p

    def test_relative_falls_back_to_the_project_root(self):
        """Not the working directory -- that is how stray files got scattered."""
        assert resolve_config_path("config/config.yml").is_absolute()
        assert str(resolve_config_path("config/config.yml")).startswith(str(PROJECT_ROOT))


class TestLoadConfig:
    def test_reads_yaml(self, tmp_path):
        p = tmp_path / "c.yml"
        p.write_text(yaml.dump({"a": {"b": 1}}))
        assert load_config(str(p)) == {"a": {"b": 1}}

    def test_empty_file_is_an_empty_dict_not_none(self, tmp_path):
        p = tmp_path / "c.yml"
        p.write_text("")
        assert load_config(str(p)) == {}

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(str(tmp_path / "nope.yml"))

    def test_bad_yaml_raises(self, tmp_path):
        p = tmp_path / "c.yml"
        p.write_text("{ not: valid: [[[")
        with pytest.raises(yaml.YAMLError):
            load_config(str(p))


class TestGetDbPath:
    def test_uses_the_configured_path(self):
        assert get_db_path({"database": {"path": "/abs/x.db"}}) == "/abs/x.db"

    def test_relative_paths_resolve_against_the_project_root(self):
        assert get_db_path({"database": {"path": "data/x.db"}}) == str(PROJECT_ROOT / "data/x.db")

    def test_falls_back_when_the_config_is_unreadable(self, tmp_path):
        got = get_db_path(config_path=str(tmp_path / "missing.yml"))
        assert got == str(PROJECT_ROOT / "data" / "timelapse.db")

    def test_falls_back_when_the_section_is_absent(self):
        assert get_db_path({}) == str(PROJECT_ROOT / "data" / "timelapse.db")
