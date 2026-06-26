#!/bin/bash
# Verify and log the monitoring stack status

echo "=== Monitoring Stack ==="
echo ""

GRAFANA_URL="http://localhost:3000"

# Check Prometheus
if docker ps --format '{{.Names}}' | grep -q "prometheus"; then
    PROM_IP=$(docker inspect prometheus -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null)
    echo "  Prometheus: running (management: 192.168.100.110, internal: $PROM_IP)"
    echo "    Targets: http://192.168.100.110:9090/targets"
else
    echo "  Prometheus: NOT RUNNING"
fi

# Check Grafana
if docker ps --format '{{.Names}}' | grep -q "grafana"; then
    GRAF_IP=$(docker inspect grafana -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null)
    echo "  Grafana:    running (management: 192.168.100.111, internal: $GRAF_IP)"
    echo "    URL: http://192.168.100.111:3000 (admin/admin)"
else
    echo "  Grafana:    NOT RUNNING"
fi

# Check cAdvisor
if docker ps --format '{{.Names}}' | grep -q "cadvisor"; then
    echo "  cAdvisor:   running (management: 192.168.100.112)"
    echo "    URL: http://192.168.100.112:8080"
else
    echo "  cAdvisor:   NOT RUNNING"
fi

# Check Telegraf
if docker ps --format '{{.Names}}' | grep -q "telegraf"; then
    echo "  Telegraf:   running (management: 192.168.100.113)"
    echo "    Metrics: http://192.168.100.113:9273/metrics"
else
    echo "  Telegraf:   NOT RUNNING"
fi

# Check Telemetry Collector
if docker ps --format '{{.Names}}' | grep -q "telemetry-collector"; then
    echo "  Telemetry:  running (management: 192.168.100.100)"
    echo "    Metrics: http://192.168.100.100:8000/metrics"
else
    echo "  Telemetry:  NOT RUNNING"
fi

echo ""
echo "=== Quick Links ==="
echo "  Grafana Dashboard:  http://192.168.100.111:3000/d/netoracle-network-overview"
echo "  Prometheus Targets: http://192.168.100.110:9090/targets"
echo "  cAdvisor:           http://192.168.100.112:8080"
echo "  Telemetry Metrics:  http://192.168.100.100:8000/metrics"
echo "  Telegraf Metrics:   http://192.168.100.113:9273/metrics"
