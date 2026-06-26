#!/bin/bash
# Start the monitoring stack (Prometheus, Grafana, cAdvisor, Telegraf, Telemetry Collector, NTP)
# via docker-compose on the containerlab management network.
# Must be run AFTER containerlab deploys the topology so the "clab" network exists.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "=== Starting Monitoring Stack (docker-compose) ==="
echo ""

# Detect the containerlab management network name and export it
# so docker-compose picks it up via ${CLAB_NET:-clab} substitution
CLAB_NET="clab"
if docker network inspect "$CLAB_NET" &>/dev/null; then
    echo "  Using network: $CLAB_NET"
elif docker network inspect "clab-netoracle-sdwan" &>/dev/null; then
    CLAB_NET="clab-netoracle-sdwan"
    echo "  Using network: $CLAB_NET"
else
    echo "  WARNING: clab network not found. Attempting to detect..."
    CLAB_NET=$(docker network ls --filter name=clab --format "{{.Name}}" | head -1)
    if [ -n "$CLAB_NET" ]; then
        echo "  Detected network: $CLAB_NET"
    else
        echo "  ERROR: No clab network found. Deploy the topology first."
        exit 1
    fi
fi

export CLAB_NET

echo "  Deploying: CLAB_NET=$CLAB_NET docker compose -f monitoring/docker-compose.yml up -d"
docker compose -f monitoring/docker-compose.yml up -d

echo ""
echo "=== Monitoring Stack Status ==="
for svc in prometheus grafana cadvisor telegraf telemetry-collector ntp-server; do
    if docker ps --format '{{.Names}}' | grep -q "$svc"; then
        echo "  [UP] $svc"
    else
        echo "  [DOWN] $svc"
    fi
done

echo ""
echo "=== Quick Links ==="
echo "  Grafana Dashboard:  http://192.168.100.111:3000/d/netoracle-network-overview"
echo "  Prometheus Targets: http://192.168.100.110:9090/targets"
echo "  cAdvisor:           http://192.168.100.112:8080"
echo "  Telemetry Metrics:  http://192.168.100.100:8000/metrics"
echo "  Telegraf Metrics:   http://192.168.100.113:9273/metrics"
