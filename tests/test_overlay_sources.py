"""Tests for the cached JSON overlay sources.

CachedJsonSource is the shared skeleton behind ships, tide and aurora: read a
file another service writes, cache it, and keep serving the last good copy when
the file is missing or half-written. The three used to carry their own copy of
this, so it is worth pinning the contract down.
"""

import json
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.overlay_sources import (  # noqa: E402
    AuroraData,
    CachedJsonSource,
    ShipsData,
    TideData,
)


class _Source(CachedJsonSource):
    section_key = "testsec"
    path_key = "test_file"
    cache_duration = 60
    label = "test"


def _cfg(path, enabled=True):
    return {"testsec": {"enabled": enabled, "test_file": str(path)}}


@pytest.fixture
def data_file(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"value": 1}))
    return p


class TestLoading:
    def test_reads_the_file(self, data_file):
        assert _Source(_cfg(data_file)).load() == {"value": 1}

    def test_disabled_returns_none_without_touching_disk(self, tmp_path):
        assert _Source(_cfg(tmp_path / "nope.json", enabled=False)).load() is None

    def test_empty_path_returns_none(self):
        assert _Source({"testsec": {"enabled": True, "test_file": ""}}).load() is None

    def test_missing_section_is_treated_as_disabled(self):
        assert _Source({}).load() is None


class TestCaching:
    def test_second_read_within_the_ttl_does_not_hit_disk(self, data_file):
        src = _Source(_cfg(data_file))
        assert src.load() == {"value": 1}
        data_file.write_text(json.dumps({"value": 2}))
        assert src.load() == {"value": 1}, "should still be serving the cache"

    def test_expired_cache_rereads(self, data_file):
        src = _Source(_cfg(data_file))
        src.load()
        data_file.write_text(json.dumps({"value": 2}))
        src._cache_time = datetime.now() - timedelta(seconds=120)
        assert src.load() == {"value": 2}


class TestFailureHandling:
    def test_missing_file_serves_the_stale_cache(self, data_file):
        src = _Source(_cfg(data_file))
        src.load()
        data_file.unlink()
        src._cache_time = datetime.now() - timedelta(seconds=120)
        assert src.load() == {"value": 1}, "blanking the overlay is worse than stale data"

    def test_missing_file_with_no_cache_returns_none(self, tmp_path):
        assert _Source(_cfg(tmp_path / "never.json")).load() is None

    def test_corrupt_json_serves_the_stale_cache(self, data_file):
        src = _Source(_cfg(data_file))
        src.load()
        data_file.write_text("{ half written")
        src._cache_time = datetime.now() - timedelta(seconds=120)
        assert src.load() == {"value": 1}

    def test_a_permanently_missing_file_warns_only_once(self, tmp_path, caplog):
        """The overlay is rebuilt twice per capture cycle. Warning every time an
        absent file's cache expires is how weather.py once produced 72,000
        identical lines."""
        import logging

        src = _Source(_cfg(tmp_path / "never.json"))
        logger = logging.getLogger("overlay")
        logger.addHandler(caplog.handler)
        try:
            for _ in range(5):
                src._cache_time = None
                src.load()
        finally:
            logger.removeHandler(caplog.handler)

        assert len([r for r in caplog.records if "not found" in r.message]) == 1

    def test_the_warning_re_arms_once_the_file_returns(self, tmp_path):
        src = _Source(_cfg(tmp_path / "later.json"))
        src.load()
        assert src._missing_logged is True

        (tmp_path / "later.json").write_text(json.dumps({"ok": True}))
        src._cache_time = None
        src.load()
        assert src._missing_logged is False


class TestSubclasses:
    def test_each_maps_to_its_own_config_section(self):
        assert (ShipsData.section_key, ShipsData.path_key) == ("barentswatch", "ships_file")
        assert (TideData.section_key, TideData.path_key) == ("tide", "tide_file")
        assert (AuroraData.section_key, AuroraData.path_key) == ("aurora", "aurora_file")

    def test_tide_caches_longer_than_the_others(self):
        """A tide file covers 24h of points; ships move minute to minute."""
        assert TideData.cache_duration > ShipsData.cache_duration

    def test_tide_and_aurora_unwrap_their_envelope(self, tmp_path):
        tide = tmp_path / "t.json"
        tide.write_text(json.dumps({"tide_data": {"points": [1, 2]}, "cached_at": "x"}))
        assert TideData({"tide": {"enabled": True, "tide_file": str(tide)}}).get_tide_data() == {
            "points": [1, 2]
        }

        aurora = tmp_path / "a.json"
        aurora.write_text(json.dumps({"aurora_data": {"kp": 3}, "cached_at": "x"}))
        got = AuroraData(
            {"aurora": {"enabled": True, "aurora_file": str(aurora)}}
        ).get_aurora_data()
        assert got == {"kp": 3}

    def test_an_unwrapped_payload_passes_through(self, tmp_path):
        tide = tmp_path / "t.json"
        tide.write_text(json.dumps({"points": [1]}))
        assert TideData({"tide": {"enabled": True, "tide_file": str(tide)}}).get_tide_data() == {
            "points": [1]
        }

    def test_ships_does_not_unwrap(self, tmp_path):
        ships = tmp_path / "s.json"
        ships.write_text(json.dumps({"items": [{"name": "boat"}]}))
        got = ShipsData(
            {"barentswatch": {"enabled": True, "ships_file": str(ships)}}
        ).get_ships_data()
        assert got == {"items": [{"name": "boat"}]}
