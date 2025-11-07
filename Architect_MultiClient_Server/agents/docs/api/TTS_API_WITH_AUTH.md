# TTS API với Authentication - Cập nhật

## 🔄 Thay đổi

API `tts_api.py` đã được viết lại theo style của `dispatch_manager.py`:

### ✅ Cải tiến

1. **Authentication bắt buộc** - Bảo mật hơn
2. **Error handling tốt hơn** - Xử lý lỗi từ LiveKit server
3. **Code structure rõ ràng** - Tách internal function và API endpoint
4. **Kiểm tra LiveKit availability** - Graceful fallback nếu không có thư viện

---

## 📖 API Specification

### Endpoint: `POST /api/tts/speak`

**Request Body:**
```json
{
  "account": {
    "appid": "your_app_id",
    "token": "your_token"
  },
  "room_name": "your_room_name",
  "text": "Text to synthesize",
  "language": "en",
  "voice": "default"
}
```

**Response (Success - 200):**
```json
{
  "success": true,
  "message": "TTS request sent successfully",
  "room_name": "your_room_name",
  "text_length": 19,
  "timestamp": 1699380000.123
}
```

**Response (Authentication Failed - 401):**
```json
{
  "detail": "Account authentication failed"
}
```

**Response (Missing Room Name - 400):**
```json
{
  "detail": "room_name is required"
}
```

**Response (Server Error - 500):**
```json
{
  "detail": "LiveKit server error: ..."
}
```

---

## 🔐 Authentication

API sử dụng `verify_account.authenticate_account()` để xác thực:

```python
from src.api.verify_account import authenticate_account

# Trong endpoint
account = request.account.dict()
if not await authenticate_account(account):
    raise HTTPException(status_code=401, detail="Account authentication failed")
```

---

## 🧪 Testing

### 1. Cập nhật credentials trong test file

Mở `test_tts_api.py` và sửa:

```python
ACCOUNT = {
    "appid": "your_actual_app_id",
    "token": "your_actual_token"
}
```

### 2. Chạy test

```powershell
python Architect_MultiClient_Server/agents/test_tts_api.py
```

### 3. Test với cURL

```bash
curl -X POST http://localhost:8002/api/tts/speak \
  -H "Content-Type: application/json" \
  -d '{
    "account": {
      "appid": "your_app_id",
      "token": "your_token"
    },
    "room_name": "test-room",
    "text": "Hello world",
    "language": "en",
    "voice": "default"
  }'
```

---

## 🏗️ Architecture

```
Client Request
     ↓
[POST /api/tts/speak]
     ↓
[authenticate_account()] ← verify_account.py
     ↓
[send_tts_to_room()] ← Internal function
     ↓
[LiveKit API] → send_data()
     ↓
[LiveKit Server]
     ↓
[Agent in Room] → DataChannel
     ↓
[TTS Manager] → handle_tts_data()
     ↓
[Audio Synthesis & Streaming]
```

---

## 📝 Code Structure

### Models

```python
class AccountModel(BaseModel):
    appid: str
    token: str

class TTSRequest(BaseModel):
    account: AccountModel
    room_name: str
    text: str
    language: Optional[str] = "en"
    voice: Optional[str] = "default"

class TTSResponse(BaseModel):
    success: bool
    message: str
    room_name: str
    text_length: int
    timestamp: float
```

### Internal Function

```python
async def send_tts_to_room(
    room_name: str, 
    text: str, 
    language: str = "en", 
    voice: str = "default"
) -> dict:
    """
    Internal function to send TTS request.
    Returns dict with status and message.
    """
    # 1. Check LiveKit availability
    # 2. Get credentials from environment
    # 3. Validate input
    # 4. Create LiveKit API client
    # 5. Send DataChannel message
    # 6. Handle errors
    # 7. Return result
```

### API Endpoint

```python
@router.post("/tts/speak", response_model=TTSResponse)
async def api_send_tts_request(request: TTSRequest):
    """
    Public API endpoint with authentication.
    """
    # 1. Authenticate account
    # 2. Validate room_name
    # 3. Call internal function
    # 4. Handle errors
    # 5. Return response
```

---

## 🔍 Error Handling

### Errors từ `send_tts_to_room()`

| Error | Status | Message |
|-------|--------|---------|
| LiveKit not available | error | "LiveKit API not available..." |
| Missing credentials | error | "LiveKit credentials not configured..." |
| Empty text | error | "Text is required and cannot be empty" |
| LiveKit server error | error | "LiveKit server error: ..." |
| Unknown error | error | "Failed to send TTS request: ..." |

### HTTP Status Codes

| Code | Meaning | When |
|------|---------|------|
| 200 | Success | TTS request sent successfully |
| 400 | Bad Request | room_name missing or empty |
| 401 | Unauthorized | Authentication failed |
| 500 | Server Error | LiveKit error or other server issue |

---

## 🆚 So sánh với version cũ

### Trước (không có auth)

```python
@router.post("/tts/speak")
async def send_tts_request(request: TTSRequest):
    # Không có authentication
    # Ai cũng có thể gọi
    # Credentials hardcoded trong code
```

### Sau (có auth)

```python
@router.post("/tts/speak")
async def api_send_tts_request(request: TTSRequest):
    # Bắt buộc authentication
    account = request.account.dict()
    if not await authenticate_account(account):
        raise HTTPException(status_code=401)
    
    # Credentials từ environment
    # Error handling tốt hơn
    # Code structure rõ ràng hơn
```

---

## 🔒 Security Best Practices

1. ✅ **Bắt buộc authentication** cho mọi request
2. ✅ **Credentials từ environment** (không hardcode)
3. ✅ **Validate input** trước khi xử lý
4. ✅ **Rate limiting** (nên thêm trong production)
5. ✅ **Logging** (để audit trail)

---

## 💡 Tips

### Tip 1: Skip authentication trong development

Nếu muốn skip auth trong development, thêm vào `.env`:

```
SKIP_AUTH=true
```

Và sửa trong `verify_account.py`:

```python
async def authenticate_account(account: dict) -> bool:
    if os.getenv("SKIP_AUTH", "false").lower() == "true":
        return True  # Skip auth in dev
    
    # Normal authentication logic
    ...
```

### Tip 2: Multiple authentication methods

Có thể extend để support nhiều auth methods:

```python
class TTSRequest(BaseModel):
    account: Optional[AccountModel] = None
    api_key: Optional[str] = None  # Alternative auth
    room_name: str
    text: str
```

### Tip 3: Batch requests

Có thể thêm endpoint cho multiple requests:

```python
@router.post("/tts/speak-batch")
async def send_tts_batch(requests: List[TTSRequest]):
    results = []
    for req in requests:
        result = await send_tts_to_room(...)
        results.append(result)
    return results
```

---

## 📊 Comparison với Direct API

| Feature | DataChannel API (tts_api) | Direct API (tts_direct_api) |
|---------|---------------------------|----------------------------|
| **Authentication** | ✅ Required | ❌ None (internal) |
| **External access** | ✅ Yes | ❌ No (same process) |
| **Latency** | Higher (~100-200ms) | Lower (~10-20ms) |
| **Use case** | External clients | Internal operations |
| **Security** | Account-based | Process-based |

---

## 🎯 Khi nào dùng API này?

✅ **Dùng khi:**
- Client bên ngoài cần trigger TTS
- Cần authentication và authorization
- Multi-tenant system
- Public API endpoint
- Distributed architecture

❌ **Không dùng khi:**
- Internal operations (dùng Direct API)
- Same-process calls (dùng Direct API)
- Need ultra-low latency (dùng Direct API)
- Development/testing only (dùng Direct API)

---

## 🚀 Deployment

### Production Checklist

- [ ] Update `ACCOUNT` credentials properly
- [ ] Set up rate limiting
- [ ] Add request logging
- [ ] Monitor authentication failures
- [ ] Set up alerts for errors
- [ ] Document API for clients
- [ ] Test error scenarios
- [ ] Load testing

---

**Last Updated:** November 7, 2025  
**Version:** 2.0.0 (với authentication)  
**Breaking Changes:** Request body now requires `account` field
