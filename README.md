# Báo Cáo Workflow Server - Mezon Call Translation

## Tổng Quan Hệ Thống
Đây là một hệ thống Speech-to-Text (STT) real-time sử dụng kiến trúc multi-client server với các công nghệ chính:
- **Vosk**: Engine STT chính
- **Silero VAD**: Voice Activity Detection
- **FastAPI**: Web framework với WebSocket support
- **LiveKit**: Platform cho real-time communication

## Workflow Chi Tiết

### 1. Khởi Tạo Server (`main.py`)
```
Startup Sequence:
├── Setup logging configuration
├── Initialize FastAPI app với lifespan manager
├── Khởi tạo async result queue (maxsize=1000)
├── Khởi tạo STT service với async queue
├── Start result dispatcher task
└── Include WebSocket router
```

**Lifespan Management:**
- **Startup**: Tạo async queue, khởi tạo STT service, start dispatcher
- **Shutdown**: Dọn dẹp resources, cancel tasks, shutdown services

### 2. Quản Lý Phiên (`session_manager.py`)
```
Session Structure:
session_id -> {
    clients: {
        client_id: {
            websocket: WebSocket connection
            transcripts: bool (nhận transcript?)
            translation: bool (nhận translation?)
            language: str (ngôn ngữ client)
        }
    }
}
```

**Chức năng chính:**
- Thêm/xóa client khỏi session
- Lấy danh sách client cần notify transcript/translation
- Quản lý thông tin ngôn ngữ của từng client

### 3. WebSocket Controller (`ws_vosk_control.py`)
```
Connection Flow:
├── Accept WebSocket connection
├── Parse query parameters (client_id, session_id, transcript, translation, language)
├── Add client to session manager
├── Start audio receiving loop
│   ├── Receive audio bytes từ client
│   ├── Submit audio to STT service (non-blocking)
│   ├── Check timeout conditions (max_duration, idle_timeout)
│   └── Continue loop
└── Cleanup on disconnect
```

**Tham số kết nối:**
- `client_id`: ID duy nhất của client
- `session_id`: ID phiên họp
- `transcript`: Client có muốn nhận transcript không
- `translation`: Client có muốn nhận translation không
- `language`: Ngôn ngữ của client
- `max_duration`: Thời gian tối đa kết nối (seconds)
- `idle_timeout`: Timeout khi không có audio (seconds)

### 4. STT Service - Vosk (`vosk_service.py`)

#### 4.1 Kiến Trúc Multi-Worker
```
STT Service Architecture:
├── Main Service Instance
├── Worker Threads (số lượng = CPU cores - 1)
│   ├── Worker 0: Queue[0] + Recognizer[0] + State[0]
│   ├── Worker 1: Queue[1] + Recognizer[1] + State[1]
│   └── Worker N: Queue[N] + Recognizer[N] + State[N]
├── Metrics Thread (monitoring)
├── Cleanup Thread (resource management)
└── Result Dispatcher (async queue → WebSocket)
```

#### 4.2 Audio Processing Pipeline
```
Audio Processing Flow:
1. Receive audio chunk từ WebSocket
2. VAD Pre-filtering:
   ├── Convert audio to numpy array
   ├── Split into overlapping chunks (512 samples, 50% overlap)
   ├── Process qua Silero VAD
   ├── Check speech probability > threshold
   └── Drop nếu là silence
3. Route to Worker (hash(client_id) % num_workers)
4. Accumulate chunks theo adaptive strategy:
   ├── High load (>70%): Process ngay (min chunks)
   ├── Medium load (50-70%): Process nhanh
   ├── Low load (<30%): Process chất lượng (max chunks)
   └── Time-based processing (min/max thresholds)
5. STT Processing:
   ├── Get/Create Vosk recognizer cho client
   ├── Process accumulated chunks
   ├── Generate partial/final results
   └── Emit results to async queue
```

#### 4.3 Adaptive Processing Strategy
- **Queue Load Monitoring**: Điều chỉnh threshold dựa trên tải queue
- **Chunk Accumulation**: Gom nhiều chunk nhỏ thành chunk lớn để tối ưu
- **Circuit Breaker**: Bảo vệ khỏi lỗi cascade
- **Resource Cleanup**: Tự động dọn dẹp client không hoạt động

### 5. VAD Service (`vad_service.py`)

#### 5.1 Silero VAD Integration
```
VAD Processing:
├── Load Silero VAD model (CPU/GPU)
├── Per-client state management
├── Chunk-based processing (512 samples)
├── Speech probability calculation
├── Circuit breaker protection
└── Fallback to energy-based detection
```

#### 5.2 Client State Management
```
Client State:
├── vad_iterator: Silero VAD instance
├── speech_timestamps: Lịch sử phát hiện speech
├── last_activity: Thời gian hoạt động cuối
├── stats: Thống kê processing
└── error_count: Đếm lỗi để reset iterator
```

### 6. Configuration Management (`app_config.py`)
Hệ thống config phân cấp với environment variable support:

```
Configuration Hierarchy:
├── AudioConfig: Sample rate, chunk size, channels
├── VADConfig: Threshold, duration, device settings
├── STTConfig: Model path, worker count, thresholds
├── QueueConfig: Queue sizes, limits
├── CircuitBreakerConfig: Failure thresholds, timeouts
├── ServerConfig: Host, port, connections
└── LoggingConfig: Log levels, file paths, rotation
```

### 7. Result Dispatcher Flow
```
Result Flow:
1. STT Worker generates result
2. Emit to async_result_queue
3. Dispatcher receives from queue
4. Determine result type (transcript/translation)
5. Get target clients từ session_manager
6. Send JSON result to WebSocket clients
7. Handle client disconnection gracefully
```

### 8. Health Check System
```
Health Endpoints:
├── /health: Detailed health status
│   ├── Service status (healthy/degraded/unhealthy)
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