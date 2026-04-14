import httpx
import jwt
from typing import Optional, Dict, Any
from orchestrator_service.config.application_config import get_config
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


async def authenticate_account(account: dict) -> Optional[Dict[str, Any]]:
    """
    Authenticate account with Mezon server and return authentication details.
    
    Args:
        account: Account credentials dictionary
        
    Returns:
        Dictionary containing:
        - token: JWT access token from Mezon
        - refresh_token: Refresh token from Mezon
        - user_id: User ID
        - api_url: API URL from Mezon
        - ws_url: WebSocket URL from Mezon
        - payload: Decoded JWT payload (tid, uid, usn, vrs, exp)
        
        Returns None if authentication fails
    """
    config = get_config()
    url = config.server.authenticate_account_url
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json={"account": account}, timeout=10)    
            if resp.status_code == 200:
                data = resp.json()
                if "token" in data and "refresh_token" in data:
                    # Decode JWT token to extract payload (without verification)
                    try:
                        payload = jwt.decode(data["token"], options={"verify_signature": False})
                        logger.info(f"✅ Account authenticated: user_id={data.get('user_id')}, usn={payload.get('usn')}")
                        
                        return {
                            "token": data.get("token"),
                            "refresh_token": data.get("refresh_token"),
                            "user_id": data.get("user_id"),
                            "api_url": data.get("api_url"),
                            "ws_url": data.get("ws_url"),
                            "payload": payload
                        }
                    except jwt.InvalidTokenError as e:
                        logger.error(f"❌ Failed to decode JWT token: {e}")
                        return None
            
            logger.warning(f"❌ Authentication failed: status_code={resp.status_code}")
            return None
        except Exception as e:
            logger.error(f"❌ Authentication error: {e}")
            return None


