#!/bin/bash
# Configure FRR containers to forward syslog to the telemetry collector
# Runs after the lab is deployed

COLLECTOR_HOST="192.168.100.100"
COLLECTOR_PORT="5514"

FRR_CONTAINERS=(
    "dc1-p1" "dc2-p1" "hub-p1"
    "dc1-pe1" "dc2-pe1" "hub-pe1"
    "dc1-ce1" "dc2-ce1" "hub-ce1"
    "branch1-ce1" "branch2-ce1" "branch3-ce1"
    "branch4-ce1" "branch5-ce1" "branch6-ce1"
)

echo "=== Configuring Syslog Forwarding ==="
echo "  Target: $COLLECTOR_HOST:$COLLECTOR_PORT"
echo ""

for container in "${FRR_CONTAINERS[@]}"; do
    if docker ps --format '{{.Names}}' | grep -q "$container"; then
        echo "  Configuring syslog on $container..."
        # Kill existing syslogd, start a new one that forwards to collector
        docker exec "$container" sh -c "
            killall syslogd 2>/dev/null || true
            syslogd -n -R $COLLECTOR_HOST:$COLLECTOR_PORT 2>/dev/null &
        " 2>/dev/null || true
        # Also configure FRR to log to syslog
        docker exec "$container" vtysh -c "
            configure terminal
            log syslog
            end
        " 2>/dev/null || true
    else
        echo "  WARNING: $container not running, skipping"
    fi
done

echo ""
echo "Syslog forwarding configured on running FRR containers."
echo "Telemetry collector should be listening on $COLLECTOR_HOST:$COLLECTOR_PORT"
