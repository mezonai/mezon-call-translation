import httpx
from orchestrator_service.config.application_config import get_config


async def authenticate_account(account: dict) -> bool:
    config = get_config()
    url = config.server.authenticate_account_url
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json={"account": account}, timeout=10)    
        if resp.status_code == 200:
            data = resp.json()
            if "token" in data:
                return True
        return False


