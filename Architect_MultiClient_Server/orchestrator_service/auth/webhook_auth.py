"""
Webhook Authentication for LiveKit webhooks.

LiveKit webhook authentication works as follows:
1. The Authorization header contains a JWT token signed with your API secret
2. The JWT contains a 'sha256' claim with a base64-encoded SHA256 hash of the body
3. WebhookReceiver verifies the JWT signature and compares the body hash

Usage:
    from src.api.webhook_auth import verify_webhook, get_webhook_receiver
    
    # Verify webhook
    is_valid, error = verify_webhook(body_str, auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail=error)
"""

from __future__ import annotations

from typing import Optional, Tuple, Any

from orchestrator_service.config.application_config import get_config


from livekit.api import TokenVerifier, WebhookReceiver as _WebhookReceiver

from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

# Get config
_config = get_config()
VERIFY_WEBHOOKS = _config.livekit.verify_webhooks


def get_webhook_receiver() -> Optional[Any]:
    """
    Create a WebhookReceiver instance for verifying webhook signatures.
    
    Uses LiveKit webhook API credentials from config.
    
    Returns:
        WebhookReceiver instance or None if not available
    """
    
    config = get_config()
    api_key = config.livekit.webhook_api_key
    api_secret = config.livekit.webhook_api_secret
    
    if not api_key or not api_secret:
        logger.warning("LIVEKIT_WEBHOOK_API_KEY and LIVEKIT_WEBHOOK_API_SECRET must be set for webhook verification")
        return None
    
    token_verifier = TokenVerifier(api_key=api_key, api_secret=api_secret)
    return _WebhookReceiver(token_verifier=token_verifier)


def verify_webhook(body: str, auth_header: str) -> Tuple[bool, Optional[str]]:
    """
    Verify the webhook signature from LiveKit using WebhookReceiver.
    
    Args:
        body: Raw request body as string
        auth_header: Authorization header value
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    receiver = get_webhook_receiver()
    if not receiver:
        return False, "WebhookReceiver not available - livekit-api not installed or credentials not configured"
    
    try:
        # WebhookReceiver.receive() will verify and parse the webhook
        # It raises an exception if verification fails
        receiver.receive(body, auth_header)
        return True, None
    except Exception as e:
        return False, str(e)


def is_verification_enabled() -> bool:
    """Check if webhook verification is enabled."""
    return VERIFY_WEBHOOKS


