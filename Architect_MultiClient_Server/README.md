# Architect Multi-Client Server (Mezon Call Translation)

This repository contains the backend services for the Mezon Call Translation project. It is structured as a multi-service architecture comprising the following components:
- **agents**: AI Agent logic and workflows.
- **dashboard**: Management and visualization interface.
- **orchestrator_service**: Orchestration layer coordinating translation tasks.
- **stt_service**: Speech-to-Text (STT) processing.
- **tts_service**: Text-to-Speech (TTS) processing.

---

## Code Quality & Type Checking

To maintain high code quality, consistent formatting, and type safety, each service (**agents**, **orchestrator_service**, **stt_service**, and **tts_service**) has its own individual `pyproject.toml` configuration file. 

This allows you to run linting, formatting, and type checking either globally from the root directory (`Architect_MultiClient_Server`) or locally inside each service directory.

### 1. Linting & Formatting with Ruff

Ruff is configured inside each service directory to lint and format its own codebase.

#### Running globally (from the root directory):
To check or format all services from the `Architect_MultiClient_Server` folder, run:
```bash
# Check for linting issues (Redirect output to a file to prevent terminal overflow)
ruff check agents/ orchestrator_service/ stt_service/ tts_service/ > ruff_errors.txt

# Automatically fix linting issues
ruff check --fix agents/ orchestrator_service/ stt_service/ tts_service/

# Format code
ruff format agents/ orchestrator_service/ stt_service/ tts_service/
```

#### Running locally (within a specific service):
You can also run Ruff directly inside any service directory. It will automatically detect its local `pyproject.toml`:
```bash
cd agents
ruff check . > ruff_errors.txt        # Check (Recommended: redirect output to a file)
ruff check --fix .                     # Fix
ruff format .                          # Format
```

---

### 2. Static Type Checking with MyPy

MyPy verifies type hints across the services. Because each service has specific library dependencies and type overrides, it is recommended to run MyPy from within the respective service directory, or target the directory from the root.

#### Running globally (from the root directory):
```bash
# Check types (Recommended: redirect output to a file)
mypy agents/ orchestrator_service/ stt_service/ tts_service/ > mypy_errors.txt
```

#### Running locally (within a specific service):
```bash
cd orchestrator_service
mypy . > mypy_errors.txt               # Check (Recommended: redirect output to a file)
```

---

> You can configure Ruff and MyPy to run automatically in your IDE (e.g., VS Code or PyCharm) on save, or integrate them into your Git pre-commit hooks to ensure only clean code is committed.

