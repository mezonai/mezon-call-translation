// index.js

const dotenv = require("dotenv");
const { MezonClient } = require("mezon-sdk");
dotenv.config();

const handleEnableTranscript = require("./commands/enable_transcript");
const handleDisableTranscript = require("./commands/disable_transcript");
const handleTranscriptStatus = require("./commands/transcript_status");
const MeetingRegistry = require("./services/meeting_registry");
const TranscriptWebSocketServer = require("./services/websocket_server");

// Global instances
const meetingRegistry = new MeetingRegistry();
let wsServer = null;

async function main() {
  const client = new MezonClient({ botId: process.env.APPLICATION_ID, token: process.env.APPLICATION_TOKEN, host: 'dev-mezon.nccsoft.vn', port: '8088', mmnApiUrl: 'https://dev-mmn.nccsoft.vn/mmn-api/', zkApiUrl: 'https://dev-mmn.nccsoft.vn/zk-api/', });
  // Start WebSocket server immediately so clients can connect while bot logs in
  const WS_PORT = process.env.WS_PORT || 8080;
  wsServer = new TranscriptWebSocketServer(WS_PORT, client, meetingRegistry);
  wsServer.start();

  try {
    await client.login();
  } catch (err) {
    console.error("❌ Failed to login:", err);
    process.exit(1);
  }

  client.on("ready", () => {
    console.log(`✅ Bot is ready! Logged in as ${client.user?.username}`);

    // Log stats every 5 minutes
    setInterval(() => {
      const stats = meetingRegistry.getStats();
      console.log(`📊 Stats: ${stats.totalMeetings} meetings, ${stats.totalUsers} users`);
    }, 5 * 60 * 1000);
  });

  client.on("error", (error) => {
    console.error("❌ Client Error:", error);
  });

  client.onChannelMessage(async (event) => {
    try {
      const raw = event?.content?.t;
      console.log(raw)
      const text = typeof raw === 'string' ? raw.toLowerCase().trim() : '';
      if (!text) return;

      // Command routing
      if (text === "*enable_transcript") {
        return handleEnableTranscript(client, event, meetingRegistry);
      }

      if (text === "*disable_transcript") {
        return handleDisableTranscript(client, event, meetingRegistry);
      }

      if (text === "*transcript_status") {
        return handleTranscriptStatus(client, event, meetingRegistry);
      }
    } catch (err) {
      console.error("❌ Error in message handler:", err);
    }
  });
}

// Cleanup on exit
process.on('SIGINT', async () => {
  console.log('\n🛑 Shutting down...');
  if (wsServer) {
    wsServer.stop();
  }
  process.exit();
});

process.on('SIGTERM', async () => {
  console.log('\n🛑 Shutting down...');
  if (wsServer) {
    wsServer.stop();
  }
  process.exit();
});

// Handle uncaught errors
process.on('uncaughtException', (err) => {
  console.error('❌ Uncaught Exception:', err);
});

process.on('unhandledRejection', (err) => {
  console.error('❌ Unhandled Rejection:', err);
});

main()
  .then(() => console.log("🚀 Bot is running"))
  .catch((err) => {
    console.error("❌ Fatal error:", err);
    process.exit(1);
  });