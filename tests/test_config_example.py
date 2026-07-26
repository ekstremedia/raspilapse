"""Keep config.example.yml and the code that reads it in step.

The example had drifted badly in both directions: keys the code needed were
missing (so a fresh clone ran a different code path than the author's camera),
and keys nobody read were still documented as if they did something. These
tests fail on either kind of drift.
"""

import ast
import re
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

EXAMPLE = PROJECT_ROOT / "config" / "config.example.yml"
CODE_DIRS = (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts")

# Keys that are read dynamically or only by external consumers, so the string
# never appears literally in this repo. Each needs a reason.
ALLOWED_UNUSED = {
    # Read by the webserver that serves the image tree, not by us.
    "output.symlink_latest.path",
}

# Config the code reads that the example deliberately leaves out.
ALLOWED_UNDOCUMENTED = {
    # Absent means "keep everything"; the example ships an explicit value.
    "database.retention_days",
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


def test_example_parses(example_config):
    assert isinstance(example_config, dict)
    assert example_config, "config.example.yml is empty"


def _attributes_never_loaded():
    """Return `self.<attr>` names that are assigned but never read, per file.

    An attribute written and never read is dead, and so is whatever config key
    fed it. `brightness_tolerance` survived the previous pass exactly this way:
    the key was read into `self._brightness_tolerance`, nothing ever looked at
    it, and a check for "does the name appear in the source" saw the string in
    the `.get()` call and passed.
    """
    dead = {}
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
                        dead[key.value] = f"{path}:{node.lineno} self.{target.attr}"
    return dead


def test_every_documented_key_is_read_somewhere(example_config, code_text):
    """A key in the example that no code reads is a promise the code doesn't keep."""
    quoted = set(re.findall(r"""["']([a-zA-Z_][a-zA-Z_0-9]*)["']""", code_text))

    unread = []
    for path in _leaf_paths(example_config):
        if path in ALLOWED_UNUSED:
            continue
        leaf = path.split(".")[-1]
        if leaf not in quoted:
            unread.append(path)

    assert not unread, (
        "config.example.yml documents keys nothing reads:\n  "
        + "\n  ".join(sorted(unread))
        + "\nRemove them, or add them to ALLOWED_UNUSED with a reason."
    )


def test_no_documented_key_feeds_a_dead_attribute(example_config):
    """Being read is not enough -- the value has to go somewhere that matters.

    Catches the case the test above cannot: a key read into an attribute that
    nothing ever loads. The key looks live because its name appears in the
    source, but changing it in config has no effect whatsoever.
    """
    dead = _attributes_never_loaded()
    documented = {p.split(".")[-1] for p in _leaf_paths(example_config)}

    offenders = sorted(f"{key}  ->  {where}" for key, where in dead.items() if key in documented)
    assert not offenders, (
        "config.example.yml documents keys whose value is read into an attribute "
        "that is never used:\n  " + "\n  ".join(offenders)
    )


def test_top_level_sections_are_documented(example_config, code_text):
    """Every config section the code looks up must exist in the example.

    Only matches lookups on a variable actually called `config` (or
    `self.config`), so nested `.get("key")` calls on other dicts don't count.
    """
    # The lookbehind is what stops this matching inside names like
    # weather_config.get("endpoint") -- those are lookups into a sub-dict, not
    # top-level sections.
    pattern = r"""(?<![\w])(?:self\.)?config(?:\.get)?[\[(]["']([a-z_]+)["']"""
    sections = set(re.findall(pattern, code_text))

    missing = sorted(s for s in sections if s not in example_config)
    assert (
        not missing
    ), f"config sections read by code but absent from config.example.yml: {missing}"


def test_no_personal_values_leaked():
    """The example is a public template, not a copy of one camera's config."""
    text = EXAMPLE.read_text()
    for pattern, what in (
        (r"\.local/", "a .local hostname"),
        (r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "a UUID"),
        (r"api_key:\s*[\"'](?!your-|<)[^\"'\s]{16,}", "what looks like a real API key"),
    ):
        assert not re.search(pattern, text), f"config.example.yml contains {what}"


def test_interval_lives_in_adaptive_timelapse(example_config):
    """There used to be two intervals: timelapse.interval (3s, read by nothing)
    next to adaptive_timelapse.interval (30s, the real one)."""
    assert "timelapse" not in example_config
    assert "interval" in example_config["adaptive_timelapse"]
