import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from chatfolio.db.base import Base
from chatfolio.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class UserRole(StrEnum):
    CANDIDATE = "candidate"
    ADMIN = "admin"


class TwoFactorMethod(StrEnum):
    EMAIL = "email"
    PHONE = "phone"
    BOTH = "both"


class OtpPurpose(StrEnum):
    TWO_FACTOR_ENROLL = "two_factor_enroll"
    TWO_FACTOR_LOGIN = "two_factor_login"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), default=UserRole.CANDIDATE
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    two_factor_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    two_factor_method: Mapped[TwoFactorMethod | None] = mapped_column(
        Enum(TwoFactorMethod, name="two_factor_method"), nullable=True
    )

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RefreshToken(UUIDPrimaryKeyMixin, Base):
    """Opaque, rotated refresh tokens. Enables logout/revocation, unlike a stateless JWT refresh."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class PasswordResetToken(UUIDPrimaryKeyMixin, Base):
    """Single-use, opaque token emailed to a candidate as a reset-password link. Modeled after
    RefreshToken (hash-only at rest, expiry, one-shot use)."""

    __tablename__ = "password_reset_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class EmailChangeRequest(UUIDPrimaryKeyMixin, Base):
    """Single-use, opaque token emailed to the NEW address to confirm an email change before it
    takes effect. Modeled after PasswordResetToken (hash-only at rest, expiry, one-shot use);
    `new_email` sits alongside the token so applying it doesn't need the request to still be
    "current" in any other sense — the token itself is the entire authorization."""

    __tablename__ = "email_change_requests"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    new_email: Mapped[str] = mapped_column(String(255))
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class OtpCode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """6-digit codes for 2FA enrollment and 2FA login, delivered by email/SMS/both. `channel`
    records where THIS code was actually sent (for the "both" method, one row exists per send
    and the same code value is delivered on every channel)."""

    __tablename__ = "otp_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[OtpPurpose] = mapped_column(Enum(OtpPurpose, name="otp_purpose"))
    channel: Mapped[TwoFactorMethod] = mapped_column(Enum(TwoFactorMethod, name="otp_channel"))
    code_hash: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
