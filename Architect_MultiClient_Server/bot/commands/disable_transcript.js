// commands/disable_transcript.js
const jwt = require('jsonwebtoken');
const fs = require('fs');
const path = require('path');
module.exports = async function handleDisableTranscript(
  client,
  event,
  meetingRegistry
) {
  try {
    // 1. Fetch channel với error handling
    let channel;
    try {
      channel = await client.channels.fetch(event.channel_id);
    } catch (err) {
      console.error('Failed to fetch channel:', err);
      const dmClan = await client.clans.fetch('0');
      const user = await dmClan.users.fetch(event.sender_id);
      await user.sendDM({
        t: "❌ Không thể truy cập channel. Vui lòng thử lại."
      });
      return;
    }

    const meetingCode = channel.meeting_code;

    // 2. Kiểm tra voice channel
    console.log(meetingCode, channel.channel_type);

    if (!meetingCode || channel.channel_type !== 10) {
      const message = await channel.messages.fetch(event.message_id);
      console.log({
        t: "❌ Bạn chỉ có thể tắt transcript trong voice channel."
      });
      return;
    }

    const userId = event.sender_id;

    // 3. Check if user is registered
    if (!meetingRegistry.hasUser(meetingCode, userId)) {
      const message = await channel.messages.fetch(event.message_id);
      console.log({
        t: "⚠️ Bạn chưa bật transcript cho meeting này."
      });
      return;
    }

    // 4. Remove user from registry
    meetingRegistry.removeUser(meetingCode, userId);

    const remainingUsers = meetingRegistry.getUsers(meetingCode);

    console.log(`✅ User ${userId} unregistered from meeting ${meetingCode}`);
    console.log(`   Remaining users: ${remainingUsers.length}`);

    // 5. cancel job dispatch 
    if (!meetingRegistry.getUserCount(meetingCode)) {
      //. Create JWT (optional - nếu vẫn cần call Python API)
      const privateKeyPath = path.join(__dirname, '..', 'private-key.pem');
      const privateKey = fs.readFileSync(privateKeyPath, 'utf8');

      const payload = {
        meetingCode: meetingCode,
        channelId: event.channel_id,
        userId: userId,
      };

      const jwtToken = jwt.sign(payload, privateKey, {
        algorithm: 'RS256',
        expiresIn: '15m'
      });
      try {
        const response = await fetch(`http://nginx:8000/agent/cancel`, {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${jwtToken}`,
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({ room_name: meetingCode })
        });

        const result = await response.json();

        if (!response.ok) {
          throw new Error(result.error || 'API call failed');
        }
      } catch (apiError) {
        console.error('❌ API call failed:', apiError.message);
        throw new Error(`Không thể kết nối đến Python API: ${apiError.message}`);
      }

    }

    // 6. Send confirmation DM
    const dmClan = await client.clans.fetch('0');
    const user = await dmClan.users.fetch(userId);

    await user.sendDM({
      t: `

    ----- 🔕 TRANSCRIPT ĐÃ TẮT -----

    🎙️ Cuộc họp: ${meetingCode}
    🏠 Kênh: ${channel.name || 'Không rõ'}
    
    ✅ Bạn sẽ không còn nhận được bản ghi và transcript từ cuộc họp này.
    `
    });


  } catch (err) {
    console.error('❌ Error in handleDisableTranscript:', err);

    try {
      const dmClan = await client.clans.fetch('0');
      const user = await dmClan.users.fetch(event.sender_id);

      await user.sendDM({
        t: `
      ❌ Lỗi Khi Tắt Transcript
      ⚠️ Lỗi: ${err.message}

      🔧 Vui lòng thử lại sau hoặc liên hệ admin.`
      });
    } catch (dmError) {
      console.error('Failed to send error DM:', dmError);
    }
  }
};