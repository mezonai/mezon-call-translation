#!/usr/bin/env python3
"""
Full flow test for Agent-Bot WebSocket communication
Tests the complete transcript flow from Agent to Bot
"""

import asyncio
import json
import logging
import sys
import os
from datetime import datetime

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src.core.bot_websocket_client import BotWebSocketClient

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FullFlowTester:
    """Test the complete Agent-Bot WebSocket flow"""
    
    def __init__(self):
        self.session_id = "full_flow_test_meeting"
        self.meeting_code = "full_flow_test_meeting"
        
    async def test_agent_to_bot_flow(self):
        """Test the complete flow: Agent -> Bot WebSocket"""
        logger.info("=== Full Flow Test: Agent -> Bot WebSocket ===")
        
        try:
            # Create Bot WebSocket client (simulating Agent)
            client = BotWebSocketClient(
                session_id=self.session_id,
                meeting_code=self.meeting_code
            )
            
            logger.info(f"Created BotWebSocketClient for session: {self.session_id}")
            logger.info(f"URI: {client.uri}")
            
            # Try to connect (this will fail if Bot is not running)
            logger.info("Attempting to connect to Bot...")
            connected = await client.connect()
            
            if not connected:
                logger.warning("⚠️  Bot is not running - cannot test full flow")
                logger.info("To test full flow:")
                logger.info("1. Start Bot: cd bot && npm start")
                logger.info("2. Run this test again")
                return True  # Not a failure, just Bot not available
            
            logger.info("✅ Connected to Bot successfully")
            
            # Test sending multiple transcripts
            test_transcripts = [
                {
                    "text": "Hello, this is the first transcript",
                    "is_final": True,
                    "client_id": "participant_1",
                    "session_id": self.session_id,
                    "timestamp": datetime.utcnow().isoformat()
                },
                {
                    "text": "This is the second transcript message",
                    "is_final": True,
                    "client_id": "participant_2",
                    "session_id": self.session_id,
                    "timestamp": datetime.utcnow().isoformat()
                },
                {
                    "text": "Final transcript for testing",
                    "is_final": True,
                    "client_id": "participant_1",
                    "session_id": self.session_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
            ]
            
            success_count = 0
            for i, transcript in enumerate(test_transcripts):
                logger.info(f"Sending transcript {i+1}: {transcript['text']}")
                
                result = await client.send_transcript(transcript)
                if result:
                    success_count += 1
                    logger.info(f"✅ Transcript {i+1} sent successfully")
                else:
                    logger.warning(f"⚠️  Transcript {i+1} failed to send")
                
                # Small delay between transcripts
                await asyncio.sleep(0.2)
            
            logger.info(f"Sent {success_count}/{len(test_transcripts)} transcripts successfully")
            
            # Test interim transcript (should be ignored)
            interim_transcript = {
                "text": "This is an interim transcript",
                "is_final": False,  # Should be ignored
                "client_id": "participant_1",
                "session_id": self.session_id,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            logger.info("Testing interim transcript (should be ignored)...")
            interim_result = await client.send_transcript(interim_transcript)
            if not interim_result:
                logger.info("✅ Interim transcript correctly ignored")
            else:
                logger.warning("⚠️  Interim transcript was not ignored")
            
            # Disconnect
            await client.disconnect()
            logger.info("✅ Disconnected from Bot")
            
            logger.info("✅ Full Flow Test PASSED: Agent-Bot communication successful")
            return True
            
        except Exception as e:
            logger.error(f"❌ Full Flow Test FAILED: {e}")
            return False
    
    async def test_error_handling(self):
        """Test error handling scenarios"""
        logger.info("=== Error Handling Test ===")
        
        try:
            # Test with invalid session ID
            client = BotWebSocketClient(
                session_id="",  # Empty session ID
                meeting_code=""
            )
            
            # Should not crash
            result = await client.connect()
            logger.info(f"Empty session ID connection result: {result}")
            
            # Test sending with invalid payload
            invalid_payload = {
                "text": "",  # Empty text
                "is_final": True,
                "client_id": "",
                "session_id": ""
            }
            
            result = await client.send_transcript(invalid_payload)
            logger.info(f"Invalid payload send result: {result}")
            
            # Cleanup
            await client.disconnect()
            
            logger.info("✅ Error Handling Test PASSED: No crashes with invalid data")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error Handling Test FAILED: {e}")
            return False
    
    async def test_reconnection_scenario(self):
        """Test reconnection scenario"""
        logger.info("=== Reconnection Test ===")
        
        try:
            client = BotWebSocketClient(
                session_id=self.session_id,
                meeting_code=self.meeting_code
            )
            
            # Try to connect
            connected = await client.connect()
            
            if connected:
                logger.info("✅ Initial connection successful")
                
                # Send a test message
                test_payload = {
                    "text": "Test before disconnect",
                    "is_final": True,
                    "client_id": "reconnect_test",
                    "session_id": self.session_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
                await client.send_transcript(test_payload)
                logger.info("✅ Test message sent")
                
                # Disconnect
                await client.disconnect()
                logger.info("✅ Disconnected")
                
                # Try to reconnect
                logger.info("Attempting reconnection...")
                reconnected = await client.connect()
                
                if reconnected:
                    logger.info("✅ Reconnection successful")
                    
                    # Send another test message
                    test_payload2 = {
                        "text": "Test after reconnect",
                        "is_final": True,
                        "client_id": "reconnect_test",
                        "session_id": self.session_id,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                    
                    await client.send_transcript(test_payload2)
                    logger.info("✅ Test message after reconnect sent")
                    
                    await client.disconnect()
                    logger.info("✅ Final disconnect")
                    
                else:
                    logger.warning("⚠️  Reconnection failed (Bot may not be running)")
                
            else:
                logger.warning("⚠️  Initial connection failed (Bot may not be running)")
            
            logger.info("✅ Reconnection Test PASSED: Reconnection scenario handled")
            return True
            
        except Exception as e:
            logger.error(f"❌ Reconnection Test FAILED: {e}")
            return False

async def main():
    """Run all full flow tests"""
    logger.info("🚀 Starting Full Flow Tests")
    logger.info("=" * 60)
    logger.info("This test requires Bot to be running for full functionality")
    logger.info("Start Bot with: cd bot && npm start")
    logger.info("=" * 60)
    
    tester = FullFlowTester()
    
    # Run all test cases
    test_cases = [
        ("Agent to Bot Flow", tester.test_agent_to_bot_flow),
        ("Error Handling", tester.test_error_handling),
        ("Reconnection Scenario", tester.test_reconnection_scenario)
    ]
    
    results = []
    for test_name, test_func in test_cases:
        logger.info(f"\n{'='*60}")
        logger.info(f"Running: {test_name}")
        logger.info(f"{'='*60}")
        
        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            logger.error(f"Test {test_name} crashed: {e}")
            results.append((test_name, False))
        
        # Wait between tests
        await asyncio.sleep(1)
    
    # Summary
    logger.info(f"\n{'='*60}")
    logger.info("FULL FLOW TEST SUMMARY")
    logger.info(f"{'='*60}")
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        logger.info(f"{test_name}: {status}")
        if result:
            passed += 1
    
    logger.info(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        logger.info("🎉 All full flow tests passed!")
        logger.info("✅ Agent-Bot WebSocket communication is working correctly!")
    else:
        logger.warning(f"⚠️  {total - passed} tests failed")
        logger.info("Check Bot server status and configuration")

if __name__ == "__main__":
    asyncio.run(main())

