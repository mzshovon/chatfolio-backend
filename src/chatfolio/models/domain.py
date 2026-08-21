import uuid

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from chatfolio.db.base import Base
from chatfolio.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class PublicDomain(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Phase-2 stub (§15) behind `features.enable_custom_domains` — a custom domain belongs to a
    Chatfolio (1:1), not directly to a CandidateProfile, since the thing being addressed is the
    published page. `verification_token` is a placeholder for a future DNS TXT-record challenge;
    no verification logic exists yet, only the field to hold the value once it does.
    """

    __tablename__ = "public_domains"

    chatfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public_chatfolios.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_token: Mapped[str] = mapped_column(String(64))
