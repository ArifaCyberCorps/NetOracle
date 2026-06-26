#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "=== NetOracle Lab - Stop ==="
echo ""

# Destroy containerlab topology
echo "Destroying containerlab topology..."
sudo containerlab destroy -t topology.clab.yml --cleanup

echo ""
echo "=== Lab is DOWN ==="
echo "Ground truth logs preserved in: $SCRIPT_DIR/ground_truth/"
echo "To restart: bash scripts/start_lab.sh"
