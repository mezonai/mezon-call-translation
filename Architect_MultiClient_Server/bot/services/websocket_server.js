// services/websocket_server.js

const WebSocket = require('ws');

class TranscriptWebSocketServer {
  constructor(port, mezonClient, meetingRegistry) {
    this.port = port;
    this.client = mezonClient;
    this.registry = meetingRegistry;
    this.wss = null;
  }

  start() {
    this.wss = new WebSocket.Server({ port: this.port });

    this.wss.on('connection', (ws, req) => {
      const clientIp = req.socket.remoteAddress;
      console.log(`📡 New WebSocket connection from ${clientIp}`);

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

      // Transcript flow (explicit type 'transcript' or default when type is undefined)
      if (data.type === 'transcript' || typeof data.type === 'undefined') {
        // Validate message format
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

        // Get users in this meeting
        const users = this.registry.getUsers(meetingCode);

        if (users.length === 0) {
          ws.send(JSON.stringify({
            type: 'warning',
            message: `No users registered for meeting ${meetingCode}`
          }));
          return;
        }

        // Broadcast to all users
        const results = await this.broadcastTranscript(
          meetingCode,
          name_user,
          text,
          users,
          timestamp
        );

        // Send response to agent
        ws.send(JSON.stringify({
          type: 'success',
          meetingCode: meetingCode,
          sentTo: users.length,
          results: results
        }));
        return;
      }

      // Unknown type
      ws.send(JSON.stringify({ type: 'error', message: `Unknown message type: ${data.type}` }));
      return;

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

    // Format message
    const time = timestamp 
      ? new Date(timestamp).toLocaleTimeString('vi-VN')
      : new Date().toLocaleTimeString('vi-VN');

    const dmMessage = {
        t: `🎙️ [${meetingCode}] (${time}) — **${name_user}**: ${text}`
    };

    // Send to each user
    for (const userId of userIds) {
      try {
        const user = await dmClan.users.fetch(userId);
        await user.sendDM(dmMessage);
        
        results.push({
          userId: userId,
          status: 'success'
        });

        console.log(`   ✅ Sent to user ${userId}`);

        // Delay to avoid rate limiting
        await new Promise(resolve => setTimeout(resolve, 500));

      } catch (error) {
        console.error(`   ❌ Failed to send to user ${userId}:`, error.message);
        results.push({
          userId: userId,
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