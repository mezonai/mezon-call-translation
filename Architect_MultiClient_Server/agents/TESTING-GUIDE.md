# Agent-Bot WebSocket Communication Testing Guide

## Overview

This guide covers testing the new Agent-Bot WebSocket communication system that replaces LiveKit Data Channel for transcript forwarding.

## Architecture

```
LiveKit Room → Agent (Python) → Bot WebSocket → Bot (Node.js) → Users (DM)
```

- **Agent**: Connects to Bot via WebSocket at `ws://bot:8080/ws/agent/{session_id}`
- **Bot**: Receives transcripts and broadcasts to enabled users
- **Rate Limiting**: 100ms per user to prevent spam
- **Auto-reconnect**: Agent automatically reconnects if Bot goes offline

## Test Scripts

### 1. Agent WebSocket Client Tests
```bash
cd agents
python test_agent_websocket_client.py
```

**Tests:**
- Client creation and configuration
- Connection failure handling
- Send transcript when not connected
- Interim transcript filtering
- Disconnect handling
- Payload format validation

### 2. Bot WebSocket Server Tests
```bash
cd agents
python test_bot_websocket.py
```

**Tests:**
- Basic connection to Bot
- Transcript flow from Agent to Bot
- Rate limiting (100ms per user)
- Reconnection scenarios

### 3. Full Flow Integration Tests
```bash
cd agents
python test_full_flow.py
```

**Tests:**
- Complete Agent → Bot communication
- Error handling scenarios
- Reconnection testing

### 4. Run All Tests
```bash
cd agents
python run_tests.py
```

## Manual Testing

### Prerequisites

1. **Start Bot Server:**
```bash
cd bot
npm start
```

2. **Start Agent:**
```bash
cd agents
python main.py
```

### Test Cases

#### Test Case 1: Basic Connection
1. Start Bot server
2. Start Agent
3. **Expected:**
   - Agent log: "Bot WebSocket connected for meeting {meeting_code}"
   - Bot log: "Agent connected for meeting {meeting_code}"

#### Test Case 2: Transcript Flow
1. User calls `*enable_transcript` in meeting
2. Agent joins room and streams audio
3. Vosk returns transcript
4. Agent forwards to Bot
5. Bot sends DM to user

**Expected:**
- Agent log: "Sent transcript to Bot: [participant_id] hello..."
- Bot log: "Transcript from agent: [meeting_code] participant_id: hello"
- User receives DM with transcript content

#### Test Case 3: Rate Limiting
1. Agent sends multiple transcripts rapidly (< 100ms interval)
2. Bot only sends DM every 100ms

**Expected:**
- Bot log: "rate_limited" for skipped transcripts
- User receives DMs with >= 100ms intervals

#### Test Case 4: Reconnection
1. Agent running and connected
2. Restart Bot server
3. Agent automatically reconnects

**Expected:**
- Agent log: "Reconnecting to Bot (attempt 1/5)"
- Agent log: "Bot WebSocket connected for meeting {meeting_code}"

## Configuration

### Environment Variables

Add to your `.env` file:

```bash
# Bot WebSocket Configuration
BOT_WS_HOST=bot
BOT_WS_PORT=8080

# Vosk WebSocket Configuration (existing)
WS_HOST=vosk
WS_PORT=8000
```

### Docker Configuration

Ensure Bot service is accessible from Agent:

```yaml
# docker-compose.yml
services:
  bot:
    ports:
      - "8080:8080"
  
  agent:
    depends_on:
      - bot
```

## Troubleshooting

### Common Issues

1. **Agent cannot connect to Bot**
   - Check Bot is running: `cd bot && npm start`
   - Check Bot port 8080 is accessible
   - Check environment variables: `BOT_WS_HOST`, `BOT_WS_PORT`

2. **Transcripts not reaching users**
   - Check Bot logs for "Agent connected" message
   - Verify users are registered for meeting
   - Check rate limiting logs

3. **Connection drops frequently**
   - Check network stability
   - Verify Bot server health
   - Check Agent reconnection logs

### Debug Logs

Enable detailed logging:

```python
# In agents/src/core/bot_websocket_client.py
logging.getLogger("bot_ws_client").setLevel(logging.DEBUG)
```

### Monitoring

Check these log patterns:

**Agent logs:**
- `"Bot WebSocket connected for meeting {meeting_code}"`
- `"Sent transcript to Bot: [participant_id] {text}"`
- `"Reconnecting to Bot (attempt {n}/5)"`

**Bot logs:**
- `"Agent connected for meeting {meeting_code}"`
- `"Transcript from agent: [meeting_code] participant_id: {text}"`
- `"✅ Sent to user {userId}"`

## Success Criteria

✅ **Agent connects successfully to Bot WebSocket**
✅ **Transcripts forwarded real-time from Agent → Bot**
✅ **Bot broadcasts transcripts to enabled users**
✅ **Rate limiting 100ms works correctly**
✅ **Auto-reconnection when Bot restarts**
✅ **No memory leaks or connection leaks**
✅ **Comprehensive logging for debugging**

## Performance Notes

- **1 meeting = 1 WebSocket connection** (persistent)
- **No transcript buffering** when Bot offline (drop immediately)
- **Rate limiting 100ms** per user prevents spam
- **Exponential backoff** for reconnection (1s, 2s, 4s, 8s, 16s)
- **Max 5 reconnection attempts** before giving up

## Next Steps

After successful testing:

1. Deploy to production environment
2. Monitor logs for any issues
3. Set up monitoring alerts for connection failures
4. Consider adding metrics for transcript delivery rates

