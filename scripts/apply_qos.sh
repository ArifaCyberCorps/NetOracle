#!/bin/bash
# Apply QoS policy to all PE routers
# Run this AFTER the lab is fully up and BGP/MPLS is converged
# Uses docker exec + tc to configure per-hop QoS

QOS_SCRIPT=$(cat << 'QOS_EOF'
# Strict priority queue for EF (DSCP 46)
tc qdisc add dev {IFACE} root handle 1: htb default 30
tc class add dev {IFACE} parent 1: classid 1:1 htb rate 1Gbit ceil 1Gbit
# EF - Priority Queue (DSCP 46)
tc class add dev {IFACE} parent 1:1 classid 1:10 htb rate 100Mbit ceil 200Mbit prio 0
tc qdisc add dev {IFACE} parent 1:10 handle 10: pfifo limit 50
tc filter add dev {IFACE} protocol ip parent 1:0 prio 1 u32 \
    match ip tos 0xb8 0xfc flowid 1:10
# AF41 - Video (DSCP 34)
tc class add dev {IFACE} parent 1:1 classid 1:20 htb rate 200Mbit ceil 500Mbit prio 1
tc qdisc add dev {IFACE} parent 1:20 handle 20: pfifo limit 100
tc filter add dev {IFACE} protocol ip parent 1:0 prio 2 u32 \
    match ip tos 0x88 0xfc flowid 1:20
# AF21 - Business (DSCP 18)
tc class add dev {IFACE} parent 1:1 classid 1:25 htb rate 300Mbit ceil 500Mbit prio 2
tc qdisc add dev {IFACE} parent 1:25 handle 25: pfifo limit 200
tc filter add dev {IFACE} protocol ip parent 1:0 prio 3 u32 \
    match ip tos 0x48 0xfc flowid 1:25
# Default - Best Effort
tc class add dev {IFACE} parent 1:1 classid 1:30 htb rate 400Mbit ceil 1Gbit prio 3
tc qdisc add dev {IFACE} parent 1:30 handle 30: pfifo limit 500
# WRED for best-effort (uses RED)
tc qdisc add dev {IFACE} parent 1:30 handle 30: red \
    limit 1000 min 300 max 900 avpkt 1000 \
    burst 5 probability 0.02 bandwidth 1Gbit
QOS_EOF
)

PE_ROUTERS=("dc1-pe1" "dc2-pe1" "hub-pe1")
INTERFACES=("eth1" "eth2" "eth3" "eth4" "eth5" "eth6")

for router in "${PE_ROUTERS[@]}"; do
    echo "=== Applying QoS on $router ==="
    for iface in "${INTERFACES[@]}"; do
        # Check if interface exists inside container
        if docker exec "$router" ip link show "$iface" &>/dev/null 2>&1; then
            echo "  Configuring $router:$iface..."
            # Clean existing qdiscs
            docker exec "$router" tc qdisc del dev "$iface" root 2>/dev/null || true
            # Apply new QoS (substituting interface name)
            SCRIPT="${QOS_SCRIPT//\{IFACE\}/$iface}"
            echo "$SCRIPT" | docker exec -i "$router" bash 2>/dev/null || \
                echo "  WARNING: QoS apply had issues on $router:$iface"
        fi
    done
done

echo "QoS policy applied to all PE routers."
echo ""
echo "Verification:"
for router in "${PE_ROUTERS[@]}"; do
    echo "--- $router ---"
    docker exec "$router" tc qdisc show 2>/dev/null | head -20
done
