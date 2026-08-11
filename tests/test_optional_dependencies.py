"""Every optional dependency must be genuinely optional.

Not "documented as optional" -- actually absent-tolerant. The install used to
name seven apt packages plus a pip line before you could take a photo, and most
of that was for features a given camera never uses.

These tests hide a dependency from the import machinery entirely, which is the
only way to catch a top-level `import x` that nobody noticed was mandatory.
Patching the module attribute to None, as the unit tests do, cannot: the import
has already succeeded by then.

numpy and Pillow are deliberately absent from this list. They are hard
requirements of the capture path, and installing them is free -- python3-picamera2
depends on both, so anyone who can run the camera already has them.
"""

import builtins
import importlib
import sys

import pytest

# dependency -> the modules that must still import without it
OPTIONAL = {
    "astral": [
        "raspilapse.daemon",
    ],
    "requests": [
        "raspilapse.storage.upload",
        "raspilapse.video.daily",
        "raspilapse.cli.retry_uploads",
    ],
    "requests_toolbelt": [
        "raspilapse.storage.upload",
    ],
    "matplotlib": [
        "raspilapse.daemon",
        "raspilapse.video.timelapse",
        "raspilapse.video.daily",
    ],
}


def _import_without(blocked, module_name):
    """Import module_name with `blocked` hidden, in a clean module cache."""
    saved = dict(sys.modules)
    for name in list(sys.modules):
        if name.startswith("raspilapse") or name.split(".")[0] == blocked:
            del sys.modules[name]

    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] == blocked:
            raise ImportError(f"No module named {name!r} (hidden by this test)")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = guarded
    try:
        importlib.import_module(module_name)
    finally:
        builtins.__import__ = real_import
        sys.modules.clear()
        sys.modules.update(saved)


@pytest.mark.parametrize(
    ("dependency", "module_name"),
    [(dep, mod) for dep, mods in OPTIONAL.items() for mod in mods],
)
def test_module_imports_without_optional_dependency(dependency, module_name):
    _import_without(dependency, module_name)


def test_the_guard_itself_works():
    """If hiding a module did nothing, every test above would pass vacuously."""
    with pytest.raises(ImportError):
        _import_without("yaml", "raspilapse.config")
