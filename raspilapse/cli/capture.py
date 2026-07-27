"""`raspilapse-capture` -- the continuous capture daemon.

The argparse body still lives in raspilapse.daemon. This module exists so the
console script has a stable target while that is untangled.
"""

from raspilapse.daemon import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
