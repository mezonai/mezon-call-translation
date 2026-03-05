"""
Orchestrator service utilities
"""
from orchestrator_service.utils.decorator import singleton
from orchestrator_service.utils.logger import get_logger

__all__ = [
    'singleton',
    'get_logger',
]
