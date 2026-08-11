"""`raspilapse-prune-videos` -- delete rendered videos past the retention window."""

from raspilapse.video.retention import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
