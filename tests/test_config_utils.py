"""Tests for the shared config and CLI helpers.

The two parameterised helpers matter most: parse_time_arg and format_duration
existed twice before this module, with quietly different defaults (1h vs 24h,
.1f vs .0f). Merging them without those parameters would have silently changed
both tools' output.
"""

from datetime import timedelta

import pytest
import yaml

from raspilapse.config import (
    PROJECT_ROOT,
    format_duration,
    get_db_path,
    load_config,
    merge_defaults,
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

    def test_relative_falls_back_to_the_project_root(self, tmp_path, monkeypatch):
        """Not the working directory -- that is how stray files got scattered.

        Run from somewhere else entirely: with the repo as cwd both the correct
        and the incorrect resolution land under PROJECT_ROOT, so the assertion
        holds either way and proves nothing.
        """
        monkeypatch.chdir(tmp_path)
        assert resolve_config_path("config/config.yml") == PROJECT_ROOT / "config" / "config.yml"


class TestLoadConfig:
    def test_reads_yaml(self, tmp_path):
        p = tmp_path / "c.yml"
        p.write_text(yaml.dump({"a": {"b": 1}}))
        assert load_config(str(p), defaults=False) == {"a": {"b": 1}}

    def test_empty_file_is_an_empty_dict_not_none(self, tmp_path):
        p = tmp_path / "c.yml"
        p.write_text("")
        assert load_config(str(p), defaults=False) == {}

    def test_defaults_are_applied_by_default(self, tmp_path):
        """The whole point: a config file only says what it wants to change."""
        p = tmp_path / "c.yml"
        p.write_text(yaml.dump({"output": {"project_name": "mycam"}}))

        config = load_config(str(p))

        assert config["output"]["project_name"] == "mycam", "what the file says wins"
        assert config["output"]["quality"] == 85, "and the rest is filled in"
        assert config["adaptive_timelapse"]["interval"] == 30

    def test_an_empty_file_still_yields_a_usable_config(self, tmp_path):
        p = tmp_path / "c.yml"
        p.write_text("")
        assert load_config(str(p))["adaptive_timelapse"]["interval"] == 30

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(str(tmp_path / "nope.yml"))

    def test_bad_yaml_raises(self, tmp_path):
        p = tmp_path / "c.yml"
        p.write_text("{ not: valid: [[[")
        with pytest.raises(yaml.YAMLError):
            load_config(str(p))


class TestMergeDefaults:
    def test_config_wins_over_defaults(self):
        merged = merge_defaults({"a": 1}, {"a": 2, "b": 3})
        assert merged == {"a": 1, "b": 3}

    def test_merges_nested_dicts_rather_than_replacing_them(self):
        merged = merge_defaults({"a": {"x": 1}}, {"a": {"x": 0, "y": 2}})
        assert merged == {"a": {"x": 1, "y": 2}}

    def test_lists_replace_rather_than_merge(self):
        """colour_gains is [red, blue]; a half-merged pair is worse than either."""
        merged = merge_defaults({"gains": [1.0]}, {"gains": [1.8, 2.0]})
        assert merged == {"gains": [1.0]}

    def test_neither_argument_is_modified(self):
        config = {"a": {"x": 1}}
        defaults = {"a": {"y": 2}}

        merge_defaults(config, defaults)

        assert config == {"a": {"x": 1}}
        assert defaults == {"a": {"y": 2}}

    def test_none_config_yields_the_defaults(self):
        assert merge_defaults(None, {"a": 1}) == {"a": 1}

    def test_an_explicit_false_is_not_treated_as_absent(self):
        """`.get(key) or default` would turn a deliberate false back on."""
        merged = merge_defaults({"enabled": False}, {"enabled": True})
        assert merged["enabled"] is False


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
