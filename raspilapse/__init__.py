"""Continuous adaptive timelapse capture for the Raspberry Pi camera.

The package is laid out by what each part talks to:

    camera/    the sensor -- capture, metering, and every exposure decision
    overlay/   the rendered frame; optional, and optional at import too
    video/     finished frames -- ffmpeg assembly, keograms, slitscans
    storage/   the capture database and the upload service
    cli/       one module per console script

Only `camera` and `config` are needed to take a photo. Everything else is a
feature you can leave uninstalled.

The version lives in __version__.py and is re-exported here. It used to be
declared in both places, and the two disagreed: this module said 0.1.0 while
__version__.py, pyproject and the CHANGELOG all said 1.4.0.
"""

from raspilapse.__version__ import __version__

__all__ = ["__version__"]
