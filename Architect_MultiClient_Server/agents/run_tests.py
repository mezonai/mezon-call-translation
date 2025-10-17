#!/usr/bin/env python3
"""
Run all tests for Agent-Bot WebSocket communication
"""

import asyncio
import subprocess
import sys
import os
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def run_test_script(script_name, description):
    """Run a test script and return results"""
    logger.info(f"\n{'='*60}")
    logger.info(f"Running: {description}")
    logger.info(f"Script: {script_name}")
    logger.info(f"{'='*60}")
    
    try:
        # Run the test script
        result = subprocess.run([
            sys.executable, script_name
        ], capture_output=True, text=True, timeout=60)
        
        # Print output
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)
        
        success = result.returncode == 0
        status = "✅ PASSED" if success else "❌ FAILED"
        logger.info(f"{description}: {status}")
        
        return success
        
    except subprocess.TimeoutExpired:
        logger.error(f"❌ {description}: TIMEOUT")
        return False
    except Exception as e:
        logger.error(f"❌ {description}: ERROR - {e}")
        return False

async def main():
    """Run all tests"""
    logger.info("🚀 Starting Agent-Bot WebSocket Tests")
    logger.info("=" * 60)
    
    # Test scripts to run
    test_scripts = [
        ("test_agent_websocket_client.py", "Agent WebSocket Client Tests"),
        ("test_bot_websocket.py", "Bot WebSocket Server Tests"),
        ("test_full_flow.py", "Full Flow Integration Tests")
    ]
    
    results = []
    
    for script, description in test_scripts:
        if os.path.exists(script):
            result = await run_test_script(script, description)
            results.append((description, result))
        else:
            logger.warning(f"⚠️  Script not found: {script}")
            results.append((description, False))
    
    # Summary
    logger.info(f"\n{'='*60}")
    logger.info("OVERALL TEST SUMMARY")
    logger.info(f"{'='*60}")
    
    passed = 0
    total = len(results)
    
    for description, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        logger.info(f"{description}: {status}")
        if result:
            passed += 1
    
    logger.info(f"\nOverall: {passed}/{total} test suites passed")
    
    if passed == total:
        logger.info("🎉 All test suites passed!")
        logger.info("✅ Agent-Bot WebSocket communication is ready!")
    else:
        logger.warning(f"⚠️  {total - passed} test suites failed")
        logger.info("Check the logs above for details")
    
    # Instructions
    logger.info(f"\n{'='*60}")
    logger.info("NEXT STEPS")
    logger.info(f"{'='*60}")
    logger.info("1. Start Bot server: cd bot && npm start")
    logger.info("2. Start Agent: cd agents && python main.py")
    logger.info("3. Test with real LiveKit room")
    logger.info("4. Check Bot logs for transcript delivery")

if __name__ == "__main__":
    asyncio.run(main())

