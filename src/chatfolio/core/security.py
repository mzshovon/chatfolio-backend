import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import jwt
from passlib.context import CryptContext

from chatfolio.config.settings import SecuritySettings
from chatfolio.models.user import UserRole

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return str(_pwd_context.hash(plain_password))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bool(_pwd_context.verify(plain_password, hashed_password))


class TokenType(StrEnum):
    ACCESS = "access"
    TWO_FACTOR_CHALLENGE = "2fa_challenge"


def create_access_token(*, user_id: uuid.UUID, role: UserRole, settings: SecuritySettings) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role.value,
        "type": TokenType.ACCESS.value,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
    }
    secret = settings.jwt_secret.get_secret_value()
    return jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: SecuritySettings) -> dict[str, Any]:
    return jwt.decode(
        token, settings.jwt_secret.get_secret_value(), algorithms=[settings.jwt_algorithm]
    )


# Deliberately short-lived (see SecuritySettings.two_factor_challenge_ttl_minutes): this token
# only proves "password already checked out for this user" between the login call and the
# follow-up OTP verification, so a 5-minute default window is plenty and keeps a leaked/logged
# challenge token from being useful for long.
def create_two_factor_challenge_token(*, user_id: uuid.UUID, settings: SecuritySettings) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": TokenType.TWO_FACTOR_CHALLENGE.value,
        "iat": now,
        "exp": now + timedelta(minutes=settings.two_factor_challenge_ttl_minutes),
    }
    secret = settings.jwt_secret.get_secret_value()
    return jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)


def decode_two_factor_challenge_token(token: str, settings: SecuritySettings) -> uuid.UUID:
    payload = jwt.decode(
        token, settings.jwt_secret.get_secret_value(), algorithms=[settings.jwt_algorithm]
    )
    if payload.get("type") != TokenType.TWO_FACTOR_CHALLENGE.value:
        raise jwt.InvalidTokenError("Not a two-factor challenge token.")
    return uuid.UUID(payload["sub"])


def generate_opaque_token() -> str:
    """Opaque random secret handed to the client (refresh token, password reset link); only its
    hash is ever persisted."""
    return secrets.token_urlsafe(48)


def hash_opaque_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_otp_code() -> str:
    """6-digit numeric code for email/SMS delivery — short enough to type from a phone screen."""
    return f"{secrets.randbelow(1_000_000):06d}"
