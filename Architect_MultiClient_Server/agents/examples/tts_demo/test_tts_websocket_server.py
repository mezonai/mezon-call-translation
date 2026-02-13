"""
Simple WebSocket server for testing TTS agent
Simulates sending text messages to the TTS agent
"""
import asyncio
import json
import websockets
from datetime import datetime
import sys

# Configuration
DEFAULT_PORT = 8089
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT

# Store connected clients
clients = {}

async def handle_client(websocket):
    """Handle WebSocket client connection"""
    print(websocket)
    path = websocket.request.path if hasattr(websocket, "request") else "/"
    # Extract session_id from path: /ws/tts/{session_id}
    session_id = path.split('/')[-1] if path.startswith('/ws/tts/') else 'unknown'
    
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ✅ Client connected: {session_id}")
    print(f"   Path: {path}")
    print(f"   Remote address: {websocket.remote_address}")
    
    # Store client
    clients[session_id] = websocket
    
    try:
        # Send welcome message
        welcome = {
            "type": "welcome",
            "session_id": session_id,
            "timestamp": datetime.now().isoformat()
        }
        await websocket.send(json.dumps(welcome))
        print(f"   📤 Sent welcome message")
        
        # Listen for messages from client
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get('type', 'unknown')
                status = data.get('status', '')
                
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 📨 Received from {session_id}: {msg_type} - {status}")
                
                if 'details' in data:
                    print(f"   Details: {data['details']}")
                    
            except json.JSONDecodeError:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 📨 Received plain message: {message}")
            except Exception as e:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error processing message: {e}")
                import traceback
                traceback.print_exc()
                
    except websockets.exceptions.ConnectionClosed as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Client disconnected: {session_id} (code: {e.code}, reason: {e.reason})")
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error in handle_client: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Remove client
        if session_id in clients:
            del clients[session_id]
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔌 Client {session_id} cleaned up")

async def send_test_messages():
    """Continuously send test messages to connected clients in cycles"""
    await asyncio.sleep(3)  # Wait for server to start
    
    test_messages = [
        "Hello! This is a test message from the WebSocket server.",
        "The weather is nice today, isn't it?",
        "Thank you for testing the text-to-speech system.",
        "This message should be converted to voice in the LiveKit room.",
        "Goodbye and have a great day!"
    ]
    
    print("\n" + "="*60)
    print("📤 Starting to send test messages (looping)...")
    print("="*60)
    
    cycle = 1
    while True:
        print(f"\n🔁 Cycle #{cycle} - sending {len(test_messages)} messages")
        
        for i, text in enumerate(test_messages, 1):
            await asyncio.sleep(8)  # Wait 8 seconds between messages
            
            if not clients:
                print(f"\n⚠️ No clients connected. Waiting...")
                continue
            
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 📤 Sending message {i}/{len(test_messages)} (cycle {cycle})")
            print(f"   Text: {text[:60]}{'...' if len(text) > 60 else ''}")
            
            # Send to all connected clients
            disconnected = []
            for session_id, websocket in list(clients.items()):
                try:
                    message = {
                        "type": "tts_request",
                        "text": text,
                        "timestamp": datetime.now().isoformat()
                    }
                    await websocket.send(json.dumps(message))
                    print(f"   ✅ Sent to {session_id}")
                except websockets.exceptions.ConnectionClosed:
                    print(f"   ⚠️ Client {session_id} disconnected")
                    disconnected.append(session_id)
                except Exception as e:
                    print(f"   ❌ Failed to send to {session_id}: {e}")
                    disconnected.append(session_id)
            
            # Cleanup disconnected clients
            for session_id in disconnected:
                if session_id in clients:
                    del clients[session_id]
        
        print(f"\n✅ Completed cycle #{cycle}. Restarting from the first message...")
        cycle += 1

async def main():
    """Start WebSocket server"""
    print("="*60)
    print("🚀 TTS WebSocket Test Server Starting...")
    print("="*60)
    print(f"   Server: ws://localhost:{PORT}")
    print(f"   Path: /ws/tts/{{session_id}}")
    print("="*60)
    
    try:
        # Start server with error handling
        async with websockets.serve(
            handle_client, 
            "localhost", 
            PORT,
            ping_interval=20,
            ping_timeout=10
        ):
            print(f"\n✅ Server running on ws://localhost:{PORT}")
            print("   Waiting for TTS agent to connect...\n")
            print(f"   💡 TIP: Set TTS_WS_URL=ws://localhost:{PORT}/ws/tts/{{session_id}}\n")
            
            # Start sending test messages in background
            asyncio.create_task(send_test_messages())
            
            # Keep server running
            await asyncio.Future()  # Run forever
            
    except OSError as e:
        if "address already in use" in str(e).lower() or e.errno == 10048:
            print(f"\n❌ Error: Port {PORT} is already in use!")
            print("   Solutions:")
            print(f"   1. Wait ~30 seconds for port to be released")
            print(f"   2. Use a different port: python test_tts_websocket_server.py 8089")
            print(f"   3. Kill the process using port {PORT}")
        else:
            print(f"\n❌ OS Error: {e}")
            import traceback
            traceback.print_exc()
    except Exception as e:
        print(f"\n❌ Error starting server: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("\n💡 Usage: python test_tts_websocket_server.py [port]")
    print(f"   Default port: {DEFAULT_PORT}")
    if len(sys.argv) > 1:
        print(f"   Using port: {PORT}\n")
    else:
        print(f"   Using default port: {PORT}\n")
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️ Server stopped by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
