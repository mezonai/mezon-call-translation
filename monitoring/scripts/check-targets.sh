#!/bin/bash

# Check Prometheus Targets Script

echo "=========================================="
echo "Checking Prometheus Targets"
echo "=========================================="

# Check if Prometheus is running
if ! curl -sf http://localhost:9090/-/healthy > /dev/null; then
    echo "❌ Prometheus is not running!"
    echo "Please start monitoring stack first: ./scripts/start.sh"
    exit 1
fi

echo ""
echo "✅ Prometheus is running"
echo ""

# Get targets from Prometheus API
echo "Fetching targets from Prometheus..."
echo ""

# Get all targets
TARGETS=$(curl -s http://localhost:9090/api/v1/targets | jq -r '.data.activeTargets[] | "\(.labels.job)\t\(.scrapeUrl)\t\(.health)\t\(.lastError // "none")"')

echo "Active Targets:"
echo "----------------------------------------"
printf "%-30s %-40s %-10s %s\n" "JOB" "TARGET" "HEALTH" "LAST ERROR"
echo "----------------------------------------"
echo "$TARGETS" | while IFS=$'\t' read -r job target health error; do
    if [ "$health" = "up" ]; then
        printf "%-30s %-40s \033[0;32m%-10s\033[0m %s\n" "$job" "$target" "$health" "$error"
    else
        printf "%-30s %-40s \033[0;31m%-10s\033[0m %s\n" "$job" "$target" "$health" "$error"
    fi
done

echo ""
echo "=========================================="
echo "Server Instances Discovery"
echo "=========================================="

# Check Docker network
echo ""
echo "Checking Docker containers in mezon-network..."
docker network inspect mezon-call-translation_mezon-network --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}' 2>/dev/null || echo "Network not found"

echo ""
echo "=========================================="
echo "DNS Resolution Test"
echo "=========================================="

# Test DNS resolution from Prometheus container
echo ""
echo "Testing DNS resolution from Prometheus container..."
docker exec mezon-prometheus nslookup server 2>/dev/null || echo "Cannot resolve 'server'"
docker exec mezon-prometheus nslookup tasks.server 2>/dev/null || echo "Cannot resolve 'tasks.server'"

echo ""
echo "=========================================="
echo "Connectivity Test"
echo "=========================================="

# Test connectivity to server
echo ""
echo "Testing connectivity to server:8000..."
docker exec mezon-prometheus wget -q -O- --timeout=5 http://server:8000/health 2>/dev/null && echo "✅ Can connect to server:8000" || echo "❌ Cannot connect to server:8000"

echo ""
echo "Testing metrics endpoint..."
docker exec mezon-prometheus wget -q -O- --timeout=5 http://server:8000/metrics 2>/dev/null | head -n 5 && echo "..." && echo "✅ Metrics endpoint is accessible" || echo "❌ Cannot access metrics endpoint"

echo ""
echo "=========================================="
echo "To view in Prometheus UI:"
echo "  http://localhost:9090/targets"
echo "=========================================="
