// services/websocket_server.js

const WebSocket = require('ws');
function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}
class TranscriptWebSocketServer {
  constructor(port, mezonClient, meetingRegistry) {
    this.port = port;
    this.client = mezonClient;
    this.registry = meetingRegistry;
    this.wss = null;
    // Bộ nhớ cache lưu tin nhắn cuối cùng của mỗi user
    // Cấu trúc: { [userId]: Message }
    this.lastMessages = {};
    this.lastClient = {};

    // Tracking last send times for rate limiting per user
    this.lastSendTimes = new Map();
  }

  start() {
    this.wss = new WebSocket.Server({ port: this.port });

    this.wss.on('connection', (ws, req) => {
      const clientIp = req.socket.remoteAddress;
      const urlPath = req.url || '/';
      console.log(`📡 New WebSocket connection from ${clientIp} url=${urlPath}`);

      // Distinguish agent connections
      if (urlPath.startsWith('/ws/agent/')) {
        const sessionId = urlPath.split('/ws/agent/')[1];
        this.handleAgentConnection(ws, sessionId, clientIp);
        return;
      }

      // Fallback to existing handler for test clients / UI
      ws.on('message', async (message) => {
        await this.handleMessage(ws, message);
      });

      ws.on('close', () => {
        console.log(`📡 WebSocket connection closed from ${clientIp}`);
      });

      ws.on('error', (error) => {
        console.error('WebSocket error:', error);
      });

      // Send welcome message
      ws.send(JSON.stringify({
        type: 'connected',
        message: 'Connected to Mezon Transcript Bot'
      }));
    });

    console.log(`🚀 WebSocket Server started on port ${this.port}`);
  }

  handleAgentConnection(ws, sessionId, clientIp) {
    console.log(`📡 Agent connected for meeting ${sessionId} from ${clientIp}`);

    ws.on('message', async (message) => {
      await this.handleAgentMessage(ws, message, sessionId);
    });

    ws.on('close', () => {
      console.log(`📡 Agent disconnected for meeting ${sessionId}`);
    });

    ws.on('error', (error) => {
      console.error(`WebSocket error for meeting ${sessionId}:`, error);
    });

    // Send welcome
    ws.send(JSON.stringify({
      type: 'connected',
      message: `Agent connected for meeting ${sessionId}`
    }));
  }

  async handleAgentMessage(ws, message, sessionId) {
    try {
      const data = JSON.parse(message.toString());

      // Validate payload from Agent
      if (!data.text || !data.client_id || !data.session_id) {
        ws.send(JSON.stringify({
          type: 'error',
          message: 'Invalid payload. Required: text, client_id, session_id'
        }));
        return;
      }

      // Process both interim and final transcripts; dropping is handled by rate limit below

      const { text, client_id, session_id, timestamp, is_final } = data;

      console.log(`📨 Transcript from agent: [${session_id}] ${client_id}: ${text}`);

      // Get users enabled for this meeting
      const users = this.registry.getUsers(session_id);

      if (users.length === 0) {
        ws.send(JSON.stringify({
          type: 'warning',
          message: `No users enabled transcript for meeting ${session_id}`
        }));
        return;
      }

      // Broadcast with rate limiting (100ms per user). Interim transcripts are allowed but
      // will be dropped if within the 100ms window.
      const results = await this.broadcastTranscriptWithRateLimit(
        session_id,
        client_id,
        text,
        users,
        timestamp,
        Boolean(is_final)
      );

      // Respond to Agent
      ws.send(JSON.stringify({
        type: 'success',
        session_id: session_id,
        sentTo: users.length,
        results: results
      }));

    } catch (error) {
      console.error('Error handling agent message:', error);
      ws.send(JSON.stringify({
        type: 'error',
        message: error.message
      }));
    }
  }

  async broadcastTranscriptWithRateLimit(sessionId, clientId, text, userIds, timestamp, isFinal) {
    const dmClan = await this.client.clans.fetch('0');
    const results = [];
    const time = timestamp
      ? new Date(timestamp).toLocaleTimeString('vi-VN')
      : new Date().toLocaleTimeString('vi-VN');

    const newContent = `🎙️ [${sessionId}] (${time}) — **${clientId}**${isFinal ? ' (final)' : ''}: ${text}`;




    for (const userId of userIds) {
      try {
        const user = await dmClan.users.fetch(userId);


        const now = Date.now();
        const lastSendKey = `${userId}_last_send`;
        const lastSend = this.lastSendTimes?.get(lastSendKey) || 0;
        const delta = now - lastSend;

        // 🚦 Rate limit: chỉ bỏ qua nếu isFinal === false và gửi quá nhanh
        if (delta < 500 && !isFinal) {
          console.log(`⏱️ Rate-limited user ${userId}: delta=${delta}ms (<500ms), skipping`);
          results.push({
            userId: userId,
            status: 'rate_limited',
            message: `Skipped due to rate limit (delta=${delta}ms)`
          });
          continue;
        }

        // ✅ Cập nhật last send time
        if (!this.lastSendTimes) this.lastSendTimes = new Map();
        this.lastSendTimes.set(lastSendKey, now);

        const lastMsg = this.lastMessages[userId];
        const lastClientId = this.lastClient?.[userId];
        const isDifferentClient = lastClientId && lastClientId !== clientId;

        if (!lastMsg || isDifferentClient) {
          // ➤ Nếu chưa có tin nhắn trước hoặc clientId đã thay đổi → gửi tin nhắn mới
          const sentMessage = await user.sendDM({ t: newContent });
          await delay(100);
          const channel = await this.client.channels.fetch(sentMessage?.channel_id);
          const message = await channel.messages.fetch(sentMessage?.message_id);
          this.lastMessages[userId] = message;
          this.lastClient = this.lastClient || {};
          this.lastClient[userId] = clientId; // 🔄 Lưu lại clientId
          console.log(`✅ Sent new message to user ${userId}`);
        } else {
          // ➤ Update tin nhắn cũ
          await lastMsg.update({ t: newContent });
          console.log(`🔁 Updated message for user ${userId}`);
        }

        // Nếu là final thì reset để lần sau gửi lại từ đầu
        if (isFinal) {
          this.lastMessages[userId] = null;
          this.lastClient[userId] = null;
        }

        results.push({ userId, status: 'success' });







      } catch (error) {
        console.error(`❌ Failed to send/update for user ${userId}:`, error.message);
        results.push({
          userId,
          status: 'error',
          error: error.message
        });
      }
    }

    return results;
  }


  async handleMessage(ws, message) {
    try {
      const data = JSON.parse(message.toString());

      // Registration flow
      if (data.type === 'register') {
        if (!data.meetingCode || !data.userId) {
          ws.send(JSON.stringify({ type: 'error', message: 'Invalid register payload. Required: meetingCode, userId' }));
          return;
        }
        const users = this.registry.addUser(data.meetingCode, data.userId);
        console.log(`👤 Registered user ${data.userId} for meeting ${data.meetingCode}. Total: ${users.length}`);
        ws.send(JSON.stringify({ type: 'registered', meetingCode: data.meetingCode, users }));
        return;
      }

      // Transcript flow
      if (data.type === 'transcript' || typeof data.type === 'undefined') {
        if (!data.meetingCode || !data.name_user || !data.text) {
          ws.send(JSON.stringify({
            type: 'error',
            message: 'Invalid message format. Required: meetingCode, name_user, text'
          }));
          return;
        }

        const { meetingCode, name_user, text, timestamp } = data;

        console.log(`📨 Received transcript for meeting ${meetingCode}`);
        console.log(`   Speaker: ${name_user}`);
        console.log(`   Text: ${text}`);

        const users = this.registry.getUsers(meetingCode);
        if (users.length === 0) {
          ws.send(JSON.stringify({
            type: 'warning',
            message: `No users registered for meeting ${meetingCode}`
          }));
          return;
        }

        const results = await this.broadcastTranscript(
          meetingCode,
          name_user,
          text,
          users,
          timestamp
        );

        ws.send(JSON.stringify({
          type: 'success',
          meetingCode: meetingCode,
          sentTo: users.length,
          results: results
        }));
        return;
      }

      ws.send(JSON.stringify({ type: 'error', message: `Unknown message type: ${data.type}` }));

    } catch (error) {
      console.error('Error handling WebSocket message:', error);
      ws.send(JSON.stringify({
        type: 'error',
        message: error.message
      }));
    }
  }

  async broadcastTranscript(meetingCode, name_user, text, userIds, timestamp) {
    const dmClan = await this.client.clans.fetch('0');
    const results = [];

    const time = timestamp
      ? new Date(timestamp).toLocaleTimeString('vi-VN')
      : new Date().toLocaleTimeString('vi-VN');

    const newContent = `🎙️ [${meetingCode}] (${time}) — **${name_user}**: ${text}`;

    for (const userId of userIds) {
      try {
        const user = await dmClan.users.fetch(userId);
        // Kiểm tra xem người dùng này đã có tin nhắn trước đó chưa
        // Kiểm tra last send time (100ms per user)
        const now = Date.now();
        const lastSendKey = `${userId}_last_send`;
        const lastSend = this.lastSendTimes?.get(lastSendKey) || 0;
        const delta = now - lastSend;
        if (delta < 500) {
          console.log(`⏱️ Rate-limited user ${userId}: delta=${delta}ms (<500ms), skipping`)
          results.push({
            userId: userId,
            status: 'rate_limited',
            message: `Skipped due to rate limit (delta=${delta}ms)`
          });
          continue;
        } else {
          // Cập nhật last send time
          if (!this.lastSendTimes) {
            this.lastSendTimes = new Map();
          }
          this.lastSendTimes.set(lastSendKey, now);

        }
        const lastMsg = this.lastMessages[userId];
        if (!lastMsg) {
          // ➤ Gửi tin nhắn đầu tiên
          const sentMessage = await user.sendDM({ t: newContent });
          // console.log('🧾 Sent message full object:', JSON.stringify(sentMessage, null, 2));
          await delay(100);
          const channel = await this.client.channels.fetch(sentMessage?.channel_id);
          const message = await channel.messages.fetch(sentMessage?.message_id);
          this.lastMessages[userId] = message; // Lưu lại để update lần sau
          console.log(`✅ Sent first message to user ${userId}`);
        } else {
          // ➤ Update tin nhắn cũ
          await lastMsg.update({ t: newContent });
          console.log(`🔁 Updated message for user ${userId}`);
        }

        results.push({ userId, status: 'success' });

      } catch (error) {
        console.error(`❌ Failed to send/update for user ${userId}:`, error.message);
        results.push({
          userId,
          status: 'error',
          error: error.message
        });
      }
    }
    return results;
  }

  stop() {
    if (this.wss) {
      this.wss.close();
      console.log('🛑 WebSocket Server stopped');
    }
  }
}

module.exports = TranscriptWebSocketServer;
