"""`raspilapse-daily` -- yesterday's video, keogram and slitscan, then upload."""

from raspilapse.video.daily import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
