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

import os
from typing import Optional, Tuple, Any


try:
    from livekit.api import TokenVerifier, WebhookReceiver as _WebhookReceiver
    WEBHOOK_AUTH_AVAILABLE = True
except ImportError:
    WEBHOOK_AUTH_AVAILABLE = False
    _WebhookReceiver = None

from src.logger import get_logger

logger = get_logger(__name__)

# Environment variable to control webhook verification
VERIFY_WEBHOOKS = os.getenv("LIVEKIT_VERIFY_WEBHOOKS", "true").lower() == "true"


def get_webhook_receiver() -> Optional[Any]:
    """
    Create a WebhookReceiver instance for verifying webhook signatures.
    
    Uses LIVEKIT_WEBHOOK_API_KEY and LIVEKIT_WEBHOOK_API_SECRET environment variables.
    
    Returns:
        WebhookReceiver instance or None if not available
    """
    if not WEBHOOK_AUTH_AVAILABLE:
        logger.warning("livekit-api not installed - webhook verification unavailable")
        return None
    
    api_key = os.getenv("LIVEKIT_WEBHOOK_API_KEY")
    api_secret = os.getenv("LIVEKIT_WEBHOOK_API_SECRET")
    
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


def is_auth_available() -> bool:
    """Check if webhook authentication is available."""
    return WEBHOOK_AUTH_AVAILABLE
