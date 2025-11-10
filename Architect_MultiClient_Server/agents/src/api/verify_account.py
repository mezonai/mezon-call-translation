import httpx
import os
from dotenv import load_dotenv
load_dotenv()

async def authenticate_account(account: dict) -> bool:
    url= os.environ.get("AUTHENTICATE_ACCOUNT_URL")
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json={"account": account}, timeout=10)    
        if resp.status_code == 200:
            data = resp.json()
            if "token" in data:
                return True
        return False


