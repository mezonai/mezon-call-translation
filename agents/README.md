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
| 2.5 TTS publish-back (`role:speaker`) | Done — Opus encoder wired in (`internal/opusenc`, `hraban/opus`/cgo), build+smoke-tested with real `libopus` |
| 2.6 STT client + transcript push + on/off control | Done — see the `room_name` caveat below |
| — `register_room`/`unregister_room` with orchestrator | Done (2026-08-18) — was skipped initially, turned out to be load-bearing, see `orchestratorclient.Client.RegisterRoom`'s doc |
| 2.7 VAD | Dropped, not dead code to maintain — see `internal/audiopipeline`'s absence of it and plan.md 2.7 |

**First real end-to-end test against `mezon-sfu` + NATS ran 2026-08-19** (see `LOCAL_TESTING.md`) — `worker-manager` → NATS → spawn → agent dial all confirmed working, but the `join` handshake itself failed to decode (`json: cannot unmarshal string into Go struct field joinedMsg.room of type uint64`). Root cause: `internal/signaling/messages.go`'s wire structs had never been checked against mezon-sfu's actual JSON output (only against the prose description in `mezon-sfu/CLAUDE.md`) -- `room`/`user_id` are sent JSON-quoted even though they're numeric, while `mid_audio`/`mid_video`/`mid_screen` are sent as bare numbers despite reading like they should be strings; every struct in that file had at least one field the wrong way round, which would have broken `room_snapshot`/`peer_joined`/`peer_left`/`peer_updated` too, one at a time, as each was fixed in isolation without checking the others. Fixed all of them at once, each `,string` decision verified against the exact `snprintf` line in `signaling.c`, not guessed -- see `messages.go`'s comments and `messages_test.go` (payloads copied verbatim from the C format strings) for the specifics. Still open: orchestrator_service/record-service/STT/TTS haven't been run against this code yet (deliberately out of scope for the first pass, see `LOCAL_TESTING.md`).

`cmd/agent` does not know or implement how it gets spawned — it only reads its parameters from the environment (below). `cmd/worker-manager` is what owns that, in this repo, for real now (not just on paper in the plan).

## Requirements

- Go 1.24+
- **`libopus-dev` (and `libopusfile-dev`) installed, plus `CGO_ENABLED=1`, to build `cmd/agent`.** `internal/opusenc` links `hraban/opus` (cgo) as of 2026-08-18 — this is the one cgo dependency in an otherwise pure-Go module, and it changes the build/deploy story from before: no more static cross-compiled binary by default, and the *runtime* environment needs `libopus` present too (e.g. `apt-get install libopus0` in a Dockerfile's final stage), not just the build stage. `cmd/worker-manager` doesn't import this package and stays cgo-free. See `internal/opusenc`'s package doc for why this tradeoff was chosen over the cgo-free alternatives.
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
| `AGENT_ROLE` | no | `audience` | `audience` (subscribe-only, record path) or `speaker` (also publishes TTS). |
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
| `AGENT_DISPATCH_SUBJECT` | no | `mezon_sfu_hook_event` | **[2026-08-20, corrected]** BE mezon's Go code names its dispatch constant `SFU_HOOK_EVENT`, but that constant's *value* — the real NATS subject — is the string `"mezon_sfu_hook_event"`. The 2026-08-19 confirmation mistook the identifier for the value and shipped the wrong default (`"SFU_HOOK_EVENT"`), which meant this subscription would never receive a real dispatch. Fixed. One shared subject for both add and delete, routed by the `action` field — mezon-sfu's own participant hook events land on this same subject too as of mezon-sfu commit `88984d6` (its `nats_hook_topic` default also changed to `mezon_sfu_hook_event`). See `mezon-sfu-migration-checklist.md` D4. |
| `AGENT_WORKER_QUEUE_GROUP` | no | `agent-worker-manager` | NATS queue group, so a future multi-instance worker-manager doesn't double-spawn on the same event. |
| `AGENT_BIN_PATH` | no | `agent` next to the `worker-manager` binary | Path to the built `agent` binary to spawn. |
| `AGENT_STOP_TIMEOUT_SECONDS` | no | `20` | How long to wait after SIGTERM before escalating to SIGKILL. Runs in the background (`manager.go`'s `killAfterTimeout`), not blocking a same-room re-`add` -- see the 2026-08-21 bullet below. Sized against the agent's own graceful-shutdown worst case (~16s, see that bullet), not arbitrary -- don't lower without checking that math still holds. |
| `AGENT_USER_ID_BASE` | no | `0` (disabled) | Interim workaround: when nonzero, every agent this manager spawns joins with this one fixed `agent_user_id`, instead of erroring on a dispatch event that never carries one (see the design-decisions bullet below). Not a final scheme — pick a value that won't collide with real Mezon user ids, coordinate with BE mezon. |

`worker-manager` also reads (and passes through unchanged to every spawned `agent`): `SFU_WS_URL`, `SFU_JWT_SECRET`, `AGENT_TOKEN_TTL_SECONDS`, all four `AGENT_RECONNECT_*` vars, `RECORD_SERVICE_*`, `WS_HOST`/`WS_PORT`, `STT_MAX_QUEUE_SIZE`, `ORCHESTRATOR_BASE_URL`, `INTERNAL_API_SECRET`, `TTS_*`, and `LOG_LEVEL` (`internal/workermanager/config.go`'s `agentPassthroughEnvKeys` -- keep in sync with `internal/config.Config` when adding new agent env vars). Per-room `ROOM_ID`/`AGENT_USER_ID`/`AGENT_ROLE` come from the NATS event, not the environment.

```bash
go build -o bin/agent ./cmd/agent
go build -o bin/worker-manager ./cmd/worker-manager
SFU_WS_URL=ws://127.0.0.1:8000/ws SFU_JWT_SECRET=default ./bin/worker-manager
```

Then publish a start event, e.g. via the NATS CLI: `nats pub mezon_sfu_hook_event '{"action":"add","room_id":"1"}'`. Requires `AGENT_USER_ID_BASE` to be set first (see the env var table above and the `agent_user_id` design-decision note below) — without it `Manager.Start` errors instead of spawning.

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
internal/opusenc/            Opus encoder ttsplayer needs, backed by hraban/opus (cgo) -- see package doc for why
internal/reconnect/          bounded exponential backoff for the agent's own session retry
internal/workermanager/      NATS start/stop subscriber + subprocess spawn/kill registry
internal/logging/            process-wide slog logger (shared by both binaries)
proto/recording.proto        source of truth for internal/recordpb -- kept in sync manually with audio-ingestion/record-service's copy, see its header
```

## Design decisions worth knowing before reviewing

- **No RTP `mid` header-extension parsing.** mezon-sfu's docs recommend reading the `mid` RTP extension over trusting SSRC, for anyone writing a raw UDP demuxer. Using pion at the `PeerConnection`/`OnTrack` level, that demuxing is already done — each `OnTrack` callback is already scoped to one negotiated transceiver/mid. Revisit if renegotiation is ever observed to misattribute a track.
- **Bounded reconnect with backoff, in-process.** A WS drop always means a full rejoin (new JWT, new SDP/ICE/DTLS) — `mezon-sfu` ties the media session 1:1 to the WS connection, so there's no "resume", and other participants will see the bot `peer_left`/`peer_joined` regardless of who initiates the rejoin. Given that, `cmd/agent/main.go`'s `run()` retries with exponential backoff (`internal/reconnect`) rather than exiting on the first drop, so a brief network glitch recovers without a full process respawn. It still gives up (process exits non-zero) after `AGENT_RECONNECT_MAX_ATTEMPTS` consecutive failures, so a genuinely broken session (bad secret, deleted room, SFU down) doesn't retry forever silently — that's the point where process-level supervision (worker manager) should take over. A graceful shutdown (SIGTERM, e.g. from a stop command) cancels the context and stops retrying immediately rather than reconnecting into a room the agent is being told to leave.
- **User-id/track-kind mapping comes from the SDP `msid`, not from `room_snapshot`/`peer_joined`.** As of the 2026-08-16 `mezon-sfu` protocol update, `a=msid:u<user_id>-p<peer_id>` is on every track section and pion surfaces it via `TrackRemote.StreamID()`. The WS roster events are still consumed (`UpsertRoster`/`RemovePeer`) but only for `role`/`is_mute`, not identity.
- **`worker-manager` shards Start/Stop dispatch by `room_id`, never calls them straight from the NATS callback.** `internal/workermanager/subscriber.go`'s `handleDispatch` routes every event through `Manager.dispatch` (`shard.go`), which hashes `room_id` (Murmur3 finalizer, not a raw `% numShards` — room_id isn't known to be sequential/uniform, hashing first avoids a permanently hot shard if it isn't) into 1 of 256 fixed worker goroutines. Same room_id always lands on the same shard (so start/stop for one room can never reorder or race each other), different rooms usually land on different shards (so one room's slow `Stop` doesn't block every other room's events -- calling `Manager.Start`/`Stop` synchronously from the NATS callback directly, which is what this replaced, would have meant exactly that). 256 is deliberately generous — the whole pool costs a few KB (idle goroutines blocked on channel receive) — see `shard.go`'s doc for the birthday-math on collision odds at this deployment's current ~10-concurrent-room scale.
- **Shutdown/startup timeouts were deliberately re-tuned 2026-08-19 around one rule: a timeout that guards genuinely necessary work should be generous; a timeout that only guards against network/remote slowness should fail fast, since waiting longer doesn't help a call that's actually stuck.** This pass found the previous numbers had never actually been checked against each other: `ttsplayer.Player.Close` could legitimately wait up to 35s (worse, the real ceiling was unbounded — it replayed the *entire* queued backlog, not just the in-flight utterance, before its 35s safety net even kicked in) while `worker-manager`'s `AGENT_STOP_TIMEOUT_SECONDS` defaulted to 10s — meaning SIGKILL was very likely already firing before graceful shutdown (`UnregisterRoom`/`ReportTTSCompleted`) ever got to run, silently defeating the very flows added earlier the same day. Fixed properly, not just re-timed: `Player.Close` now cancels the in-flight utterance instead of waiting it out (`stopCtx`, aborts the `Synthesize` HTTP call immediately since `ttsclient` already uses `NewRequestWithContext`) and drops anything still queued rather than playing out a backlog during shutdown. Every remaining network-facing timeout in the shutdown/startup path was cut to 2-3s (`rtcagent.PeerAgent.Close`, `recordclient.Forwarder.Close`, `orchestratorclient` calls, `signaling.Dial`'s WS handshake) on the same "guards network slowness, not real work" reasoning. Worst-case at the time: Stop ≈9s (was ≈35-40s+), Start ≈8s (was ≈15s) — see each constant's doc comment for the per-step breakdown. **[2026-08-21 update]** `rtcagent.PeerAgent.Close` grew a second wait stage (`trackWG`/`trackCloseGrace`, 3s) to fix a real truncated-recording bug, pushing `session.close()`'s sequential worst case to ~16s (`peerAgent.Close` 5s + `player.Close` 8s + `orch.UnregisterRoom` 3s) — see `trackCloseGrace`'s doc comment in `internal/rtcagent/peer.go`. `AGENT_STOP_TIMEOUT_SECONDS`'s default moved to 20s to keep covering that, which is safe to do because of the change below.
- **`worker-manager.Stop` frees a room's registry slot immediately (SIGTERM sent, entry deleted) instead of waiting for the process to actually exit.** Before 2026-08-21, `Stop` blocked its caller for up to `AGENT_STOP_TIMEOUT_SECONDS` (SIGTERM grace, then SIGKILL) before returning, and removed the room from `Manager.agents` only once `reap()` saw the process actually exit. Combined with `shard.go` routing every event for one `room_id` through the same goroutine (by design, so start/stop for a room can't race/reorder), a same-room `add` arriving while the previous `delete`'s agent was still mid-graceful-shutdown would queue behind that entire blocking `Stop` call -- even though the room already looked agent-less to everyone else (mezon-sfu treats the WS close, which happens near-instantly on SIGTERM via `cmd/agent/main.go`'s ctx cancellation, as an immediate leave). Worse the higher `AGENT_STOP_TIMEOUT_SECONDS` goes, which is exactly backwards given the bullet above. Fixed: `Stop` now deletes the registry entry and sends SIGTERM synchronously (fast), then hands the wait-then-maybe-SIGKILL part to a detached goroutine (`killAfterTimeout`) that doesn't block anything -- a same-room `Start` queued right behind can spawn a replacement immediately, while the old process's local cleanup (record-service flush, orchestrator reporting, and its eventual SIGKILL if it overruns) keeps running independently in the background. `reap()`'s existing `cur == a` guard (only delete-on-exit if this room's registry entry still points at *this* process) already anticipated exactly this -- a stale agent being reaped after a newer one replaced it -- so no new invariant was introduced, just an existing one put to use.
- **`worker-manager` keeps no state on disk.** The room_id→subprocess map is in-memory only. If `worker-manager` restarts, already-spawned agents keep running (they're started in their own process group — `Setpgid: true` — specifically so a signal to the manager doesn't cascade to them), but the new instance can't `Stop()` them by room_id anymore; a later stop event for that room just logs "not found" and no-ops. Not implemented: a state file + PID-liveness reconciliation on startup would close this gap, deliberately skipped for now rather than half-built before there's a real deployment topology to size it against.
- **`agent_user_id` always comes from config (`AGENT_USER_ID_BASE`), never from the dispatch event.** Confirmed 2026-08-19 against BE mezon's actual dispatch payload (`{"action":"add","room_id":"..."}`, see `internal/workermanager/events.go`'s `dispatchEvent` doc): there is no `agent_user_id` or `role` field to read, so `StartEvent` doesn't even have an `AgentUserID` field anymore -- `Manager.Start` uses `AGENT_USER_ID_BASE` directly as every spawned agent's id (not `+room_id`: checked mezon-sfu's source, `user_id` is scoped per WS connection/room, nothing enforces cross-room uniqueness, so one fixed id for every concurrently running agent is fine, same as a normal bot account), erroring only if `AGENT_USER_ID_BASE` was never configured (see the env var table above). This is still an interim, explicitly-configured choice, not a final decision (BE mezon still needs to either add the field to the payload, or formally agree on the id to use) — see `mezon-sfu-migration-checklist.md` D4/B1, open on purpose. Also confirmed: whatever convention gets picked long-term, it cannot be a LiveKit-style string identity (`agent-<uuid>`) — mezon-sfu's JWT handshake parses the `identity`/`sub` claim with C's `strtoll` (`handshake.c:78-126`), which rejects any value containing a non-digit character anywhere in the string; only a JSON number or a purely-numeric string works.
- **Decode (`internal/audiopipeline`) and encode (`internal/opusenc`) intentionally use different Opus libraries, for different reasons.** Decode uses `pion/opus` (pure Go, RFC 6716 decoder, no C dependency) because it exists and is sufficient -- no encoder support needed there, decoding straight to 16kHz mono (`NewDecoderWithOutput(16000, 1)`) matches the old Python agent's `SAMPLE_RATE`/`CHANNELS` convention without a separate resampling step. Encode uses `hraban/opus` (cgo/`libopus`) because `pion/opus` has no exported `Encoder` at the version pinned here, and of the cgo-free alternatives tried (WASM builds of libopus via `wazero`, e.g. `jj11hh/opus`), none had more than single-digit GitHub stars -- for a component that ships spoken audio into every call, `hraban/opus` (352 stars, the binding the Go/pion ecosystem actually reaches for) won on reputation once `libopus-dev` became available on this dev machine to build/verify against (`sudo apt-get install libopus-dev libopusfile-dev`; `go test ./internal/opusenc/...` does a real encode, not just a compile check). This is the one cgo dependency in an otherwise pure-Go module -- see Requirements above for what that means for building/deploying `cmd/agent`. See `internal/opusenc`'s package doc for the tradeoff written out in full and what swapping it later would look like.
- **A per-track session (Opus decoder + sinks) is closed from the track's own read-loop goroutine (`rtcagent.PeerAgent.OnTrackEnded`), never from the `peer_left` WS event handler.** `OnTrackEnded` and `OnAudioPacket` both fire from the same single goroutine per track, so most of a session's state needs no lock of its own. `peer_left` arrives on a *different* goroutine (the signaling client's dispatch loop); closing a sink from there while the track's read loop might still be calling `SendPCM` would race. This does mean sink cleanup is only as prompt as pion noticing the track ended (e.g. on renegotiation removing that mid) -- not yet verified against a real `mezon-sfu`, see Status. The one deliberate exception to "no lock needed" is the STT sink: `audiopipeline.Bridge.SetSTTEnabled` can attach/detach it from a *third* goroutine (whichever one is handling the `transcript_control` SSE request) at any time, so that one field is mutex-guarded -- see `internal/audiopipeline`'s package doc.
- **TTS requests are queued and played back one at a time, off the SSE listener's goroutine.** `ttsplayer.Player.Speak` enqueues and returns immediately (dropping and logging if the queue's full, never blocking); a single background worker drains it, mirroring the old Python `TTSManager`'s `_request_queue` ("only one audio stream active at a time"). Without this, two `tts_play` requests arriving close together would interleave `WriteSample` calls on the same track (garbled audio), and/or a slow synthesis call would stall the SSE read loop, delaying every other event behind it.
- **`room_name` for STT/transcript endpoints is a guess; `room_id` is not, anymore.** Orchestrator's contract has two identifiers: `room_name` (LiveKit room name / Mezon meeting channel, reused across calls over time) and `room_id` (orchestrator's own stable UUID for *this* call). mezon-sfu only gives this agent a numeric room_id of its own, used as `room_name` everywhere the old contract wanted one (in `push_transcript`, SSE `agent-requests`, the STT session_id) -- the only thing available, not a confirmed-working substitute, verified safe only insofar as neither of those endpoints consults any registry keyed by it (checked against orchestrator_service source, 2026-08-18). `room_id` proper is *not* a guess: `cmd/agent`'s `registerRoomWithOrchestrator` mints a UUID and registers it via `orchestratorclient.Client.RegisterRoom` before joining mezon-sfu, and that UUID (not the mezon-sfu room_id) is what's threaded into `internal/tracksink.RecordSinkFactory`/`internal/ttsplayer` as `room_id` -- see RegisterRoom's doc for why conflating the two would have silently broken recording-event resolution.

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

## Running as a deployed service (dev/prod)

No Docker in dev/prod (same policy as `audio-ingestion/record-service`, PLAN.md D14) — systemd on the host instead. `worker-manager` is the one long-lived unit; it spawns/kills `agent` subprocesses itself (see Design decisions above), so there is no separate unit per agent or per room. Full install steps, unit file, and env file example: **[`deploy/systemd/README.md`](deploy/systemd/README.md)**.

## Background

`mezon-call-translation` used to run entirely on LiveKit (`livekit-agents` Python SDK). Infra is retiring LiveKit in favor of `mezon-sfu`, an in-house SFU with a from-scratch JSON/WebSocket signaling protocol and no LiveKit-compatible SDK. Full context:

- [`../mezon-sfu-migration-summary.md`](../mezon-sfu-migration-summary.md) — quick context dump
- [`../mezon-sfu-migration-plan.md`](../mezon-sfu-migration-plan.md) — task list this module is being built against
- [`../mezon-sfu-migration-checklist.md`](../mezon-sfu-migration-checklist.md) — full LiveKit↔mezon-sfu comparison, protocol catalog, architecture decisions
- [`../../mezon-sfu/CLAUDE.md`](../../mezon-sfu/CLAUDE.md) — `mezon-sfu` protocol/architecture reference
