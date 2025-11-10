module.exports = async function handleRemoveAgent(client, event, account) {
    const baseurl = process.env.AGENT_BASE_URL;
    const channel = await client.channels.fetch(event.voice_channel_id);
    const meeting_code = channel.meeting_code;
    const apiUrl = `${baseurl}/api/cancel_dispatch`;
    const payload = {
        account,
        room_name: meeting_code
    };
    try {
        const response = await fetch(apiUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        console.log('Agent removed, API response:', data);
    } catch (error) {
        console.error('Error removing agent:', error);
    }
}