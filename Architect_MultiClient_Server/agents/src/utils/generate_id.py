import secrets
import string

def generate_id(prefix: str = "FALLBACK_", length: int = 12):
    chars = string.ascii_letters + string.digits
    random_part = ''.join(secrets.choice(chars) for _ in range(length))
    return f"{prefix}{random_part}"