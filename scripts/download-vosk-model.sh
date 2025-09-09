#!/bin/bash

# Script để download Vosk model

echo "📥 Downloading Vosk model..."

# Tạo thư mục models nếu chưa có
mkdir -p models/vosk-model

# Download Vosk model (ví dụ: English model)
cd models/vosk-model

echo "Downloading English model (small)..."
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip

echo "Extracting model..."
unzip vosk-model-small-en-us-0.15.zip

echo "Cleaning up..."
rm vosk-model-small-en-us-0.15.zip

echo "✅ Vosk model downloaded successfully!"
echo "Model location: $(pwd)/vosk-model-small-en-us-0.15"

# Quay lại thư mục gốc
cd ../..

echo ""
echo "Available models:"
echo "- English (small): https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
echo "- English (medium): https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip"
echo "- Vietnamese: https://alphacephei.com/vosk/models/vosk-model-small-vi-0.4.zip"
echo ""
echo "To download other models, modify this script or download manually to models/vosk-model/"
