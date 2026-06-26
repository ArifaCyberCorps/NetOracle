#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "=== NetOracle Lab - Stop ==="
echo ""

# Stop monitoring stack (docker-compose)
echo "Stopping monitoring stack..."
if [ -f "monitoring/docker-compose.yml" ]; then
    docker compose -f monitoring/docker-compose.yml down 2>/dev/null || true
fi

# Destroy containerlab topology
echo "Destroying containerlab topology..."
sudo containerlab destroy -t topology.clab.yml --cleanup

echo ""
echo "=== Lab is DOWN ==="
echo "Ground truth logs preserved in: $SCRIPT_DIR/ground_truth/"
echo "To restart: bash scripts/start_lab.sh"
