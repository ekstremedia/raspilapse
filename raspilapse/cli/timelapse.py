"""`raspilapse-timelapse` -- assemble a video from captured frames."""

from raspilapse.video.timelapse import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
