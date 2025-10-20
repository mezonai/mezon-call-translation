// commands/enable_transcript.js

const jwt = require('jsonwebtoken');
const fs = require('fs');
const path = require('path');

module.exports = async function handleEnableTranscript(
  client,
  event,
  meetingRegistry
) {
  try {
    // 1. Fetch channel
    const channel = await client.channels.fetch(event.channel_id);
    const meetingCode = channel.meeting_code;

    if (!meetingCode) {
      const message = await channel.messages.fetch(event.message_id);
      await message.reply({
        t: "❌ Bạn chỉ có thể bật transcript trong voice channel."
      });
      return;
    }

    const userId = event.sender_id;

    // 2. Check if already registered
    if (meetingRegistry.hasUser(meetingCode, userId)) {
      const message = await channel.messages.fetch(event.message_id);
      await message.reply({
        t: "⚠️ Bạn đã bật transcript cho meeting này rồi."
      });
      return;
    }

    // 3. Create JWT (optional - nếu vẫn cần call Python API)
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

    // 4. Call Python API (optional)
    try {
      const response = await fetch('http://nginx:8000/agent/join', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${jwtToken}`,
          'Content-Type': 'application/json'
        }
      });

      const result = await response.json();

      if (!response.ok) {
        throw new Error(result.error || 'API call failed');
      }
    } catch (apiError) {
      // Ghi log chi tiết và ném lỗi ra ngoài
      console.error('❌ API call failed:', apiError.message);
      throw new Error(`Không thể kết nối đến Python API: ${apiError.message}`);
    }

    // 5. Add to meeting registry
    const users = meetingRegistry.addUser(meetingCode, userId);

    console.log(`✅ User ${userId} registered for meeting ${meetingCode}`);
    console.log(`   Total users in meeting: ${users.length}`);

    // 6. Send confirmation DM
    const dmClan = await client.clans.fetch('0');
    const user = await dmClan.users.fetch(userId);

    await user.sendDM({
      t: `
    ----- TRANSCRIPT ĐÃ BẬT ----- 

    📋 Cuộc họp: ${meetingCode}
    🏠 Kênh: ${channel.name || 'Không rõ'}
    👥 Thành viên: ${users.length}
    
    Bạn sẽ nhận bản ghi âm & văn bản theo thời gian thực.  
    Tắt bằng lệnh: *disable_transcript
    `
    });



  } catch (err) {
    console.error('❌ Error in handleEnableTranscript:', err);

    try {
      const dmClan = await client.clans.fetch('0');
      const user = await dmClan.users.fetch(event.sender_id);

      await user.sendDM({
        t: `❌ Lỗi Khi Bật Transcript
        ⚠️ Lỗi: ${err.message}

        🔧 Vui lòng thử lại sau hoặc liên hệ admin.`
      });
    } catch (dmError) {
      console.error('Failed to send error DM:', dmError);
    }
  }
};