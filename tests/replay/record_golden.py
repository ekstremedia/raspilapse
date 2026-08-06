"""Record golden outputs for every replay sequence.

Run this against known-good code, commit the result, and never run it to "fix"
a failing test -- re-recording turns a regression into a new baseline, which is
the one way this whole apparatus can fail silently.

    python3 tests/replay/record_golden.py

The bar for re-recording: the commit's whole purpose is the behaviour change,
the commit message says so, and something other than these tests says the new
behaviour is better. `compare.py` is that something -- it runs both
controllers against the same recorded light, closed-loop, and reports which
exposes it more accurately and with less flicker.

Re-recorded twice so far. For the exposure ladder; and for the white balance
fix, where `fixed_colour_gains` stopped being overridden by a reference the
camera learned from its own manual frames. That second one moved
`settings.ColourGains` on 749 of 3239 frames and no other field at all -- not
one exposure decision changed -- and what said the new behaviour was better
was the camera's own database rather than anything here: a one-frame colour
step at each of seven consecutive dusks, and daylight gains creeping 2.500 to
2.547 over nine days against a config that asked for 2.5.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.replay.harness import (  # noqa: E402
    GOLDEN_DIR,
    SEQUENCE_DIR,
    dump_frames,
    load_sequence,
    replay,
)


def main() -> int:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    names = sorted(p.stem for p in SEQUENCE_DIR.glob("*.json"))
    if not names:
        print("No sequences found. Run extract_sequences.py first.")
        return 1

    for name in names:
        sequence = load_sequence(name)
        results = replay(sequence)
        path = GOLDEN_DIR / f"{name}.json"
        dump_frames(path, {"name": name}, results)
        modes = {}
        for r in results:
            modes[r["mode"]] = modes.get(r["mode"], 0) + 1
        print(f"  {name}: {len(results)} frames -> {path}   modes={modes}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
