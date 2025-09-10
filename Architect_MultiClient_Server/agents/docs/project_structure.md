agent/
├── README.md
├── requirements.txt
├── .env                          # Environment variables
├── .gitignore
├── main.py                       # Entry point chính
│
├── src/                          # Source code chính
│   ├── __init__.py
│   ├── config.py                 # Configuration settings
│   ├── logger.py                 # Logging configuration
│   │
│   ├── core/                     # Core business logic
│   │   ├── __init__.py
│   │   ├── transcript_manager.py # Transcript management
│   │   ├── websocket_client.py   # WebSocket client
│   │   └── handlers.py           # Event handlers
│   │
│   └── utils/                    # Utility functions
│       ├── __init__.py
│       ├── helpers.py            # Helper functions
│       └── validators.py         # Validation functions
│
├── tests/                        # Test files
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_transcript_manager.py
│   ├── test_websocket_client.py
│   └── test_handlers.py
│
├── docs/                         # Documentation
│   ├── README.md
│   ├── API.md
│   ├── DEPLOYMENT.md
│   └── TROUBLESHOOTING.md
│
├── scripts/                      # Build/deployment scripts
│   ├── start.sh
│   ├── stop.sh
│   └── deploy.sh
│
└── docker/                       # Docker configurations
    ├── Dockerfile
    ├── docker-compose.yml
    └── .dockerignore