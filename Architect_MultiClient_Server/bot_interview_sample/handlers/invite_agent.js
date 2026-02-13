const fetch = require('node-fetch');
const { EventSource } = require('eventsource');
const { pushSSEMessage } = require('./interview_response');
module.exports = async function handleInviteAgent(client, event, account) {
    const baseurl = process.env.AGENT_BASE_URL;
    const channel = await client.channels.fetch(event.voice_channel_id);
    const meeting_code = channel.meeting_code;
    const apiUrl = `${baseurl}/api/create_dispatch`;
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
        const text = await response.text();
        let data;
        try {
            data = JSON.parse(text);
        } catch (e) {
            console.error('Invalid JSON response:', text);
            data = null;
        }
        console.log('Agent invited, API response:', data);
        try {

            const sseUrl = `${baseurl}/api/stream_message?appid=${encodeURIComponent(account.appid)}&token=${encodeURIComponent(account.token)}&room=${encodeURIComponent(meeting_code)}`;
            const es = new EventSource(sseUrl);
            console.log(sseUrl);
            es.onopen = () => console.log(`[SSE][Room ${meeting_code}] connection opened`);
            es.onerror = (err) => console.error(`[SSE][Room ${meeting_code}] error`, err);

            es.onmessage = (event) => {
                console.log(`[SSE][Room ${meeting_code}] data:`, event.data);
                pushSSEMessage(meeting_code, event.data);
            };
        } catch (error) {
            console.error('Error setting up SSE:', error);
        }

    } catch (error) {
        console.error('Error inviting agent:', error);
    }
}

