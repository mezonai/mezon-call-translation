#!/bin/bash

# Setup script cho Mezon Call Translation Docker

echo "🚀 Setting up Mezon Call Translation Docker Environment"

# Tạo thư mục cần thiết
echo "📁 Creating necessary directories..."
mkdir -p models
mkdir -p logs
mkdir -p scripts

# Copy environment file nếu chưa có
if [ ! -f .env ]; then
    echo "📋 Creating .env file from template..."
    cp env.example .env
    echo "⚠️  Please edit .env file with your actual configuration"
fi

# Tạo thư mục cho Vosk models
echo "📁 Creating models directory structure..."
mkdir -p models/vosk-model

echo "✅ Setup completed!"
echo ""
echo "Next steps:"
echo "1. Edit .env file with your LiveKit credentials"
echo "2. Download Vosk model and place it in models/vosk-model/"
echo "3. Run: docker-compose up -d"
echo ""
echo "For development with hot reload:"
echo "docker-compose -f docker-compose.yml -f docker-compose.override.yml up"
