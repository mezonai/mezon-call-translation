#!/bin/bash

# Start Monitoring Stack Script

set -e

echo "=========================================="
echo "Starting Mezon Monitoring Stack"
echo "=========================================="

# Check if .env exists
if [ ! -f .env ]; then
    echo "⚠️  .env file not found. Creating from .env.example..."
    cp .env.example .env
    echo "✅ Created .env file. Please review and update if needed."
fi

# Check if main application is running
echo ""
echo "Checking main application..."
if ! docker network ls | grep -q "mezon-call-translation_mezon-network"; then
    echo "⚠️  Main application network not found!"
    echo "Please start the main application first:"
    echo "  cd .. && docker-compose up -d"
    exit 1
fi

echo "✅ Main application network found"

# Start monitoring stack
echo ""
echo "Starting monitoring services..."
docker-compose up -d

# Wait for services to be healthy
echo ""
echo "Waiting for services to be healthy..."
sleep 10

# Check Prometheus
echo ""
echo "Checking Prometheus..."
if curl -sf http://localhost:9090/-/healthy > /dev/null; then
    echo "✅ Prometheus is healthy"
else
    echo "❌ Prometheus is not responding"
fi

# Check Grafana
echo ""
echo "Checking Grafana..."
if curl -sf http://localhost:3000/api/health > /dev/null; then
    echo "✅ Grafana is healthy"
else
    echo "❌ Grafana is not responding"
fi

# Check AlertManager
echo ""
echo "Checking AlertManager..."
if curl -sf http://localhost:9093/-/healthy > /dev/null; then
    echo "✅ AlertManager is healthy"
else
    echo "❌ AlertManager is not responding"
fi

# Display access information
echo ""
echo "=========================================="
echo "✅ Monitoring Stack Started Successfully!"
echo "=========================================="
echo ""
echo "Access URLs:"
echo "  Prometheus:   http://localhost:9090"
echo "  Grafana:      http://localhost:3000"
echo "  AlertManager: http://localhost:9093"
echo ""
echo "Grafana Credentials:"
echo "  Username: admin"
echo "  Password: admin (change in .env)"
echo ""
echo "To view logs:"
echo "  docker-compose logs -f"
echo ""
echo "To stop:"
echo "  docker-compose down"
echo "=========================================="
