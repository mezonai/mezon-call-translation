import httpx

async def authenticate_account(account: dict) -> bool:
    url = "https://gw.mezon.ai:443/v2/apps/authenticate/token"
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json={"account": account}, timeout=10)    
        if resp.status_code == 200:
            data = resp.json()
            if "token" in data:
                return True
        return False
