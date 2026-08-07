"""Tests for the capture watchdog's escalation and reboot gate.

check_service.sh had no tests. That was survivable while its worst action was a
service restart, but it can reboot the machine, and it now shares a reboot
floor with check_network.sh -- two independent things that reboot the camera
need the floor between them to actually work.

Driven with stub systemctl on PATH and a real temp image directory, so the
staleness check runs against genuine file mtimes rather than a mocked clock.
"""

import os
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_service.sh"

STUBS = {
    "systemctl": r"""#!/bin/bash
echo "systemctl $*" >>"$CALLS"
case "$*" in
    *"is-active"*) exit "${STUB_ACTIVE:-0}" ;;
esac
exit 0
""",
    "logger": "#!/bin/bash\nexit 0\n",
    "sync": "#!/bin/bash\nexit 0\n",
}


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        p = bin_dir / name
        p.write_text(body)
        p.chmod(0o755)

    images = tmp_path / "images"
    images.mkdir()
    config = tmp_path / "config.yml"
    config.write_text(f"output:\n  directory: {images}\n")

    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS": str(tmp_path / "calls"),
        "RASPILAPSE_WATCHDOG_STATE": str(tmp_path / "watchdog_state"),
        "RASPILAPSE_LAST_REBOOT": str(tmp_path / "last_reboot"),
        "RASPILAPSE_CONFIG": str(config),
        "_images": str(images),
    }


def frame(env, age_seconds):
    """Put a JPEG in the image directory with a chosen age."""
    p = Path(env["_images"]) / "frame.jpg"
    p.write_bytes(b"x")
    when = time.time() - age_seconds
    os.utime(p, (when, when))


def run(env, state=None):
    if state is not None:
        Path(env["RASPILAPSE_WATCHDOG_STATE"]).write_text(state + "\n")
    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        env={**os.environ, **{k: v for k, v in env.items() if not k.startswith("_")}},
        capture_output=True,
        text=True,
    )
    calls = Path(env["CALLS"])
    return proc, calls.read_text() if calls.exists() else ""


class TestStallDetection:
    """The one thing this watchdog looks at: the age of the newest frame."""

    def test_a_recent_frame_is_left_alone(self, env):
        frame(env, 30)
        _, calls = run(env)
        assert "restart" not in calls
        assert "reboot" not in calls

    def test_a_stale_frame_restarts_the_service(self, env):
        frame(env, 1200)
        _, calls = run(env)
        assert "systemctl restart raspilapse.service" in calls

    def test_a_dead_service_is_left_to_restart_always(self, env):
        frame(env, 1200)
        env |= {"STUB_ACTIVE": "1"}
        _, calls = run(env)
        assert "restart" not in calls


class TestRebootGate:
    """The floor shared with check_network.sh, so the two cannot ping-pong."""

    def test_it_reboots_once_the_restarts_have_not_helped(self, env):
        frame(env, 1200)
        _, calls = run(env, state="2")
        assert "systemctl reboot" in calls
        assert Path(env["RASPILAPSE_LAST_REBOOT"]).exists()

    def test_a_recent_netwatch_reboot_blocks_this_one(self, env):
        """The shared floor. Without it, a camera that is both stalled and
        offline gets rebooted by whichever watchdog notices first, forever."""
        frame(env, 1200)
        Path(env["RASPILAPSE_LAST_REBOOT"]).write_text(str(int(time.time()) - 60))
        _, calls = run(env, state="2")
        assert "systemctl reboot" not in calls

    def test_an_old_stamp_does_not_block(self, env):
        frame(env, 1200)
        Path(env["RASPILAPSE_LAST_REBOOT"]).write_text(str(int(time.time()) - 86400))
        _, calls = run(env, state="2")
        assert "systemctl reboot" in calls

    def test_a_corrupt_stamp_does_not_block_forever(self, env):
        # Garbage must not read as "rebooted just now" and wedge recovery.
        frame(env, 1200)
        Path(env["RASPILAPSE_LAST_REBOOT"]).write_text("not-a-number")
        _, calls = run(env, state="2")
        assert "systemctl reboot" in calls


class TestNoSpam:
    """It runs every five minutes into a journal already at its size cap."""

    def test_the_healthy_path_is_silent(self, env):
        """It runs every five minutes into a journal at its size cap. The
        `find | sort | head` it used to do emitted two SIGPIPE lines a run."""
        frame(env, 30)
        proc, _ = run(env)
        assert proc.stdout.strip() == ""
        assert proc.stderr.strip() == ""

    def test_an_empty_image_directory_is_treated_as_stalled(self, env):
        _, calls = run(env)
        assert "systemctl restart raspilapse.service" in calls
