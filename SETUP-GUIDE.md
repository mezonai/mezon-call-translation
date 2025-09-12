# Mezon Call Translation - Docker Setup Guide

## 🚀 Quick Start

### Windows Users
```powershell
# 1. Setup environment
.\scripts\setup.ps1

# 2. Download Vosk model
.\scripts\download-vosk-model.ps1

# 3. Edit .env file with your LiveKit credentials
notepad .env

# 4. Run in development mode
.\scripts\run-dev.ps1

# OR run in production mode
.\scripts\run-prod.ps1
```

### Linux/macOS Users
```bash
# 1. Setup environment
chmod +x scripts/*.sh
./scripts/setup.sh

# 2. Download Vosk model
./scripts/download-vosk-model.sh

# 3. Edit .env file with your LiveKit credentials
nano .env

# 4. Run in development mode
./scripts/run-dev.sh

# OR run in production mode
./scripts/run-prod.sh
```

## 📁 Project Structure

```
mezon-call-translation/
├── Architect_MultiClient_Server/
│   ├── Server/                 # FastAPI server với Vosk
│   └── agents/                 # LiveKit agent
├── models/                     # Vosk models (auto-created)
├── logs/                       # Application logs (auto-created)
├── scripts/                    # Setup và run scripts
│   ├── setup.ps1              # Windows setup
│   ├── setup.sh               # Linux/macOS setup
│   ├── download-vosk-model.ps1 # Windows model download
│   ├── download-vosk-model.sh  # Linux/macOS model download
│   ├── run-dev.ps1            # Windows development
│   ├── run-dev.sh             # Linux/macOS development
│   ├── run-prod.ps1           # Windows production
│   └── run-prod.sh            # Linux/macOS production
├── docker-compose.yml         # Main compose file
├── docker-compose.override.yml # Development overrides
├── docker-compose.prod.yml    # Production overrides
├── Dockerfile.server          # Server Dockerfile
├── Dockerfile.agent           # Agent Dockerfile
├── .env                       # Environment variables (auto-created)
└── env.example                # Environment template
```

## 🔧 Services

### 1. Server (FastAPI + Vosk)
- **Port**: 8000
- **Health Check**: http://localhost:8000/health/simple
- **Function**: WebSocket server xử lý audio và trả về transcription

### 2. Agent (LiveKit)
- **Port**: 8080
- **Function**: LiveKit agent kết nối với server để xử lý real-time audio
- **Command**: `python agents/main.py dev`

## ⚙️ Configuration

### Environment Variables (.env)
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

# Logging
LOG_LEVEL=INFO
```

## 🐳 Docker Commands

### Build Images
```bash
# Build all
docker-compose build

# Build specific service
docker-compose build server
docker-compose build agent
```

### Run Services
```bash
# Development (with hot reload)
docker-compose -f docker-compose.yml -f docker-compose.override.yml up

# Production
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Individual services
docker-compose up server
docker-compose up agent
```

### View Logs
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f server
docker-compose logs -f agent
```

### Stop Services
```bash
# Stop services
docker-compose down

# Stop and remove volumes
docker-compose down -v

# Stop and remove images
docker-compose down --rmi all
```

## 🔍 Troubleshooting

### Common Issues

1. **Server không start được**
   - Kiểm tra Vosk model có tồn tại không
   - Xem logs: `docker-compose logs server`

2. **Agent không kết nối được server**
   - Kiểm tra server đã chạy chưa
   - Kiểm tra environment variables
   - Xem logs: `docker-compose logs agent`

3. **Windows PowerShell Issues**
   - Nếu gặp lỗi execution policy: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`
   - Nếu Docker Desktop chưa chạy: Start Docker Desktop trước khi chạy scripts

4. **Linux/macOS Permission Issues**
   - Đảm bảo scripts có quyền execute: `chmod +x scripts/*.sh`
   - Nếu gặp lỗi permission với Docker: `sudo usermod -aG docker $USER` và logout/login lại

### Debug Commands
```bash
# Enter server container
docker-compose exec server bash

# Enter agent container
docker-compose exec agent bash

# Check container status
docker-compose ps

# Check resource usage
docker stats
```

## 📝 Development

### Hot Reload
Scripts tự động enable hot reload cho development:
- Code changes sẽ được reflect ngay lập tức
- Không cần rebuild container

### Adding New Models
1. Download model vào `models/vosk-model/`
2. Update `VOSK_MODEL_PATH` trong `.env`
3. Restart services

## 🚀 Production Deployment

1. Sử dụng `docker-compose.prod.yml` cho production
2. Cấu hình resource limits phù hợp
3. Sử dụng external volumes cho models và logs
4. Cấu hình reverse proxy (nginx) nếu cần
5. Setup monitoring và logging

## 📞 Support

Nếu gặp vấn đề, hãy kiểm tra:
1. Docker và Docker Compose đã cài đặt chưa
2. Environment variables đã cấu hình đúng chưa
3. Vosk model đã download chưa
4. Ports 8000 và 8080 có bị conflict không
5. Logs của services để xem lỗi cụ thể
