"""Tests for the cached JSON overlay sources.

CachedJsonSource is the shared skeleton behind ships, tide and aurora: read a
file another service writes, cache it, and keep serving the last good copy when
the file is missing or half-written. The three used to carry their own copy of
this, so it is worth pinning the contract down.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

import raspilapse.overlay.sources.json_sources as overlay_sources  # noqa: E402
from raspilapse.overlay.sources.json_sources import (  # noqa: E402
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
        src = _Source(_cfg(tmp_path / "never.json"))
        # The module's own logger, not getLogger("overlay") by name -- a rename
        # there would silently give this test zero records and a confusing pass.
        overlay_sources.logger.addHandler(caplog.handler)
        try:
            for _ in range(5):
                src._cache_time = None
                src.load()
        finally:
            overlay_sources.logger.removeHandler(caplog.handler)

        assert len([r for r in caplog.records if "not found" in r.getMessage()]) == 1

    def test_a_missing_file_is_not_stat_ed_on_every_render(self, tmp_path):
        """The backoff has to apply when nothing has *ever* loaded.

        Gating the TTL on `_cache is not None` made the stamp dead in exactly
        the case that needs it: a file that has never existed. Every render then
        paid a filesystem check -- twice per capture cycle, forever.
        """
        src = _Source(_cfg(tmp_path / "never.json"))
        assert src.load() is None
        stamped = src._cache_time
        assert stamped is not None, "the failed attempt must be recorded"

        with patch.object(Path, "exists", side_effect=AssertionError("hit the disk")):
            assert src.load() is None
        assert src._cache_time == stamped, "a cached miss must not re-stamp"

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


class TestNullMeasurements:
    """A forecast point with an explicit null level must not take the overlay down.

    `.get("level_cm", 0)` only defaults a *missing* key; a present null comes
    back as None and reaches the interpolation arithmetic as a TypeError.
    """

    @staticmethod
    def _tide(tmp_path, points):
        tmp_path.mkdir(parents=True, exist_ok=True)
        p = tmp_path / "tide.json"
        p.write_text(json.dumps({"tide_data": {"points": points}}))
        return TideData({"tide": {"enabled": True, "tide_file": str(p)}})

    def test_a_null_level_does_not_raise(self, tmp_path):
        now = datetime.now()
        src = self._tide(
            tmp_path,
            [
                {"time": (now - timedelta(hours=1)).isoformat(), "level_cm": None},
                {"time": (now + timedelta(hours=1)).isoformat(), "level_cm": 120},
            ],
        )
        assert src.get_current_level() is not None

    def test_all_nulls_still_yields_a_number(self, tmp_path):
        now = datetime.now()
        src = self._tide(
            tmp_path,
            [
                {"time": (now - timedelta(hours=1)).isoformat(), "level_cm": None},
                {"time": (now + timedelta(hours=1)).isoformat(), "level_cm": None},
            ],
        )
        assert src.get_current_level() == 0.0

    def test_a_missing_key_behaves_the_same_as_a_null(self, tmp_path):
        now = datetime.now()
        earlier = (now - timedelta(hours=1)).isoformat()
        later = (now + timedelta(hours=1)).isoformat()
        absent = self._tide(tmp_path / "a", [{"time": earlier}, {"time": later}])
        explicit = self._tide(
            tmp_path / "b",
            [{"time": earlier, "level_cm": None}, {"time": later, "level_cm": None}],
        )
        assert absent.get_current_level() == explicit.get_current_level()
