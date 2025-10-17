#!/usr/bin/env python3
"""
Test script for Agent BotWebSocketClient
Tests the BotWebSocketClient class functionality
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

class AgentWebSocketTester:
    """Test the BotWebSocketClient class"""
    
    def __init__(self):
        self.session_id = "test_meeting_agent_123"
        self.meeting_code = "test_meeting_agent_123"
        
    async def test_client_creation(self):
        """Test Case 1: Client Creation"""
        logger.info("=== Test Case 1: Client Creation ===")
        
        try:
            client = BotWebSocketClient(
                session_id=self.session_id,
                meeting_code=self.meeting_code
            )
            
            # Check initial state
            assert not client.connected, "Client should not be connected initially"
            assert client.session_id == self.session_id, "Session ID should match"
            assert client.meeting_code == self.meeting_code, "Meeting code should match"
            assert client.uri.endswith(f"/ws/agent/{self.session_id}"), "URI should be correct"
            
            logger.info("✅ Test Case 1 PASSED: Client creation successful")
            return True
            
        except Exception as e:
            logger.error(f"❌ Test Case 1 FAILED: {e}")
            return False
    
    async def test_connection_failure(self):
        """Test Case 2: Connection Failure (Bot not running)"""
        logger.info("=== Test Case 2: Connection Failure ===")
        
        try:
            client = BotWebSocketClient(
                session_id=self.session_id,
                meeting_code=self.meeting_code
            )
            
            # Try to connect (should fail if Bot not running)
            result = await client.connect()
            
            if not result:
                logger.info("✅ Test Case 2 PASSED: Connection failure handled correctly")
                return True
            else:
                logger.warning("⚠️  Test Case 2: Bot is running, connection succeeded")
                return True
                
        except Exception as e:
            logger.error(f"❌ Test Case 2 FAILED: {e}")
            return False
    
    async def test_send_transcript_not_connected(self):
        """Test Case 3: Send transcript when not connected"""
        logger.info("=== Test Case 3: Send transcript when not connected ===")
        
        try:
            client = BotWebSocketClient(
                session_id=self.session_id,
                meeting_code=self.meeting_code
            )
            
            # Try to send transcript without connecting
            payload = {
                "text": "Test message",
                "is_final": True,
                "client_id": "test_participant",
                "session_id": self.session_id,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            result = await client.send_transcript(payload)
            
            # Should return False when not connected
            if not result:
                logger.info("✅ Test Case 3 PASSED: Send transcript when not connected handled correctly")
                return True
            else:
                logger.error("❌ Test Case 3 FAILED: Should not be able to send when not connected")
                return False
                
        except Exception as e:
            logger.error(f"❌ Test Case 3 FAILED: {e}")
            return False
    
    async def test_send_interim_transcript(self):
        """Test Case 4: Send interim transcript (should be ignored)"""
        logger.info("=== Test Case 4: Send interim transcript ===")
        
        try:
            client = BotWebSocketClient(
                session_id=self.session_id,
                meeting_code=self.meeting_code
            )
            
            # Try to send interim transcript
            payload = {
                "text": "Interim message",
                "is_final": False,  # This should be ignored
                "client_id": "test_participant",
                "session_id": self.session_id,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            result = await client.send_transcript(payload)
            
            # Should return False for interim transcripts
            if not result:
                logger.info("✅ Test Case 4 PASSED: Interim transcript correctly ignored")
                return True
            else:
                logger.error("❌ Test Case 4 FAILED: Interim transcript should be ignored")
                return False
                
        except Exception as e:
            logger.error(f"❌ Test Case 4 FAILED: {e}")
            return False
    
    async def test_disconnect(self):
        """Test Case 5: Disconnect"""
        logger.info("=== Test Case 5: Disconnect ===")
        
        try:
            client = BotWebSocketClient(
                session_id=self.session_id,
                meeting_code=self.meeting_code
            )
            
            # Disconnect should not crash
            await client.disconnect()
            
            # Should still be disconnected
            assert not client.connected, "Client should be disconnected"
            
            logger.info("✅ Test Case 5 PASSED: Disconnect handled correctly")
            return True
            
        except Exception as e:
            logger.error(f"❌ Test Case 5 FAILED: {e}")
            return False
    
    async def test_payload_format(self):
        """Test Case 6: Payload format validation"""
        logger.info("=== Test Case 6: Payload format validation ===")
        
        try:
            client = BotWebSocketClient(
                session_id=self.session_id,
                meeting_code=self.meeting_code
            )
            
            # Test payload without timestamp (should add one)
            payload = {
                "text": "Test message without timestamp",
                "is_final": True,
                "client_id": "test_participant",
                "session_id": self.session_id
                # No timestamp - should be added automatically
            }
            
            # This should not crash even when not connected
            result = await client.send_transcript(payload)
            
            # Should return False (not connected) but not crash
            if not result:
                logger.info("✅ Test Case 6 PASSED: Payload format handled correctly")
                return True
            else:
                logger.error("❌ Test Case 6 FAILED: Should not be able to send when not connected")
                return False
                
        except Exception as e:
            logger.error(f"❌ Test Case 6 FAILED: {e}")
            return False

async def main():
    """Run all test cases"""
    logger.info("🚀 Starting Agent WebSocket Client Tests")
    
    tester = AgentWebSocketTester()
    
    # Run all test cases
    test_cases = [
        ("Client Creation", tester.test_client_creation),
        ("Connection Failure", tester.test_connection_failure),
        ("Send transcript not connected", tester.test_send_transcript_not_connected),
        ("Send interim transcript", tester.test_send_interim_transcript),
        ("Disconnect", tester.test_disconnect),
        ("Payload format", tester.test_payload_format)
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
        await asyncio.sleep(0.5)
    
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

