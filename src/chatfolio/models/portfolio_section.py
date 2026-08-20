from enum import StrEnum

from sqlalchemy import Enum, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from chatfolio.db.base import Base
from chatfolio.models.mixins import ProfileChildMixin, TimestampMixin


class SectionType(StrEnum):
    INTRO = "intro"
    SUMMARY = "summary"


class SectionStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


class GeneratedBy(StrEnum):
    AI = "ai"
    MANUAL = "manual"


class PortfolioSection(ProfileChildMixin, TimestampMixin, Base):
    __tablename__ = "portfolio_sections"
    __table_args__ = (
        UniqueConstraint("profile_id", "section_type", name="uq_portfolio_section_profile_type"),
    )

    section_type: Mapped[SectionType] = mapped_column(Enum(SectionType, name="section_type"))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[SectionStatus] = mapped_column(
        Enum(SectionStatus, name="section_status"), default=SectionStatus.DRAFT
    )
    generated_by: Mapped[GeneratedBy] = mapped_column(
        Enum(GeneratedBy, name="generated_by"), default=GeneratedBy.AI
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
