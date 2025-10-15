// test_agent.js
const WebSocket = require('ws');

const ws = new WebSocket('ws://127.0.0.1:8080');

ws.on('open', () => {
  console.log('Connected to bot');

  // 1) Register a user to receive transcripts for the meeting (uses existing registry logic on server)
  ws.send(JSON.stringify({
    type: 'register',
    meetingCode: 'BvDcmJeHg',
    userId: '1946168514767228928'
  }));

});

ws.on('message', (data) => {
  const msg = JSON.parse(data.toString());
  console.log('Response:', msg);

  // After server confirms registration, send multiple transcripts quickly
  if (msg.type === 'registered') {
    const messages = [
      'First message from John',
      'Second quick update',
      'Third note with more details',
      'Fourth: testing concurrency',
      'Fifth and final burst'
    ];

    // Fire off all messages nearly at once
    messages.forEach((text, idx) => {
      ws.send(JSON.stringify({
        type: 'transcript',
        meetingCode: 'BvDcmJeHg',
        name_user: 'John Doe',
        text: `${idx + 1}/${messages.length}: ${text}`,
        timestamp: new Date().toISOString()
      }));
    });
  }
});

ws.on('error', (err) => {
  console.error('WebSocket error:', err.message || err);
});