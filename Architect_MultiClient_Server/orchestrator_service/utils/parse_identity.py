import json

def parse_participant_identity(identity: str) -> str:
    """
    Extract extName from identity if it's in JSON format.
    
    Args:
        identity: Participant identity (can be plain string or JSON like '{"extName":"user.name"}')
        
    Returns:
        Extracted extName if JSON format, otherwise original identity
    """
    if isinstance(identity, str) and identity.startswith("{"):
        try:
            identity_obj = json.loads(identity)
            if "extName" in identity_obj:
                return identity_obj["extName"]
        except (json.JSONDecodeError, TypeError):
            # Keep original identity if parsing fails
            pass
    return identity