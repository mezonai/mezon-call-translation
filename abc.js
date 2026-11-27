import { ChannelMessageAck, ChannelMessageSend } from "mezon-js-protobuf";

var useSSL = true; // Enable if server is run with an SSL certificate.
var client = new Client("defaultkey", "dev-mezon.nccsoft.vn", "8088", useSSL);

// SendSystemMessage has no api_index, so the socket (which every client method uses) cannot route it.
// Call mezon-api's HTTP route directly. It listens on TCP (socket.port, 7350) and sends CORS headers
// only with --runtime.env mod=localhost; otherwise it binds a unix socket that a browser cannot reach.
var mezonApiUrl = "http://127.0.0.1:7350";

// export interface ChannelMessageSend {
    // clan_id: string;
    // channel_id: string;
    // content: string;

    // mentions: MessageMention[];
    // attachments: MessageAttachment[];
    // references: MessageRef[];

    // mode: number;
    // anonymous_message: boolean;
    // mention_everyone: boolean;
    // avatar: string;
    // is_public: boolean;
    // code: number;

    // topic_id: string;
    // id: string;


async function sendSystemMessage(token: string, message: Partial<ChannelMessageSend>) {
    const res = await fetch(mezonApiUrl + "/mezon.api.Mezon/SendSystemMessage", {
        method: "POST",
        headers: {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/proto",
            "Accept": "application/proto",
        },
        // finish() returns a Uint8Array over a fresh ArrayBuffer; TS 5.7+ only types it as ArrayBufferLike.
        body: ChannelMessageSend.encode(ChannelMessageSend.fromPartial(message)).finish() as BodyInit,
    });
    if (!res.ok) {
        // 403: bad token or caller not in Valkey set "sys:admin"; 400: bad body, mode or ids; 500: send failed.
        throw new Error("SendSystemMessage HTTP " + res.status);
    }
    return ChannelMessageAck.decode(new Uint8Array(await res.arrayBuffer()));
}