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


@pytest.mark.parametrize("name", SEQUENCE_NAMES)
def test_diagnostics_describe_the_frame_they_were_recorded_with(name):
    """A frame's diagnostics must match the settings it was taken with.

    They did not, on every handover frame. The harness read diagnostics after
    the day-to-night seeding had already run, and seed_from_metadata overwrites
    the shutter, gain and ladder position they report -- so the golden recorded
    the seed rather than the exposure. A frame carrying settings of gain 1.0
    alongside a reported applied_gain of 5.9 baked that into the baseline, and
    any later fix to the ordering would have read as a regression against it.
    """
    # Both the committed baseline and a fresh replay. Checking only the files
    # would catch a bad re-recording but not the harness bug that produced it:
    # reintroducing the ordering fault leaves the files on disk untouched, so
    # the golden alone stays green while every new recording is wrong.
    for source, frames in (
        ("golden", load_golden(name)["frames"]),
        ("replay", replay(load_sequence(name))),
    ):
        for i, frame in enumerate(frames):
            settings, diagnostics = frame["settings"], frame["diagnostics"]

            # abs=0.001: the diagnostics round gain to three places for the
            # metadata JSON, so they differ from the raw float in the last one.
            # The skew this guards against was gain 1.0 against a reported 5.9.
            assert diagnostics["applied_gain"] == pytest.approx(
                settings["AnalogueGain"], rel=1e-4, abs=0.001
            ), f"{name} {source} frame {i}: diagnostics gain disagrees with the settings"

            # abs=1.5 microseconds: ExposureTime is int(seconds * 1e6), a
            # truncation, while applied_exposure_s is rounded to six places, so
            # the two legitimately differ in the last microsecond. The skew this
            # guards against was four orders of magnitude larger.
            assert diagnostics["applied_exposure_s"] * 1e6 == pytest.approx(
                settings["ExposureTime"], rel=1e-4, abs=1.5
            ), f"{name} {source} frame {i}: diagnostics exposure disagrees with the settings"
