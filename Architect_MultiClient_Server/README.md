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

# Check for specific rules (e.g., SIM for flake8-simplify)
ruff check --select SIM agents/ orchestrator_service/ stt_service/ tts_service/ > ruff_sim_errors.txt
```

#### Running locally (within a specific service):
You can also run Ruff directly inside any service directory. It will automatically detect its local `pyproject.toml`:
```bash
cd agents
ruff check . > ruff_errors.txt                              # Check (Recommended: redirect output to a file)
ruff check --fix .                                          # Fix
ruff format .                                               # Format
ruff check --select SIM . > ruff_sim_errors.txt             # Check for specific rules (e.g., SIM)
```

---

### 2. Static Type Checking with MyPy

MyPy verifies type hints across the services. To prevent namespace collisions (e.g., shadowing external libraries) and `[import-not-found]` errors, you must **ALWAYS run MyPy from the root directory** (`Architect_MultiClient_Server`).

#### Running locally (within a specific service, recommended approach):
You can run MyPy directly inside any service directory, which will detect the local configurations:
```bash
cd orchestrator_service
mypy .
```

#### Check a specific service or module:
To ensure MyPy correctly understands the package structure and resolves absolute imports, use the -p (package) or -m (module) flag. Do not change directories.
```bash
# Make sure you are in the root directory: Architect_MultiClient_Server/

# Check an ENTIRE service package recursively (Use -p for package)
mypy -p orchestrator_service > mypy-errors.txt

# Check with a specific pyproject.toml config file
mypy --config-file orchestrator_service/pyproject.toml -p orchestrator_service > mypy-errors.txt

# Check a SPECIFIC sub-module precisely (Use -m for module)
mypy -m orchestrator_service.services.room_registry
```

---

> You can configure Ruff and MyPy to run automatically in your IDE (e.g., VS Code or PyCharm) on save, or integrate them into your Git pre-commit hooks to ensure only clean code is committed.

