from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from chatfolio.db.base import Base
from chatfolio.models.mixins import UUIDPrimaryKeyMixin

NPS_SCORE_MIN = 0
NPS_SCORE_MAX = 5
FEEDBACK_MESSAGE_MAX_LENGTH = 2100


class Feedback(UUIDPrimaryKeyMixin, Base):
    """Open, unauthenticated feedback submission — no owning user. Admin-only to read."""

    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint(
            f"nps_score >= {NPS_SCORE_MIN} AND nps_score <= {NPS_SCORE_MAX}",
            name="ck_feedback_nps_score_range",
        ),
    )

    nps_score: Mapped[int] = mapped_column(Integer, nullable=False)
    message: Mapped[str | None] = mapped_column(
        String(FEEDBACK_MESSAGE_MAX_LENGTH), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
