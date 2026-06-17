# Architect Multi-Client Server (Mezon Call Translation)

This repository contains the backend services for the Mezon Call Translation project. It is structured as a multi-service architecture comprising the following components:
- **agents**: AI Agent logic and workflows.
- **dashboard**: Management and visualization interface.
- **orchestrator_service**: Orchestration layer coordinating translation tasks.
- **stt_service**: Speech-to-Text (STT) processing.
- **tts_service**: Text-to-Speech (TTS) processing.

---

## Code Quality & Type Checking

To maintain high code quality, consistent formatting, and type safety, the project uses **Ruff** for linting and formatting, and **MyPy** for static type analysis.

### 1. Linting & Formatting with Ruff

Ruff is an extremely fast Python linter and code formatter.

#### Check for linting issues:
To scan the codebase for code quality issues, unused imports, or style violations, run:
```bash
cd Architect_MultiClient_Server
ruff check agents/ dashboard/ orchestrator_service/ stt_service/ tts_service/
```

#### Automatically fix linting issues:
To let Ruff automatically resolve fixable violations (such as sorting imports, removing unused imports, etc.):
```bash
cd Architect_MultiClient_Server
ruff check --fix agents/ dashboard/ orchestrator_service/ stt_service/ tts_service/
```

#### Format the code:
To apply code formatting matching the rules configured in `pyproject.toml` (e.g. double quotes, line-length limit):
```bash
cd Architect_MultiClient_Server
ruff format agents/ dashboard/ orchestrator_service/ stt_service/ tts_service/
```

### 2. Static Type Checking with MyPy

MyPy verifies type hints across the services to ensure type safety.

#### Run type checking:
```bash
mypy agents/ dashboard/ orchestrator_service/ stt_service/ tts_service/
```

---

> You can configure Ruff and MyPy to run automatically in your IDE (e.g., VS Code or PyCharm) on save, or integrate them into your Git pre-commit hooks to ensure only clean code is committed.
