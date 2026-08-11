"""Shared pytest fixtures.

The autouse fixture here exists because the suite used to write into the real
logs/ directory. logs/overlay.log reached 5.4 MB of test fixtures -- entries
like "Ships file not found: /tmp/nonexistent_ships_test.json" and tracebacks
from deliberately unwritable paths -- alongside two stray zero-byte files,
logs/script_name.log and logs/test_script.log, created by the logging tests.
"""

import logging

import pytest

from raspilapse import logging_setup
from raspilapse.overlay.sources import weather

# Every module holding process-wide cached state. This used to have to reset
# each one twice, under both `x` and `src.x`, because the flat layout let the
# same file be imported under two names with two copies of its module-level
# state. There is one name per module now.
_STATEFUL_MODULES = (weather,)


def _reset_module_state() -> None:
    logging_setup.reset()
    for module in _STATEFUL_MODULES:
        module.reset_cache()


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
    logging_setup.reset()
    return str(path)
