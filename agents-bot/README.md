# agents-bot

A standalone Go service that logs into the Mezon platform as a bot account and is the only component that sees Mezon's own identity/chat events. It exists because `mezon-sfu` is a pure meeting/media SFU with no data channel and no participant-identity API of its own (SFU peers are just numeric user ids) — `agents-bot` fills that gap by joining Mezon's real chat/voice events and exposing what it learns over a small internal HTTP API.

Concretely, it:
- Logs into Mezon via `mezon-sdk-go` and listens for `VoiceJoinedEvent`/`VoiceLeavedEvent` and channel chat messages, building an in-memory `user_id -> {username, display_name, clan_nick, avatar}` cache (`internal/userresolver`) — the only source of Mezon display names, since `mezon-sfu`/the Go `agent` only ever carry the numeric user id.
- Forwards inbound Mezon chat messages to `orchestrator_service` (`POST /api/v2/agent_push_chat_external`) for any channel a room has been registered against, so the meeting UI can show real chat alongside transcripts.
- Serves an HTTP API so other services can resolve user profiles, look up a room's participant roster, and register/unregister which Mezon channel maps to which active call.

Who talks to it:
- `agents/cmd/agent` (`internal/agentsbotclient`) calls `POST /api/rooms/register` / `/unregister` when it joins/leaves a `mezon-sfu` room, and `GET /api/bot/profile`.
- `orchestrator_service` (`services/agents_bot_user_client.py`, `AGENTS_BOT_BASE_URL`) calls `GET /api/users/{id}` / `POST /api/users` to resolve display names for participant rosters and transcripts.

What it does **not** do (anymore): outbound chat (agent/orchestrator -> Mezon chat) used to be routed through `agents-bot` (`POST /api/rooms/{room_name}/chat`), but as of `mezon-sfu` commit `c41e59b` (2026-09-09) the Go `agent` sends outbound chat directly over its own `mezon-sfu` WS session (`signaling.Client.SendRoomMessage`); the old `agents-bot` chat-send endpoint and its client wrapper were deleted as dead code. Inbound chat still has no `mezon-sfu`-level equivalent, so `agents-bot` remains the only path for it.

## Requirements

- Go 1.26.4 (from `go.mod`; not yet verified against an older toolchain).
- A Mezon bot account (app id + token) with permission to see voice and channel events on the target Mezon clans/channels.
- `orchestrator_service` reachable over HTTP if inbound-chat forwarding is needed — otherwise `agents-bot` still starts and serves user/room APIs, chat forwarding just fails per-message (logged, non-fatal, see `forwardChatIfActive`).

## Configuration

All via environment variables (`internal/config/config.go`), loaded from a `.env` file if present (see `.env.example`):

| Var | Required | Default | Meaning |
|---|---|---|---|
| `MEZON_BOT_ID` | yes | — | Mezon bot application id used to log in. |
| `MEZON_BOT_TOKEN` | yes | — | Mezon bot token used to log in. |
| `MEZON_HOST` | no | SDK default (`gw.mezon.ai`, per `.env.example`) | Mezon server host to connect to. |
| `MEZON_PORT` | no | SDK default (`443`) | Mezon server port. |
| `MEZON_USE_SSL` | no | `true` | Any value other than `"false"` is treated as SSL-on. |
| `MEZON_TLS_INSECURE` | no | `false` | Skip TLS certificate verification — only for dev self-signed certs. |
| `GATEWAY_PORT` | no | `8003` | Port the internal HTTP API listens on. |
| `ORCHESTRATOR_BASE_URL` | no | `http://localhost:8002` | Base URL used to POST forwarded chat to `orchestrator_service`. |
| `INTERNAL_API_SECRET` | no | `""` (unset) | Bearer token sent as `Authorization` on the orchestrator push call, if set. |
| `LOG_LEVEL` | no | `info` | Any `log/slog`-parseable level string. |

## Running standalone

```bash
go build ./cmd/agents-bot
MEZON_BOT_ID=... MEZON_BOT_TOKEN=... ./agents-bot
```

Exits non-zero and logs the reason if config validation fails, the Mezon SDK client can't be constructed, Mezon login fails, or the HTTP server fails to start. On SIGINT/SIGTERM it shuts the HTTP server down (5s grace) and returns.

### HTTP API

| Method & path | Purpose |
|---|---|
| `GET /healthz` | Liveness + current user-cache size. |
| `GET /api/bot/profile` | This bot account's own username/avatar (used by `agent` to attribute chat/UI as the bot). |
| `GET /api/users/{id}` | Resolve one cached user profile; optional `?room_name=` for clan-scoped nickname. |
| `POST /api/users` | Batch-resolve `{user_ids, room_name?}` -> profiles + not-found list. |
| `POST /api/rooms/register` | Register `{room_name, room_id}` as an active call so its channel's chat gets forwarded. `room_name` is the Mezon voice-channel id (as string); `room_id` is orchestrator's UUID for the call. |
| `POST /api/rooms/unregister` | Unregister a room; ignored (not an error) if `room_id` doesn't match the currently-registered session, to protect against a stale unregister racing a newer session reusing the same `room_name`. |
| `GET /api/rooms/{room_name}/participants` | Current voice-channel roster (identities + resolved display labels) from the passive cache — not a live Mezon API call. |

## Layout

```
cmd/agents-bot/main.go        entrypoint: env/config -> gateway.New -> gateway.Run, signal-driven shutdown
internal/config/              env var parsing/validation
internal/gateway/             core service: Mezon SDK client + event handlers, HTTP server/handlers, active-room registry
internal/userresolver/        thread-safe in-memory user_id -> profile cache, populated passively from Mezon events
internal/orchestratorclient/  HTTP client for forwarding chat to orchestrator's agent_push_chat_external endpoint
internal/logging/             process-wide slog logger
```

## Design notes / caveats

- **One long-lived process, not a per-room subprocess.** Unlike `agents/cmd/agent` (one process per call, spawned by `agents/cmd/worker-manager`), `agents-bot` logs into Mezon once and stays up for as long as the deployment runs; it multiplexes every concurrently active room through its own event handlers and a `room_name -> RoomInfo` map guarded by a mutex (see `internal/config`'s package doc and `Gateway.activeRooms`).
- **Passive-only user cache, and it can only know what it has seen.** `internal/userresolver`'s package doc explains why: the SDK's `Users.Fetch` only opens DM channels and never returns username/display_name, so the cache is populated solely by observing `VoiceJoinedEvent` and channel messages. A user who joins voice but never speaks still resolves (coarser voice-only name); a user `agents-bot` has never seen in either event returns not-found. Voice data never overwrites a richer chat-derived entry (see `CacheFromVoiceJoined`'s fill-if-empty policy).
- **Chat forwarding is scoped to registered rooms only.** `forwardChatIfActive` forwards a channel message only if that channel id is a currently-registered `room_name`; unregistered/unrelated channel traffic (e.g. other clan chats the bot account can see) is never pushed to orchestrator.
- **Two identifiers per room, not one.** `room_name` is the Mezon voice-channel id as a string (stable per channel, reused across calls over time); `room_id` is orchestrator's own per-call UUID, used to reject a stale unregister from an older session after a channel gets reused.
- **Why this is a separate binary from `agents`/`worker-manager`:** it isn't discoverable from `agents-bot`'s own code — `mezon-sfu` has no data channel, so chat/identity had to be solved by a bot joining Mezon's real chat API directly, architecturally unrelated to the per-room SFU media pipeline `agent`/`worker-manager` implement. It shares the bot's `AGENT_USER_ID` identity with the SFU agents by convention, not by any code-level coupling.

## Open questions

- Go 1.26.4 in `go.mod` has not been cross-checked against what's actually available in CI/deploy images (unlike `agents/README.md`, which specifies a `1.24+` floor deliberately) — worth confirming before relying on this file for a minimum-version claim.
- No Dockerfile/deploy manifest was found for `agents-bot` in this repo at the time of writing; how it's actually deployed (systemd unit, container, etc.) is not documented anywhere and wasn't guessed at here.

