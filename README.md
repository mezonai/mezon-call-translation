# Mezon Call Translation

> Real-time Speech-to-Text (STT) system with multi-client support, horizontal scaling, and LiveKit integration

[![Docker](https://img.shields.io/badge/Docker-Ready-blue)](https://docker.com)
[![FastAPI](https://img.shields.io/badge/FastAPI-Framework-green)](https://fastapi.tiangolo.com)
[![Vosk](https://img.shields.io/badge/Vosk-STT%20Engine-orange)](https://alphacephei.com/vosk/)
[![LiveKit](https://img.shields.io/badge/LiveKit-Integration-purple)](https://livekit.io)

## 🚀 Quick Start

### Option 1: Basic Setup
```bash
# 1. Setup environment
./scripts/setup.sh                    # Linux/macOS
.\scripts\setup.ps1                    # Windows

# 2. Download Vosk model
./scripts/download-vosk-model.sh       # Linux/macOS
.\scripts\download-vosk-model.ps1       # Windows

# 3. Configure environment
cp env.example .env                    # Edit with your LiveKit credentials

# 4. Run the system
./scripts/run-dev.sh                   # Development mode
./scripts/run-prod.sh                  # Production mode
```

### Option 2: Horizontal Scaling (Recommended for Production)
```bash
# Start with 5 server instances behind load balancer
./scripts/scale-deploy.sh start 5      # Linux/macOS
.\scripts\scale-deploy.ps1 start 5      # Windows

# Scale to 10 instances
./scripts/scale-deploy.sh scale 10

# Check status
./scripts/scale-deploy.sh status
```

**System will be available at**: http://localhost:8000

## 📋 Table of Contents

- [System Overview](#-system-overview)
- [Architecture](#-architecture)
- [Features](#-features)
- [Quick Start](#-quick-start)
- [Documentation](#-documentation)
- [Deployment Options](#-deployment-options)
- [API Reference](#-api-reference)
- [Monitoring](#-monitoring)
- [Contributing](#-contributing)
- [Support](#-support)

## 🎯 System Overview

**Mezon Call Translation** is a production-ready, scalable Speech-to-Text system designed for real-time communication platforms. It provides:

- **Real-time STT**: Convert speech to text with low latency using Vosk engine
- **Multi-client Support**: Handle multiple simultaneous audio streams
- **Horizontal Scaling**: Scale across multiple server instances with load balancing
- **LiveKit Integration**: Seamless integration with LiveKit rooms and agents
- **High Availability**: Circuit breaker pattern, health monitoring, and graceful degradation

### Key Technologies

- **[Vosk](https://alphacephei.com/vosk/)**: Offline speech recognition engine
- **[FastAPI](https://fastapi.tiangolo.com)**: Modern web framework with WebSocket support
- **[LiveKit](https://livekit.io)**: Real-time communication platform
- **[Docker](https://docker.com)**: Containerization and orchestration
- **[Nginx](https://nginx.org)**: Load balancer and proxy

## 🏗️ Architecture

### High-Level Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Multiple      │    │  Nginx Load     │    │  Server Pool    │
│   Clients       │◄──►│  Balancer       │◄──►│  (Scalable)     │
│                 │    │  (Port 8000)    │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                                │                       │
                                ▼                       ▼
                       ┌─────────────────┐    ┌─────────────────┐
                       │  LiveKit Agent  │    │  Vosk STT       │
                       │  (Port 8080)    │    │  Workers        │
                       └─────────────────┘    └─────────────────┘
```

### Core Components

1. **[Server](docs/overviews/server_architecture.md)** - FastAPI with multi-worker STT processing
2. **[Agent](docs/overviews/agents_architecture.md)** - LiveKit integration with VAD processing
3. **Load Balancer** - Nginx for traffic distribution and WebSocket proxy
4. **Session Management** - Multi-client session coordination
5. **Health Monitoring** - Comprehensive health checks and metrics

## ✨ Features

### Core Capabilities
- ✅ **Real-time Speech-to-Text** with Vosk engine
- ✅ **Multi-client Session Management** with language support
- ✅ **WebSocket-based Communication** for low latency
- ✅ **Adaptive Processing** based on system load
- ✅ **Circuit Breaker Pattern** for fault tolerance
- ✅ **Voice Activity Detection** (VAD) for efficiency

### Scalability & Operations
- 🚀 **Horizontal Scaling** with automatic load balancing
- 📊 **Comprehensive Monitoring** and health checks
- 🔄 **Auto-recovery** and graceful degradation
- 📈 **Performance Metrics** and analysis tools
- 🐳 **Docker Containerization** for easy deployment
- 🔧 **Configuration Management** via environment variables

### Integration Features
- 🎤 **LiveKit Integration** for room management
- 🌐 **REST API** for system management
- 📡 **WebSocket API** for real-time communication
- 🔐 **JWT Authentication** support
- 📝 **Multi-language Support** for transcripts

## 📚 Documentation

### Setup & Installation
- **[Setup Guide](docs/setup/SETUP-GUIDE.md)** - Complete installation and configuration guide
- **Environment Configuration** - LiveKit credentials and system settings
- **Model Management** - Vosk model download and configuration

### Architecture Documentation
- **[Server Architecture](docs/overviews/server_architecture.md)** - Detailed server design and components
- **[Agent Architecture](docs/overviews/agents_architecture.md)** - LiveKit agent implementation
- **System Design Patterns** - Circuit breaker, session management, worker pools

### Operations & Monitoring
- **[Metrics Guide](docs/operations/metrics-guide.md)** - Performance monitoring and log analysis
- **Health Check Endpoints** - System status and monitoring
- **Troubleshooting** - Common issues and solutions

### Development
- **API Documentation** - REST and WebSocket endpoints
- **Configuration Reference** - All environment variables and settings
- **Development Setup** - Hot reload and debugging

## 🚀 Deployment Options

### 1. Development Mode
```bash
# Hot reload enabled, debug logging
./scripts/run-dev.sh
```
- ✅ Hot reload for code changes
- ✅ Enhanced debugging
- ✅ Local volume mounting

### 2. Production Mode
```bash
# Optimized for production
./scripts/run-prod.sh
```
- ✅ Performance optimizations
- ✅ Resource limits
- ✅ Production logging

### 3. Horizontal Scaling (Recommended)
```bash
# Multiple server instances with load balancing
./scripts/scale-deploy.sh start 5
```
- ✅ Multiple server instances
- ✅ Nginx load balancer
- ✅ Auto-scaling capabilities
- ✅ High availability

### 4. Manual Docker Compose
```bash
# Custom scaling
docker-compose up -d --scale server=3
```

## 🔌 API Reference

### WebSocket API
```
ws://localhost:8000/ws/vosk/
```
**Parameters**:
- `client_id`: Unique client identifier
- `session_id`: Session identifier
- `transcript`: Enable transcript delivery
- `translation`: Enable translation delivery
- `language`: Client language (en, vi, etc.)

**Input**: Binary audio data  
**Output**: JSON transcript/translation results

### REST API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Detailed health status |
| `/health/simple` | GET | Simple health check |
| `/agent/join` | POST | LiveKit agent dispatch |
| `/ws/stats` | GET | WebSocket statistics |

### Health Check Example
```bash
# Simple health check
curl http://localhost:8000/health/simple

# Detailed health information
curl http://localhost:8000/health
```

## 📊 Monitoring

### Built-in Monitoring
- **Health Endpoints**: Real-time system status
- **Metrics Collection**: Performance and usage statistics
- **Log Analysis**: Comprehensive logging with structured format
- **Worker Statistics**: STT worker performance tracking

### Key Metrics
- **Audio Processing Latency**: Real-time performance tracking
- **Worker Load Distribution**: Load balancing effectiveness
- **Session Management**: Client connection statistics
- **Error Rates**: System reliability monitoring

### Monitoring Tools
```bash
# Check system status
./scripts/scale-deploy.sh status

# View real-time logs
docker-compose logs -f server

# Monitor resource usage
docker stats
```

For detailed metrics analysis, see the [Metrics Guide](docs/operations/metrics-guide.md).

## 🔧 Configuration

### Environment Variables
```bash
# Core Configuration
LIVEKIT_URL=wss://your-livekit-server.com
LIVEKIT_API_KEY=your-api-key
LIVEKIT_API_SECRET=your-api-secret
VOSK_MODEL_PATH=/app/models/vosk-model

# Performance Tuning
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
LOG_LEVEL=INFO
```

### Advanced Configuration
The system supports extensive configuration for:
- Audio processing parameters
- Worker pool management
- Circuit breaker settings
- Health check intervals
- Scaling parameters

See [Setup Guide](docs/setup/SETUP-GUIDE.md) for complete configuration options.

## 🤝 Contributing

### Development Setup
1. Fork the repository
2. Follow the [Setup Guide](docs/setup/SETUP-GUIDE.md)
3. Use development mode: `./scripts/run-dev.sh`
4. Make changes with hot reload enabled
5. Test thoroughly with multiple clients
6. Submit pull request with documentation updates

### Architecture Guidelines
- Follow the existing service pattern
- Maintain thread-safe operations
- Add appropriate error handling
- Include health check integration
- Update documentation

## 🐛 Troubleshooting

### Common Issues

**Server won't start**:
```bash
# Check Vosk model exists
ls -la models/vosk-model/

# Download if missing
./scripts/download-vosk-model.sh
```

**Agent connection failed**:
```bash
# Verify server is running
curl http://localhost:8000/health/simple

# Check environment variables
cat .env
```

**Poor performance**:
```bash
# Check resource usage
docker stats

# Scale up servers
./scripts/scale-deploy.sh scale 10
```

For comprehensive troubleshooting, see the [Setup Guide](docs/setup/SETUP-GUIDE.md).

## 📞 Support

### Getting Help
1. **Check Documentation**: Review relevant guides in `/docs`
2. **System Requirements**: Ensure Docker & Docker Compose are installed
3. **Health Checks**: Verify system status via health endpoints
4. **Log Analysis**: Examine service logs for specific errors
5. **Resource Check**: Ensure adequate CPU, memory, and disk space

### Debug Information
```bash
# System status
docker-compose ps
docker stats

# Service health
curl http://localhost:8000/health

# Recent logs
docker-compose logs --tail=100 server
```

### Resources
- **[Setup Guide](docs/setup/SETUP-GUIDE.md)** - Installation and configuration
- **[Server Architecture](docs/overviews/server_architecture.md)** - System design
- **[Operations Guide](docs/operations/metrics-guide.md)** - Monitoring and troubleshooting

---

## 📄 License

This project is part of the Mezon platform ecosystem. See the project documentation for licensing information.

## 🏷️ Tags

`speech-to-text` `real-time` `vosk` `fastapi` `livekit` `docker` `microservices` `websocket` `audio-processing` `scalable`/degraded/unhealthy)
│   ├── Uptime information
│   ├── Component details
│   └── HTTP status codes (200/503)
└── /health/simple: Simple boolean check
```

## Đặc Điểm Kỹ Thuật Nổi Bật

### 1. **Scalability**
- Multi-worker architecture cho STT processing
- Async/await pattern cho I/O operations
- Queue-based load balancing
- Adaptive processing based on system load

### 2. **Reliability**
- Circuit breaker pattern cho error handling
- Graceful degradation (VAD fallback)
- Resource cleanup và memory management
- Health monitoring và metrics

### 3. **Performance Optimization**
- VAD pre-filtering để giảm STT workload
- Chunk accumulation strategy
- Overlapping audio processing
- GPU acceleration support (VAD)

### 4. **Real-time Capabilities**
- WebSocket-based communication
- Non-blocking audio submission
- Async result dispatching
- Low-latency processing pipeline

### 5. **Horizontal Scaling with Load Balancer**
- Nginx load balancer for multiple server instances
- Docker Compose scaling capabilities
- Health check integration with load balancer
- Session-independent client routing
- Zero-downtime scaling operations

### 6. **Multi-tenant Support**
- Session-based client isolation
- Per-client language settings
- Flexible subscription model (transcript/translation)
- Resource sharing với isolation

## Luồng Hoạt Động Tổng Thể

1. **Client kết nối** → WebSocket với parameters
2. **Audio streaming** → Continuous audio chunks
3. **VAD filtering** → Loại bỏ silence
4. **STT processing** → Multi-worker Vosk recognition
5. **Result dispatching** → Async delivery to subscribed clients
6. **Session management** → Multi-client coordination
7. **Resource cleanup** → Automatic maintenance

Hệ thống được thiết kế để xử lý real-time speech-to-text cho nhiều client đồng thời với độ trễ thấp và độ tin cậy cao.

## Horizontal Scaling với Load Balancer

### Kiến Trúc Scaling
Hệ thống hỗ trợ horizontal scaling với Nginx load balancer:

```
Client → Nginx Load Balancer → Multiple Server Instances
                           ↓
                      STT Processing Workers
                           ↓
                      Shared Result Queue
                           ↓
                      Agent Services
```

### Cách Thức Hoạt Động
1. **Nginx Load Balancer**: 
   - Phân phối traffic đến multiple server instances
   - Health check tự động cho các backend servers
   - WebSocket proxy với timeout configuration
   - Round-robin load balancing (có thể cấu hình khác)

2. **Server Scaling**:
   - Multiple FastAPI server instances chạy song song
   - Mỗi instance có bộ STT workers riêng biệt
   - Session management độc lập trên từng instance
   - Agent kết nối qua Nginx thay vì direct connection

### Triển Khai Scaling

#### 1. Quick Start với Script
**Linux/macOS:**
```bash
# Start với 5 server instances
./scripts/scale-deploy.sh start 5

# Scale to 10 instances
./scripts/scale-deploy.sh scale 10

# Kiểm tra status
./scripts/scale-deploy.sh status
```

**Windows:**
```powershell
# Start với 5 server instances
.\scripts\scale-deploy.ps1 start 5

# Scale to 10 instances
.\scripts\scale-deploy.ps1 scale 10

# Kiểm tra status
.\scripts\scale-deploy.ps1 status
```

#### 2. Manual Docker Compose
```bash
# Build images
docker-compose build

# Start với 3 server instances
docker-compose up -d --scale server=3

# Scale to 5 instances
docker-compose up -d --scale server=5

# Check status
docker-compose ps
```

### Health Check và Monitoring
- **Load Balancer Health**: `http://localhost:8000/health/simple`
- **Nginx Status**: Automatic health checks to backend servers
- **Container Status**: `docker-compose ps`
- **Logs**: `docker-compose logs -f [service_name]`

### Cấu Hình Nginx
File `nginx.conf` được tối ưu cho:
- WebSocket proxy support
- Health check integration
- Timeout configuration cho long-running connections
- Load balancing strategy (có thể điều chỉnh)

### Performance Benefits
- **Increased Throughput**: Multiple servers xử lý concurrent requests
- **High Availability**: Server failure không ảnh hưởng toàn hệ thống
- **Zero Downtime Scaling**: Thêm/bớt instances mà không interrupt service
- **Resource Optimization**: Phân tải đều across multiple instances