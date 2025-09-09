#!/bin/bash

# Development run script (Linux/macOS)

echo "🚀 Starting Mezon Call Translation in Development Mode"

# Kiểm tra Docker có chạy không
if ! docker --version > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker first."
    exit 1
fi
echo "✅ Docker is running"

# Kiểm tra file .env
if [ ! -f .env ]; then
    echo "⚠️  .env file not found. Running setup..."
    ./scripts/setup.sh
fi

# Kiểm tra Vosk model
if [ ! -d "models/vosk-model/vosk-model-small-en-us-0.15" ]; then
    echo "⚠️  Vosk model not found. Downloading..."
    ./scripts/download-vosk-model.sh
fi

echo "Starting services with hot reload..."
docker-compose -f docker-compose.yml -f docker-compose.override.yml up
