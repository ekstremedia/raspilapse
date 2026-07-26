"""Shared pytest fixtures.

The autouse fixture here exists because the suite used to write into the real
logs/ directory. logs/overlay.log reached 5.4 MB of test fixtures -- entries
like "Ships file not found: /tmp/nonexistent_ships_test.json" and tracebacks
from deliberately unwritable paths -- alongside two stray zero-byte files,
logs/script_name.log and logs/test_script.log, created by the logging tests.
"""

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import logging_config  # noqa: E402

# src/ modules support being imported both as `src.x` and as bare `x`, because
# the systemd units run the scripts directly. Test modules use both styles, and
# Python treats the two as separate module objects with separate module-level
# state. Anything holding a process-wide cache therefore has to be reset under
# both names.
_STATEFUL_MODULES = ("weather",)


def _reset_module_state() -> None:
    logging_config.reset()
    for name in _STATEFUL_MODULES:
        for candidate in (name, f"src.{name}"):
            module = sys.modules.get(candidate)
            reset = getattr(module, "reset_cache", None)
            if reset is not None:
                reset()


@pytest.fixture(autouse=True)
def isolate_logging(tmp_path, monkeypatch):
    """Send log output to a per-test temp directory and reset cached state."""
    monkeypatch.setenv("RASPILAPSE_LOG_DIR", str(tmp_path / "logs"))

    # get_logger memoises by script name. Without a reset between tests, the
    # second test to ask for a given name gets the first test's handlers,
    # pointing at the first test's tmp_path.
    _reset_module_state()

    # If the suite is run from a systemd job, JOURNAL_STREAM would be set and
    # console: auto would resolve differently than it does interactively.
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)

    yield

    _reset_module_state()

    # Anything created with logging.getLogger() directly, bypassing get_logger.
    for logger in logging.Logger.manager.loggerDict.values():
        if isinstance(logger, logging.Logger):
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """A temp log directory, for tests that assert on log file contents."""
    path = tmp_path / "logs"
    path.mkdir(exist_ok=True)
    monkeypatch.setenv("RASPILAPSE_LOG_DIR", str(path))
    logging_config.reset()
    return str(path)
