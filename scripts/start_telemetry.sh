#!/bin/bash
# Configure syslog forwarding from FRR containers to the telemetry collector.
# The telemetry collector itself runs as a docker-compose service
# (monitoring/docker-compose.yml); this script only sets up syslog forwarding.
# Runs after the lab is deployed and monitoring stack is up.

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "=== Configuring Syslog Forwarding ==="
echo "  Collector target: 192.168.100.100:5514"
echo ""

bash scripts/enable_syslog.sh
