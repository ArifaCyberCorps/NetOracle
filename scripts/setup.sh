#!/bin/bash
set -e

echo "=== NetOracle Lab Setup ==="
echo ""

# Check prerequisites
echo "[1/6] Checking prerequisites..."
if ! command -v docker &>/dev/null; then
    echo "ERROR: Docker not found. Install Docker first."
    exit 1
fi

if ! command -v containerlab &>/dev/null; then
    echo "ERROR: Containerlab not found."
    echo "Install: sudo bash -c \"$(curl -sL https://get.containerlab.dev)\""
    exit 1
fi

if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python3 not found."
    exit 1
fi

# Install Python dependencies
echo "[2/6] Installing Python dependencies..."
pip3 install pyyaml scapy 2>/dev/null || pip install pyyaml scapy

# Create directory structure
echo "[3/6] Creating directory structure..."
mkdir -p ground_truth logs

# Pre-pull FRR image
echo "[4/8] Pre-pulling FRR container image..."
docker pull quay.io/frrouting/frr:9.1.0

# Build custom CE image with strongSwan pre-installed (IPsec VTI overlay)
echo "[5/8] Building CE image (FRR + strongSwan)..."
docker build -t netoracle/ce-ipsec:9.1 -f docker/Dockerfile.ce .

# Build host image with traffic generators baked in
echo "[6/8] Building host image (iperf3, mgen, scapy, etc.)..."
docker build -t netoracle/host:9.1 -f docker/Dockerfile.host .

# Build telemetry-collector image
echo "[7/8] Building telemetry-collector image..."
docker build -t netoracle/telemetry-collector:9.1 -f docker/Dockerfile.telemetry-collector .

# Build NTP server image
echo "[8/8] Building NTP server image..."
docker build -t netoracle/ntp:9.1 -f docker/Dockerfile.ntp .

# Verify lab file exists
echo ""
echo "Verifying topology file..."
if [ -f "topology.clab.yml" ]; then
    echo "  Found: topology.clab.yml"
else
    echo "  ERROR: topology.clab.yml not found in current directory"
    exit 1
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Start the lab:   bash scripts/start_lab.sh"
echo "  2. Verify:          bash scripts/verify_lab.sh"
echo "  3. Run traffic:     python3 -m traffic.scheduler baseline 3600"
echo "  4. Run scenario:    python3 -m fault_injection.scenario_runner bgp_flap_cascade"
