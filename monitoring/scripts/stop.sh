#!/bin/bash

# Stop Monitoring Stack Script

set -e

echo "=========================================="
echo "Stopping Mezon Monitoring Stack"
echo "=========================================="

# Stop services
echo ""
echo "Stopping monitoring services..."
docker-compose down

echo ""
echo "=========================================="
echo "✅ Monitoring Stack Stopped Successfully!"
echo "=========================================="
echo ""
echo "To start again:"
echo "  ./scripts/start.sh"
echo ""
echo "To remove all data (WARNING: This deletes all metrics and dashboards):"
echo "  docker-compose down -v"
echo "=========================================="
