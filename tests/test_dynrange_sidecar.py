"""The DNG sidecar: keeper cadence and the retention prune."""

import os

from raspilapse.dynrange.sidecar import keep_sidecar, prune_sidecars


class TestKeepSidecar:
    def test_frame_zero_is_always_a_keeper(self):
        """A fresh install produces its first negative immediately."""
        assert keep_sidecar(0, 20) is True

    def test_cadence(self):
        keepers = [i for i in range(45) if keep_sidecar(i, 20)]
        assert keepers == [0, 20, 40]

    def test_zero_or_negative_cadence_disables(self):
        assert keep_sidecar(0, 0) is False
        assert keep_sidecar(40, -5) is False


def dng(root, name, age):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"negative")
    stamp = 1_700_000_000 - age
    os.utime(path, (stamp, stamp))
    return path


class TestPruneSidecars:
    def test_oldest_beyond_the_cap_are_removed(self, tmp_path):
        oldest = dng(tmp_path, "2026/08/01/a.dng", age=300)
        middle = dng(tmp_path, "2026/08/02/b.dng", age=200)
        newest = dng(tmp_path, "2026/08/03/c.dng", age=100)
        assert prune_sidecars(str(tmp_path), max_files=2) == 1
        assert not oldest.exists()
        assert middle.exists() and newest.exists()

    def test_under_the_cap_nothing_happens(self, tmp_path):
        kept = dng(tmp_path, "a.dng", age=300)
        assert prune_sidecars(str(tmp_path), max_files=5) == 0
        assert kept.exists()

    def test_only_dng_files_are_candidates(self, tmp_path):
        for name in ("frame.jpg", "frame_metadata.json", "notes.txt"):
            (tmp_path / name).write_bytes(b"bystander")
        dng(tmp_path, "old.dng", age=300)
        dng(tmp_path, "new.dng", age=100)
        prune_sidecars(str(tmp_path), max_files=1)
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "frame.jpg",
            "frame_metadata.json",
            "new.dng",
            "notes.txt",
        ]

    def test_missing_directory_is_a_quiet_noop(self, tmp_path):
        assert prune_sidecars(str(tmp_path / "absent"), max_files=5) == 0

    def test_nonpositive_cap_never_prunes(self, tmp_path):
        survivor = dng(tmp_path, "a.dng", age=300)
        assert prune_sidecars(str(tmp_path), max_files=0) == 0
        assert survivor.exists()
