# Request/Response Models
from typing import Any, ClassVar

from pydantic import BaseModel, Field


class JWTPayload(BaseModel):  # type: ignore[explicit-any]
    jti: str = Field(..., description="JWT ID")
    user_id: str = Field(..., description="User ID")
    exp: int = Field(..., description="Expiration time")
    iat: int = Field(..., description="Issued at time")
    username: str | None = Field(default=None, description="Username")
    display_name: str | None = Field(default=None, description="Display name")
    avatar_url: str | None = Field(default=None, description="Avatar URL")


class MezonAuthResponse(BaseModel):  # type: ignore[explicit-any]
    token: str = Field(..., description="JWT access token for bot session")
    refresh_token: str = Field(..., description="Refresh token for obtaining new access tokens")
    user_id: str = Field(..., description="User ID from Mezon")
    api_url: str | None = Field(default=None, description="API URL")
    ws_url: str | None = Field(default=None, description="WebSocket URL")
    # TODO: Use `Any` because the payload returned from jwt.decode() has dict[str, Any] type
    payload: dict[str, Any] = Field(..., description="User information from Mezon")  # type: ignore[explicit-any]


class ExchangeCodeRequest(BaseModel):  # type: ignore[explicit-any]
    code: str = Field(..., description="Authorization code from Mezon OAuth2 callback")
    state: str = Field(..., description="State parameter for CSRF protection (11 alphanumeric chars)")


class TokenResponseBase(BaseModel):  # type: ignore[explicit-any]
    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="Refresh token")
    token_type: str = Field(default="Bearer", description="Token type")
    expires_in: int = Field(..., description="Access token expiry in seconds")


class ExchangeCodeResponse(TokenResponseBase):  # type: ignore[explicit-any]
    # TODO: Use `Any` because the return result from auth_service.exchange_code_for_token() has dict[str, Any] type
    user: dict[str, Any] = Field(..., description="User information from Mezon")  # type: ignore[explicit-any]


class OAuth2ConfigResponse(BaseModel):  # type: ignore[explicit-any]
    client_id: str = Field(..., description="Mezon OAuth2 client ID")
    auth_url: str = Field(..., description="Mezon authorization URL")
    redirect_uri: str = Field(..., description="Registered redirect URI")


class UserProfile(BaseModel):  # type: ignore[explicit-any]
    user_id: str = Field(..., description="User ID from Mezon")
    username: str | None = Field(default=None, description="Username from Mezon")
    display_name: str | None = Field(default=None, description="Display name from Mezon")
    avatar: str | None = Field(default=None, description="Avatar URL from Mezon")


class CurrentUserResponse(BaseModel):  # type: ignore[explicit-any]
    status: str = Field(..., description="Status of the request")
    user: UserProfile = Field(..., description="User information from Mezon")


# New Request/Response Models for Refresh and Logout
class RefreshTokenRequest(BaseModel):  # type: ignore[explicit-any]
    refresh_token: str = Field(..., description="Refresh token obtained from login")


class RefreshTokenResponse(TokenResponseBase):  # type: ignore[explicit-any]
    pass


class LogoutRequest(BaseModel):  # type: ignore[explicit-any]
    refresh_token: str = Field(..., description="Refresh token to revoke")


class AccountModel(BaseModel):  # type: ignore[explicit-any]
    appid: str = Field(..., description="App ID from Mezon")
    token: str = Field(..., description="Token from Mezon")


class BotLoginRequest(BaseModel):  # type: ignore[explicit-any]
    account: AccountModel = Field(..., description="Bot account credentials")

    class Config:
        # TODO: Use `Any` instead of complex dict[str, dict[str, dict[str, str]]]
        json_schema_extra: ClassVar[dict[str, Any]] = {  # type: ignore[explicit-any]
            "examples": [{"account": {"appid": "string", "token": "string"}}]
        }


class BotLoginResponse(TokenResponseBase):  # type: ignore[explicit-any]
    pass
