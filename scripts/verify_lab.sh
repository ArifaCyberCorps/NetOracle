#!/bin/bash
# Lab verification script - run after start_lab.sh

echo "=== NetOracle Lab Verification ==="
echo ""

PASS=0
FAIL=0

check() {
    local desc="$1"
    if [ $? -eq 0 ]; then
        echo "  [PASS] $desc"
        PASS=$((PASS + 1))
    else
        echo "  [FAIL] $desc"
        FAIL=$((FAIL + 1))
    fi
}

echo "--- Container Status ---"
for node in dc1-p1 dc2-p1 hub-p1 dc1-pe1 dc2-pe1 hub-pe1 \
            dc1-ce1 dc2-ce1 hub-ce1 \
            branch1-ce1 branch2-ce1 branch3-ce1 \
            branch4-ce1 branch5-ce1 branch6-ce1 \
            dc1-host dc2-host hub-host \
            branch1-host branch2-host branch3-host \
            branch4-host branch5-host branch6-host; do
    docker ps --format '{{.Names}}' | grep -q "$node"
    check "Container $node is running"
done

echo ""
echo "--- Routing Convergence (P Routers) ---"
for router in dc1-p1 dc2-p1 hub-p1; do
    docker exec "$router" vtysh -c 'show ip ospf neighbor' 2>/dev/null | grep -q "Full"
    check "$router OSPF neighbors Full"
done

echo ""
echo "--- BGP VPNv4 (PE Routers) ---"
for router in dc1-pe1 dc2-pe1 hub-pe1; do
    docker exec "$router" vtysh -c 'show bgp vpnv4 unicast summary' 2>/dev/null | grep -q "Established"
    check "$router MP-BGP VPNv4 Established"
done

echo ""
echo "--- MPLS LDP ---"
for router in dc1-p1 dc2-p1 hub-p1; do
    docker exec "$router" vtysh -c 'show mpls ldp discovery' 2>/dev/null | grep -q "LDP Id"
    check "$router LDP discovery active"
done

echo ""
echo "--- Monitoring Stack (docker-compose) ---"
for mon in prometheus grafana cadvisor telegraf telemetry-collector ntp-server; do
    docker ps --format '{{.Names}}' | grep -q "$mon"
    check "Container $mon is running"
done

echo ""
echo "--- Prometheus Targets ---"
if docker ps --format '{{.Names}}' | grep -q "prometheus"; then
    docker exec prometheus wget -q -O- http://localhost:9090/api/v1/targets 2>/dev/null | grep -q "up" || true
    TARGET_COUNT=$(docker exec prometheus wget -q -O- http://localhost:9090/api/v1/targets 2>/dev/null | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(len(d.get('data',{}).get('activeTargets',[])))" 2>/dev/null)
    if [ -n "$TARGET_COUNT" ]; then
        echo "  [INFO] Prometheus targets: $TARGET_COUNT"
    fi
fi

echo ""
echo "--- IPsec SD-WAN Overlay ---"
for ce in hub-ce1 dc1-ce1 dc2-ce1 branch1-ce1 branch2-ce1 branch3-ce1 branch4-ce1 branch5-ce1 branch6-ce1; do
    docker ps --format '{{.Names}}' | grep -q "$ce"
    check "Container $ce is running"
    docker exec "$ce" ipsec status 2>/dev/null | grep -q "ESTABLISHED"
    if [ $? -eq 0 ]; then
        echo "  [INFO] $ce has established IPsec SAs"
    else
        echo "  [WARN] $ce: no IPsec SAs or strongSwan not running"
    fi
done

echo ""
echo "--- Overlay BGP (VTI) ---"
for ce in hub-ce1 dc1-ce1 dc2-ce1 branch1-ce1 branch2-ce1 branch3-ce1 branch4-ce1 branch5-ce1 branch6-ce1; do
    docker exec "$ce" vtysh -c 'show bgp summary json' 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    peers = d.get('peers', {})
    established = sum(1 for p in peers.values() if isinstance(p, dict) and p.get('state') == 'Established')
    total = len(peers)
    print(f'  [BGP] $ce: {established}/{total} peers established')
except: pass
" 2>/dev/null || echo "  [BGP] $ce: could not check BGP status"
done

echo ""
echo "--- Telemetry Collector ---"
if docker ps --format '{{.Names}}' | grep -q "telemetry-collector"; then
    PASS=$((PASS + 1))
    echo "  [PASS] telemetry-collector container is running"
else
    FAIL=$((FAIL + 1))
    echo "  [FAIL] telemetry-collector container is not running"
fi

# Check that telemetry CSV output exists
if ls ground_truth/*.csv 2>/dev/null | head -1 > /dev/null; then
    PASS=$((PASS + 1))
    echo "  [PASS] Ground truth logs exist"
else
    FAIL=$((FAIL + 1))
    echo "  [FAIL] No ground truth logs found (run traffic scheduler first)"
fi

echo ""
echo "--- CE Underlay Reachability ---"
docker exec branch1-host ping -c 1 -W 2 172.16.0.2 &>/dev/null
check "Branch1 -> DC1 reachable (underlay)"

docker exec branch1-host ping -c 1 -W 2 172.16.2.2 &>/dev/null
check "Branch1 -> Hub reachable (underlay)"

docker exec branch2-host ping -c 1 -W 2 172.16.0.2 &>/dev/null
check "Branch2 -> DC1 reachable (underlay)"

docker exec branch5-host ping -c 1 -W 2 172.16.1.2 &>/dev/null
check "Branch5 -> DC2 reachable (underlay)"

echo ""
echo "--- Traffic Generator Readiness ---"
for host in dc1-host dc2-host hub-host branch1-host branch2-host branch3-host branch4-host branch5-host branch6-host; do
    docker exec "$host" which iperf3 &>/dev/null
    check "$host has iperf3 installed"
    docker exec "$host" python3 -c "import pyftpdlib" &>/dev/null
    check "$host has pyftpdlib (FTP)"
    docker exec "$host" python3 -c "import smbprotocol" &>/dev/null
    check "$host has smbprotocol (SMB)"
    docker exec "$host" python3 -c "import scapy" &>/dev/null
    check "$host has scapy"
done

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
