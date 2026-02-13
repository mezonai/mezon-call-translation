"""
Agent control state - gates when audio tracks are allowed to flow into transcription.
Default: transcription OFF until explicitly enabled via DataChannel (topic='agent_control').
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class AgentControlState:
    """Thread-safe-ish state container for agent runtime control."""

    transcription_enabled: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def set_transcription_enabled(self, enabled: bool) -> bool:
        """
        Set transcription enabled flag.

        Returns True if value changed, False if unchanged.
        """
        async with self._lock:
            if self.transcription_enabled == enabled:
                return False
            self.transcription_enabled = enabled
            return True

    async def get_transcription_enabled(self) -> bool:
        async with self._lock:
            return self.transcription_enabled

