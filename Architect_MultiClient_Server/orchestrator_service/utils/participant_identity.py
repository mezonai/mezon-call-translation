"""
Participant identity utilities
"""
import json
import logging

logger = logging.getLogger(__name__)


def parse_participant_identity(participant_identity: str) -> str:
    """
    Parse participant identity to extract extName if it's a JSON string.
    
    If participant_identity is a JSON string with "extName" field, return the extName value.
    Otherwise, return the original participant_identity.
    """
    if not participant_identity:
        return participant_identity
    
    try:
        # Try to parse as JSON
        data = json.loads(participant_identity)
        
        # Check if it's a dict with extName field
        if isinstance(data, dict) and "extName" in data:
            ext_name = data["extName"]
            logger.debug(f"Extracted extName '{ext_name}' from participant_identity")
            return ext_name
        else:
            # JSON but no extName field, return original
            logger.debug(f"JSON parsed but no extName field found, keeping original")
            return participant_identity
            
    except (json.JSONDecodeError, TypeError, ValueError):
        # Not valid JSON, return original
        logger.debug(f"Not JSON format, keeping original participant_identity")
        return participant_identity
