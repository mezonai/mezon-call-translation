// commands/transcript_status.js

module.exports = async function handleTranscriptStatus(
  client, 
  event, 
  meetingRegistry
) {
  try {
    const userId = event.sender_id;
    
    // 1. Fetch channel với error handling
    let channel;
    try {
      channel = await client.channels.fetch(event.channel_id);
    } catch (err) {
      console.error('Failed to fetch channel:', err);
      const dmClan = await client.clans.fetch('0');
      const user = await dmClan.users.fetch(userId);
      await user.sendDM({
        t: "❌ Không thể truy cập channel. Vui lòng thử lại."
      });
      return;
    }

    const meetingCode = channel.meeting_code;
    
    // 2. Get all meetings stats
    const stats = meetingRegistry.getStats();

    // 3. Build status message
    let statusMsg = `📊 **Transcript Status**\n\n`;
    
    // Kiểm tra voice channel
    if (meetingCode && channel.type === 2) {
      const users = meetingRegistry.getUsers(meetingCode);
      const isUserRegistered = meetingRegistry.hasUser(meetingCode, userId);
      
      statusMsg += `📍 **Current Channel:** ${channel.name || 'Unknown'}\n`;
      statusMsg += `🎙️ **Meeting Code:** \`${meetingCode}\`\n`;
      statusMsg += `👥 **Users listening:** ${users.length}\n`;
      statusMsg += `📢 **Your status:** ${isUserRegistered ? '✅ Enabled' : '❌ Disabled'}\n\n`;
      
      if (users.length > 0) {
        statusMsg += `**Active users:**\n`;
        for (const uid of users.slice(0, 10)) { // Limit to 10
          statusMsg += `• <@${uid}>\n`;
        }
        if (users.length > 10) {
          statusMsg += `• ... and ${users.length - 10} more\n`;
        }
      }
    } else {
      statusMsg += `⚠️ This is not a voice channel\n\n`;
    }
    
    statusMsg += `\n━━━━━━━━━━━━━━━━━\n`;
    statusMsg += `🌐 **Global Stats:**\n`;
    statusMsg += `• Total meetings: ${stats.totalMeetings}\n`;
    statusMsg += `• Total users: ${stats.totalUsers}`;

    // 4. Reply in channel
    const message = await channel.messages.fetch(event.message_id);
    await message.reply({
      t: statusMsg
    });

  } catch (err) {
    console.error('❌ Error in handleTranscriptStatus:', err);
    
    try {
      const dmClan = await client.clans.fetch('0');
      const user = await dmClan.users.fetch(event.sender_id);
      
      await user.sendDM({ 
        t: `❌ **Lỗi Khi Kiểm Tra Status**

⚠️ **Lỗi:** ${err.message}` 
      });
    } catch (dmError) {
      console.error('Failed to send error DM:', dmError);
    }
  }
};