# agents

Two Go binaries that together replace the old LiveKit-based agent (`Architect_MultiClient_Server/agents/`, Python) — see [Background](#background) for why:

- **`cmd/agent`** — the per-room WebRTC client that actually joins a `mezon-sfu` room to record/transcribe/translate a call.
- **`cmd/worker-manager`** — subscribes to NATS start/stop events and spawns/kills one `agent` subprocess per room.

They live in the same module on purpose, mirroring how LiveKit's own `agents` framework keeps the worker/dispatch layer and the job/agent behavior in one codebase (LiveKit just has an SDK that makes the pairing more ergonomic). See `internal/workermanager`'s package doc for why this isn't a module inside `orchestrator_service` instead.

## Status

Work in progress, built incrementally against [`mezon-sfu-migration-plan.md`](../mezon-sfu-migration-plan.md). Current state:

| Layer | Status |
|---|---|
| 1. Worker manager (NATS start/stop → spawn/kill subprocess) | Done — NATS subject names not finalized with BE mezon yet |
| 2.1 Bootstrap & auth (JWT signing, config) | Done |
| 2.2 WS signaling client (join/offer/roster/keepalive) | Done |
| 2.3 WebRTC transport (pion, mid↔user_id table) | Done |
| 2.4 Opus decode + forward to record-service (gRPC) | Done — best-effort, see `internal/audiopipeline` |
| 2.5 TTS publish-back (`role:speaker`) | Join + track + HTTP client + queue done; **Speak() fails until a real Opus encoder is wired in** (`internal/opusenc`) |
| 2.6 STT client + transcript push + on/off control | Done — see the `room_name` caveat below |
| 2.7 VAD | Dropped, not dead code to maintain — see `internal/audiopipeline`'s absence of it and plan.md 2.7 |

**Not yet tested against a running `mezon-sfu`, NATS server, or orchestrator_service** — no local build of `mezon-sfu` exists on the dev machine this was written on (missing `libuv`/`libsrtp2`/`liburing`/BoringSSL/`nats.c`), no local `nats-server` either, and orchestrator_service wasn't run against this code. `go build ./...` and `go vet ./...` are clean throughout; end-to-end verification is still open — see plan.md section 3.

`cmd/agent` does not know or implement how it gets spawned — it only reads its parameters from the environment (below). `cmd/worker-manager` is what owns that, in this repo, for real now (not just on paper in the plan).

## Requirements

- Go 1.24+
- A reachable NATS server (for `worker-manager`) and `mezon-sfu` instance (signaling WS port + media UDP port, for `agent`) — see [Topology](#topology) below.
- Optionally, `orchestrator_service` reachable over HTTP — without it the agent still joins/records, just without transcript push, STT on/off control, or a way to trigger TTS (see 2.6's `ORCHESTRATOR_BASE_URL`).

## `agent` configuration

All via environment variables (`internal/config/config.go`):

| Var | Required | Default | Meaning |
|---|---|---|---|
| `ROOM_ID` | yes | — | Room to join. Also embedded in the JWT `room` claim — `mezon-sfu` reads room membership only from the JWT as of the 2026-08-16 protocol update, not from the `join` message body. |
| `AGENT_USER_ID` | yes | — | Identity the agent presents as (JWT `identity` claim). Who issues this / whether bots get a reserved id range is still open — see `mezon-sfu-migration-checklist.md` B1. |
| `SFU_WS_URL` | no | `ws://127.0.0.1:8000/ws` | mezon-sfu signaling WebSocket endpoint. |
| `SFU_JWT_SECRET` | no | `default` | HS256 shared secret to sign the join token. `mezon-sfu` isn't validating this signature yet (secret to be shared by the mezon-sfu team later) — keep this swappable, don't hardcode it anywhere else. |
| `AGENT_ROLE` | no | `audience` | `audience` (subscribe-only, record path) or `speaker` (also publishes TTS — see 2.5's encoder gap). |
| `AGENT_TOKEN_TTL_SECONDS` | no | `21600` (6h) | JWT `exp` lifetime. |
| `AGENT_RECONNECT_MAX_ATTEMPTS` | no | `8` | Consecutive failed sessions to retry before giving up and exiting. `0` disables reconnect (first failure exits immediately). |
| `AGENT_RECONNECT_BASE_DELAY_MS` / `AGENT_RECONNECT_MAX_DELAY_MS` | no | `1000` / `30000` | Exponential backoff bounds between reconnect attempts. |
| `AGENT_RECONNECT_STABLE_AFTER_SECONDS` | no | `30` | A session that stayed up at least this long before dying resets the attempt counter. |
| `RECORD_SERVICE_GRPC_ADDR` | no | `record-service:50051` | gRPC target for audio forwarding. Same var name as the old Python agent's `RecordServiceConfig`. |
| `RECORD_SERVICE_MAX_QUEUE_SIZE` | no | `200` | Per-track forwarding queue depth before frames start getting dropped (never blocked). |
| `WS_HOST` / `WS_PORT` | no | `localhost` / `8000` | Vosk STT service address. Same var names as the old Python agent (kept as-is; yes, they're generic, not `STT_`-prefixed). |
| `STT_MAX_QUEUE_SIZE` | no | `32` | Per-track STT forwarding queue depth. |
| `ORCHESTRATOR_BASE_URL` | no | `http://localhost:8002` | Base URL for `push_transcript` and the SSE agent-request listener. Set empty-equivalent (unset) to run without any orchestrator integration. |
| `INTERNAL_API_SECRET` | no | `""` | Bearer token sent as `Authorization` to orchestrator, if set. |
| `TTS_SERVICE_BASE_URL` | no | `http://localhost:8008` | TTS synthesis HTTP endpoint. Only used when `AGENT_ROLE=speaker`. |
| `TTS_SAMPLE_RATE` | no | `24000` | Sample rate the TTS service's response PCM is at (Kokoro's default) — not returned in the response, so the agent has to already know it. |
| `TTS_RECORD_MAX_QUEUE_SIZE` | no | `200` | Forwarding queue depth for the agent's own TTS audio going to record-service. |
| `LOG_LEVEL` | no | `INFO` | Any `log/slog` level string. |

Recording, STT, and the orchestrator SSE listener are all best-effort: if the corresponding service is unreachable at startup, the agent logs it and joins the room anyway with that piece disabled for the run — none of these ever block joining.

Running standalone (without the worker manager), e.g. for manual testing:

```bash
go build ./cmd/agent
ROOM_ID=1 AGENT_USER_ID=999001 SFU_WS_URL=ws://127.0.0.1:8000/ws ./agent
```

Exits 0 on graceful shutdown (SIGINT/SIGTERM); exits non-zero and logs the reason if the JWT can't be signed, the WS dial fails, the join handshake errors out, or the reconnect budget (`AGENT_RECONNECT_*`) is exhausted after repeated WS drops.

## `worker-manager` configuration

All via environment variables (`internal/workermanager/config.go`):

| Var | Required | Default | Meaning |
|---|---|---|---|
| `NATS_URL` | no | `nats://127.0.0.1:4222` | NATS server to subscribe on. |
| `AGENT_START_SUBJECT` / `AGENT_STOP_SUBJECT` | no | `mezon.agent.start` / `mezon.agent.stop` | **Placeholders** — not finalized with BE mezon yet, see `mezon-sfu-migration-plan.md` section 1. |
| `AGENT_WORKER_QUEUE_GROUP` | no | `agent-worker-manager` | NATS queue group, so a future multi-instance worker-manager doesn't double-spawn on the same event. |
| `AGENT_BIN_PATH` | no | `agent` next to the `worker-manager` binary | Path to the built `agent` binary to spawn. |
| `AGENT_STOP_TIMEOUT_SECONDS` | no | `10` | How long to wait after SIGTERM before escalating to SIGKILL on Stop. |

`worker-manager` also reads (and passes through unchanged to every spawned `agent`): `SFU_WS_URL`, `SFU_JWT_SECRET`, `AGENT_TOKEN_TTL_SECONDS`, all four `AGENT_RECONNECT_*` vars, `RECORD_SERVICE_*`, `WS_HOST`/`WS_PORT`, `STT_MAX_QUEUE_SIZE`, `ORCHESTRATOR_BASE_URL`, `INTERNAL_API_SECRET`, `TTS_*`, and `LOG_LEVEL` (`internal/workermanager/config.go`'s `agentPassthroughEnvKeys` -- keep in sync with `internal/config.Config` when adding new agent env vars). Per-room `ROOM_ID`/`AGENT_USER_ID`/`AGENT_ROLE` come from the NATS event, not the environment.

```bash
go build -o bin/agent ./cmd/agent
go build -o bin/worker-manager ./cmd/worker-manager
SFU_WS_URL=ws://127.0.0.1:8000/ws SFU_JWT_SECRET=default ./bin/worker-manager
```

Then publish a start event, e.g. via the NATS CLI: `nats pub mezon.agent.start '{"room_id":1,"agent_user_id":999001}'`.

## Layout

```
cmd/agent/main.go            entrypoint: wires config -> JWT -> signaling.Client -> rtcagent.PeerAgent -> audiopipeline.Bridge (+ ttsplayer.Player if speaking)
cmd/worker-manager/main.go   entrypoint: wires workermanager.Config -> Manager -> NATS subscriber
internal/config/             agent's env parsing
internal/sfuauth/            JWT signing (HS256, mezon-sfu claim shape)
internal/signaling/          WS client + mezon-sfu JSON protocol types
internal/rtcagent/           pion PeerConnection, mid/user_id/track-kind table, RTP intake, optional publish track (role:speaker)
internal/audiopipeline/      Opus decode once per mic track, fans PCM out to record-service and/or STT sinks
internal/recordclient/       gRPC client to record-service (Go port of the old Python RecordForwarder/RecordServiceClient)
internal/recordpb/           generated from proto/recording.proto -- regenerate, don't hand-edit (see file header)
internal/sttclient/          WS client to the Vosk STT service (Go port of STTWebSocketClient), implements audiopipeline.Sink
internal/orchestratorclient/ push_transcript + SSE agent-request listener (tts_play, transcript_control)
internal/ttsclient/          HTTP client to the TTS service (Go port of process_text_to_audio)
internal/ttsplayer/          synthesize -> Opus encode -> publish via TrackLocalStaticSample, + record-service forwarding of the agent's own speech
internal/opusenc/            Opus encoder interface ttsplayer needs -- no implementation yet, see Status
internal/reconnect/          bounded exponential backoff for the agent's own session retry
internal/workermanager/      NATS start/stop subscriber + subprocess spawn/kill registry
internal/logging/            process-wide slog logger (shared by both binaries)
proto/recording.proto        source of truth for internal/recordpb -- kept in sync manually with audio-ingestion/record-service's copy, see its header
```

## Design decisions worth knowing before reviewing

- **No RTP `mid` header-extension parsing.** mezon-sfu's docs recommend reading the `mid` RTP extension over trusting SSRC, for anyone writing a raw UDP demuxer. Using pion at the `PeerConnection`/`OnTrack` level, that demuxing is already done — each `OnTrack` callback is already scoped to one negotiated transceiver/mid. Revisit if renegotiation is ever observed to misattribute a track.
- **Bounded reconnect with backoff, in-process.** A WS drop always means a full rejoin (new JWT, new SDP/ICE/DTLS) — `mezon-sfu` ties the media session 1:1 to the WS connection, so there's no "resume", and other participants will see the bot `peer_left`/`peer_joined` regardless of who initiates the rejoin. Given that, `cmd/agent/main.go`'s `run()` retries with exponential backoff (`internal/reconnect`) rather than exiting on the first drop, so a brief network glitch recovers without a full process respawn. It still gives up (process exits non-zero) after `AGENT_RECONNECT_MAX_ATTEMPTS` consecutive failures, so a genuinely broken session (bad secret, deleted room, SFU down) doesn't retry forever silently — that's the point where process-level supervision (worker manager) should take over. A graceful shutdown (SIGTERM, e.g. from a stop command) cancels the context and stops retrying immediately rather than reconnecting into a room the agent is being told to leave.
- **User-id/track-kind mapping comes from the SDP `msid`, not from `room_snapshot`/`peer_joined`.** As of the 2026-08-16 `mezon-sfu` protocol update, `a=msid:u<user_id>-p<peer_id>` is on every track section and pion surfaces it via `TrackRemote.StreamID()`. The WS roster events are still consumed (`UpsertRoster`/`RemovePeer`) but only for `role`/`is_mute`, not identity.
- **`worker-manager` keeps no state on disk.** The room_id→subprocess map is in-memory only. If `worker-manager` restarts, already-spawned agents keep running (they're started in their own process group — `Setpgid: true` — specifically so a signal to the manager doesn't cascade to them), but the new instance can't `Stop()` them by room_id anymore; a later stop event for that room just logs "not found" and no-ops. Not implemented: a state file + PID-liveness reconciliation on startup would close this gap, deliberately skipped for now rather than half-built before there's a real deployment topology to size it against.
- **`agent_user_id` must come from the start event, not be invented here.** Who issues a bot's user_id (BE mezon, or a reserved range) is still open — see `mezon-sfu-migration-checklist.md` B1. `Manager.Start` errors out on a missing/zero `agent_user_id` rather than guessing.
- **Opus decoding uses `pion/opus` (pure Go), not `hraban/opus` (cgo/libopus).** The dev machine this was written on has no `libopus` installed (`pkg-config opus` fails) -- `pion/opus` is a from-scratch RFC 6716 decoder with no C dependency, and can decode straight to 16kHz mono (`NewDecoderWithOutput(16000, 1)`), matching the old Python agent's `SAMPLE_RATE`/`CHANNELS` convention without a separate resampling step. It's decode-only, though -- see the next point for what that costs on the publish side.
- **No Opus encoder is wired in for TTS publish (`internal/opusenc`).** `pion/opus` v0.1.0 has no exported `Encoder`. `hraban/opus` (cgo/libopus) is the right call for actually encoding, but couldn't be built or verified on this dev machine (no `libopus-dev`), and landing an untested cgo file felt worse than an honest, clearly-isolated gap. Everything around it -- role:speaker join, track publish/negotiation, the TTS HTTP client, the SSE `tts_play` trigger, the per-utterance queue -- is real and wired end-to-end; only the actual `Encode()` call is missing. See `internal/opusenc`'s package doc for what to drop in.
- **A per-track session (Opus decoder + sinks) is closed from the track's own read-loop goroutine (`rtcagent.PeerAgent.OnTrackEnded`), never from the `peer_left` WS event handler.** `OnTrackEnded` and `OnAudioPacket` both fire from the same single goroutine per track, so most of a session's state needs no lock of its own. `peer_left` arrives on a *different* goroutine (the signaling client's dispatch loop); closing a sink from there while the track's read loop might still be calling `SendPCM` would race. This does mean sink cleanup is only as prompt as pion noticing the track ended (e.g. on renegotiation removing that mid) -- not yet verified against a real `mezon-sfu`, see Status. The one deliberate exception to "no lock needed" is the STT sink: `audiopipeline.Bridge.SetSTTEnabled` can attach/detach it from a *third* goroutine (whichever one is handling the `transcript_control` SSE request) at any time, so that one field is mutex-guarded -- see `internal/audiopipeline`'s package doc.
- **TTS requests are queued and played back one at a time, off the SSE listener's goroutine.** `ttsplayer.Player.Speak` enqueues and returns immediately (dropping and logging if the queue's full, never blocking); a single background worker drains it, mirroring the old Python `TTSManager`'s `_request_queue` ("only one audio stream active at a time"). Without this, two `tts_play` requests arriving close together would interleave `WriteSample` calls on the same track (garbled audio), and/or a slow synthesis call would stall the SSE read loop, delaying every other event behind it.
- **`room_name` for STT/transcript endpoints is a guess.** Orchestrator's `push_transcript` and SSE `agent-requests` were built around LiveKit's room name (a string, the meeting code) as distinct from orchestrator's internal `room_id` (UUID). mezon-sfu only has a numeric `room_id`. This code passes `room_id` (as a decimal string) everywhere the old contract wanted `room_name` -- the only thing available, not a confirmed-working substitute. See `internal/orchestratorclient`'s package doc.

## Regenerating `internal/recordpb`

```bash
protoc --go_out=. --go_opt=module=github.com/mezonai/mezon-call-translation/agents \
  --go-grpc_out=. --go-grpc_opt=module=github.com/mezonai/mezon-call-translation/agents \
  proto/recording.proto
```

Needs `protoc-gen-go`/`protoc-gen-go-grpc` on `PATH` (`go install google.golang.org/protobuf/cmd/protoc-gen-go@latest` / `google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest`). If `proto/recording.proto`'s messages change, keep `audio-ingestion/record-service/proto/recording.proto` and `Architect_MultiClient_Server/agents/src/proto/recording.proto` in sync too — see this file's header comment.

## Topology

Three separate network dependencies, all infra decisions this module doesn't configure — see `mezon-sfu-migration-plan.md` section 0:
- `worker-manager` → NATS server (`NATS_URL`).
- `agent` → `mezon-sfu`'s signaling (WS) and media (UDP) ports. `mezon-sfu`'s TURN support is not wired up yet, so for now this assumes `agent` and `mezon-sfu` are reachable directly (same LAN/VPC).
- `agent` → `orchestrator_service` (HTTP, optional) and the Vosk STT service (WS, optional) -- both best-effort, see Configuration above.

## Background

`mezon-call-translation` used to run entirely on LiveKit (`livekit-agents` Python SDK). Infra is retiring LiveKit in favor of `mezon-sfu`, an in-house SFU with a from-scratch JSON/WebSocket signaling protocol and no LiveKit-compatible SDK. Full context:

- [`../mezon-sfu-migration-summary.md`](../mezon-sfu-migration-summary.md) — quick context dump
- [`../mezon-sfu-migration-plan.md`](../mezon-sfu-migration-plan.md) — task list this module is being built against
- [`../mezon-sfu-migration-checklist.md`](../mezon-sfu-migration-checklist.md) — full LiveKit↔mezon-sfu comparison, protocol catalog, architecture decisions
- [`../../mezon-sfu/CLAUDE.md`](../../mezon-sfu/CLAUDE.md) — `mezon-sfu` protocol/architecture reference
