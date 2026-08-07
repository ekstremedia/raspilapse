#!/bin/bash
#
# Raspilapse network watchdog.
#
# NetworkManager can wedge permanently after a wifi dropout that has nothing to
# do with the password. When the access point reboots, association times out;
# NM reads that timeout as wrong credentials, asks for new secrets, finds no
# agent on a headless machine, and gives up:
#
#   wpa_supplicant: Authentication with 66:7f:f0:07:03:c0 timed out.
#   NetworkManager: Activation: (wifi) disconnected during association, asking for new key
#   NetworkManager: state change: activated -> need-auth (reason 'supplicant-disconnect')
#   NetworkManager: no secrets: No agents were available for this request.
#   NetworkManager: state change: need-auth -> failed (reason 'no-secrets')
#
# That last line sets an autoconnect *blocked reason* on the profile, not a
# retry counter, so connection.autoconnect-retries=-1 does not help and neither
# does any other setting in NetworkManager.conf. It is cleared by an agent
# supplying secrets, an explicit `nmcli con up`/`dev connect`, a change to the
# profile's secrets, or restarting NM -- which is why this has to be a script
# and not configuration.
#
# Observed cost on this camera: wifi died at 23:31 and stayed dead for eight and
# a half hours, until a human power-cycled it. Capture never stopped, so the
# only symptom was the uploads and the live image going quiet, which looks
# exactly like a frozen Pi from the outside.
#
# Installed as raspilapse-netwatch.{service,timer} by:
#     ./scripts/install.sh --with-netwatch
#
# Runs as root: it calls nmcli, systemctl restart and, as a last resort, reboot.
#
# Usage:
#   check_network.sh              detect and act
#   check_network.sh --dry-run    detect and log the step it would take
#
set -uo pipefail

STATE_FILE="${RASPILAPSE_NETWORK_STATE:-/var/lib/raspilapse/network_state}"
REBOOT_STAMP="${RASPILAPSE_LAST_REBOOT:-/var/lib/raspilapse/last_reboot}"
WIFI_IFACE="${RASPILAPSE_WIFI_IFACE:-wlan0}"

LOG_TAG="raspilapse-netwatch"

# Two runs of the 2-minute timer before anything is touched, so an access point
# rebooting or a client roaming between bands rides out untouched.
GRACE_CHECKS=2
# Continuous failure before a reboot is even considered.
REBOOT_AFTER_SECONDS=1800
# Floor between watchdog reboots, shared with check_service.sh. A hard ceiling
# of four a day even if every other gate misfires.
MIN_REBOOT_INTERVAL=21600

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

log() { logger -t "$LOG_TAG" -- "$@"; echo "$*"; }

# Run a recovery step, or just say what it would have been.
act() {
    if [ "$DRY_RUN" -eq 1 ]; then
        log "DRY RUN: would run: $*"
        return 0
    fi
    "$@"
}

# --- state -------------------------------------------------------------------
# One line: "<consecutive_failures> <first_failure_epoch> <ssid_was_seen>"

read_state() {
    local count first seen
    read -r count first seen < <([ -f "$STATE_FILE" ] && cat "$STATE_FILE"; echo)
    # A truncated or hand-edited state file must not take the watchdog out with
    # an arithmetic error -- that would disable recovery silently, which is the
    # one failure mode this script exists to prevent.
    [[ "${count:-}" =~ ^[0-9]+$ ]] || count=0
    [[ "${first:-}" =~ ^[0-9]+$ ]] || first=0
    [[ "${seen:-}" =~ ^[01]$ ]] || seen=0
    echo "$count $first $seen"
}

write_state() {
    mkdir -p "$(dirname "$STATE_FILE")"
    echo "$1 $2 $3" >"$STATE_FILE"
}

clear_state() { rm -f "$STATE_FILE"; }

# --- detection ---------------------------------------------------------------

# True when the machine has a working way out. Deliberately no DNS and no
# internet host: an ISP outage is not something reconnecting the wifi can fix,
# and this must not reboot the camera every 30 minutes because a name server
# is down. The default gateway answering is the narrowest question that
# actually distinguishes "our link works" from "our link is wedged".
network_ok() {
    local route dev gw
    # awk rather than `head -1`: head closes the pipe early, and under systemd
    # (IgnoreSIGPIPE=yes) the writer reports a write error instead of dying
    # quietly. See the same fix in check_service.sh.
    route=$(ip -4 route show default 2>/dev/null | awk 'NR==1')
    [ -n "$route" ] || return 1

    dev=$(echo "$route" | sed -n 's/.* dev \([^ ]*\).*/\1/p')
    gw=$(echo "$route" | sed -n 's/.*via \([^ ]*\).*/\1/p')

    # Ethernet, or anything else that is not the wifi we manage, carrying the
    # default route means the box is reachable. Never touch wlan0 in that case.
    [ -n "$dev" ] && [ "$dev" != "$WIFI_IFACE" ] && return 0

    [ -n "$gw" ] || return 1
    ping -c1 -W2 -I "$WIFI_IFACE" "$gw" >/dev/null 2>&1
}

# SSIDs of the configured wifi profiles. Read from the profiles rather than
# hardcoded: this is a public repo, and the camera's network is not its business.
configured_ssids() {
    local name
    nmcli -t -f NAME,TYPE con show 2>/dev/null |
        awk -F: '$2 == "802-11-wireless" { print $1 }' |
        while IFS= read -r name; do
            [ -n "$name" ] && nmcli -g 802-11-wireless.ssid con show "$name" 2>/dev/null
        done
}

# The whole design turns on this question. If our SSID is not on the air then
# the access point is off or we are out of range, nothing local can fix it, and
# rebooting every 30 minutes for a week would be far worse than sitting
# offline. If it IS on the air and we are still not associated, NM is wedged
# and escalating is justified.
#
# A scan that fails outright counts as wedged, not as "AP gone": a radio that
# cannot even scan is a local fault.
ssid_visible() {
    local visible ssid
    # Note the polarity: a scan that *errors* returns success here, i.e. counts
    # as "our network is there". A radio that cannot scan is a local fault, and
    # local faults are exactly what escalation is for. An empty but successful
    # scan is the opposite -- nothing on the air, so leave it alone.
    visible=$(nmcli -t -f SSID dev wifi list --rescan yes 2>/dev/null) || return 0
    [ -n "$visible" ] || return 1

    while IFS= read -r ssid; do
        [ -n "$ssid" ] || continue
        printf '%s\n' "$visible" | grep -qxF "$ssid" && return 0
    done < <(configured_ssids)
    return 1
}

primary_wifi_uuid() {
    nmcli -t -f UUID,TYPE,AUTOCONNECT-PRIORITY con show 2>/dev/null |
        awk -F: '$2 == "802-11-wireless" { print $3, $1 }' |
        sort -rn |
        awk 'NR == 1 { print $2 }'
}

diagnose() {
    log "  device: $(nmcli -t -f DEVICE,STATE,CONNECTION dev status 2>/dev/null | tr '\n' ' ')"
    log "  routes: $(ip -4 route show default 2>/dev/null | tr '\n' ';')"
    log "  radio:  $(nmcli -t radio wifi 2>/dev/null)"
}

# --- recovery ----------------------------------------------------------------

reboot_is_allowed() {
    local first="$1" seen="$2" now stamp
    now=$(date +%s)

    [ "$seen" -eq 1 ] || {
        log "  not rebooting: our SSID has not been seen since the failure began"
        return 1
    }
    [ $((now - first)) -ge "$REBOOT_AFTER_SECONDS" ] || {
        log "  not rebooting: failing for $(((now - first) / 60))m, threshold is $((REBOOT_AFTER_SECONDS / 60))m"
        return 1
    }
    if [ -f "$REBOOT_STAMP" ]; then
        stamp=$(cat "$REBOOT_STAMP" 2>/dev/null || echo 0)
        [ $((now - stamp)) -ge "$MIN_REBOOT_INTERVAL" ] || {
            log "  not rebooting: last watchdog reboot was $(((now - stamp) / 60))m ago"
            return 1
        }
    fi
    return 0
}

do_reboot() {
    log "CRITICAL: network still down after every recovery step, rebooting"
    if [ "$DRY_RUN" -eq 1 ]; then
        log "DRY RUN: would reboot"
        return 0
    fi
    date +%s >"$REBOOT_STAMP"
    # Without the sync, ext4's commit window can lose the stamp across the very
    # reboot it exists to rate-limit, and the floor silently stops existing.
    sync
    clear_state
    systemctl reboot
}

main() {
    # Before anything else, and unconditionally. `nmcli radio wifi off` is
    # persisted by NM in /var/lib/NetworkManager/NetworkManager.state and
    # survives reboots, so a run killed between the off and the on below would
    # leave a headless camera with its radio disabled forever. The EXIT trap on
    # that step does not run on SIGKILL; this does.
    if [ "$(nmcli -t radio wifi 2>/dev/null)" = "disabled" ]; then
        log "wifi radio was disabled, re-enabling"
        nmcli radio wifi on
    fi
    rfkill unblock wifi 2>/dev/null || true

    local count first seen now
    read -r count first seen <<<"$(read_state)"
    now=$(date +%s)

    if network_ok; then
        if [ "$count" -gt 0 ]; then
            log "network recovered after $count failed checks"
            clear_state
        fi
        exit 0
    fi

    count=$((count + 1))
    [ "$first" -eq 0 ] && first=$now
    if [ "$seen" -eq 0 ] && ssid_visible; then
        seen=1
    fi
    write_state "$count" "$first" "$seen"

    if [ "$count" -eq 1 ]; then
        log "WARNING: no route out via $WIFI_IFACE (SSID visible: $([ "$seen" -eq 1 ] && echo yes || echo no))"
        diagnose
    fi

    local step=$((count - GRACE_CHECKS))
    if [ "$step" -le 0 ]; then
        exit 0
    fi

    case "$step" in
    1)
        # The actual fix for 'no-secrets'. Explicit activation is what clears
        # the blocked reason and makes NM re-read the system-stored PSK.
        log "recovery 1/5: nmcli dev connect $WIFI_IFACE"
        act nmcli -w 30 dev connect "$WIFI_IFACE"
        ;;
    2)
        # Step 1 lets NM pick by priority and it may land on a fallback profile
        # on a different subnet. Target the highest-priority one explicitly.
        local uuid
        uuid=$(primary_wifi_uuid)
        if [ -n "$uuid" ]; then
            log "recovery 2/5: nmcli con up $uuid"
            act nmcli -w 30 con up uuid "$uuid"
        else
            log "recovery 2/5: skipped, no wifi profile found"
        fi
        ;;
    3)
        # Resets wpa_supplicant and the driver's association state.
        # Deliberately NOT `modprobe -r brcmfmac`: that needs a firmware reload
        # on insert, and a failure there is unrecoverable without physical
        # access to the camera. Do not add it.
        log "recovery 3/5: cycling the wifi radio"
        trap 'nmcli radio wifi on 2>/dev/null || true' EXIT
        act nmcli radio wifi off
        sleep 5
        act nmcli radio wifi on
        trap - EXIT
        ;;
    4)
        # Clears every in-memory blocked reason. wpa_supplicant is D-Bus
        # activated and comes back with it. Re-check first: this drops any SSH
        # session, and there is no reason to pay that if the link just returned.
        if network_ok; then
            log "recovery 4/5: skipped, network came back"
            exit 0
        fi
        log "recovery 4/5: restarting NetworkManager"
        act systemctl restart NetworkManager
        ;;
    *)
        if reboot_is_allowed "$first" "$seen"; then
            do_reboot
        fi
        ;;
    esac
}

main
exit 0
