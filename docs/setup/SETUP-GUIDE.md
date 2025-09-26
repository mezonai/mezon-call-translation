# Mezon Call Translation - Setup Guide

This comprehensive setup guide will help you get the Mezon Call Translation system up and running. The system provides real-time speech-to-text capabilities with multi-client support and horizontal scaling.

## 📋 Prerequisites

- **Docker** and **Docker Compose** installed
- **Git** for cloning the repository
- At least **4GB RAM** and **2 CPU cores** recommended
- **LiveKit** account and credentials (for agent functionality)

## 🚀 Quick Start

### Windows Users
```powershell
# 1. Setup environment and create necessary directories
.\scripts\setup.ps1

# 2. Download Vosk model (required for speech recognition)
.\scripts\download-vosk-model.ps1

# 3. Configure environment variables
notepad .env  # Edit with your LiveKit credentials

# 4. Run in development mode (with hot reload)
.\scripts\run-dev.ps1

# OR run in production mode
.\scripts\run-prod.ps1

# OR run with horizontal scaling (5 server instances)
.\scripts\scale-deploy.ps1 start 5
```

### Linux/macOS Users
```bash
# 1. Make scripts executable and setup environment
chmod +x scripts/*.sh
./scripts/setup.sh

# 2. Download Vosk model (required for speech recognition)
./scripts/download-vosk-model.sh

# 3. Configure environment variables
nano .env  # Edit with your LiveKit credentials

# 4. Run in development mode (with hot reload)
./scripts/run-dev.sh

# OR run in production mode
./scripts/run-prod.sh

# OR run with horizontal scaling (5 server instances)
./scripts/scale-deploy.sh start 5
```

## 📁 Project Structure

```
mezon-call-translation/
├── Architect_MultiClient_Server/
│   ├── Server/                 # FastAPI server with Vosk STT
│   │   ├── config/             # Configuration management
│   │   ├── controller/         # WebSocket & API controllers
│   │   ├── models/             # Data models
│   │   ├── service/            # Core services (STT, health, auth)
│   │   ├── utils/              # Utilities (circuit breaker, logging)
│   │   ├── main.py             # Server entry point
│   │   └── session_manager.py  # Multi-client session management
│   └── agents/                 # LiveKit agent
│       ├── src/                # Agent source code
│       ├── tests/              # Agent tests
│       └── main.py             # Agent entry point
├── docs/                       # Project documentation
│   ├── setup/                  # Setup guides
│   ├── overviews/              # Architecture overviews
│   └── operations/             # Operations guides
├── models/                     # Vosk models (auto-created)
├── logs/                       # Application logs (auto-created)
├── scripts/                    # Setup and deployment scripts
│   ├── setup.ps1/.sh          # Environment setup
│   ├── download-vosk-model.*   # Model download
│   ├── run-dev.*               # Development mode
│   ├── run-prod.*              # Production mode
│   └── scale-deploy.*          # Horizontal scaling management
├── docker-compose.yml          # Main compose file with Nginx LB
├── docker-compose.override.yml # Development overrides
├── docker-compose.prod.yml     # Production overrides
├── nginx.conf                  # Load balancer configuration
├── Dockerfile.server           # Server container
├── Dockerfile.agent            # Agent container
├── .env                        # Environment variables (auto-created)
└── env.example                 # Environment template
```

## 🔧 Services Architecture

### 1. Server (FastAPI + Vosk STT)
- **Port**: 8000 (through Nginx load balancer)
- **Internal Architecture**: Multi-worker processing with adaptive load balancing
- **Health Check**: http://localhost:8000/health/simple
- **Function**: 
  - WebSocket server for real-time audio processing
  - Speech-to-text using Vosk engine
  - Multi-client session management
  - Circuit breaker pattern for reliability

### 2. Nginx Load Balancer
- **Port**: 8000 (external access)
- **Function**: 
  - Distributes traffic across multiple server instances
  - WebSocket proxy support
  - Health check integration
  - Horizontal scaling support

### 3. Agent (LiveKit Integration)
- **Port**: 8080
- **Function**: 
  - LiveKit agent for room management
  - Real-time audio stream processing
  - VAD (Voice Activity Detection) integration
  - Connects to server through WebSocket
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
VOSK_MODEL_PATH=/app/models/vosk-model

# Agent Configuration
WS_HOST=nginx  # Connect through load balancer
WS_PORT=8000

# Logging
LOG_LEVEL=INFO
```

### Additional Configuration Options

The system supports extensive configuration through environment variables:

- **Audio Processing**: Sample rates, chunk sizes, processing thresholds
- **Worker Management**: Number of workers, queue sizes, cleanup intervals
- **Circuit Breaker**: Failure thresholds, timeout settings
- **Health Monitoring**: Check intervals, metrics collection
- **Scaling**: Load balancer settings, instance management

See the [Server Architecture Documentation](../overviews/server_architecture.md) for detailed configuration options.

## 🐳 Docker Commands

### Build Images
```bash
# Build all services
docker-compose build

# Build specific service
docker-compose build server
docker-compose build agent
```

### Run Services

#### Standard Deployment
```bash
# Development (with hot reload and debugging)
docker-compose -f docker-compose.yml -f docker-compose.override.yml up

# Production (optimized, background)
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Individual services
docker-compose up server
docker-compose up agent
docker-compose up nginx
```

#### Horizontal Scaling
```bash
# Start with multiple server instances (recommended for production)
docker-compose up -d --scale server=5

# Scale existing deployment
docker-compose up -d --scale server=10

# Using scaling scripts (easier management)
./scripts/scale-deploy.sh start 5    # Linux/macOS
.\scripts\scale-deploy.ps1 start 5    # Windows
```

### View Logs
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f server
docker-compose logs -f agent
docker-compose logs -f nginx

# Scaling script logs
./scripts/scale-deploy.sh logs server    # Linux/macOS
.\scripts\scale-deploy.ps1 logs server    # Windows
```

### Management Commands
```bash
# Stop services
docker-compose down

# Stop and remove volumes
docker-compose down -v

# Stop and remove images
docker-compose down --rmi all

# View system status
docker-compose ps
docker stats
```

## 🔍 Troubleshooting

### Common Issues

#### 1. Server Startup Issues
- **Vosk Model Missing**:
  ```bash
  # Download model manually
  ./scripts/download-vosk-model.sh    # Linux/macOS
  .\scripts\download-vosk-model.ps1    # Windows
  ```
- **Port Conflicts**: Check if port 8000 is already in use
- **Memory Issues**: Ensure at least 4GB RAM available
- **Check logs**: `docker-compose logs server`

#### 2. Agent Connection Issues
- **Server Not Running**: Ensure server is up first
- **Environment Variables**: Verify LiveKit credentials in `.env`
- **Network Issues**: Check agent can reach server through nginx
- **Check logs**: `docker-compose logs agent`

#### 3. Load Balancer Issues
- **Nginx Configuration**: Verify `nginx.conf` syntax
- **Backend Health**: Check server instances are healthy
- **WebSocket Support**: Ensure WebSocket upgrade headers
- **Check logs**: `docker-compose logs nginx`

#### 4. Horizontal Scaling Issues
- **Resource Limits**: Ensure sufficient system resources
- **Container Naming**: Remove `container_name` for scaling
- **Health Checks**: Verify all instances pass health checks
- **Use scaling scripts**: Prefer `scale-deploy.*` scripts

#### 5. Platform-Specific Issues

**Windows PowerShell**:
```powershell
# Fix execution policy
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Ensure Docker Desktop is running
# Start Docker Desktop before running scripts
```

**Linux/macOS**:
```bash
# Fix script permissions
chmod +x scripts/*.sh

# Fix Docker permissions (if needed)
sudo usermod -aG docker $USER
# Then logout and login again
```

### Debug Commands
```bash
# Container inspection
docker-compose exec server bash
docker-compose exec agent bash
docker-compose exec nginx sh

# System status
docker-compose ps
docker stats

# Health checks
curl -f http://localhost:8000/health/simple
curl -f http://localhost:8000/health

# View all logs with timestamps
docker-compose logs -f -t

# Check scaling status
./scripts/scale-deploy.sh status    # Linux/macOS
.\scripts\scale-deploy.ps1 status    # Windows
```

### Performance Monitoring

For detailed performance analysis, see the [Metrics Guide](../operations/metrics-guide.md) which covers:
- Log metrics interpretation
- System performance monitoring
- Worker load balancing analysis
- VAD efficiency tracking

## 📝 Development

### Development Mode Features
- **Hot Reload**: Code changes automatically reflected
- **Debug Logging**: Enhanced logging for development
- **Volume Mounting**: Local code mounted for live editing
- **Development Tools**: Additional debugging tools available

### Development Workflow
```bash
# Start development environment
./scripts/run-dev.sh    # Linux/macOS
.\scripts\run-dev.ps1    # Windows

# Make code changes (auto-reloaded)
# Test changes immediately
# View logs for debugging
docker-compose logs -f server
```

### Adding New Vosk Models
1. Download model to `models/vosk-model/`
2. Update `VOSK_MODEL_PATH` in `.env`
3. Restart services: `docker-compose restart server`

### Development Resources
- **Server Architecture**: [docs/overviews/server_architecture.md](../overviews/server_architecture.md)
- **Agent Architecture**: [docs/overviews/agents_architecture.md](../overviews/agents_architecture.md)
- **Metrics Guide**: [docs/operations/metrics-guide.md](../operations/metrics-guide.md)

## 🚀 Production Deployment

### Production Configuration
1. Use `docker-compose.prod.yml` for optimized settings
2. Configure appropriate resource limits
3. Set up external volumes for persistence
4. Enable horizontal scaling with load balancer
5. Configure monitoring and alerting

### Production Scaling
```bash
# Start production with scaling
./scripts/scale-deploy.sh start 10    # Linux/macOS
.\scripts\scale-deploy.ps1 start 10    # Windows

# Monitor performance
./scripts/scale-deploy.sh status
./scripts/scale-deploy.sh logs server
```

### Production Checklist
- [ ] LiveKit credentials configured
- [ ] Vosk model downloaded and tested
- [ ] Resource limits set appropriately
- [ ] Health checks configured
- [ ] Monitoring and alerting set up
- [ ] Log rotation configured
- [ ] Backup strategy for models/logs
- [ ] Load testing completed

## 📚 Documentation

### Quick Links
- **[Project Overview](../overviews/server_architecture.md)**: System architecture and design
- **[Agent Architecture](../overviews/agents_architecture.md)**: LiveKit agent implementation
- **[Operations Guide](../operations/metrics-guide.md)**: Monitoring and troubleshooting

### API Documentation
- **WebSocket API**: Real-time audio processing endpoints
- **REST API**: Health checks and management endpoints
- **Agent API**: LiveKit integration endpoints

## 📞 Support

### Before Seeking Help
1. **System Requirements**: Docker & Docker Compose installed
2. **Environment**: `.env` file properly configured
3. **Models**: Vosk model downloaded successfully
4. **Ports**: 8000 and 8080 available
5. **Logs**: Check service logs for specific errors
6. **Health**: Verify health endpoints respond

### Getting Help
- Review the troubleshooting section above
- Check the [operations guide](../operations/metrics-guide.md) for metrics analysis
- Examine Docker logs for detailed error information
- Verify system resources (CPU, memory, disk space)

### Common Commands for Support
```bash
# System status
docker-compose ps
docker stats

# Health checks
curl http://localhost:8000/health

# Detailed logs
docker-compose logs -f --timestamps

# Resource usage
df -h  # Disk space
free -h  # Memory usage
top  # CPU usage
```
