#!/usr/bin/env python3
"""
Quick script to run MongoDB migration

This is a convenience wrapper that can be executed directly.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.migrate_mongodb import main

if __name__ == "__main__":
    asyncio.run(main())
