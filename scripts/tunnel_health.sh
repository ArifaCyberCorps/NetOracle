#!/bin/bash
# tunnel_health.sh — SD-WAN IPsec tunnel health monitor
# Runs as a background daemon inside each CE container.
# Periodically checks:
#   - IPsec SA status (establishment, rekey, DPD events)
#   - VTI interface reachability (ICMP probe over tunnel)
#   - Reports via syslog for telemetry collector ingestion
#
# This enables the telemetry pipeline to track DPD events,
# SA flapping, rekey timing, and overlay path health.

HOSTNAME=$(hostname)
SLEEP_INTERVAL=30   # seconds between health checks
SYSLOG_TAG="ipsec-health"

log() {
    local priority="$1"
    shift
    logger -t "$SYSLOG_TAG" -p "daemon.$priority" "$HOSTNAME: $*"
}

get_sa_status() {
    ipsec status 2>/dev/null | grep -c "ESTABLISHED" || echo 0
}

get_sa_total() {
    ipsec status 2>/dev/null | grep -cP '^\s+\S+\[' || echo 0
}

get_vti_list() {
    ip link show type vti 2>/dev/null | grep -oP '^\d+:\s+\K\S+' | tr -d ':' || true
}

# Track previous SA state for DPD detection
prev_established=0
prev_spis=""

# Initial state
if command -v ipsec &>/dev/null; then
    log "info" "Tunnel health monitor started (interval=${SLEEP_INTERVAL}s)"
else
    log "err" "ipsec command not found, health monitor disabled"
    exit 1
fi

while true; do
    # --- IPsec SA health ---
    current_established=$(get_sa_status)
    current_total=$(get_sa_total)

    # DPD / SA flap detection
    if [ "$current_established" -lt "$prev_established" ]; then
        lost=$((prev_established - current_established))
        log "warning" "DPD: $lost IKE SA(s) lost (${prev_established}→${current_established})"
    elif [ "$current_established" -gt "$prev_established" ]; then
        gained=$((current_established - prev_established))
        log "info" "IKE SA(s) established: ${prev_established}→${current_established} (+${gained})"
    fi
    prev_established=$current_established

    # --- VTI interface reachability ---
    for vti in $(get_vti_list); do
        # Get the VTI IP (first IP on the interface)
        vti_ip=$(ip -4 addr show dev "$vti" 2>/dev/null | grep -oP 'inet \K[\d.]+/\d+' | head -1)
        if [ -z "$vti_ip" ]; then
            log "warning" "VTI $vti has no IP address"
            continue
        fi
        # The remote end is the network neighbor (/31 → add/subtract 1)
        base_ip=$(echo "$vti_ip" | cut -d/ -f1)
        prefix=$(echo "$vti_ip" | cut -d/ -f2)
        if [ "$prefix" = "31" ]; then
            # Calculate the other end of the /31
            IFS=. read -r a b c d <<< "$base_ip"
            last_octet=$d
            # If local is even, remote is odd; if odd, remote is even
            if [ $((last_octet % 2)) -eq 0 ]; then
                remote_octet=$((last_octet + 1))
            else
                remote_octet=$((last_octet - 1))
            fi
            remote_ip="${a}.${b}.${c}.${remote_octet}"

            # ICMP probe over the VTI interface (sources from VTI IP, goes over IPsec tunnel)
            if ping -c 1 -W 2 -I "$vti" "$remote_ip" &>/dev/null; then
                :
            else
                log "warning" "VTI $vti health: UNREACHABLE to $remote_ip"
            fi
        fi
    done

    # --- Rekey detection (SPI changes) ---
    current_spis=$(ip xfrm state 2>/dev/null | grep -oP 'spi \K[\da-f]+(?=\()' | sort | tr '\n' ' ')
    if [ -n "$current_spis" ] && [ -n "$prev_spis" ] && [ "$current_spis" != "$prev_spis" ]; then
        log "info" "Rekey detected: ESP SPIs changed"
    fi
    prev_spis="$current_spis"

    # --- Report summary ---
    log "debug" "SA status: ${current_established}/${current_total} established, VTI count: $(get_vti_list | wc -w)"

    sleep "$SLEEP_INTERVAL"
done
