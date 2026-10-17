#!/system/bin/sh
# Detached entry for a root boot hook. Never changes Android boot partitions.
set -u
PATH=/system/bin:/system/xbin:/system/sbin
export PATH
umask 077
BOOT_BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
BOOT_CTL="$BOOT_BASE/f50-proxy"
BOOT_RUN="$BOOT_BASE/run"
BOOT_MIN_UPTIME=30
BOOT_READY_TIMEOUT=180
BOOT_TRIAL_SECONDS=120
BOOT_SESSION=''

boot_enabled() { [ ! -e "$BOOT_BASE/autostart.disabled" ]; }
boot_uptime() { read -r boot_up boot_rest < /proc/uptime; printf '%s\n' "${boot_up%%.*}"; }
boot_identity() { cat /proc/sys/kernel/random/boot_id; }
boot_sleep() { sleep "$1"; }
boot_note() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
boot_control() { "$BOOT_CTL" "$@" 8>&-; }
boot_result() {
    printf '%s\n' "$1" > "$BOOT_RUN/boot.result.new"
    mv -f "$BOOT_RUN/boot.result.new" "$BOOT_RUN/boot.result"
}
boot_ready() {
    [ "$(getprop sys.boot_completed)" = 1 ] || return 1
    ip link show dev "$BOOT_LAN" >/dev/null 2>&1 || return 1
    [ -n "$(ip -4 -o addr show dev "$BOOT_LAN" 2>/dev/null)" ] || return 1
    ip -4 route get 1.1.1.1 >/dev/null 2>&1
}
boot_probe() {
    # Exercise the native mixed inbound and its configured routing/DNS. A
    # captive-portal HTTP200 is not a successful 204 connectivity check.
    boot_response=$(
        printf 'GET http://www.gstatic.com/generate_204 HTTP/1.1\r\nHost: www.gstatic.com\r\nConnection: close\r\n\r\n' |
            timeout 15 nc -w 12 127.0.0.1 "$BOOT_LOCAL_PORT" | head -n 1
    )
    case "$boot_response" in 'HTTP/1.0 204 '*|'HTTP/1.1 204 '*) return 0;; *) return 1;; esac
}
boot_cleanup() {
    if [ -n "$BOOT_SESSION" ]; then
        boot_control _boot-stop "$BOOT_SESSION" || :
        BOOT_SESSION=''
    fi
}
boot_main() {
    [ "$(id -u)" = 0 ] || { boot_note 'Root is required; skipped.'; return 1; }
    mkdir -p "$BOOT_RUN" "$BOOT_BASE/log" || return 1
    exec 8>"$BOOT_RUN/boot.lock"
    flock -n 8 8>&8 || return 0
    boot_enabled || { boot_note 'Autostart disabled; skipped.'; boot_result disabled; return 0; }
    BOOT_ID=$(boot_identity) || return 1
    if [ "$(cat "$BOOT_RUN/boot.attempt" 2>/dev/null)" = "$BOOT_ID" ]; then
        boot_note 'This boot was already handled; skipped.'
        return 0
    fi
    # One bounded attempt per boot. A broken node must not restart forever.
    printf '%s\n' "$BOOT_ID" > "$BOOT_RUN/boot.attempt"
    if boot_control _alive; then
        boot_note 'Gateway already running; preserving its current lease.'
        boot_result already-running
        return 0
    fi
    boot_settings=$(boot_control _boot-config) || { boot_result invalid-profile; return 1; }
    BOOT_LAN=''
    BOOT_LOCAL_PORT=''
    while IFS='=' read -r boot_key boot_value; do
        case "$boot_key" in
            LAN_INTERFACE) BOOT_LAN=$boot_value;;
            LOCAL_TEST_PORT) BOOT_LOCAL_PORT=$boot_value;;
            *) boot_result invalid-profile; return 1;;
        esac
    done <<EOF_BOOT_SETTINGS
$boot_settings
EOF_BOOT_SETTINGS
    [ -n "$BOOT_LAN" ] && [ -n "$BOOT_LOCAL_PORT" ] || { boot_result invalid-profile; return 1; }
    boot_note 'Waiting for boot completion, hotspot and uplink.'
    boot_result waiting-network
    boot_until=$(( $(boot_uptime) + BOOT_READY_TIMEOUT ))
    while :; do
        boot_enabled || { boot_result disabled; return 0; }
        if [ "$(boot_uptime)" -ge "$BOOT_MIN_UPTIME" ] && boot_ready; then break; fi
        if [ "$(boot_uptime)" -ge "$boot_until" ]; then
            boot_note 'Network readiness timed out; no interception was enabled.'
            boot_result network-timeout
            return 1
        fi
        boot_sleep 3
    done
    boot_enabled || { boot_result disabled; return 0; }
    # Core identity, stale-state cleanup and creation of our exact session happen
    # in one controller transaction. A concurrent manual start keeps its lease.
    if boot_started=$(boot_control _boot-start "$BOOT_TRIAL_SECONDS"); then
        printf '%s\n' "$boot_started"
    else
        boot_start_status=$?
        case "$boot_start_status" in
            3) boot_result already-running; return 0;;
            4) boot_result disabled; return 0;;
        esac
        boot_note 'Gateway start failed; leaving original forwarding available.'
        boot_result start-failed
        return 1
    fi
    BOOT_SESSION=$(printf '%s\n' "$boot_started" | sed -n 's/^BOOT_SESSION=//p')
    case "$BOOT_SESSION" in
        ''|*[!A-Za-z0-9-]*) boot_result invalid-session; return 1;;
    esac
    trap 'boot_cleanup; exit 1' HUP INT TERM
    boot_tries=0
    while [ "$boot_tries" -lt 3 ]; do
        boot_enabled || break
        if boot_probe && boot_enabled && boot_control _boot-confirm "$BOOT_SESSION"; then
            BOOT_SESSION=''
            trap - HUP INT TERM
            boot_result started
            boot_note 'Connectivity check passed; gateway confirmed, watchdog active.'
            return 0
        fi
        boot_tries=$((boot_tries + 1))
        [ "$boot_tries" -ge 3 ] || boot_sleep 3
    done
    boot_cleanup
    trap - HUP INT TERM
    if boot_enabled; then
        boot_result health-failed
        boot_note 'Connectivity checks failed; removed this boot session.'
    else
        boot_result disabled
        boot_note 'Autostart disabled while starting; removed this boot session.'
    fi
    return 1
}

# === BOOT DISPATCH ===
boot_main
