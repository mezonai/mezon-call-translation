# LiveKit Agent Project Structure

## Overview
This document outlines the structure of the LiveKit Agent component, which handles real-time audio processing and WebSocket communication with the main server.

## Directory Structure

```
agents/
├── README.md
├── requirements.txt
├── .env                          # Environment variables
├── .gitignore
├── main.py                       # Main entry point
│
├── src/                          # Core source code
│   ├── __init__.py
│   ├── config.py                 # Configuration settings
│   ├── logger.py                 # Logging configuration
│   │
│   ├── config/                   # Configuration modules
│   │   ├── __init__.py
│   │   ├── audio_config.py       # Audio processing configuration
│   │   ├── constants.py          # System constants
│   │   └── vad_config.py         # VAD configuration
│   │
│   ├── core/                     # Core business logic
│   │   ├── __init__.py
│   │   ├── agent_manager.py      # Agent lifecycle management
│   │   ├── handlers.py           # LiveKit event handlers
│   │   ├── transcript_manager.py # Transcript processing
│   │   ├── vad_processor.py      # Voice Activity Detection
│   │   ├── websocket_client.py   # WebSocket communication
│   │   │
│   │   ├── audio/                # Audio processing pipeline
│   │   │   ├── pipeline.py       # Audio processing pipeline
│   │   │   └── processor.py      # Audio data processor
│   │   │
│   │   └── websocket/            # WebSocket management
│   │       └── manager.py        # WebSocket connection manager
│   │
│   ├── services/                 # Service layer
│   │   ├── config_service.py     # Configuration service
│   │   └── metrics_service.py    # Metrics collection service
│   │
│   ├── testing/                  # Testing utilities
│   │   └── load_test.py          # Load testing implementation
│   │
│   └── utils/                    # Utility functions
│       ├── buffer_pool.py        # Audio buffer pool management
│       ├── circuit_breaker.py    # Circuit breaker implementation
│       ├── error_handling.py     # Error handling utilities
│       ├── logging_manager.py    # Advanced logging management
│       ├── resource_management.py # Resource cleanup utilities
│       │
│       ├── thread_safe/          # Thread-safe utilities
│       │   ├── buffer.py         # Thread-safe buffer
│       │   └── queue.py          # Thread-safe queue
│       │
│       └── vad/                  # VAD utilities
│           └── zcr_filter.py     # Zero-crossing rate filter
│
├── tests/                        # Test files
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_transcript_manager.py
│   ├── test_websocket_client.py
│   ├── test_handlers.py
│   └── test_websocket.py         # WebSocket integration tests
│
├── docs/                         # Documentation
│   ├── README.md                 # Agent-specific documentation
│   ├── API.md                    # API reference
│   ├── DEPLOYMENT.md             # Deployment guide
│   └── TROUBLESHOOTING.md        # Troubleshooting guide
│
├── scripts/                      # Build/deployment scripts
│   ├── start.sh                  # Start script
│   ├── stop.sh                   # Stop script
│   ├── deploy.sh                 # Deployment script
│   └── run_load_test.py          # Load testing script
│
└── logs/                         # Log files (auto-created)
    └── agent.log                 # Agent application logs
```

## Component Descriptions

### Core Components

- **`main.py`**: Application entry point that initializes the LiveKit agent
- **`agent_manager.py`**: Manages agent lifecycle, identity, and room connections
- **`handlers.py`**: Processes LiveKit events (track subscription, participant events)
- **`websocket_client.py`**: Handles WebSocket communication with the STT server
- **`transcript_manager.py`**: Manages transcript delivery via LiveKit data channels
- **`vad_processor.py`**: Implements Voice Activity Detection for audio efficiency

### Audio Processing

- **`audio/pipeline.py`**: Audio processing pipeline with VAD integration
- **`audio/processor.py`**: Low-level audio data processing and formatting
- **`vad/zcr_filter.py`**: Zero-crossing rate filtering for speech detection

### Services

- **`config_service.py`**: Centralized configuration management
- **`metrics_service.py`**: Performance metrics collection and reporting

### Utilities

- **`buffer_pool.py`**: Efficient audio buffer management with pooling
- **`circuit_breaker.py`**: Fault tolerance for external service calls
- **`error_handling.py`**: Standardized error handling and recovery
- **`resource_management.py`**: Automatic resource cleanup and management
- **`thread_safe/`**: Thread-safe data structures for concurrent operations

### Testing

- **`testing/load_test.py`**: Load testing framework for performance validation
- **`tests/`**: Unit and integration tests for all components
- **`scripts/run_load_test.py`**: Load testing execution script

## Key Features

1. **Real-time Audio Processing**: Efficient audio stream handling with VAD
2. **LiveKit Integration**: Seamless room management and participant tracking
3. **WebSocket Communication**: Reliable connection to STT server with auto-reconnection
4. **Thread-safe Operations**: Concurrent processing with proper synchronization
5. **Comprehensive Testing**: Load testing and unit testing framework
6. **Resource Management**: Automatic cleanup and efficient resource utilization
7. **Error Resilience**: Circuit breaker pattern and graceful error handling
8. **Performance Monitoring**: Built-in metrics collection and reporting