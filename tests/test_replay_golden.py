"""The refactor must not change a single exposure decision.

Each recorded sequence is replayed through the controller and compared against
output recorded from the code as it was before the package move. A failure here
means the reorganisation changed behaviour, which it is not allowed to do.

See tests/replay/record_golden.py before re-recording anything.
"""

import pytest

from tests.replay.harness import GOLDEN_DIR, SEQUENCE_DIR, load_golden, load_sequence, replay

SEQUENCE_NAMES = sorted(p.stem for p in SEQUENCE_DIR.glob("*.json"))


def test_sequences_exist():
    """A silent zero-sequence run would make every test below vacuous."""
    assert SEQUENCE_NAMES, "no replay sequences found in tests/replay/sequences/"
    for name in SEQUENCE_NAMES:
        assert (GOLDEN_DIR / f"{name}.json").exists(), f"{name} has no golden file"


@pytest.mark.parametrize("name", SEQUENCE_NAMES)
def test_replay_matches_golden(name):
    sequence = load_sequence(name)
    golden = load_golden(name)
    actual = replay(sequence)

    assert len(actual) == len(
        golden["frames"]
    ), f"{name}: replayed {len(actual)} frames, golden has {len(golden['frames'])}"

    for i, (got, want) in enumerate(zip(actual, golden["frames"])):
        if got != want:
            timestamp = sequence["frames"][i].get("timestamp", "?")
            differing = sorted(k for k in set(got) | set(want) if got.get(k) != want.get(k))
            pytest.fail(
                f"{name} frame {i} ({timestamp}) diverged in {differing}\n"
                f"  golden: { {k: want.get(k) for k in differing} }\n"
                f"  actual: { {k: got.get(k) for k in differing} }"
            )


@pytest.mark.parametrize("name", SEQUENCE_NAMES)
def test_replay_is_deterministic(name):
    """Two runs of the same sequence must agree.

    Guards the harness itself: if replay() ever picked up wall-clock time,
    a random seed or leaked state between controllers, every golden comparison
    above would start failing for reasons that have nothing to do with the
    code under test.
    """
    sequence = load_sequence(name)
    assert replay(sequence) == replay(sequence)


def test_sequences_exercise_every_mode():
    """The golden files are only as good as the paths they touch."""
    seen = set()
    for name in SEQUENCE_NAMES:
        for frame in load_golden(name)["frames"]:
            seen.add(frame["mode"])
    assert seen == {"day", "transition", "night"}, f"modes covered: {sorted(seen)}"
