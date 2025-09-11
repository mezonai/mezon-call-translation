# Mezon Call Translation - Docker Setup

Hướng dẫn chạy project Mezon Call Translation sử dụng Docker cho cả Linux/macOS và Windows.

## Cấu trúc Services

### 1. Server (FastAPI + Vosk)
- **Port**: 8000
- **Chức năng**: WebSocket server xử lý audio và trả về transcription
- **Model**: Vosk STT engine

### 2. Agent (LiveKit)
- **Port**: 8080  
- **Chức năng**: LiveKit agent kết nối với server để xử lý real-time audio
- **Command**: `python agents/main.py dev`

## Prerequisites

### Linux/macOS
- Docker và Docker Compose
- Bash shell

### Windows
- Docker Desktop
- PowerShell 5.0+ hoặc PowerShell Core


## Dependencies

### Core Dependencies
- **FastAPI**: Web framework cho server
- **Vosk**: Speech-to-Text engine
- **LiveKit**: Real-time communication platform
- **WebSockets**: Real-time communication
- **NumPy**: Audio data processing

### Optional Dependencies
- **VAD (Voice Activity Detection)**: Chỉ cần khi `VAD_ENABLED=true`
  - PyTorch và TorchAudio cho Silero VAD model

### Requirements Files
- `requirements.txt`: Tất cả dependencies
- `requirements-server.txt`: Chỉ cho FastAPI server
- `requirements-agent.txt`: Chỉ cho LiveKit agent
- `requirements-vad.txt`: Chỉ cho VAD (optional)

## Quick Start

### Linux/macOS

#### 1. Setup Environment
```bash
# Chạy setup script
chmod +x scripts/*.sh
./scripts/setup.sh

# Download Vosk model
./scripts/download-vosk-model.sh
```

#### 2. Chạy Services

**Development (với hot reload):**
```bash
./scripts/run-dev.sh
```

**Production:**
```bash
./scripts/run-prod.sh
```

### Windows

#### 1. Setup Environment
```powershell
# Chạy setup script
.\scripts\setup.ps1

# Download Vosk model
.\scripts\download-vosk-model.ps1
```

#### 2. Chạy Services

**Development (với hot reload):**
```powershell
.\scripts\run-dev.ps1
```

**Production:**
```powershell
.\scripts\run-prod.ps1
```

## Manual Commands

### 1. Cấu hình Environment
Chỉnh sửa file `.env`:
```bash
# LiveKit Configuration
LIVEKIT_URL=wss://your-livekit-server.com
LIVEKIT_API_KEY=your-api-key
LIVEKIT_API_SECRET=your-api-secret

# Server Configuration
SERVER_HOST=0.0.0.0
SERVER_PORT=8000

# Vosk Model Configuration
VOSK_MODEL_PATH=/app/models/vosk-model/vosk-model-small-en-us-0.15
```

### 2. Chạy Services

#### Development (với hot reload)
```bash
# Linux/macOS
docker-compose -f docker-compose.yml -f docker-compose.override.yml up

# Windows
docker-compose -f docker-compose.yml -f docker-compose.override.yml up
```

#### Production
```bash
# Linux/macOS & Windows
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

#### Chỉ chạy server
```bash
docker-compose up server
```

#### Chỉ chạy agent
```bash
docker-compose up agent
```

## Commands

### Build images
```bash
# Build tất cả
docker-compose build

# Build chỉ server
docker-compose build server

# Build chỉ agent
docker-compose build agent
```

### Logs
```bash
# Xem logs tất cả services
docker-compose logs -f

# Xem logs server
docker-compose logs -f server

# Xem logs agent
docker-compose logs -f agent
```

### Stop và cleanup
```bash
# Stop services
docker-compose down

# Stop và xóa volumes
docker-compose down -v

# Stop và xóa images
docker-compose down --rmi all
```

## Health Checks

- **Server**: `http://localhost:8000/health/simple`
- **Agent**: Kiểm tra logs để xem trạng thái kết nối

## Troubleshooting

### 1. Server không start được
- Kiểm tra Vosk model có tồn tại không
- Xem logs: `docker-compose logs server`

### 2. Agent không kết nối được server
- Kiểm tra server đã chạy chưa
- Kiểm tra environment variables
- Xem logs: `docker-compose logs agent`

### 3. Model không load được
- Kiểm tra đường dẫn model trong `.env`
- Đảm bảo model đã được download và extract đúng

### 4. Windows PowerShell Issues
- Nếu gặp lỗi execution policy: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`
- Nếu Docker Desktop chưa chạy: Start Docker Desktop trước khi chạy scripts

### 5. Linux/macOS Permission Issues
- Đảm bảo scripts có quyền execute: `chmod +x scripts/*.sh`
- Nếu gặp lỗi permission với Docker: `sudo usermod -aG docker $USER` và logout/login lại

## Development

### Hot Reload
Sử dụng `docker-compose.override.yml` để enable hot reload:

**Linux/macOS:**
```bash
docker-compose -f docker-compose.yml -f docker-compose.override.yml up
```

**Windows:**
```powershell
docker-compose -f docker-compose.yml -f docker-compose.override.yml up
```

### Debug
```bash
# Vào container server
docker-compose exec server bash

# Vào container agent  
docker-compose exec agent bash
```

## Platform-Specific Notes

### Windows
- Sử dụng PowerShell scripts (`.ps1`)
- Đảm bảo Docker Desktop đang chạy
- Có thể cần thay đổi execution policy

### Linux/macOS
- Sử dụng Bash scripts (`.sh`)
- Đảm bảo scripts có quyền execute
- Có thể cần sudo cho Docker commands

## Production Deployment

1. Sử dụng `docker-compose.prod.yml` cho production
2. Cấu hình resource limits phù hợp
3. Sử dụng external volumes cho models và logs
4. Cấu hình reverse proxy (nginx) nếu cần

## Notes

- Server sẽ tự động restart nếu crash
- Agent phụ thuộc vào server (health check)
- Models được mount từ host để dễ thay đổi
- Logs được lưu trong thư mục `logs/`
