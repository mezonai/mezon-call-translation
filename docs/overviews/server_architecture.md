# Architect Multi-Client Server - Architecture Overview

## 1. Project Overview / Introduction

### Project Description
**Architect Multi-Client Server** is a real-time multi-client server system specialized in audio processing with Speech-to-Text (STT) capabilities and LiveKit integration. The system is designed to serve multiple clients simultaneously, process audio streams, and provide real-time transcription capabilities.

### Main Objectives
- **Real-time Audio Processing**: Process audio streams from multiple clients simultaneously
- **Speech-to-Text**: Convert speech to text using Vosk engine
- **Multi-session Support**: Support multiple work sessions with multiple clients
- **LiveKit Integration**: Integrate with LiveKit for meeting room management and agent dispatch
- **High Availability**: Ensure high availability with circuit breaker pattern
- **Monitoring & Health Check**: System monitoring and health reporting

### Project Scope
- **Backend API Server**: FastAPI-based REST API and WebSocket server
- **Audio Processing Pipeline**: Audio processing pipeline with Vosk STT
- **Session Management**: Session and client connection management
- **Health Monitoring**: Monitoring system and status reporting
- **Agent Integration**: Integration with LiveKit agents

## 2. Architecture Document

### Overall Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    CLIENT LAYER                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │   Client A  │  │   Client B  │  │   Client N  │              │
│  │   (Audio)   │  │   (Audio)   │  │   (Audio)   │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
└─────────────────────────────────────────────────────────────────┘
                              │
                         WebSocket Connections
                              │
┌─────────────────────────────────────────────────────────────────┐
│                   API GATEWAY LAYER                             │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              FastAPI Server                                 ││
│  │  • CORS Middleware                                          ││
│  │  • Authentication (JWT)                                     ││
│  │  • WebSocket Handler                                        ││
│  │  • Health Check Endpoints                                   ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│                 SESSION MANAGEMENT LAYER                        │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              Session Manager                                ││
│  │  • Client Registration                                      ││
│  │  • Session Lifecycle                                        ││
│  │  • Message Routing                                          ││
│  │  • Client Capabilities (transcript/translation)             ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│                  AUDIO PROCESSING LAYER                         │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              STT Vosk Service                               ││
│  │  • Multi-threaded Workers (configurable)                    ││
│  │  • Audio Queue Management                                   ││
│  │  • Chunk Accumulation                                       ││
│  │  • Circuit Breaker Protection                               ││
│  │  • Vosk Recognition Engine                                  ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│                  INTEGRATION LAYER                              │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              LiveKit Service                                ││
│  │  • Agent Dispatch Management                                ││
│  │  • Room Management                                          ││
│  │  • LiveKit API Integration                                  ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│                   MONITORING LAYER                              │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  • Health Service                                           ││
│  │  • Metrics Collection                                       ││
│  │  • WebSocket Monitor                                        ││
│  │  • Circuit Breaker Status                                   ││
│  │  • Performance Tracking                                     ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

### Main Components

#### 1. **API Layer**
- **FastAPI Server**: HTTP REST API and WebSocket server
- **CORS Middleware**: Handle Cross-Origin requests
- **JWT Authentication**: Token-based authentication
- **Routing**: Route requests to controllers

#### 2. **Controller Layer**
- **WebSocket Vosk Controller** (`ws_vosk_control.py`): Handle WebSocket connections for STT
- **Agent Controller** (`agents_control.py`): Manage LiveKit agent dispatch

#### 3. **Service Layer**
- **Vosk STT Service**: Core service for Speech-to-Text processing
- **LiveKit Service**: Integration with LiveKit platform
- **Health Service**: System health monitoring
- **Auth Service**: Authentication processing

#### 4. **Session Management**
- **Session Manager**: Manage client sessions and message routing

#### 5. **Utilities**
- **Circuit Breaker**: Fault tolerance pattern
- **WebSocket Monitor**: Monitor WebSocket connections
- **Logging Config**: System logging configuration

## 3. System Design / Technical Specs

### Main Processing Flow

#### 3.1 Audio Processing Flow

```
┌──────────────┐    WebSocket    ┌─────────────────┐
│Client/Agents │ ──────────────→ │  WebSocket      │
│   (Audio)    │    Audio Data   │  Handler        │
└──────────────┘                 └─────────────────┘
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────────┐
│                Session Manager                              │
│  • Register client with capabilities                        │
│  • Route to appropriate session                             │
└─────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────────┐
│              STT Vosk Service                               │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ 1. Audio Validation & Conversion                        ││
│  │    • Convert to numpy array                             ││
│  │    • Validate audio data                                ││
│  │    • Split into optimal chunks (512 samples)            ││
│  └─────────────────────────────────────────────────────────┘│
│                           │                                 │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ 2. Worker Assignment                                    ││
│  │    • Hash-based worker selection                        ││
│  │    • Queue management                                   ││
│  │    • Load balancing                                     ││
│  └─────────────────────────────────────────────────────────┘│
│                           │                                 │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ 3. Chunk Accumulation                                   ││
│  │    • Accumulate chunks per client                       ││
│  │    • Adaptive processing based on queue load            ││
│  │    • Time-based and size-based triggers                 ││
│  └─────────────────────────────────────────────────────────┘│
│                           │                                 │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ 4. Vosk Recognition                                     ││
│  │    • Create/reuse recognizer per client                 ││
│  │    • AcceptWaveform processing                          ││
│  │    • Generate partial/final results                     ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────────┐
│              Result Dispatcher                              │
│  • Route transcript back to originating client              │
│  • Queue translation if requested                           │
│  • Emit via AsyncIO queue                                   │
└─────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌─────────────┐    WebSocket    ┌─────────────────┐
│   Client    │ ←────────────── │  WebSocket      │
│ (Transcript)│   JSON Result   │  Response       │
└─────────────┘                 └─────────────────┘
```

#### 3.2 LiveKit Integration Flow

```
┌─────────────┐    HTTP POST    ┌─────────────────┐
│   Client    │ ──────────────→ │  Agent          │
│             │   /agent/join   │  Controller     │
└─────────────┘                 └─────────────────┘
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────────┐
│              JWT Validation                                 │
│  • Extract and decode JWT token                             │
│  • Validate room permissions                                │
│  • Extract room name                                        │
└─────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────────┐
│              LiveKit Service                                │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ 1. Check Existing Dispatch                              ││
│  │    • List current dispatches for room                   ││
│  │    • Verify agent not already dispatched                ││
│  └─────────────────────────────────────────────────────────┘│
│                           │                                 │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ 2. Create New Dispatch                                  ││
│  │    • Create agent dispatch request                      ││
│  │    • Configure agent for room                           ││
│  │    • Return dispatch information                        ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌─────────────┐    HTTP Response ┌─────────────────┐
│   Client    │ ←─────────────── │  JSON Response  │
│             │   Dispatch Info  │  (Created/      │
└─────────────┘                  │   Exists/Error) │
                                 └─────────────────┘
```

### Technical Details

#### 3.3 Worker Thread Architecture

```
STT Vosk Service
├── Worker Thread 1
│   ├── Audio Queue (maxsize: configurable)
│   ├── Recognizer Pool (per client)
│   ├── Client State Management
│   └── Result Emission
├── Worker Thread 2
│   └── ...
├── Worker Thread N
│   └── ...
├── Cleanup Thread
│   ├── Periodic resource cleanup
│   ├── Inactive client removal
│   └── Memory management
└── Metrics Thread
    ├── Performance monitoring
    ├── Queue size tracking
    └── Health metrics
```

#### 3.4 Session Management Schema

```json
{
  "sessions": {
    "<session_id>": {
      "clients": {
        "<client_id>": {
          "websocket": "WebSocket_Instance",
          "transcripts": true/false,
          "translation": true/false,
          "language": "en/vi/etc",
          "last_text": "cached_text"
        }
      },
      "transcripts": {},
      "translation": {}
    }
  }
}
```

#### 3.5 Circuit Breaker Protection

```
┌─────────────────────────────────────────────────────────────┐
│                Circuit Breaker States                       │
│                                                             │
│  CLOSED (Normal)           OPEN (Failed)                    │
│  ┌─────────────┐          ┌─────────────┐                   │
│  │ Calls pass  │          │ Calls fail  │                   │
│  │ through     │ ────────→│ immediately │                   │
│  │             │ Failures │             │                   │
│  └─────────────┘ exceed   └─────────────┘                   │
│         ▲       threshold        │                          │
│         │                        │ Timeout                  │
│         │                        ▼                          │
│  ┌─────────────┐          ┌─────────────┐                   │
│  │ Reset to    │          │ HALF_OPEN   │                   │
│  │ CLOSED      │ ←────────│ (Testing)   │                   │
│  └─────────────┘ Success  └─────────────┘                   │
│                 threshold                                   │
└─────────────────────────────────────────────────────────────┘
```

### API Endpoints

#### 3.6 REST Endpoints

```
POST /agent/join
├── Headers: Authorization: Bearer <JWT>
├── Body: {"url": "optional_livekit_url"}
└── Response: {"result": "created/exists/error", "dispatch": {...}}

GET /health
├── Response: {
│   "status": "healthy/degraded/unhealthy",
│   "timestamp": timestamp,
│   "uptime": seconds,
│   "details": {...}
│ }

GET /health/simple
└── Response: {"status": "healthy/unhealthy", "timestamp": timestamp}

POST /admin/emergency-cleanup
└── Response: {"status": "success", "details": {...}}

GET /ws/stats
└── Response: {
    "websocket_stats": {...},
    "frequent_disconnect_codes": [...],
    "active_clients_info": {...},
    "circuit_breaker_status": {...}
  }
```

#### 3.7 WebSocket Endpoint

```
WS /ws/vosk/
├── Query Parameters:
│   ├── client_id: string (required)
│   ├── session_id: string (required)
│   ├── transcript: boolean (required)
│   ├── translation: boolean (required)
│   ├── language: string (optional)
│   ├── max_duration: int (optional, seconds)
│   └── idle_timeout: int (optional, seconds)
├── Input: Binary audio data
└── Output: JSON messages
    ├── {"type": "transcripts", "text": "...", "is_final": true/false}
    └── {"type": "translation", "text": "...", "is_final": true/false}
```

### Performance Characteristics

#### 3.8 Scalability Metrics

- **Concurrent Clients**: Supports multiple clients (limited by system resources)
- **Worker Threads**: Configurable (default based on CPU cores)
- **Audio Processing Latency**: < 100ms for real-time processing
- **Queue Management**: Adaptive thresholds based on load
- **Memory Usage**: Efficient chunk accumulation with cleanup
- **Fault Tolerance**: Circuit breaker protection with automatic recovery

#### 3.9 Configuration Parameters

```python
# Audio Processing
sample_rate: 16000
min_text_length: 3
min_chunks: 3
max_chunks: 10
min_time_threshold: 0.5s
max_time_threshold: 3.0s

# Queue Management  
audio_queue_maxsize: 100
result_queue_maxsize: 1000

# Worker Management
num_workers: auto (CPU based)
metrics_interval_sec: 5.0
client_cleanup_interval: 30.0

# Circuit Breaker
failure_threshold: 5
timeout: 60.0s
success_threshold: 3
```

## Conclusion

The **Architect Multi-Client Server** system is designed as a powerful and highly scalable platform for real-time audio processing. With its multi-layer architecture and patterns like Circuit Breaker and Session Management, the system ensures high reliability and performance in production environments.

Key features include multi-client processing capabilities, real-time STT with Vosk engine, LiveKit integration, and comprehensive monitoring system - all designed to support applications requiring high-quality real-time audio processing.