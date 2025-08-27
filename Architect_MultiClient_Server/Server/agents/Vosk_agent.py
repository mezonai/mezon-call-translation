import asyncio
import json
import websockets
from livekit import agents, rtc
from dotenv import load_dotenv
import time
load_dotenv()

BACKEND_WS_URL = "ws://localhost:8001/ws/vosk/"  # URL backend
SESSION_ID = "session123"  # Ví dụ session ID, có thể generate động
TRANSCRIPT = True
TRANSLATION = False
LANGUAGE = "en"

async def entrypoint(ctx: agents.JobContext):
    await ctx.connect()
    disconnected = asyncio.Event()

    
    async def manage_speaker_connection(track: rtc.RemoteAudioTrack, speaker_id: str):
        """
        Kết nối tới backend WebSocket một lần và gửi audio liên tục.
        Nhận dữ liệu phản hồi từ backend (ví dụ transcript) song song.
        """
        uri = (
            f"{BACKEND_WS_URL}?client_id={speaker_id}"
            f"&session_id={SESSION_ID}"
            f"&transcript={str(TRANSCRIPT).lower()}"
            f"&translation={str(TRANSLATION).lower()}"
            f"&language={str(LANGUAGE).lower()}"
        )
        await ctx.room.local_participant.set_name("Certified Yapper")
        try:
            async with websockets.connect(uri) as websocket:
                print(f"✅ WebSocket connected for participant {speaker_id}")

                # Task nhận dữ liệu từ backend
                async def receive_from_backend():
                    latest_partial = ""
                    async for message in websocket:
                        try:
                            data = json.loads(message)

                            if data.get("type") == "transcripts":
                                text = data.get("text", "").strip()
                                is_final = bool(data.get("is_final", False))

                                if not text:
                                    continue

                                if is_final:
                                    print(f"Final is: {text}")
                                    if text == latest_partial:
                                        latest_partial = ""  # reset after final
                                        continue
                                        
                                    else:
                                         await ctx.room.local_participant.send_text(
                                            text=text,
                                            topic="lk.chat",
                                        )
                                else:
                                    if text != latest_partial:
                                        print(f"Partial: {text}")
                                        await ctx.room.local_participant.send_text(
                                            text=text,
                                            topic="lk.chat",
                                        )
                                        latest_partial = text

                                                                    
                        except json.JSONDecodeError as e:
                            print("Invalid JSON received:", e)

                recv_task = asyncio.create_task(receive_from_backend())

                # Stream audio từ LiveKit
                stream = rtc.AudioStream.from_track(track=track, sample_rate=16000, num_channels=1)
                async for event in stream:
                    frame = event.frame
                    pcm16 = bytes(frame.data)  # Convert C array -> bytes
                    try:
                        await websocket.send(pcm16)
                    except websockets.ConnectionClosed as e:
                        print(f"❌ WebSocket closed for {speaker_id}: {e}")
                        break

                print(f"⏹️ Finished audio stream for {speaker_id}")
                recv_task.cancel()

        except Exception as e:
            print(f"❌ Error connecting or sending audio for {speaker_id}: {e}")

    def on_track_subscribed(track: rtc.RemoteAudioTrack, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        speaker_id = participant.identity
        print(f"🎙️ Subscribed track {track.sid} from participant {speaker_id}")
        asyncio.create_task(manage_speaker_connection(track, speaker_id))

    def on_disconnected():
        print("Room disconnected")
        disconnected.set()

    ctx.room.on("track_subscribed", on_track_subscribed)
    ctx.room.on("disconnected", on_disconnected)

    await disconnected.wait()


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
