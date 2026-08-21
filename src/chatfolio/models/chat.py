import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from chatfolio.db.base import Base
from chatfolio.models.mixins import UUIDPrimaryKeyMixin


class MessageRole(StrEnum):
    RECRUITER = "recruiter"
    ASSISTANT = "assistant"


class RecruiterIntent(StrEnum):
    SKILL_INQUIRY = "skill_inquiry"
    PROJECT_INQUIRY = "project_inquiry"
    EXPERIENCE_INQUIRY = "experience_inquiry"
    EDUCATION_INQUIRY = "education_inquiry"
    ROLE_FIT_INQUIRY = "role_fit_inquiry"
    AVAILABILITY_INQUIRY = "availability_inquiry"
    CONTACT_REQUEST = "contact_request"
    GENERAL_INTRODUCTION = "general_introduction"
    UNKNOWN = "unknown"


# Intents where an ungrounded answer risks fabricating a fact — these get the canned "I don't
# have that information" fallback instead of a generation call when retrieval finds nothing
# above the similarity threshold. Small talk / general intro / contact requests still generate
# (using just the approved intro+summary as context) since there's nothing specific to fabricate.
INFORMATIONAL_INTENTS = frozenset(
    {
        RecruiterIntent.SKILL_INQUIRY,
        RecruiterIntent.PROJECT_INQUIRY,
        RecruiterIntent.EXPERIENCE_INQUIRY,
        RecruiterIntent.EDUCATION_INQUIRY,
        RecruiterIntent.AVAILABILITY_INQUIRY,
    }
)


class ChatSession(UUIDPrimaryKeyMixin, Base):
    """The session id itself (a v4 UUID, 122 bits of entropy) is the opaque handle a recruiter's
    browser holds — no separate token column needed on top of an already-unguessable primary key.
    """

    __tablename__ = "chat_sessions"

    chatfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public_chatfolios.id", ondelete="CASCADE"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Null until the first message is answered — deliberately NOT server_default=func.now():
    # if it were set at session-creation time, a recruiter's very first message (sent, as
    # normal, within a second or two of starting the session) would look like a cooldown
    # violation against the session-start timestamp rather than against a previous message.
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    rapid_fire_count: Mapped[int] = mapped_column(Integer, default=0)
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_by_candidate: Mapped[bool] = mapped_column(Boolean, default=False)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.created_at"
    )
    recruiter_metadata: Mapped["RecruiterMetadata | None"] = relationship(
        back_populates="session", cascade="all, delete-orphan", uselist=False
    )


class ChatMessage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "chat_messages"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole, name="message_role"))
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(50), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class RecruiterMetadata(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "recruiter_metadata"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    name: Mapped[str | None] = mapped_column(String(255), default=None)
    company: Mapped[str | None] = mapped_column(String(255), default=None)
    role: Mapped[str | None] = mapped_column(String(255), default=None)
    required_skills: Mapped[str | None] = mapped_column(Text, default=None)
    experience_expectation: Mapped[str | None] = mapped_column(String(255), default=None)
    location_pref: Mapped[str | None] = mapped_column(String(255), default=None)
    timeline: Mapped[str | None] = mapped_column(String(255), default=None)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    session: Mapped[ChatSession] = relationship(back_populates="recruiter_metadata")
