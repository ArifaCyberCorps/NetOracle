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
echo "[4/7] Pre-pulling FRR container image..."
docker pull frrouting/frr:9.1

# Pre-pull Ubuntu image
echo "[5/7] Pre-pulling Ubuntu image..."
docker pull ubuntu:22.04

# Build custom CE image with strongSwan pre-installed (IPsec VTI overlay)
echo "[6/7] Building CE image (FRR + strongSwan)..."
docker build -t netoracle/ce-ipsec:9.1 -f docker/Dockerfile.ce .

# Verify lab file exists
echo "[7/7] Verifying topology file..."
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
echo "  1. Start the lab:   sudo containerlab deploy -t topology.clab.yml"
echo "  2. Apply QoS:       bash scripts/apply_qos.sh"
echo "  3. Verify:          bash scripts/verify_lab.sh"
echo "  4. Run traffic:     python3 -m traffic.scheduler baseline 3600"
echo "  5. Run scenario:    python3 -m fault_injection.scenario_runner bgp_flap_cascade"
