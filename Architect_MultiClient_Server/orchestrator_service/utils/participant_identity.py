"""
Participant identity utilities
"""
import json
from orchestrator_service.utils.logger import get_logger
import re

logger = get_logger(__name__)


def parse_participant_identity(participant_identity: str) -> str:
    """
    Parse participant identity to extract extName if it's a JSON string.

    If participant_identity is a JSON string with "extName" field, return the sanitized extName value.
    Otherwise, return the original participant_identity.
    """
    if not participant_identity:
        return participant_identity

    try:
        data = json.loads(participant_identity)

        if isinstance(data, dict) and "extName" in data:
            ext_name = data["extName"]

            if isinstance(ext_name, str):

                sanitized = re.sub(r'[^a-zA-Z0-9_-]+', '_', ext_name)

                # If sanitized becomes empty → fallback
                if sanitized:
                    logger.debug(f"Sanitized extName: '{sanitized}'")
                    return sanitized
                else:
                    logger.debug("extName sanitized to empty, fallback to original")
                    return "unknown_participant"

        # JSON parsed but no valid extName
        logger.debug("JSON parsed but no valid extName, keeping original")
        return participant_identity

    except (json.JSONDecodeError, TypeError, ValueError):
        logger.debug("Not JSON format, keeping original participant_identity")
        return participant_identity