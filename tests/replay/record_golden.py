"""Record golden outputs for every replay sequence.

Run this ONCE against known-good code, commit the result, and never run it
again to "fix" a failing test -- re-recording turns a regression into a new
baseline, which is the one way this whole apparatus can fail silently.

    python3 tests/replay/record_golden.py

If a golden file genuinely must change (an intended behaviour change, e.g. the
exposure ladder), re-record in its own commit, with nothing else in the diff,
so the exposure change is reviewable as an exposure change.
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
