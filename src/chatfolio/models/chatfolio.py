from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from chatfolio.db.base import Base
from chatfolio.models.mixins import ProfileChildMixin, TimestampMixin


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
