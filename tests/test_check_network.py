"""Tests for the network watchdog's escalation ladder.

The script shells out to nmcli, ip, ping and systemctl, so the whole ladder is
driven here by putting stub executables ahead of them on PATH. That covers the
decision logic -- which is the part with the teeth, since one branch of it
reboots the camera -- without needing a radio or a broken access point.

The load-bearing test is the one asserting that a genuinely absent access point
never reaches a reboot, however long it stays absent. Getting that wrong turns
a wifi outage into a reboot loop.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_network.sh"

# One dispatcher per command. Each records its own invocation so a test can
# assert on the order of recovery steps, then answers from the environment.
STUBS = {
    "nmcli": r"""#!/bin/bash
echo "nmcli $*" >>"$CALLS"
case "$*" in
    *"-t radio wifi"*) echo "${STUB_RADIO:-enabled}" ;;
    *"radio wifi on"*|*"radio wifi off"*) : ;;
    *"dev wifi list"*)
        [ "${STUB_SCAN_FAILS:-0}" = "1" ] && exit 1
        printf '%s\n' ${STUB_VISIBLE:-} ;;
    *"-t -f NAME,TYPE con show"*) echo "HomeNet:802-11-wireless" ;;
    *"-t -f UUID,TYPE,AUTOCONNECT-PRIORITY con show"*) echo "uuid-1:802-11-wireless:10" ;;
    *"-g 802-11-wireless.ssid con show"*) echo "HomeNet" ;;
    *"-t -f DEVICE,STATE,CONNECTION dev status"*) echo "wlan0:disconnected:" ;;
esac
exit 0
""",
    "ip": r"""#!/bin/bash
echo "ip $*" >>"$CALLS"
[ -n "${STUB_ROUTE:-}" ] && echo "$STUB_ROUTE"
exit 0
""",
    "ping": r"""#!/bin/bash
echo "ping $*" >>"$CALLS"
exit "${STUB_PING:-1}"
""",
    "systemctl": r"""#!/bin/bash
echo "systemctl $*" >>"$CALLS"
exit 0
""",
    "rfkill": "#!/bin/bash\nexit 0\n",
    "logger": "#!/bin/bash\nexit 0\n",
    "sleep": "#!/bin/bash\nexit 0\n",
}

ROUTE_OK = "default via 192.168.0.1 dev wlan0 proto dhcp metric 600"


@pytest.fixture
def env(tmp_path):
    """A stubbed PATH, an empty state dir, and the knobs the stubs read."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        p = bin_dir / name
        p.write_text(body)
        p.chmod(0o755)

    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS": str(tmp_path / "calls"),
        "RASPILAPSE_NETWORK_STATE": str(tmp_path / "state"),
        "RASPILAPSE_LAST_REBOOT": str(tmp_path / "last_reboot"),
        "RASPILAPSE_WIFI_IFACE": "wlan0",
        "STUB_VISIBLE": "HomeNet",
    }


def run(env, state=None, args=()):
    """Run the script once, optionally pre-seeding the escalation state."""
    if state is not None:
        Path(env["RASPILAPSE_NETWORK_STATE"]).write_text(state + "\n")
    proc = subprocess.run(
        ["bash", str(SCRIPT), *args], env={**os.environ, **env}, capture_output=True, text=True
    )
    calls = Path(env["CALLS"])
    return proc, calls.read_text() if calls.exists() else ""


def state_of(env):
    p = Path(env["RASPILAPSE_NETWORK_STATE"])
    return p.read_text().strip() if p.exists() else None


class TestHealthy:
    def test_a_working_link_does_nothing_at_all(self, env):
        env |= {"STUB_ROUTE": ROUTE_OK, "STUB_PING": "0"}
        proc, calls = run(env)
        assert proc.returncode == 0
        assert "dev connect" not in calls
        assert "systemctl" not in calls
        assert state_of(env) is None

    def test_a_non_wifi_default_route_is_left_alone(self, env):
        # Ethernet carrying the route means the box is reachable; touching
        # wlan0 could only make things worse.
        env |= {"STUB_ROUTE": "default via 10.0.0.1 dev eth0 metric 100", "STUB_PING": "1"}
        proc, calls = run(env)
        assert proc.returncode == 0
        assert "ping" not in calls
        assert state_of(env) is None

    def test_recovery_clears_the_counter(self, env):
        env |= {"STUB_ROUTE": ROUTE_OK, "STUB_PING": "0"}
        run(env, state="4 1786000000 1")
        assert state_of(env) is None


class TestEscalation:
    """Offline with the SSID on the air: NM is wedged, so climb the ladder."""

    def setup_env(self, env):
        env |= {"STUB_ROUTE": "", "STUB_PING": "1"}
        return env

    def test_the_first_checks_only_watch(self, env):
        self.setup_env(env)
        _, calls = run(env)
        assert "dev connect" not in calls
        assert state_of(env).startswith("1 ")

    def test_step_one_is_the_explicit_activation_that_clears_no_secrets(self, env):
        self.setup_env(env)
        _, calls = run(env, state="2 1786000000 1")
        assert "dev connect wlan0" in calls

    def test_step_two_targets_the_highest_priority_profile(self, env):
        self.setup_env(env)
        _, calls = run(env, state="3 1786000000 1")
        assert "con up uuid uuid-1" in calls

    def test_step_three_cycles_the_radio(self, env):
        self.setup_env(env)
        _, calls = run(env, state="4 1786000000 1")
        assert "radio wifi off" in calls
        assert "radio wifi on" in calls

    def test_step_four_restarts_networkmanager(self, env):
        self.setup_env(env)
        _, calls = run(env, state="5 1786000000 1")
        assert "systemctl restart NetworkManager" in calls

    def test_step_four_backs_off_if_the_link_returned(self, env):
        env |= {"STUB_ROUTE": ROUTE_OK, "STUB_PING": "0"}
        _, calls = run(env, state="5 1786000000 1")
        assert "restart NetworkManager" not in calls


class TestRebootGate:
    def _offline(self, env):
        env |= {"STUB_ROUTE": "", "STUB_PING": "1"}
        return env

    def test_it_reboots_only_after_every_step_and_the_time_gate(self, env, tmp_path):
        self._offline(env)
        import time

        old = int(time.time()) - 7200
        _, calls = run(env, state=f"7 {old} 1")
        assert "systemctl reboot" in calls
        # The stamp must be on disk before the reboot, or the rate limit is
        # lost across the very reboot it exists to bound.
        assert Path(env["RASPILAPSE_LAST_REBOOT"]).exists()

    def test_a_missing_access_point_never_reboots_however_long_it_is_gone(self, env):
        """The one that matters. If the SSID is not on the air, nothing local
        can fix it, and rebooting hourly for a week would be far worse than
        sitting offline until it comes back."""
        self._offline(env)
        env |= {"STUB_VISIBLE": "SomeoneElsesWifi"}
        import time

        old = int(time.time()) - 86400
        for count in range(7, 20):
            _, calls = run(env, state=f"{count} {old} 0")
            assert "systemctl reboot" not in calls, f"rebooted at count={count}"

    def test_a_recent_reboot_blocks_another(self, env):
        self._offline(env)
        import time

        Path(env["RASPILAPSE_LAST_REBOOT"]).write_text(str(int(time.time()) - 60))
        _, calls = run(env, state=f"9 {int(time.time()) - 7200} 1")
        assert "systemctl reboot" not in calls

    def test_a_short_outage_does_not_reboot_even_at_a_high_count(self, env):
        self._offline(env)
        import time

        _, calls = run(env, state=f"9 {int(time.time()) - 60} 1")
        assert "systemctl reboot" not in calls


class TestSafety:
    def test_a_disabled_radio_is_re_enabled_before_anything_else(self, env):
        """`nmcli radio wifi off` is persisted by NM and survives reboots, so a
        run killed mid-cycle would otherwise strand a headless camera."""
        env |= {"STUB_ROUTE": ROUTE_OK, "STUB_PING": "0", "STUB_RADIO": "disabled"}
        _, calls = run(env)
        assert "radio wifi on" in calls

    def test_dry_run_decides_but_never_acts(self, env):
        env |= {"STUB_ROUTE": "", "STUB_PING": "1"}
        proc, calls = run(env, state="5 1786000000 1", args=("--dry-run",))
        assert "would run" in proc.stdout
        assert "systemctl restart NetworkManager" not in calls

    def test_dry_run_does_not_reboot(self, env):
        import time

        env |= {"STUB_ROUTE": "", "STUB_PING": "1"}
        proc, calls = run(env, state=f"9 {int(time.time()) - 7200} 1", args=("--dry-run",))
        assert "systemctl reboot" not in calls
        assert not Path(env["RASPILAPSE_LAST_REBOOT"]).exists()

    def test_a_corrupt_state_file_does_not_wedge_the_watchdog(self, env):
        # An arithmetic error here would disable recovery silently, which is
        # the single thing this script exists to prevent.
        env |= {"STUB_ROUTE": "", "STUB_PING": "1"}
        proc, _ = run(env, state="garbage not numbers")
        assert proc.returncode == 0
        assert state_of(env).startswith("1 ")

    def test_a_failed_scan_counts_as_a_local_fault(self, env):
        # A radio that cannot scan is broken locally, so escalation (and
        # eventually a reboot) is the right answer.
        import time

        env |= {"STUB_ROUTE": "", "STUB_PING": "1", "STUB_SCAN_FAILS": "1"}
        run(env, state=f"1 {int(time.time()) - 7200} 0")
        assert state_of(env).endswith(" 1")
