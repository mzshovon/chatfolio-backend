import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from chatfolio.db.base import Base
from chatfolio.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class GoogleCalendarConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A candidate's linked Google account for meeting scheduling. Belongs to the User (the OAuth
    identity), not the CandidateProfile — a login and a calendar connection are both
    account-level, not portfolio-content. One connection per user; reconnecting overwrites the
    existing row rather than creating a second one (get_or_create-style upsert in the service).
    """

    __tablename__ = "google_calendar_connections"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    google_account_email: Mapped[str | None] = mapped_column(String(255))
    # Fernet ciphertext, not plaintext — see core.security.encrypt_secret/decrypt_secret.
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    # Google only issues a refresh_token on the first consent (or when prompt=consent forces a
    # new one); nullable so a re-auth that Google didn't hand a fresh one for doesn't wipe it.
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    access_token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Space-separated, exactly as Google's token response returns it — matches the `scope` query
    # param convention used everywhere else in this flow.
    granted_scopes: Mapped[str] = mapped_column(Text)
