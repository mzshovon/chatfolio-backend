import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from chatfolio.db.base import Base
from chatfolio.models.mixins import ProfileChildMixin, TimestampMixin, UUIDPrimaryKeyMixin


class PublicChatfolio(ProfileChildMixin, TimestampMixin, Base):
    """The candidate's public-facing page. 1:1 with CandidateProfile (enforced below, since
    ProfileChildMixin's profile_id FK is not unique by default — most children are 1:N).

    Subdomain ("{slug}.chatfolio.com") is deliberately NOT a stored column: it's derived from
    slug wherever it's needed. Storing both would mean keeping two supposedly-equal fields in
    sync on every rename — a drift bug waiting to happen for no benefit the requirement asks for.
    """

    __tablename__ = "public_chatfolios"
    __table_args__ = (UniqueConstraint("profile_id", name="uq_public_chatfolio_profile"),)

    slug: Mapped[str] = mapped_column(String(63), unique=True, index=True)
    previous_slug: Mapped[str | None] = mapped_column(String(63), default=None, index=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    contact_cta_config: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    cv_downloadable: Mapped[bool] = mapped_column(Boolean, default=True)


class PortfolioVisit(UUIDPrimaryKeyMixin, Base):
    """One row per real page view of a published Chatfolio. Deliberately an event log, not a
    single incrementing counter — a "visitors this period vs. the period before" delta needs two
    separate time windows to compare, which a bare counter can't give you."""

    __tablename__ = "portfolio_visits"

    chatfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public_chatfolios.id", ondelete="CASCADE"), index=True
    )
    visited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
