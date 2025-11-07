// Map lưu queue cho từng room_name
const roomQueues = new Map();
const fetch = require('node-fetch');
const baseurl = process.env.AGENT_BASE_URL;
const ttsApiUrl = `${baseurl}/api/tts/speak`;

function pushSSEMessage(room_name, data) {
    if (!roomQueues.has(room_name)) {
        roomQueues.set(room_name, []);
    }
    roomQueues.get(room_name).push(data);
}

async function processQueueAndCallTTS(ttsApiUrl) {
    const account = {
        appid: process.env.APPLICATION_ID,
        token: process.env.APPLICATION_TOKEN
    };
    const language = 'en';
    const voice = 'default';

    while (true) {
        for (const [room_name, queue] of roomQueues.entries()) {
            if (queue.length >= 5) {
                const messages = queue.splice(0, 5);
                const joined = messages.join(' ');
                const response = `I received your message with length five with content is ${joined}`;
                console.log(`[TTS][Batch][Room ${room_name}]`, response);
                try {
                    const payload = {
                        account,
                        room_name,
                        text: response,
                        language,
                        voice
                    };
                    const res = await fetch(ttsApiUrl, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify(payload)
                    });
                    if (!res.ok) {
                        const err = await res.text();
                        throw new Error(`TTS API error: ${res.status} - ${err}`);
                    }
                    const data = await res.json();
                    console.log('TTS API response:', data);
                } catch (error) {
                    console.error('Error calling TTS API:', error);
                }
            }
        }
        await new Promise(r => setTimeout(r, 1000));
    }
}
processQueueAndCallTTS(ttsApiUrl);

module.exports = {
    pushSSEMessage
};
