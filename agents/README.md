# agents

This Go module contains two binaries:

- `cmd/agent`: one WebRTC client per room. It joins `mezon-sfu`, receives
  participant audio, and forwards that audio to `record-service`.
- `cmd/worker-manager`: consumes NATS start/stop events and manages agent
  subprocesses.

Realtime STT, Orchestrator agent-request SSE, outbound agent chat, and agent
TTS playback are intentionally not part of these binaries.

## Requirements

- Go 1.24+
- A reachable `mezon-sfu` instance
- A reachable NATS server for `worker-manager`
- `record-service` for durable participant audio recording
- `orchestrator_service` for room and participant lifecycle registration

`libopus` is needed only when running tests that use `internal/opusenc` to
generate Opus test packets; the production agent no longer encodes TTS audio.

## Agent configuration

All values are environment variables parsed by `internal/config`.

| Variable | Required | Default | Purpose |
|---|---:|---:|---|
| `ROOM_ID` | yes | — | SFU room to join |
| `AGENT_USER_ID` | yes | — | Numeric SFU identity |
| `SFU_WS_URL` | no | `ws://127.0.0.1:8000/ws` | SFU signaling endpoint |
| `SFU_JWT_SECRET` | no | `default` | Join-token signing secret |
| `AGENT_ROLE` | no | `audience` | SFU role (`audience` or `speaker`) |
| `AGENT_TOKEN_TTL_SECONDS` | no | `21600` | Join-token lifetime |
| `RECORD_SERVICE_GRPC_ADDR` | no | `record-service:50051` | Recording gRPC endpoint |
| `RECORD_SERVICE_MAX_QUEUE_SIZE` | no | `200` | Per-track recording queue |
| `ORCHESTRATOR_BASE_URL` | no | `http://localhost:8002` | Room lifecycle API base URL |
| `INTERNAL_API_SECRET` | no | empty | Orchestrator bearer token |
| `AGENT_SOCKET_DIR` | no | `/tmp/mezon-agents` | Control-socket directory |
| `AGENT_MAX_LIFETIME_SECONDS` | no | `10800` | Maximum session lifetime; `0` disables |
| `AGENT_EMPTY_ROOM_GRACE_SECONDS` | no | `15` | Delay before leaving an empty room |
| `AGENT_CHAT_AVATAR_URL` | no | empty | Avatar included in the SFU token |
| `LOG_LEVEL` | no | `INFO` | `log/slog` level |

Reconnect behavior is controlled by `AGENT_RECONNECT_MAX_ATTEMPTS`,
`AGENT_RECONNECT_BASE_DELAY_MS`, `AGENT_RECONNECT_MAX_DELAY_MS`, and
`AGENT_RECONNECT_STABLE_AFTER_SECONDS`.

Run an agent directly:

```bash
go build -o bin/agent ./cmd/agent
ROOM_ID=1 AGENT_USER_ID=999001 ./bin/agent
```

## Worker-manager configuration

Important variables include:

| Variable | Default | Purpose |
|---|---:|---|
| `NATS_URL` | `nats://127.0.0.1:4222` | NATS connection |
| `AGENT_DISPATCH_SUBJECT` | `mezon_sfu_hook_event` | Start/stop event subject |
| `AGENT_WORKER_QUEUE_GROUP` | `agent-worker-manager` | NATS queue group |
| `AGENT_BIN_PATH` | sibling `agent` binary | Agent executable |
| `AGENT_STOP_TIMEOUT_SECONDS` | `20` | Grace before forced termination |
| `AGENT_USER_ID_BASE` | `0` | Numeric identity used for spawned agents |
| `WORKER_MANAGER_STATE_DIR` | `/tmp/mezon-agents` | Process registry directory |

Build and run:

```bash
go build -o bin/agent ./cmd/agent
go build -o bin/worker-manager ./cmd/worker-manager
./bin/worker-manager
```

## Layout

```text
cmd/agent/                 per-room agent entrypoint
cmd/worker-manager/        NATS worker-manager entrypoint
internal/audiopipeline/    Opus decode and participant recording pipeline
internal/tracksink/        record-service sink construction
internal/recordclient/     record-service gRPC client
internal/orchestratorclient/ room/participant lifecycle HTTP client
internal/rtcagent/         pion WebRTC transport
internal/signaling/        mezon-sfu WebSocket protocol
internal/workermanager/    subprocess lifecycle and recovery
```

## Tests

```bash
go test ./...
```

The `internal/opusenc` package remains as test support for generating Opus
packets consumed by audio-pipeline tests.
