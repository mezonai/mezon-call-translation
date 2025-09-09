#!/bin/bash

# Production run script (Linux/macOS)

echo "🚀 Starting Mezon Call Translation in Production Mode"

# Kiểm tra Docker có chạy không
if ! docker --version > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker first."
    exit 1
fi
echo "✅ Docker is running"

# Kiểm tra file .env
if [ ! -f .env ]; then
    echo "❌ .env file not found. Please run setup first:"
    echo "./scripts/setup.sh"
    exit 1
fi

# Kiểm tra Vosk model
if [ ! -d "models/vosk-model/vosk-model-small-en-us-0.15" ]; then
    echo "❌ Vosk model not found. Please download first:"
    echo "./scripts/download-vosk-model.sh"
    exit 1
fi

echo "Building and starting services..."
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d

echo "✅ Services started in background!"
echo ""
echo "To view logs:"
echo "docker-compose logs -f"
echo ""
echo "To stop services:"
echo "docker-compose down"
