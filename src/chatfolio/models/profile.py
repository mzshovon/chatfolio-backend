import uuid
from datetime import date
from enum import StrEnum

from sqlalchemy import JSON, Boolean, Date, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from chatfolio.db.base import Base
from chatfolio.models.mixins import ProfileChildMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ProfileStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


class CandidateProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "candidate_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    full_name: Mapped[str | None] = mapped_column(String(255), default=None)
    title: Mapped[str | None] = mapped_column(String(255), default=None)
    bio: Mapped[str | None] = mapped_column(Text, default=None)
    location: Mapped[str | None] = mapped_column(String(255), default=None)
    contact_email: Mapped[str | None] = mapped_column(String(255), default=None)
    phone: Mapped[str | None] = mapped_column(String(50), default=None)
    social_links: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    status: Mapped[ProfileStatus] = mapped_column(
        Enum(ProfileStatus, name="profile_status"), default=ProfileStatus.DRAFT
    )

    experiences: Mapped[list["Experience"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="Experience.start_date.desc()",
    )
    projects: Mapped[list["Project"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    skills: Mapped[list["Skill"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    education: Mapped[list["Education"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="Education.start_date.desc()",
    )


class Experience(ProfileChildMixin, Base):
    __tablename__ = "experiences"

    company: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(255))
    start_date: Mapped[date | None] = mapped_column(Date, default=None)
    end_date: Mapped[date | None] = mapped_column(Date, default=None)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)

    profile: Mapped[CandidateProfile] = relationship(back_populates="experiences")


class Project(ProfileChildMixin, Base):
    __tablename__ = "projects"

    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    tech_stack: Mapped[list[str]] = mapped_column(JSON, default=list)
    impact: Mapped[str | None] = mapped_column(Text, default=None)
    links: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)

    profile: Mapped[CandidateProfile] = relationship(back_populates="projects")


class Skill(ProfileChildMixin, Base):
    __tablename__ = "skills"

    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str | None] = mapped_column(String(100), default=None)
    proficiency: Mapped[str | None] = mapped_column(String(50), default=None)

    profile: Mapped[CandidateProfile] = relationship(back_populates="skills")


class Education(ProfileChildMixin, Base):
    __tablename__ = "education"

    institution: Mapped[str] = mapped_column(String(255))
    degree: Mapped[str | None] = mapped_column(String(255), default=None)
    field: Mapped[str | None] = mapped_column(String(255), default=None)
    start_date: Mapped[date | None] = mapped_column(Date, default=None)
    end_date: Mapped[date | None] = mapped_column(Date, default=None)

    profile: Mapped[CandidateProfile] = relationship(back_populates="education")
