import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer
import os
import traceback
import logging

logger = logging.getLogger(__name__)


auth_scheme = HTTPBearer()


def load_public_key():
    try:
        current_dir = os.path.dirname(__file__)
        base_dir = os.path.abspath(os.path.join(current_dir, ".."))
        file_path = os.path.join(base_dir, "public-key.pem")

        logger.info(f"Đang cố gắng đọc public key từ: {file_path}")

        with open(file_path, 'r') as f:
            key = f.read()
            logger.info("✅ Public key đã được load thành công")
            return key

    except FileNotFoundError:
        logger.error(f"❌ Không tìm thấy file {file_path}") 
        return None
    except Exception as e:
        logger.error(f"❌ Lỗi không xác định khi đọc public key: {str(e)}\n{traceback.format_exc()}")
        return None


PUBLIC_KEY = load_public_key()


async def get_current_payload(request: Request):
    auth_header = request.headers.get('Authorization')
    
    if not auth_header:
        logger.warning("⚠️ Thiếu header Authorization trong request")
        raise HTTPException(status_code=401, detail='Missing Authorization header')

    try:
        parts = auth_header.split(' ')
        if len(parts) != 2 or parts[0] != 'Bearer':
            raise ValueError("Invalid format")
        token = parts[1]
    except Exception as e:
        logger.warning(f"⚠️ Header Authorization sai định dạng: {auth_header}")
        raise HTTPException(
            status_code=401,
            detail='Invalid Authorization header format. Use: Bearer <token>'
        )

    if not PUBLIC_KEY:
        logger.critical("🚨 PUBLIC_KEY chưa được load (None)")
        raise HTTPException(status_code=500, detail='Public key not loaded')

    try:
        decoded_payload = jwt.decode(
            token,
            PUBLIC_KEY,
            algorithms=['RS256']
        )
        logger.info(f"✅ Token đã decode thành công: {decoded_payload}")
        return decoded_payload

    except jwt.ExpiredSignatureError:
        logger.warning("⚠️ Token hết hạn")
        raise HTTPException(status_code=401, detail='Token has expired')

    except jwt.InvalidTokenError as e:
        logger.error(f"❌ Token không hợp lệ: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=401, detail=f'Invalid token: {str(e)}')

    except Exception as e:
        logger.error(f"💥 Lỗi server khi decode token: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f'Server error: {str(e)}')
