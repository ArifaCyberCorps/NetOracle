#!/bin/bash
# setup_ipsec.sh — Install strongSwan, create VTI interfaces, configure IPsec SAs
# Runs once per CE container at startup.
# Architecture: hub-and-spoke route-reflector with IPsec VTI overlay tunnels
#
# VTI = Virtual Tunnel Interface (kernel IPsec VTI driver)
# Each VTI maps to a unique IPsec SA via a numeric "mark"
# Traffic routed into a VTI is automatically IPsec-encrypted with that SA

set -e

HOSTNAME=$(hostname)

# ---------- topology database ----------
# Underlay: CE loopbacks reachable through MPLS (eBGP CE→PE → OSPF core → PE→CE eBGP)
# Overlay:  VTI interfaces sourced from CE loopback, destined to remote CE loopback
declare -A LO=(
    [hub-ce1]=10.0.2.3   [dc1-ce1]=10.0.0.3   [dc2-ce1]=10.0.1.3
    [branch1-ce1]=10.0.3.1 [branch2-ce1]=10.0.4.1 [branch3-ce1]=10.0.5.1
    [branch4-ce1]=10.0.6.1 [branch5-ce1]=10.0.7.1 [branch6-ce1]=10.0.8.1
)

# AS mapping (used for BGP route-reflector)
declare -A ASN=(
    [hub-ce1]=65503 [dc1-ce1]=65501 [dc2-ce1]=65502
    [branch1-ce1]=65506 [branch2-ce1]=65507 [branch3-ce1]=65508
    [branch4-ce1]=65504 [branch5-ce1]=65505 [branch6-ce1]=65509
)

# ---------- hub-ce1: establish IPsec VTI to every other CE ----------
# VTI tunnel parameters:  (vti_name  remote_node  vti_mark  hub_ip/31  remote_ip/31)
HUB_TUNNELS=(
    "vti100 dc1-ce1   100  10.99.0.0  10.99.0.1"
    "vti101 dc2-ce1   101  10.99.0.10 10.99.0.11"
    "vti102 branch1-ce1 102 10.99.0.2  10.99.0.3"
    "vti103 branch2-ce1 103 10.99.0.4  10.99.0.5"
    "vti104 branch3-ce1 104 10.99.0.6  10.99.0.7"
    "vti105 branch4-ce1 105 10.99.0.8  10.99.0.9"
    "vti106 branch5-ce1 106 10.99.0.12 10.99.0.13"
    "vti107 branch6-ce1 107 10.99.0.14 10.99.0.15"
)

# ---------- spoke tunnel definitions (each spoke's VTI to hub-ce1) ----------
# Format:  vti_name  local_ip  remote_ip  mark
declare -A SPOKE_TUNNELS=(
    [dc1-ce1]="vti100 10.99.0.1 10.99.0.0 100"
    [dc2-ce1]="vti101 10.99.0.11 10.99.0.10 101"
    [branch1-ce1]="vti102 10.99.0.3 10.99.0.2 102"
    [branch2-ce1]="vti103 10.99.0.5 10.99.0.4 103"
    [branch3-ce1]="vti104 10.99.0.7 10.99.0.6 104"
    [branch4-ce1]="vti105 10.99.0.9 10.99.0.8 105"
    [branch5-ce1]="vti106 10.99.0.13 10.99.0.12 106"
    [branch6-ce1]="vti107 10.99.0.15 10.99.0.14 107"
)

# ---------- dc2-ce1 ↔ branch5-ce1 direct peering ----------
declare -A DIRECT_TUNNELS=(
    [dc2-ce1]="vti200 10.99.1.0 branch5-ce1 200"
    [branch5-ce1]="vti200 10.99.1.1 dc2-ce1 200"
)

# ---------- utilities ----------
log() { echo "[ipsec] $*"; }

install_strongswan() {
    if ! command -v ipsec &>/dev/null; then
        log "Installing strongSwan..."
        apk add --no-cache strongswan 2>/dev/null || apk add strongswan
    else
        log "strongSwan already installed"
    fi
}

create_vti() {
    local vti_name=$1 local_ip=$2 remote_ip=$3 mark=$4 vti_ip=$5
    # Check if VTI already exists
    ip link show "$vti_name" &>/dev/null && { log "VTI $vti_name already exists"; return 0; }
    log "Creating VTI $vti_name ($local_ip → $remote_ip, mark=$mark, ip=$vti_ip)"
    ip tunnel add "$vti_name" local "$local_ip" remote "$remote_ip" key "$mark" mode vti
    ip addr add "$vti_ip" dev "$vti_name" 2>/dev/null || true
    ip link set "$vti_name" up
    # Disable reverse-path-filter and policy so the VTI works correctly
    sysctl -w "net.ipv4.conf.${vti_name}.rp_filter=0" &>/dev/null || true
    sysctl -w "net.ipv4.conf.${vti_name}.disable_policy=1" &>/dev/null || true
}

write_ipsec_conf() {
    local conf_file="/etc/ipsec.conf"
    log "Writing $conf_file"
    cat > "$conf_file" <<'IPSECHEADER'
config setup
    charondebug="dmn 2, ike 2, cfg 2, knl 2"
    uniqueids=yes
    strictcrlpolicy=no

conn %default
    # ---------- lifetimes & rekeying ----------
    ikelifetime=24h
    lifetime=4h
    rekeymargin=3m
    rekeyfuzz=100%
    keyingtries=%forever
    # ---------- IKEv2 ----------
    keyexchange=ikev2
    authby=psk
    mobike=no
    # ---------- ESP crypto ----------
    type=tunnel
    esp=aes256-sha256-modp2048!
    ike=aes256-sha256-modp2048!
    leftfirewall=no
    rightfirewall=no
    leftsubnet=0.0.0.0/0
    rightsubnet=0.0.0.0/0
    # ---------- Dead Peer Detection ----------
    dpdaction=clear
    dpddelay=30s
    dpdtimeout=150s
    # ---------- SA lifecycle ----------
    close_action=restart
    install_policy=yes
    compress=no

IPSECHEADER

    local local_lo="${LO[$HOSTNAME]}"
    if [ -z "$local_lo" ]; then
        log "ERROR: Unknown hostname $HOSTNAME, cannot write ipsec.conf"
        return 1
    fi

    if [ "$HOSTNAME" = "hub-ce1" ]; then
        for entry in "${HUB_TUNNELS[@]}"; do
            read -r vti_name remote_node mark hub_ip remote_ip <<< "$entry"
            local remote_lo="${LO[$remote_node]}"
            cat >> "$conf_file" <<CONN

conn $vti_name
    left=$local_lo
    leftid=@$HOSTNAME
    right=$remote_lo
    rightid=@$remote_node
    auto=start
    mark=$mark

CONN
        done
    elif [ "$HOSTNAME" = "dc2-ce1" ] || [ "$HOSTNAME" = "branch5-ce1" ]; then
        # Spoke with direct peer tunnel
        set -- ${DIRECT_TUNNELS[$HOSTNAME]}
        local dt_vti=$1 dt_ip=$2 dt_peer=$3 dt_mark=$4
        local dt_peer_lo="${LO[$dt_peer]}"
        # Tunnel to hub
        set -- ${SPOKE_TUNNELS[$HOSTNAME]}
        local sp_vti=$1 sp_ip=$2 sp_hub_ip=$3 sp_mark=$4
        local hub_lo="${LO[hub-ce1]}"

        cat >> "$conf_file" <<CONN

conn $sp_vti
    left=$local_lo
    leftid=@$HOSTNAME
    right=$hub_lo
    rightid=@hub-ce1
    auto=start
    mark=$sp_mark

conn $dt_vti
    left=$local_lo
    leftid=@$HOSTNAME
    right=$dt_peer_lo
    rightid=@$dt_peer
    auto=start
    mark=$dt_mark

CONN
    else
        # Regular spoke: single tunnel to hub-ce1
        set -- ${SPOKE_TUNNELS[$HOSTNAME]}
        local sp_vti=$1 sp_ip=$2 sp_hub_ip=$3 sp_mark=$4
        local hub_lo="${LO[hub-ce1]}"

        cat >> "$conf_file" <<CONN

conn $sp_vti
    left=$local_lo
    leftid=@$HOSTNAME
    right=$hub_lo
    rightid=@hub-ce1
    auto=start
    mark=$sp_mark

CONN
    fi
}

write_ipsec_secrets() {
    local secrets_file="/etc/ipsec.secrets"
    log "Writing $secrets_file"
    > "$secrets_file"
    for node in "${!LO[@]}"; do
        if [ "$node" != "$HOSTNAME" ]; then
            echo "@$HOSTNAME @$node : PSK \"netoracle-ipsec-${HOSTNAME}-${node}\"" >> "$secrets_file"
        fi
    done
    chmod 600 "$secrets_file"
}

create_vti_interfaces() {
    local local_lo="${LO[$HOSTNAME]}"
    log "Creating VTI interfaces for $HOSTNAME (lo=$local_lo)"

    if [ "$HOSTNAME" = "hub-ce1" ]; then
        for entry in "${HUB_TUNNELS[@]}"; do
            read -r vti_name remote_node mark hub_ip remote_ip <<< "$entry"
            create_vti "$vti_name" "$local_lo" "${LO[$remote_node]}" "$mark" "${hub_ip}/31"
        done
    elif [ "$HOSTNAME" = "dc2-ce1" ]; then
        # Tunnel to hub-ce1
        set -- ${SPOKE_TUNNELS[$HOSTNAME]}
        create_vti "$1" "$local_lo" "${LO[hub-ce1]}" "$4" "${2}/31"
        # Tunnel to branch5-ce1
        set -- ${DIRECT_TUNNELS[$HOSTNAME]}
        create_vti "$1" "$local_lo" "${LO[$3]}" "$4" "${2}/31"
    elif [ "$HOSTNAME" = "branch5-ce1" ]; then
        # Tunnel to hub-ce1
        set -- ${SPOKE_TUNNELS[$HOSTNAME]}
        create_vti "$1" "$local_lo" "${LO[hub-ce1]}" "$4" "${2}/31"
        # Tunnel to dc2-ce1
        set -- ${DIRECT_TUNNELS[$HOSTNAME]}
        create_vti "$1" "$local_lo" "${LO[$3]}" "$4" "${2}/31"
    else
        # Regular spoke: single tunnel to hub-ce1
        set -- ${SPOKE_TUNNELS[$HOSTNAME]}
        create_vti "$1" "$local_lo" "${LO[hub-ce1]}" "$4" "${2}/31"
    fi
}

# ========== main ==========
install_strongswan
create_vti_interfaces
write_ipsec_conf
write_ipsec_secrets

log "Starting strongSwan..."
ipsec start

# Start tunnel health monitor (background daemon)
log "Starting tunnel health monitor..."
if [ -x /usr/local/bin/tunnel_health.sh ]; then
    nohup /usr/local/bin/tunnel_health.sh >/dev/null 2>&1 &
    log "Tunnel health monitor PID: $!"
else
    log "WARNING: tunnel_health.sh not found"
fi

log "IPsec setup complete for $HOSTNAME"
exit 0
