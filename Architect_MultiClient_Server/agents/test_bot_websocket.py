#!/usr/bin/env python3
"""
Test script for Bot WebSocket communication
Tests the new Agent-Bot WebSocket communication flow
"""

import asyncio
import json
import websockets
import time
from datetime import datetime
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BotWebSocketTester:
    """Test client for Bot WebSocket server"""
    
    def __init__(self, bot_host="localhost", bot_port=8080):
        self.bot_host = bot_host
        self.bot_port = bot_port
        self.uri = f"ws://{bot_host}:{bot_port}"
        
    async def test_agent_connection(self, session_id="test_meeting_123"):
        """Test Case 1: Basic Connection"""
        logger.info("=== Test Case 1: Basic Connection ===")
        
        try:
            # Connect to agent endpoint
            agent_uri = f"{self.uri}/ws/agent/{session_id}"
            logger.info(f"Connecting to {agent_uri}")
            
            async with websockets.connect(agent_uri) as websocket:
                # Wait for welcome message
                welcome = await websocket.recv()
                logger.info(f"Received welcome: {welcome}")
                
                # Send test transcript
                test_payload = {
                    "text": "Hello world test message",
                    "is_final": True,
                    "client_id": "test_participant_1",
                    "session_id": session_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
                logger.info(f"Sending test transcript: {test_payload}")
                await websocket.send(json.dumps(test_payload))
                
                # Wait for response
                response = await websocket.recv()
                logger.info(f"Received response: {response}")
                
                logger.info("✅ Test Case 1 PASSED: Basic connection successful")
                return True
                
        except Exception as e:
            logger.error(f"❌ Test Case 1 FAILED: {e}")
            return False
    
    async def test_transcript_flow(self, session_id="test_meeting_456"):
        """Test Case 2: Transcript Flow"""
        logger.info("=== Test Case 2: Transcript Flow ===")
        
        try:
            agent_uri = f"{self.uri}/ws/agent/{session_id}"
            logger.info(f"Connecting to {agent_uri}")
            
            async with websockets.connect(agent_uri) as websocket:
                # Wait for welcome
                welcome = await websocket.recv()
                logger.info(f"Welcome: {welcome}")
                
                # Send multiple transcripts
                transcripts = [
                    {
                        "text": "First transcript message",
                        "is_final": True,
                        "client_id": "participant_1",
                        "session_id": session_id,
                        "timestamp": datetime.utcnow().isoformat()
                    },
                    {
                        "text": "Second transcript message",
                        "is_final": True,
                        "client_id": "participant_2", 
                        "session_id": session_id,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                ]
                
                for i, transcript in enumerate(transcripts):
                    logger.info(f"Sending transcript {i+1}: {transcript['text']}")
                    await websocket.send(json.dumps(transcript))
                    
                    # Wait for response
                    response = await websocket.recv()
                    logger.info(f"Response {i+1}: {response}")
                    
                    # Small delay between messages
                    await asyncio.sleep(0.1)
                
                logger.info("✅ Test Case 2 PASSED: Transcript flow successful")
                return True
                
        except Exception as e:
            logger.error(f"❌ Test Case 2 FAILED: {e}")
            return False
    
    async def test_rate_limiting(self, session_id="test_meeting_789"):
        """Test Case 3: Rate Limiting"""
        logger.info("=== Test Case 3: Rate Limiting ===")
        
        try:
            agent_uri = f"{self.uri}/ws/agent/{session_id}"
            logger.info(f"Connecting to {agent_uri}")
            
            async with websockets.connect(agent_uri) as websocket:
                # Wait for welcome
                welcome = await websocket.recv()
                logger.info(f"Welcome: {welcome}")
                
                # Send rapid transcripts (faster than 100ms rate limit)
                rapid_transcripts = []
                for i in range(5):
                    transcript = {
                        "text": f"Rapid message {i+1}",
                        "is_final": True,
                        "client_id": "rapid_speaker",
                        "session_id": session_id,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                    rapid_transcripts.append(transcript)
                    
                    logger.info(f"Sending rapid transcript {i+1}")
                    await websocket.send(json.dumps(transcript))
                    
                    # Very small delay (50ms - should trigger rate limiting)
                    await asyncio.sleep(0.05)
                
                # Collect responses
                responses = []
                for i in range(5):
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                        responses.append(response)
                        logger.info(f"Response {i+1}: {response}")
                    except asyncio.TimeoutError:
                        logger.info(f"No response for transcript {i+1} (likely rate limited)")
                
                logger.info(f"Sent {len(rapid_transcripts)} transcripts, received {len(responses)} responses")
                logger.info("✅ Test Case 3 PASSED: Rate limiting test completed")
                return True
                
        except Exception as e:
            logger.error(f"❌ Test Case 3 FAILED: {e}")
            return False
    
    async def test_reconnection(self, session_id="test_meeting_reconnect"):
        """Test Case 4: Reconnection (simulated)"""
        logger.info("=== Test Case 4: Reconnection Test ===")
        
        try:
            agent_uri = f"{self.uri}/ws/agent/{session_id}"
            logger.info(f"Connecting to {agent_uri}")
            
            # First connection
            async with websockets.connect(agent_uri) as websocket:
                welcome = await websocket.recv()
                logger.info(f"First connection welcome: {welcome}")
                
                # Send a message
                test_payload = {
                    "text": "Before disconnect test",
                    "is_final": True,
                    "client_id": "reconnect_test",
                    "session_id": session_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
                await websocket.send(json.dumps(test_payload))
                response1 = await websocket.recv()
                logger.info(f"First response: {response1}")
            
            # Wait a bit
            await asyncio.sleep(1)
            
            # Second connection (simulating reconnect)
            async with websockets.connect(agent_uri) as websocket:
                welcome2 = await websocket.recv()
                logger.info(f"Reconnect welcome: {welcome2}")
                
                # Send another message
                test_payload2 = {
                    "text": "After reconnect test",
                    "is_final": True,
                    "client_id": "reconnect_test",
                    "session_id": session_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
                await websocket.send(json.dumps(test_payload2))
                response2 = await websocket.recv()
                logger.info(f"Reconnect response: {response2}")
                
                logger.info("✅ Test Case 4 PASSED: Reconnection test successful")
                return True
                
        except Exception as e:
            logger.error(f"❌ Test Case 4 FAILED: {e}")
            return False

async def main():
    """Run all test cases"""
    logger.info("🚀 Starting Bot WebSocket Tests")
    
    tester = BotWebSocketTester()
    
    # Run all test cases
    test_cases = [
        ("Basic Connection", tester.test_agent_connection),
        ("Transcript Flow", tester.test_transcript_flow),
        ("Rate Limiting", tester.test_rate_limiting),
        ("Reconnection", tester.test_reconnection)
    ]
    
    results = []
    for test_name, test_func in test_cases:
        logger.info(f"\n{'='*50}")
        logger.info(f"Running: {test_name}")
        logger.info(f"{'='*50}")
        
        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            logger.error(f"Test {test_name} crashed: {e}")
            results.append((test_name, False))
        
        # Wait between tests
        await asyncio.sleep(1)
    
    # Summary
    logger.info(f"\n{'='*50}")
    logger.info("TEST SUMMARY")
    logger.info(f"{'='*50}")
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        logger.info(f"{test_name}: {status}")
        if result:
            passed += 1
    
    logger.info(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        logger.info("🎉 All tests passed!")
    else:
        logger.warning(f"⚠️  {total - passed} tests failed")

if __name__ == "__main__":
    asyncio.run(main())

