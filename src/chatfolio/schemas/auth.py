import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from chatfolio.models.user import TwoFactorMethod, UserRole


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    requires_two_factor: Literal[False] = False
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: UserRole
    is_active: bool


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class TwoFactorSetupRequest(BaseModel):
    method: TwoFactorMethod
    # Required (and stored) the first time the account enables a phone-involving method; may be
    # omitted on a later setup call if a phone is already on file.
    phone: str | None = Field(default=None, min_length=6, max_length=32)


class TwoFactorSetupResponse(BaseModel):
    method: TwoFactorMethod
    masked_destinations: list[str]


class TwoFactorVerifySetupRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class TwoFactorChallengeResponse(BaseModel):
    """Returned from `/auth/login` instead of `TokenResponse` when the account has 2FA enabled —
    branch the frontend on `requires_two_factor` rather than on HTTP status, since this is a
    normal step in the login flow, not an error."""

    requires_two_factor: Literal[True] = True
    challenge_token: str
    method: TwoFactorMethod
    masked_destinations: list[str]


class TwoFactorLoginVerifyRequest(BaseModel):
    challenge_token: str
    code: str = Field(min_length=6, max_length=6)


class TwoFactorResendRequest(BaseModel):
    challenge_token: str
