// test_agent.js (updated for /ws/agent/{session_id} endpoint)
const WebSocket = require('ws');

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

const sessionId = 'BvDcmJeHg';
const clientId = 'John.Doe';
const ws = new WebSocket(`ws://127.0.0.1:8080/ws/agent/${sessionId}`);

ws.on('open', async () => {
  console.log('Connected to bot agent endpoint');

  // Send 20 messages, 200ms apart, with Agent payload format
  for (let i = 1; i <= 20; i++) {
    const payload = {
      text: `${i}/20: test transcript payload`,
      is_final: i % 5 === 0, // mark every 5th as final, others interim
      client_id: clientId,
      session_id: sessionId,
      timestamp: new Date().toISOString()
    };
    ws.send(JSON.stringify(payload));
    await delay(200);
  }
});

ws.on('message', (data) => {
  try {
    const msg = JSON.parse(data.toString());
    console.log('Response:', msg);
  } catch (e) {
    console.log('Raw message:', data.toString());
  }
});

ws.on('error', (err) => {
  console.error('WebSocket error:', err.message || err);
});