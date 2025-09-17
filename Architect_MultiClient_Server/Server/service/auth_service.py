import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

auth_scheme = HTTPBearer()

def decode_jwt(token: str):
    try:
        # ⚠️ verify_signature=False: dùng cho dev. 
        # Nếu muốn bảo mật hơn thì truyền secret vào.
        payload = jwt.decode(token, options={"verify_signature": False})
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

def get_current_payload(credentials: HTTPAuthorizationCredentials = Depends(auth_scheme)):
    token = credentials.credentials
    return decode_jwt(token)
