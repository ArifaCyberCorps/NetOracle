#!/bin/bash
# Start the telemetry collector and configure syslog forwarding

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "=== Starting Telemetry Collection ==="
echo ""

# Start the telemetry collector in the background
echo "[1/3] Starting telemetry collector..."
python3 -m telemetry.collector &
TELEMETRY_PID=$!
echo "  PID: $TELEMETRY_PID"
echo $TELEMETRY_PID > /tmp/telemetry_collector.pid

# Wait for collector to start
sleep 2

# Configure syslog forwarding from FRR containers
echo "[2/3] Configuring syslog forwarding from FRR devices..."
bash scripts/enable_syslog.sh

echo "[3/3] Telemetry collection started"
echo ""
echo "  Output:    $SCRIPT_DIR/telemetry_data/"
echo "  Collector: localhost:5514 (syslog UDP)"
echo "  PID:       $TELEMETRY_PID"
echo ""
echo "To stop:         kill \$(cat /tmp/telemetry_collector.pid)"
echo "To view data:    ls -la telemetry_data/"
echo "To tail live:    tail -f telemetry_data/interface_counters.csv"
