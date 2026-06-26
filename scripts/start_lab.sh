#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "=== NetOracle Lab - Start ==="
echo ""

# Deploy containerlab topology
echo "[1/3] Deploying containerlab topology..."
sudo containerlab deploy -t topology.clab.yml --reconfigure

echo "[2/3] Waiting for routing convergence (30s)..."
sleep 30

# Apply QoS policies
echo "[3/3] Applying QoS policies..."
bash scripts/apply_qos.sh

# Start telemetry collection
echo "[4/5] Starting telemetry collector..."
bash scripts/start_telemetry.sh

echo "[5/5] Monitoring stack status:"
bash scripts/start_monitoring.sh

echo ""
echo "=== Lab is UP ==="
echo ""
echo "Quick verification:"
echo "  Show containers:    sudo containerlab inspect -t topology.clab.yml"
echo "  Enter router:       docker exec -it hub-pe1 vtysh"
echo "  Check OSPF:         docker exec hub-pe1 vtysh -c 'show ip ospf neighbor'"
echo "  Check BGP:          docker exec hub-pe1 vtysh -c 'show bgp vpnv4 unicast summary'"
echo "  Check LDP:          docker exec dc1-p1 vtysh -c 'show mpls ldp binding'"
echo "  Check IPsec SAs:    docker exec hub-ce1 ipsec status"
echo "  Check VTI:          docker exec hub-ce1 ip link show type vti"
echo "  Ping across:        docker exec branch1-host ping -c 3 172.16.0.2"
echo ""
echo "IPsec SD-WAN Overlay:"
echo "  hub-ce1 IPsec:      docker exec hub-ce1 ipsec status | grep ESTABLISHED"
echo "  hub-ce1 VTI:        docker exec hub-ce1 ip addr show type vti"
echo "  Overlay BGP:        docker exec hub-ce1 vtysh -c 'show bgp summary'"
echo ""
echo "Monitoring:"
echo "  Grafana Dashboard:  http://192.168.100.111:3000 (admin/admin)"
echo "  Prometheus:         http://192.168.100.110:9090"
echo "  cAdvisor:           http://192.168.100.112:8080"
echo "  Telemetry Metrics:  http://192.168.100.100:8000/metrics"
echo ""
echo "Telemetry CSV:"
echo "  collector PID:      cat /tmp/telemetry_collector.pid"
echo "  tail interfaces:    tail -f telemetry_data/interface_counters.csv"
echo "  tail BGP:           tail -f telemetry_data/bgp_events.csv"
echo ""
echo "Start traffic:        python3 -m traffic.scheduler baseline 3600"
echo "Run scenario:         python3 -m fault_injection.scenario_runner progressive_congestion"
