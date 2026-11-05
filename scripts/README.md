# Service scripts

These scripts support the maintained batch STT service, Orchestrator, and the
standalone TTS service. The Go agent and worker manager are built separately;
see `agents/README.md`.

## Setup

```bash
./scripts/setup.sh
```

Useful options:

- `--skip-models`
- `--skip-venv`
- `--skip-env`
- `--kokoro-voices <comma-separated names>`
- `--all-kokoro-voices`

The setup script downloads the Gipformer fallback model used by STT, the
Kokoro model used by the standalone TTS service, and the Orchestrator
bi-encoder model. It creates `.env` files from each service's template and
installs Python dependencies into service-local virtual environments.

## Models

Individual downloaders are also available:

```bash
bash scripts/download-gipformer-model.sh
bash scripts/download-kokoro-model.sh
bash scripts/download-bi-encoder-model.sh
```

## systemd

Create units for `mezon-stt-service`, `mezon-orchestrator-service`, and
`mezon-tts-service`:

```bash
sudo ./scripts/create-systemd-services.sh --enable --start
```

Preview unit files without writing them:

```bash
./scripts/create-systemd-services.sh --dry-run
```

Control all three units:

```bash
sudo ./scripts/manage-services.sh status
sudo ./scripts/manage-services.sh restart
sudo ./scripts/manage-services.sh follow
```

## Validation

```bash
./scripts/health-check.sh
```

The check validates service directories, requirements, required STT model
assets, environment files, and installed systemd units when systemd is
available.
