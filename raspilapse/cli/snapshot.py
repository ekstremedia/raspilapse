"""`raspilapse-snapshot` -- one frame, with no adaptive exposure logic."""

from raspilapse.camera.capture import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
