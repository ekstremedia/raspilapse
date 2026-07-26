#!/bin/bash
#
# Raspilapse installer.
#
# Renders systemd/*.in into /etc/systemd/system, substituting the user, group,
# project directory and interpreter of *this* machine. The checked-in units are
# templates precisely so they cannot be copied verbatim -- a unit hardcoding
# User=pi and /home/pi/raspilapse silently half-works for one person and breaks
# for everyone else.
#
# Usage:
#   ./scripts/install.sh                       install capture, cleanup, daily-video, upload-retry
#   ./scripts/install.sh --only capture,cleanup
#   ./scripts/install.sh --with-watchdog       also install the stall watchdog (runs as root)
#   ./scripts/install.sh --check               check dependencies and config, install nothing
#   ./scripts/install.sh --dry-run             print the rendered units and exit
#   ./scripts/install.sh --uninstall           remove everything this script installs
#
set -euo pipefail

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; BOLD=$'\033[1m'; NC=$'\033[0m'
ok()   { echo "${GREEN}✓${NC} $*"; }
warn() { echo "${YELLOW}⚠${NC} $*"; }
err()  { echo "${RED}✗${NC} $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Every unit runs /usr/bin/python3, so that is the interpreter whose packages
# must be checked -- not whatever `python3` resolves to in this shell, which
# may be a virtualenv.
PYTHON="${RASPILAPSE_PYTHON:-/usr/bin/python3}"

# All units these keys map to. Order matters only for readability.
ALL_COMPONENTS=(capture cleanup daily-video upload-retry)
declare -A COMPONENT_UNITS=(
    [capture]="raspilapse.service"
    [cleanup]="raspilapse-cleanup.service raspilapse-cleanup.timer"
    [daily-video]="raspilapse-daily-video.service raspilapse-daily-video.timer"
    [upload-retry]="raspilapse-upload-retry.service raspilapse-upload-retry.timer"
    [watchdog]="raspilapse-watchdog.service raspilapse-watchdog.timer"
)
# Units to `systemctl enable`. Timers are enabled; the services they trigger
# are not, and daily-video/upload-retry/watchdog services have no [Install]
# section at all so that enabling them is impossible.
declare -A ENABLE_UNITS=(
    [capture]="raspilapse.service"
    [cleanup]="raspilapse-cleanup.timer"
    [daily-video]="raspilapse-daily-video.timer"
    [upload-retry]="raspilapse-upload-retry.timer"
    [watchdog]="raspilapse-watchdog.timer"
)

APT_PACKAGES=(
    python3-picamera2 python3-yaml python3-pil python3-numpy
    python3-requests python3-requests-toolbelt python3-matplotlib ffmpeg
)
# Python module -> how to get it. picamera2 is deliberately absent: it is
# checked separately because it is the one that needs real hardware.
declare -A PY_MODULES=(
    [yaml]="sudo apt install -y python3-yaml"
    [PIL]="sudo apt install -y python3-pil"
    [numpy]="sudo apt install -y python3-numpy"
    [requests]="sudo apt install -y python3-requests"
    [requests_toolbelt]="sudo apt install -y python3-requests-toolbelt"
    [astral]="pip3 install --break-system-packages 'astral>=3.2'"
)

components=()
with_watchdog=0
mode=install

usage() { sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --only) IFS=',' read -ra components <<<"${2:?--only needs a comma-separated list}"; shift 2 ;;
        --with-watchdog) with_watchdog=1; shift ;;
        --check)     mode=check;     shift ;;
        --dry-run)   mode=dry-run;   shift ;;
        --uninstall) mode=uninstall; shift ;;
        -h|--help)   usage; exit 0 ;;
        *) err "Unknown option: $1"; usage; exit 2 ;;
    esac
done

[ ${#components[@]} -eq 0 ] && components=("${ALL_COMPONENTS[@]}")
[ "$with_watchdog" -eq 1 ] && components+=(watchdog)

for c in "${components[@]}"; do
    if [ -z "${COMPONENT_UNITS[$c]:-}" ]; then
        err "Unknown component '$c'. Valid: ${ALL_COMPONENTS[*]} watchdog"
        exit 2
    fi
done

if [ "$EUID" -eq 0 ] && [ "$mode" != "dry-run" ]; then
    err "Do not run this as root. Run it as the user the service should run as;"
    err "it will call sudo where it needs to."
    exit 1
fi

# ---------------------------------------------------------------- rendering --

render_unit() {
    # render_unit <unit-name> -> rendered text on stdout
    local unit="$1" template="$PROJECT_DIR/systemd/$1.in"
    [ -f "$template" ] || { err "Missing template: $template"; return 1; }
    sed -e "s|@USER@|$(id -un)|g" \
        -e "s|@GROUP@|$(id -gn)|g" \
        -e "s|@PROJECT_DIR@|$PROJECT_DIR|g" \
        -e "s|@PYTHON@|$PYTHON|g" \
        "$template"
}

selected_units() {
    local c u
    for c in "${components[@]}"; do
        for u in ${COMPONENT_UNITS[$c]}; do echo "$u"; done
    done
}

# ------------------------------------------------------------------- checks --

check_dependencies() {
    local missing=0 mod

    if [ ! -x "$PYTHON" ]; then
        err "$PYTHON not found. The systemd units use it directly."
        return 1
    fi
    ok "Interpreter: $PYTHON ($("$PYTHON" --version 2>&1))"

    for mod in "${!PY_MODULES[@]}"; do
        if "$PYTHON" -c "import $mod" 2>/dev/null; then
            ok "$mod"
        else
            err "$mod is missing.  Install with: ${PY_MODULES[$mod]}"
            missing=1
        fi
    done

    if "$PYTHON" -c "import picamera2" 2>/dev/null; then
        ok "picamera2"
    else
        warn "picamera2 is missing. Capture will not work on this machine."
        warn "  sudo apt install -y python3-picamera2   (apt, never pip)"
    fi

    command -v ffmpeg >/dev/null && ok "ffmpeg" || {
        err "ffmpeg is missing (needed for video generation): sudo apt install -y ffmpeg"
        missing=1
    }

    if [ "$missing" -ne 0 ]; then
        echo
        err "Install everything at once with:"
        err "  sudo apt install -y ${APT_PACKAGES[*]}"
        err "  pip3 install --break-system-packages 'astral>=3.2'"
        return 1
    fi
}

check_config() {
    local cfg="$PROJECT_DIR/config/config.yml"

    if [ ! -f "$cfg" ]; then
        warn "config/config.yml does not exist."
        warn "  cp config/config.example.yml config/config.yml && nano config/config.yml"
        return 1
    fi

    if ! "$PYTHON" -c "import yaml,sys; yaml.safe_load(open('$cfg'))" 2>/dev/null; then
        err "config/config.yml is not valid YAML."
        return 1
    fi
    ok "config/config.yml parses"

    # Warnings, not failures: the install still works, the user just gets
    # more log volume or a queue that can never drain.
    "$PYTHON" - "$cfg" <<'PY'
import sys, yaml

cfg = yaml.safe_load(open(sys.argv[1])) or {}

if (cfg.get("logging") or {}).get("console") is True:
    print("\033[1;33m⚠\033[0m logging.console is 'true': every line is written to "
          "both logs/ and the journal.\n"
          "  Set it to 'auto' to skip the console handler under systemd.")

up = cfg.get("video_upload") or {}
if up.get("enabled") and not (up.get("url") and up.get("api_key")):
    print("\033[1;33m⚠\033[0m video_upload.enabled is true but url/api_key are empty: "
          "every daily video will queue and never send.")

out = (cfg.get("output") or {}).get("directory")
if out:
    print(f"\033[0;32m✓\033[0m Images will be written to {out}")
PY
}

prepare_directories() {
    local out
    out=$("$PYTHON" -c "
import yaml
cfg = yaml.safe_load(open('$PROJECT_DIR/config/config.yml')) or {}
print((cfg.get('output') or {}).get('directory') or '')" 2>/dev/null)
    [ -n "$out" ] || return 0

    if [ ! -d "$out" ]; then
        sudo mkdir -p "$out"
        # www-data so a webserver can serve the tree; the capture user owns it.
        sudo chown -R "$(id -un):$(getent group www-data >/dev/null && echo www-data || id -gn)" "$out"
        sudo chmod -R 775 "$out"
        ok "Created $out"
    else
        ok "Image directory exists: $out"
    fi
    mkdir -p "$PROJECT_DIR/logs"
}

# ------------------------------------------------------------------ actions --

do_dry_run() {
    local unit
    while read -r unit; do
        echo "${BOLD}──── /etc/systemd/system/$unit ────${NC}"
        render_unit "$unit"
        echo
    done < <(selected_units)
    echo "${BOLD}──── /etc/systemd/journald.conf.d/journald-raspilapse.conf ────${NC}"
    cat "$PROJECT_DIR/systemd/journald-raspilapse.conf"
}

STAGING=""
cleanup_staging() { [ -n "$STAGING" ] && rm -rf "$STAGING"; }
trap cleanup_staging EXIT

do_install() {
    local unit c
    # Render into a temp dir, never into the repo. The old installer wrote a
    # generated raspilapse.service into the project root, which left every
    # user with a dirty working tree.
    STAGING="$(mktemp -d)"

    while read -r unit; do
        render_unit "$unit" >"$STAGING/$unit"
    done < <(selected_units)

    sudo install -m 644 -t /etc/systemd/system "$STAGING"/*
    ok "Installed $(selected_units | wc -l) unit file(s)"

    sudo mkdir -p /etc/systemd/journald.conf.d
    sudo install -m 644 "$PROJECT_DIR/systemd/journald-raspilapse.conf" \
        /etc/systemd/journald.conf.d/
    sudo systemctl restart systemd-journald
    ok "Capped the journal at 200 MB"

    sudo systemctl daemon-reload

    for c in "${components[@]}"; do
        # shellcheck disable=SC2086
        sudo systemctl enable ${ENABLE_UNITS[$c]} >/dev/null
        # enable only arranges for the next boot. Start the timers now too, so
        # `systemctl list-timers` below reflects reality rather than intent.
        for unit in ${ENABLE_UNITS[$c]}; do
            [[ "$unit" == *.timer ]] && sudo systemctl start "$unit"
        done
        ok "Enabled $c"
    done

    sudo systemctl reset-failed 'raspilapse-*' 2>/dev/null || true
}

do_uninstall() {
    local unit units=()
    for unit in "${COMPONENT_UNITS[@]}" "${COMPONENT_UNITS[watchdog]}"; do
        units+=($unit)
    done

    for unit in "${units[@]}"; do
        [ -f "/etc/systemd/system/$unit" ] || continue
        sudo systemctl stop "$unit" 2>/dev/null || true
        sudo systemctl disable "$unit" 2>/dev/null || true
        sudo rm -f "/etc/systemd/system/$unit"
        ok "Removed $unit"
    done

    sudo rm -f /etc/systemd/journald.conf.d/journald-raspilapse.conf
    sudo systemctl daemon-reload
    sudo systemctl reset-failed 'raspilapse-*' 2>/dev/null || true

    echo
    echo "Captured images, videos and data/timelapse.db were left untouched."
    echo "Config is still at $PROJECT_DIR/config/config.yml"
}

# --------------------------------------------------------------------- main --

echo "${BOLD}Raspilapse installer${NC}"
echo "  project:    $PROJECT_DIR"
echo "  user:       $(id -un):$(id -gn)"
echo "  components: ${components[*]}"
echo

case "$mode" in
    dry-run)
        do_dry_run
        exit 0
        ;;
    uninstall)
        do_uninstall
        exit 0
        ;;
    check)
        rc=0
        check_dependencies || rc=1
        echo
        check_config || rc=1
        echo
        if [ "$rc" -eq 0 ]; then ok "Ready to install."; else err "Fix the above first."; fi
        exit "$rc"
        ;;
esac

check_dependencies || exit 1
echo
check_config || exit 1
echo
prepare_directories
echo
do_install

echo
echo "${BOLD}Done.${NC} Current schedule:"
echo
systemctl list-timers 'raspilapse-*' --no-pager 2>/dev/null || true
echo
echo "  Start now:   sudo systemctl start raspilapse"
echo "  Status:      systemctl status raspilapse"
echo "  Logs:        tail -f $PROJECT_DIR/logs/auto_timelapse.log"
echo "               journalctl -u raspilapse -f    (systemd + libcamera)"
echo "  Uninstall:   ./scripts/install.sh --uninstall"
