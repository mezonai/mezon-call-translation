# Hướng Dẫn Chạy Agent và Server Vosk Không Dùng Docker

Tài liệu này hướng dẫn cách chạy **LiveKit Agent** và **Vosk STT Server** trực tiếp trên máy local mà không cần Docker, phù hợp cho development và debugging.

---

## 📋 Mục Lục

1. [Yêu Cầu Hệ Thống](#yêu-cầu-hệ-thống)
2. [Cài Đặt Dependencies](#cài-đặt-dependencies)
3. [Cấu Hình Environment](#cấu-hình-environment)
4. [Chạy Vosk STT Server](#chạy-vosk-stt-server)
5. [Chạy LiveKit Agent](#chạy-livekit-agent)
6. [Kiểm Tra Hoạt Động](#kiểm-tra-hoạt-động)
7. [Troubleshooting](#troubleshooting)

---

## 🖥️ Yêu Cầu Hệ Thống

### Windows

- **OS**: Windows 10/11
- **Python**: 3.10 hoặc 3.11 (khuyến nghị 3.11)
- **RAM**: Tối thiểu 4GB (khuyến nghị 8GB)
- **Disk**: ~2GB cho models (Vosk model + Silero TTS model)

### Linux/macOS

- **OS**: Ubuntu 20.04+, macOS 12+
- **Python**: 3.10 hoặc 3.11
- **RAM**: Tối thiểu 4GB (khuyến nghị 8GB)
- **Disk**: ~2GB cho models

---

## 📦 Cài Đặt Dependencies

### Bước 1: Tạo Virtual Environment

#### Windows (PowerShell)
```powershell
# Tạo virtual environment
python -m venv venv

# Activate
.\venv\Scripts\Activate.ps1

# Nếu gặp lỗi ExecutionPolicy, chạy:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

#### Linux/macOS
```bash
# Tạo virtual environment
python3 -m venv venv

# Activate
source venv/bin/activate
```

### Bước 2: Install Python Packages

#### Cho Agent (TTS + LiveKit)
```bash
# Navigate to project root
cd e:\NCC\mezon-call-translation

# Install agent dependencies
pip install -r requirements-agent.txt
```

**requirements-agent.txt** bao gồm:
- `livekit-agents` - LiveKit Agent framework
- `livekit` - LiveKit Python SDK
- `torch` - PyTorch cho TTS model
- `numpy` - Array processing
- `python-dotenv` - Environment variables

#### Cho Server Vosk (STT)
```bash
# Install server dependencies
pip install -r requirements-server.txt
```

**requirements-server.txt** bao gồm:
- `fastapi` - Web framework
- `uvicorn` - ASGI server
- `websockets` - WebSocket support
- `vosk` - STT engine
- `numpy` - Audio processing
- `motor` - MongoDB async driver (nếu dùng MongoDB)
- `prometheus-client` - Metrics export

### Bước 3: Download Models

#### Download Vosk Model (STT)

**Windows:**
```powershell
.\scripts\download-vosk-model.ps1
```

**Linux/macOS:**
```bash
chmod +x scripts/download-vosk-model.sh
./scripts/download-vosk-model.sh
```

**Hoặc manual download:**
```bash
# Create models directory
mkdir -p models/vosk-model

# Download model (small English model ~40MB)
cd models/vosk-model
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip
```

#### Download Silero TTS Model (Auto)

TTS model sẽ tự động download khi agent khởi động lần đầu:
- **Model**: Silero V3 English
- **URL**: https://models.silero.ai/models/tts/en/v3_en.pt
- **Path**: `models/silero_v3_en.pt`
- **Size**: ~50MB

---

## ⚙️ Cấu Hình Environment

### Bước 1: Copy File Mẫu

```bash
# Copy env.example to .env
cp env.example .env
```

### Bước 2: Cấu Hình LiveKit Credentials

Mở file `.env` và điền thông tin LiveKit:

```env
# LiveKit Configuration
LIVEKIT_URL=wss://your-livekit-server.com
LIVEKIT_API_KEY=your_api_key
LIVEKIT_API_SECRET=your_api_secret

# Agent Configuration
ENABLE_TTS=true
TTS_MODEL_PATH=models/silero_v3_en.pt

# MongoDB (optional, set to false if not using)
ENABLE_MONGODB=false
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB_NAME=mezon_call_translation
MONGODB_COLLECTION_NAME=transcriptions

# Logging
LOG_LEVEL=INFO
```

### Bước 3: Cấu Hình Vosk Server

Kiểm tra file `.env` có các cấu hình cho Vosk server:

```env
# Vosk Server Configuration
SERVER_HOST=0.0.0.0
SERVER_PORT=8000

# Vosk Model Path
VOSK_MODEL_PATH=models/vosk-model/vosk-model-small-en-us-0.15

# Circuit Breaker
STT_CIRCUIT_BREAKER_FAILURE_THRESHOLD=5

# Audio Processing
VOSK_SAMPLE_RATE=16000
VOSK_MIN_TEXT_LENGTH=2
```

---

## 🚀 Chạy Vosk STT Server

### Terminal 1: Start Vosk Server

#### Windows (PowerShell)
```powershell
# Activate virtual environment
.\venv\Scripts\Activate.ps1

# Navigate to server directory
cd Architect_MultiClient_Server

# Run server
python -m uvicorn server_vosk.main:app --host 0.0.0.0 --port 8000 --reload
```

#### Linux/macOS
```bash
# Activate virtual environment
source venv/bin/activate

# Navigate to server directory
cd Architect_MultiClient_Server/

# Run server
python -m uvicorn server_vosk.main:app --host 0.0.0.0 --port 8000 --reload
```

### Kiểm Tra Server Đã Chạy

```bash
# Health check
curl http://localhost:8000/health

# Expected response:
# {"status":"healthy","vosk_model_loaded":true,...}
```

### WebSocket Endpoint

Server sẽ lắng nghe WebSocket connections tại:
- **URL**: `ws://localhost:8000/ws/vosk`
- **Protocol**: Binary audio frames (16kHz, int16)

### Server Logs

Khi server chạy thành công, bạn sẽ thấy:

```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
2024-11-07 10:30:15 | INFO | server_vosk.main | Vosk model loaded successfully
2024-11-07 10:30:15 | INFO | server_vosk.main | Server starting on http://0.0.0.0:8000
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

---

## 🤖 Chạy LiveKit Agent

### Terminal 2: Start Agent

#### Windows (PowerShell)
```powershell
# Activate virtual environment (terminal mới)
.\venv\Scripts\Activate.ps1

# Navigate to agents directory
cd Architect_MultiClient_Server\agents

# Run agent
python main.py start
```

#### Linux/macOS
```bash
# Activate virtual environment (terminal mới)
source venv/bin/activate

# Navigate to agents directory
cd Architect_MultiClient_Server/agents

# Run agent
python main.py start
```

### Chế Độ Development (Auto-reload)

Nếu muốn agent tự động reload khi code thay đổi:

```bash
# Add 'dev' flag
python main.py dev
```

### Agent Logs

Khi agent khởi động thành công:

```
2024-11-07 10:31:00 | INFO | Agent starting...
2024-11-07 10:31:01 | INFO | Loading Silero TTS model...
2024-11-07 10:31:03 | INFO | ✅ Silero TTS model loaded successfully
2024-11-07 10:31:03 | INFO | TTS Manager initialized (session=xxx, sample_rate=48000Hz)
2024-11-07 10:31:03 | INFO | ✅ Published TTS audio track: TR_xxx
2024-11-07 10:31:04 | INFO | ✅ TTS DataChannel handler registered for topic='tts_control'
2024-11-07 10:31:04 | INFO | Agent worker started, waiting for jobs...
```

---

## ✅ Kiểm Tra Hoạt Động

### 1. Kiểm Tra Vosk Server Health

```bash
# Health endpoint
curl http://localhost:8000/health

# Metrics endpoint (Prometheus format)
curl http://localhost:8000/metrics
```

### 2. Test WebSocket STT

Dùng script test có sẵn:

```bash
# Test WebSocket connection
cd stress_test_server
python check_script.py
```

### 3. Kiểm Tra Agent Connection

Mở LiveKit room trong browser và kiểm tra:
- Agent có join room không
- TTS audio track có được publish không
- DataChannel `tts_control` có sẵn không

### 4. Test TTS Functionality

Dùng debug script:

```bash
# Test TTS via DataChannel
cd Architect_MultiClient_Server/agents/examples/tts_demo
python debug_datachannel.py
```

### 5. Monitor Logs Realtime

**Vosk Server logs:**
```bash
tail -f logs/app.log
```

**Agent logs** (nếu log to file):
```bash
tail -f logs/agent.log
```

---

## 🔧 Troubleshooting

### Lỗi: "Port 8000 already in use"

**Nguyên nhân:** Vosk server đã chạy hoặc port bị chiếm

**Giải pháp:**

```bash
# Windows - Kill process on port 8000
netstat -ano | findstr :8000
taskkill /PID <PID> /F

# Linux/macOS
lsof -ti:8000 | xargs kill -9

# Hoặc đổi port trong .env
SERVER_PORT=8001
```

### Lỗi: "Vosk model not found"

**Nguyên nhân:** Model chưa được download hoặc path sai

**Giải pháp:**

```bash
# Kiểm tra model path
ls -la models/vosk-model/vosk-model-small-en-us-0.15/

# Download lại nếu thiếu
./scripts/download-vosk-model.sh

# Kiểm tra env variable
echo $VOSK_MODEL_PATH
```

### Lỗi: "TTS model download failed"

**Nguyên nhân:** Network issue hoặc disk full

**Giải pháp:**

```bash
# Manual download
mkdir -p models
cd models
wget https://models.silero.ai/models/tts/en/v3_en.pt

# Verify file
ls -lh silero_v3_en.pt
# Should be ~50MB
```

### Lỗi: "ModuleNotFoundError: No module named 'livekit'"

**Nguyên nhân:** Dependencies chưa được install

**Giải pháp:**

```bash
# Activate venv
source venv/bin/activate  # Linux/macOS
.\venv\Scripts\Activate.ps1  # Windows

# Install dependencies
pip install -r requirements-agent.txt
pip install -r requirements-server.txt
```

### Lỗi: "Circuit breaker threshold reached"

**Nguyên nhân:** Quá nhiều lỗi STT liên tiếp (5 lỗi)

**Giải pháp:**

```bash
# Kiểm tra Vosk server có chạy không
curl http://localhost:8000/health

# Restart Vosk server
# Ctrl+C to stop, then restart

# Tăng threshold trong .env
STT_CIRCUIT_BREAKER_FAILURE_THRESHOLD=10
```

### Lỗi: "WebSocket connection failed"

**Nguyên nhân:** Vosk server không chạy hoặc firewall block

**Giải pháp:**

```bash
# Kiểm tra server running
netstat -an | findstr :8000  # Windows
netstat -an | grep :8000     # Linux/macOS

# Test WebSocket connection
wscat -c ws://localhost:8000/ws/vosk

# Tắt firewall tạm thời để test (Windows)
# Control Panel > Windows Defender Firewall > Turn off
```

### Lỗi: "MongoDB connection failed" (nếu dùng MongoDB)

**Nguyên nhân:** MongoDB không chạy

**Giải pháp:**

```bash
# Tắt MongoDB nếu không dùng
# File .env
ENABLE_MONGODB=false

# Hoặc start MongoDB
# Windows
net start MongoDB

# Linux
sudo systemctl start mongod

# macOS
brew services start mongodb-community
```

### Agent Không Join Room

**Kiểm tra:**

1. **LiveKit credentials đúng không:**
```bash
echo $LIVEKIT_URL
echo $LIVEKIT_API_KEY
```

2. **Network connectivity:**
```bash
ping meet.nccsoft.vn  # Hoặc LiveKit server của bạn
```

3. **Agent logs:**
```bash
# Tìm error messages
grep -i "error\|failed" logs/agent.log
```

### Audio Bị Chậm/Dè (TTS)

**Đã fix!** Đảm bảo code mới nhất:
- Chunk size = 20ms (960 samples @ 48kHz)
- Sample rate = 48000 Hz
- Fixed frame size

```bash
# Pull latest code
git pull origin phase_1

# Restart agent
python main.py start
```

---

## 📊 Monitoring & Debugging

### Prometheus Metrics

Vosk server expose metrics tại `http://localhost:8000/metrics`:

```bash
# View all metrics
curl http://localhost:8000/metrics

# Specific metrics
curl http://localhost:8000/metrics | grep circuit_breaker
curl http://localhost:8000/metrics | grep websocket
```

### Debug Endpoints

```bash
# Server health
curl http://localhost:8000/health

# Server info
curl http://localhost:8000/info

# Circuit breaker status
curl http://localhost:8000/circuit-breaker/status
```

### Log Levels

Thay đổi log level trong `.env`:

```env
# DEBUG - Very verbose
LOG_LEVEL=DEBUG

# INFO - Normal operation (recommended)
LOG_LEVEL=INFO

# WARNING - Only warnings and errors
LOG_LEVEL=WARNING

# ERROR - Only errors
LOG_LEVEL=ERROR
```

---

## 🎯 Quick Start Commands

### Development Mode (Recommended)

**Terminal 1 - Vosk Server:**
```bash
source venv/bin/activate
cd Architect_MultiClient_Server/server_vosk
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 2 - Agent:**
```bash
source venv/bin/activate
cd Architect_MultiClient_Server/agents
python main.py dev
```

### Production Mode

**Terminal 1 - Vosk Server:**
```bash
source venv/bin/activate
cd Architect_MultiClient_Server/server_vosk
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

**Terminal 2 - Agent:**
```bash
source venv/bin/activate
cd Architect_MultiClient_Server/agents
python main.py start
```

---

## 🔄 So Sánh: Docker vs Non-Docker

| Tiêu Chí | Docker | Non-Docker |
|----------|--------|------------|
| **Setup Time** | Nhanh (docker-compose up) | Chậm hơn (install dependencies) |
| **Isolation** | Hoàn toàn isolated | Dùng chung system |
| **Resource Usage** | Cao hơn (~500MB overhead) | Thấp hơn |
| **Debugging** | Khó hơn (vào container) | Dễ dàng (direct access) |
| **Hot Reload** | Phức tạp | Đơn giản (--reload flag) |
| **Portability** | Cao (chạy mọi nơi) | Thấp (phụ thuộc OS) |
| **Best For** | Production, CI/CD | Development, Debugging |

---

## 📚 Tài Liệu Liên Quan

- [SETUP-GUIDE.md](./SETUP-GUIDE.md) - Hướng dẫn setup tổng quát
- [agents_architecture.md](../overviews/agents_architecture.md) - Kiến trúc Agent
- [server_architecture.md](../overviews/server_architecture.md) - Kiến trúc Server
- [metrics-guide.md](../operations/metrics-guide.md) - Hướng dẫn metrics

---

## 💡 Tips & Best Practices

### 1. Dùng Virtual Environment

**Luôn luôn** dùng virtual environment để tránh conflict:

```bash
# Kiểm tra đang ở venv
which python  # Linux/macOS
where python  # Windows

# Output nên chứa "venv"
```

### 2. Keep Dependencies Updated

```bash
# Update pip
pip install --upgrade pip

# Update packages
pip install --upgrade -r requirements-agent.txt
pip install --upgrade -r requirements-server.txt
```

### 3. Monitor Resource Usage

```bash
# Windows - Task Manager
Ctrl + Shift + Esc

# Linux
htop
# hoặc
ps aux | grep python

# macOS
Activity Monitor
```

### 4. Use Screen/Tmux (Linux/macOS)

Để giữ processes chạy khi đóng terminal:

```bash
# Install tmux
sudo apt install tmux  # Ubuntu
brew install tmux      # macOS

# Start new session
tmux new -s vosk-server

# Detach: Ctrl+B, then D
# Reattach: tmux attach -t vosk-server
```

### 5. Automated Restart (Production)

Dùng systemd (Linux) hoặc Windows Service để tự động restart:

```bash
# Example systemd service file
sudo nano /etc/systemd/system/vosk-server.service

[Unit]
Description=Vosk STT Server
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/project
ExecStart=/path/to/venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

---

## 🆘 Support & Contact

Nếu gặp vấn đề không giải quyết được:

1. Kiểm tra [Troubleshooting](#troubleshooting) section
2. Xem logs chi tiết (`logs/app.log`, console output)
3. Kiểm tra GitHub Issues
4. Contact team lead

---

**Happy Coding! 🚀**
