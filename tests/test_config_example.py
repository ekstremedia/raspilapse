"""Keep the config, the defaults and the documentation in step.

There are three artefacts now, and each can drift from the others:

    raspilapse/config.py DEFAULTS   what the code falls back to
    config/config.example.yml       the starter file, deliberately short
    docs/CONFIG-REFERENCE.yml       every setting, annotated

The reference had drifted badly in both directions before: keys the code needed
were missing, so a fresh clone ran a different code path than the author's
camera, and keys nothing read were still documented as if they did something.

The load-bearing test here is test_every_hard_indexed_key_has_a_default. It is
what allows the example to be short: the code indexes some config paths without
a fallback, and any such path not covered by DEFAULTS is a KeyError waiting for
whoever writes a small config file.
"""

import ast
import re
from pathlib import Path

import pytest
import yaml

from raspilapse.config import DEFAULTS, load_config, merge_defaults

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXAMPLE = PROJECT_ROOT / "config" / "config.example.yml"
REFERENCE = PROJECT_ROOT / "docs" / "CONFIG-REFERENCE.yml"
CODE_DIRS = (PROJECT_ROOT / "raspilapse", PROJECT_ROOT / "scripts")

# Keys that are read dynamically or only by external consumers, so the string
# never appears literally in this repo. Each needs a reason.
ALLOWED_UNUSED = {
    # Read by the webserver that serves the image tree, not by us.
    "output.symlink_latest.path",
}

# Roots whose subscripts are config lookups. `adaptive_config` and friends are
# sub-dicts lifted out of the config, so their paths need re-rooting.
CONFIG_ROOTS = {
    "config": (),
    "adaptive_config": ("adaptive_timelapse",),
}


def _leaf_paths(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict) and value:
                yield from _leaf_paths(value, path)
            else:
                yield path
    else:
        yield prefix


def _subscript_chain(node):
    """Unwind a['x']['y'] into (root name, ['x', 'y']), or (None, None)."""
    keys = []
    current = node
    while isinstance(current, ast.Subscript):
        key = current.slice
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            return None, None
        keys.append(key.value)
        current = current.value
    keys.reverse()
    if isinstance(current, ast.Name):
        return current.id, keys
    if isinstance(current, ast.Attribute):
        return current.attr, keys
    return None, None


def _hard_indexed_paths():
    """Config paths the code subscripts without a fallback.

    These are the ones that raise KeyError rather than returning a default, so
    every one of them has to be in DEFAULTS for a short config file to work.
    """
    found = {}
    for directory in CODE_DIRS:
        for path in sorted(directory.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text())
            parents = {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
            for node in ast.walk(tree):
                if not isinstance(node, ast.Subscript):
                    continue
                # Only the outermost link of a chain carries the full path.
                parent = parents.get(id(node))
                if isinstance(parent, ast.Subscript) and parent.value is node:
                    continue
                root, keys = _subscript_chain(node)
                if root not in CONFIG_ROOTS or not keys:
                    continue
                full = CONFIG_ROOTS[root] + tuple(keys)
                found.setdefault(full, f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    return found


def _in_defaults(path_parts):
    node = DEFAULTS
    for part in path_parts:
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


@pytest.fixture(scope="module")
def reference_config():
    return yaml.safe_load(REFERENCE.read_text())


@pytest.fixture(scope="module")
def example_config():
    return yaml.safe_load(EXAMPLE.read_text())


@pytest.fixture(scope="module")
def code_text():
    parts = []
    for directory in CODE_DIRS:
        for path in directory.rglob("*.py"):
            if "__pycache__" not in path.parts:
                parts.append(path.read_text())
    return "\n".join(parts)


def test_both_files_parse(example_config, reference_config):
    assert isinstance(example_config, dict) and example_config
    assert isinstance(reference_config, dict) and reference_config


def test_every_hard_indexed_key_has_a_default():
    """The invariant that lets config.example.yml be short.

    Anything the code subscripts without a fallback must be in DEFAULTS, or a
    config file that omits it raises KeyError. Adding `config["new_section"]`
    to the code without a default breaks here rather than on someone's camera.
    """
    missing = {
        ".".join(path): where
        for path, where in sorted(_hard_indexed_paths().items())
        if not _in_defaults(path)
    }
    assert (
        not missing
    ), "config paths indexed without a fallback and with no default:\n  " + "\n  ".join(
        f"{path}  ({where})" for path, where in missing.items()
    )


def test_the_hard_index_scan_finds_something():
    """A scan that silently found nothing would make the test above vacuous."""
    found = _hard_indexed_paths()
    assert len(found) > 10, f"only found {len(found)} hard-indexed paths; the scan is broken"


def test_example_needs_no_defaults_to_be_valid(example_config):
    """The starter file must be loadable and complete once defaults apply."""
    merged = merge_defaults(example_config)
    for path in _hard_indexed_paths():
        node = merged
        for part in path:
            assert (
                isinstance(node, dict) and part in node
            ), f"{'.'.join(path)} is missing from the example even with defaults applied"
            node = node[part]


def test_example_stays_short():
    """Its whole purpose is being readable in one screen."""
    lines = EXAMPLE.read_text().splitlines()
    assert len(lines) < 120, (
        f"config.example.yml is {len(lines)} lines. It replaced a 681-line file that "
        "newcomers bounced off; settings belong in docs/CONFIG-REFERENCE.yml."
    )


def _empty_dict_paths(node, prefix=""):
    """Paths in DEFAULTS whose value is `{}`.

    `logging: {}` and `camera.controls: {}` exist so that code subscripting
    them gets a dict rather than a KeyError. They set no values, so there is
    nothing for the reference to document -- but _leaf_paths reports them as
    leaves, because an empty dict has no children to recurse into.
    """
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and not value:
            yield path
        elif isinstance(value, dict):
            yield from _empty_dict_paths(value, path)


def test_defaults_are_documented(reference_config):
    """Every default must appear in the reference, or nobody can discover it."""
    documented = set(_leaf_paths(reference_config))
    placeholders = set(_empty_dict_paths(DEFAULTS))

    undocumented = [
        path for path in _leaf_paths(DEFAULTS) if path and path not in documented | placeholders
    ]
    assert (
        not undocumented
    ), "DEFAULTS has keys docs/CONFIG-REFERENCE.yml does not document:\n  " + "\n  ".join(
        sorted(undocumented)
    )


def _attributes_never_loaded():
    """Return `self.<attr>` names that are assigned but never read, per file.

    An attribute written and never read is dead, and so is whatever config key
    fed it. `brightness_tolerance` survived a previous pass exactly this way:
    the key was read into `self._brightness_tolerance`, nothing ever looked at
    it, and a check for "does the name appear in the source" saw the string in
    the `.get()` call and passed.
    """
    # A list, not a dict keyed by name: `enabled` is read into a dozen
    # different attributes, and a dict would keep only the last one seen --
    # quietly discarding a real offender behind an innocent namesake.
    dead = []
    for directory in CODE_DIRS:
        for path in sorted(directory.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text())
            # Any attribute read, whatever the receiver -- DatabaseConfig sets
            # self.db_path and CaptureDatabase reads it as self.config.db_path,
            # so restricting this to `self.X` would call it dead.
            loaded = {
                n.attr
                for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load)
            }
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
                    continue
                fn = node.value.func
                if not (isinstance(fn, ast.Attribute) and fn.attr == "get" and node.value.args):
                    continue
                key = node.value.args[0]
                if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                    continue
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                        and target.attr not in loaded
                    ):
                        dead.append((key.value, f"{path}:{node.lineno} self.{target.attr}"))
    return dead


def test_every_documented_key_is_read_somewhere(reference_config, code_text):
    """A documented key that no code reads is a promise the code doesn't keep."""
    quoted = set(re.findall(r"""["']([a-zA-Z_][a-zA-Z_0-9]*)["']""", code_text))

    unread = []
    for path in _leaf_paths(reference_config):
        if path in ALLOWED_UNUSED:
            continue
        leaf = path.split(".")[-1]
        if leaf not in quoted:
            unread.append(path)

    assert not unread, (
        "docs/CONFIG-REFERENCE.yml documents keys nothing reads:\n  "
        + "\n  ".join(sorted(unread))
        + "\nRemove them, or add them to ALLOWED_UNUSED with a reason."
    )


def test_no_documented_key_feeds_a_dead_attribute(reference_config):
    """Being read is not enough -- the value has to go somewhere that matters.

    Catches the case the test above cannot: a key read into an attribute that
    nothing ever loads. The key looks live because its name appears in the
    source, but changing it in config has no effect whatsoever.
    """
    dead = _attributes_never_loaded()
    documented = {p.split(".")[-1] for p in _leaf_paths(reference_config)}

    offenders = sorted(f"{key}  ->  {where}" for key, where in dead if key in documented)
    assert not offenders, (
        "the reference documents keys whose value is read into an attribute "
        "that is never used:\n  " + "\n  ".join(offenders)
    )


def test_top_level_sections_are_documented(reference_config, code_text):
    """Every config section the code looks up must exist in the reference.

    Only matches lookups on a variable actually called `config` (or
    `self.config`), so nested `.get("key")` calls on other dicts don't count.
    """
    # The lookbehind is what stops this matching inside names like
    # weather_config.get("endpoint") -- those are lookups into a sub-dict, not
    # top-level sections.
    pattern = r"""(?<![\w])(?:self\.)?config(?:\.get)?[\[(]["']([a-z_]+)["']"""
    sections = set(re.findall(pattern, code_text))

    missing = sorted(s for s in sections if s not in reference_config)
    assert not missing, f"config sections read by code but absent from the reference: {missing}"


@pytest.mark.parametrize("path", [EXAMPLE, REFERENCE])
def test_no_personal_values_leaked(path):
    """These are public templates, not a copy of one camera's config."""
    text = path.read_text()
    for pattern, what in (
        (r"\.local/", "a .local hostname"),
        (r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "a UUID"),
        (r"api_key:\s*[\"'](?!your-|<)[^\"'\s]{16,}", "what looks like a real API key"),
    ):
        assert not re.search(pattern, text), f"{path.name} contains {what}"


def test_interval_lives_in_adaptive_timelapse(reference_config):
    """There used to be two intervals: timelapse.interval (3s, read by nothing)
    next to adaptive_timelapse.interval (30s, the real one)."""
    assert "timelapse" not in reference_config
    assert "interval" in reference_config["adaptive_timelapse"]


def test_loading_the_example_applies_defaults():
    """The seam itself: load_config must merge, and must be able not to."""
    with_defaults = load_config(EXAMPLE)
    raw = load_config(EXAMPLE, defaults=False)

    assert with_defaults["system"]["save_metadata"] is True
    assert "system" not in raw, "the example should not need to spell out system settings"
