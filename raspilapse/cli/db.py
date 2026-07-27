"""`raspilapse-db` -- capture database statistics and maintenance."""

from raspilapse.storage.database import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
